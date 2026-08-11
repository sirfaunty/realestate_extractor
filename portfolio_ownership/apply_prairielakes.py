#!/usr/bin/env python3
"""
Apply the Prairie Lakes Corporate Center I & II harvest (KAINC-1019 /
KAINC-1020, one delivery, two real spine keys) to the KA master — run ON
WINDOWS from realestate_extractor:

    python portfolio_ownership/apply_prairielakes.py

Delivery verified pre-merge: 4,317 provisions (1,727 Bldg I / 2,590 Bldg II) ·
25 leases · 75 registry docs · 2,764 pages · 5 prop docs / 51 prop provisions ·
zero NULL cites · zero orphans · two-key domain clean; delivery suite ALL PASS
with 21/21 perturbations firing and a QUIET clean control.

Merge spec (§8, Maplewood/Gateway multi-building pattern): canonical tables on
intersection (integer PKs excluded); own_plc_* registries verbatim; chain (18),
suite-history (25) and roster-recon (7) preserved verbatim as plc_lease_chain /
plc_suite_history / plc_roster_recon; prop layer doc_id offset; 17 non-resolved
open items rolled as PLC<id> with delivered severities; lease_open_item copied
verbatim as plc_open_item (18 rows incl. 1 resolved, provenance).

Expected end state: total provisions 78,503 · 20 ownership properties.
"""

import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')
OUTER_ZIP = os.path.join(HERE, 'inbox', 'Lease Modules', 'Prairie Lakes Lease Module.zip')
INNER_ZIP = 'PLC_LEASE_MODULE_Part1_CORE.zip'
DB_MEMBER = 'plc_module.db'

KEYS = ('KAINC-1019', 'KAINC-1020')
EXPECT = {'KAINC-1019': 1727, 'KAINC-1020': 2590}
DOC_OFFSET_EXPECT = 69


def main():
    if not os.path.exists(MASTER):
        sys.exit(f'Master not found: {MASTER}')
    if os.path.exists(MASTER + '-journal'):
        sys.exit('Hot journal present — restore master from the pristine zip first.')
    if not os.path.exists(OUTER_ZIP):
        sys.exit(f'Delivery not found: {OUTER_ZIP}')

    tmpdir = tempfile.mkdtemp(prefix='plc_')
    with zipfile.ZipFile(OUTER_ZIP) as z:
        z.extract(INNER_ZIP, tmpdir)
    with zipfile.ZipFile(os.path.join(tmpdir, INNER_ZIP)) as z:
        z.extract(DB_MEMBER, tmpdir)
    deliv = os.path.join(tmpdir, DB_MEMBER)

    m = sqlite3.connect(MASTER)
    d = sqlite3.connect(f'file:{deliv.replace(os.sep, "/")}?mode=ro', uri=True)
    mq = lambda s, p=(): m.execute(s, p).fetchone()[0]
    dq = lambda s: d.execute(s).fetchone()[0]
    cols = lambda c, t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]

    if mq("SELECT count(*) FROM lease_provision WHERE property_id='KAINC-1019'") > 0:
        print('PLC rows already present — merge appears applied. Nothing to do.')
        return

    print('Integrity check on master…')
    if m.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        sys.exit('Master integrity check failed — restore from zip first.')

    got = dict(d.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    if got != EXPECT:
        sys.exit(f'Delivery counts {got} != expected {EXPECT}')
    if dq('SELECT count(*) FROM lease_provision WHERE source IS NULL OR source_pages IS NULL'):
        sys.exit('Delivery has NULL citations — abort.')
    for k in KEYS:
        if mq('SELECT count(*) FROM dim_property WHERE property_key=?', (k,)) != 1:
            sys.exit(f'Spine missing {k} — abort.')

    pre = dict(m.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    pre_leases = dict(m.execute('SELECT property_id, count(*) FROM lease_lease GROUP BY property_id'))

    def fail(msg):
        m.execute('ROLLBACK')
        sys.exit(f'ABORT (rolled back): {msg}')

    m.execute('BEGIN')

    for table, pk in (('lease_lease', 'lease_id'), ('lease_provision', 'prov_id')):
        shared = [c for c in cols(d, table) if c in cols(m, table) and c != pk]
        n = 0
        for r in d.execute(f"SELECT {','.join(shared)} FROM {table}"):
            m.execute(f"INSERT INTO {table} ({','.join(shared)}) "
                      f"VALUES ({','.join('?' * len(shared))})", r)
            n += 1
        print(f'  {table}: +{n}')

    for src_name, dst_name in (
            ('own_plc_reg_document_registry', 'own_plc_reg_document_registry'),
            ('own_plc_reg_page_text', 'own_plc_reg_page_text'),
            ('lease_lease_chain', 'plc_lease_chain'),
            ('lease_suite_history', 'plc_suite_history'),
            ('lease_roster_recon', 'plc_roster_recon'),
            ('lease_open_item', 'plc_open_item')):
        sql = d.execute('SELECT sql FROM sqlite_master WHERE name=?', (src_name,)).fetchone()[0]
        if dst_name != src_name:
            sql = sql.replace(src_name, dst_name, 1)
        m.execute(sql)
        ccols = cols(d, src_name)
        n = 0
        for r in d.execute(f"SELECT {','.join(ccols)} FROM {src_name}"):
            m.execute(f"INSERT INTO {dst_name} ({','.join(ccols)}) "
                      f"VALUES ({','.join('?' * len(ccols))})", r)
            n += 1
        print(f'  {dst_name}: +{n}')

    offset = mq('SELECT max(doc_id) FROM prop_document')
    if offset != DOC_OFFSET_EXPECT:
        fail(f'unexpected master prop_document max doc_id {offset} (expect {DOC_OFFSET_EXPECT})')
    dcols = cols(d, 'prop_document')
    for r in d.execute(f"SELECT {','.join(dcols)} FROM prop_document"):
        row = dict(zip(dcols, r)); row['doc_id'] += offset
        m.execute(f"INSERT INTO prop_document ({','.join(dcols)}) "
                  f"VALUES ({','.join('?' * len(dcols))})", [row[c] for c in dcols])
    ptcols = [c for c in cols(d, 'prop_document_page_text') if c != 'pt_id']
    for r in d.execute(f"SELECT {','.join(ptcols)} FROM prop_document_page_text"):
        row = dict(zip(ptcols, r)); row['doc_id'] += offset
        m.execute(f"INSERT INTO prop_document_page_text ({','.join(ptcols)}) "
                  f"VALUES ({','.join('?' * len(ptcols))})", [row[c] for c in ptcols])
    pvcols = [c for c in cols(d, 'prop_provision') if c != 'prov_id']
    for r in d.execute(f"SELECT {','.join(pvcols)} FROM prop_provision"):
        row = dict(zip(pvcols, r))
        row['doc_id'] += offset
        row['source'] = f"prop_doc_{row['doc_id']}"
        m.execute(f"INSERT INTO prop_provision ({','.join(pvcols)}) "
                  f"VALUES ({','.join('?' * len(pvcols))})", [row[c] for c in pvcols])

    rolled = 0
    for iid, pid, item, sev, detail in d.execute(
            "SELECT item_id, property_id, item, severity, detail FROM lease_open_item "
            "WHERE status != 'resolved'"):
        m.execute("INSERT INTO wh_open_item (module_id,item_id,category,description,severity,"
                  "source_table) VALUES ('own',?,?,?,?,'plc_open_item')",
                  (f'PLC{iid}', item, f'[{pid}] {detail or ""}', sev))
        rolled += 1
    if rolled != 17:
        fail(f'rolled {rolled} open items != 17')

    try:
        m.execute('INSERT INTO wh_framework (key, value) VALUES (?,?)',
                  ('merge_plc_20260727',
                   'Prairie Lakes I & II (KAINC-1019/1020) merged, two real spine keys one '
                   'delivery: 4,317 provisions (1,727/2,590), 25 leases, 75 registry docs / '
                   f'2,764 pages, prop layer 5 docs / 51 prov (offset +{offset}). Chain (18) / '
                   'suite-history (25) / roster-recon (7) preserved verbatim as plc_* tables. '
                   'Valeo expiry discrepancy (roster 11-30-2034 vs lease 9/30/2034) stays open '
                   '— document governs. 17 open items rolled as PLC<id> with delivered '
                   'severities.'))
    except sqlite3.Error as e:
        print('note: wh_framework insert skipped ->', e)
    m.execute("UPDATE wh_module SET status = status || ' | Prairie Lakes I/II (KAINC-1019/1020) "
              "merged 2026-07-27: 4,317 provisions, 25 leases, 75 docs — 20 properties.' "
              "WHERE module_id='own'")

    post = dict(m.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    for pid, n in pre.items():
        if post.get(pid) != n:
            fail(f'survival: {pid} provisions changed {n} -> {post.get(pid)}')
    post_leases = dict(m.execute('SELECT property_id, count(*) FROM lease_lease GROUP BY property_id'))
    for pid, n in pre_leases.items():
        if post_leases.get(pid) != n:
            fail(f'survival: {pid} lease count changed {n} -> {post_leases.get(pid)}')

    checks = [
        ('PLC-I provisions', mq("SELECT count(*) FROM lease_provision WHERE property_id='KAINC-1019'"), 1727),
        ('PLC-II provisions', mq("SELECT count(*) FROM lease_provision WHERE property_id='KAINC-1020'"), 2590),
        ('total provisions', mq('SELECT count(*) FROM lease_provision'), 78503),
        ('PLC leases', mq(f"SELECT count(*) FROM lease_lease WHERE property_id IN {KEYS}"), 25),
        ('registry rows', mq('SELECT count(*) FROM own_plc_reg_document_registry'), 75),
        ('page text rows', mq('SELECT count(*) FROM own_plc_reg_page_text'), 2764),
        ('chains', mq('SELECT count(*) FROM plc_lease_chain'), 18),
        ('suite history', mq('SELECT count(*) FROM plc_suite_history'), 25),
        ('roster recon', mq('SELECT count(*) FROM plc_roster_recon'), 7),
        ('PLC prop docs', mq(f"SELECT count(*) FROM prop_document WHERE property_id IN {KEYS}"), 5),
        ('doc_id unique', mq('SELECT count(*) - count(DISTINCT doc_id) FROM prop_document'), 0),
        ('NULL cites (PLC)', mq(f"SELECT count(*) FROM lease_provision WHERE property_id IN {KEYS} "
                                'AND (source IS NULL OR source_pages IS NULL)'), 0),
        ('orphan keys (PLC)', mq(f"""SELECT count(DISTINCT p.tenant_key) FROM lease_provision p
                                     LEFT JOIN lease_lease l ON l.property_id=p.property_id
                                     AND l.tenant_key=p.tenant_key
                                     WHERE p.property_id IN {KEYS} AND p.tenant_key IS NOT NULL
                                     AND l.tenant_key IS NULL"""), 0),
        ('ownership span', mq('SELECT count(DISTINCT property_id) FROM lease_provision'), 21),
    ]
    ok = True
    for name, got_v, exp in checks:
        s = 'PASS' if got_v == exp else 'FAIL'
        if got_v != exp:
            ok = False
        print(f'  [{s}] {name}: {got_v} (expect {exp})')
    if not ok:
        fail('post-merge checks failed')

    m.execute('COMMIT')
    print('\nMERGE COMMITTED — Prairie Lakes I & II applied.')
    print('Next: have Claude re-baseline the verify suite and run it (expect ALL PASS).')


if __name__ == '__main__':
    main()
