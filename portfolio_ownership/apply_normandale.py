#!/usr/bin/env python3
"""
Apply the Normandale Shopping Center harvest (KAINC-1015, rev. 2 wave-2,
delivered 2026-07) to the KA master — run ON WINDOWS from realestate_extractor:

    python portfolio_ownership/apply_normandale.py

Delivery verified pre-merge: counts tie to report (6,376 provisions · 24 lease
rows · 33 registry docs · 2,759 pages · 3 prop docs / 25 prop provisions),
zero NULL cites, zero orphans, single-key domain; delivery suite ALL PASS with
13/13 perturbations firing.

Merge spec (§8): canonical lease_lease/lease_provision on column intersection
(integer PKs excluded); own_nsc_* registries verbatim; chain (7) and
suite-history (8) schemas are richer than canonical — preserved verbatim as
nsc_lease_chain / nsc_suite_history; prop layer with doc_id offset; 12 open
items rolled as NSC<id> (delivery declares severities — carried through);
own_nsc_open_item copied for provenance.

Expected end state: total provisions 63,629 · 17 ownership properties.
"""

import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')
OUTER_ZIP = os.path.join(HERE, 'inbox', 'Lease Modules',
                         'Normandale Shopping Center Lease Module.zip')
INNER_ZIP = 'NSC_HARVEST_PART1_CORE.zip'
DB_MEMBER = 'nsc_module.db'

KEY = 'KAINC-1015'
EXPECT_PROV = 6376


def main():
    if not os.path.exists(MASTER):
        sys.exit(f'Master not found: {MASTER}')
    if os.path.exists(MASTER + '-journal'):
        sys.exit('Hot journal present — restore master from the pristine zip first.')
    if not os.path.exists(OUTER_ZIP):
        sys.exit(f'Delivery not found: {OUTER_ZIP}')

    tmpdir = tempfile.mkdtemp(prefix='nsc_')
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

    if mq("SELECT count(*) FROM lease_provision WHERE property_id=?", (KEY,)) > 0:
        print('NSC rows already present — merge appears applied. Nothing to do.')
        return

    print('Integrity check on master…')
    if m.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        sys.exit('Master integrity check failed — restore from zip first.')

    # delivery sanity from the DB
    if dq('SELECT count(*) FROM lease_provision') != EXPECT_PROV:
        sys.exit('Delivery provision count mismatch — abort.')
    if dq("SELECT count(*) FROM lease_provision WHERE property_id != 'KAINC-1015'"):
        sys.exit('Delivery domain violation — abort.')
    if dq('SELECT count(*) FROM lease_provision WHERE source IS NULL OR source_pages IS NULL'):
        sys.exit('Delivery has NULL citations — abort.')
    if mq('SELECT count(*) FROM dim_property WHERE property_key=?', (KEY,)) != 1:
        sys.exit(f'Spine missing {KEY} — abort.')

    pre = dict(m.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    pre_leases = dict(m.execute('SELECT property_id, count(*) FROM lease_lease GROUP BY property_id'))
    doc_offset = mq('SELECT max(doc_id) FROM prop_document')

    def fail(msg):
        m.execute('ROLLBACK')
        sys.exit(f'ABORT (rolled back): {msg}')

    m.execute('BEGIN')

    # canonical tables, intersection minus integer PKs
    for table, pk in (('lease_lease', 'lease_id'), ('lease_provision', 'prov_id')):
        shared = [c for c in cols(d, table) if c in cols(m, table) and c != pk]
        n = 0
        for r in d.execute(f"SELECT {','.join(shared)} FROM {table}"):
            m.execute(f"INSERT INTO {table} ({','.join(shared)}) "
                      f"VALUES ({','.join('?' * len(shared))})", r)
            n += 1
        print(f'  {table}: +{n}')

    # module tables verbatim (registries + rich chain/history + open items)
    for src_name, dst_name in (
            ('own_nsc_reg_document_registry', 'own_nsc_reg_document_registry'),
            ('own_nsc_reg_page_text', 'own_nsc_reg_page_text'),
            ('lease_lease_chain', 'nsc_lease_chain'),
            ('lease_suite_history', 'nsc_suite_history'),
            ('own_nsc_open_item', 'own_nsc_open_item')):
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

    # prop layer with doc_id offset
    dcols = cols(d, 'prop_document')
    for r in d.execute(f"SELECT {','.join(dcols)} FROM prop_document"):
        row = dict(zip(dcols, r)); row['doc_id'] += doc_offset
        m.execute(f"INSERT INTO prop_document ({','.join(dcols)}) "
                  f"VALUES ({','.join('?' * len(dcols))})", [row[c] for c in dcols])
    ptcols = [c for c in cols(d, 'prop_document_page_text') if c != 'pt_id']
    for r in d.execute(f"SELECT {','.join(ptcols)} FROM prop_document_page_text"):
        row = dict(zip(ptcols, r)); row['doc_id'] += doc_offset
        m.execute(f"INSERT INTO prop_document_page_text ({','.join(ptcols)}) "
                  f"VALUES ({','.join('?' * len(ptcols))})", [row[c] for c in ptcols])
    pvcols = [c for c in cols(d, 'prop_provision') if c != 'prov_id']
    for r in d.execute(f"SELECT {','.join(pvcols)} FROM prop_provision"):
        row = dict(zip(pvcols, r))
        row['doc_id'] += doc_offset
        row['source'] = f"prop_doc_{row['doc_id']}"
        m.execute(f"INSERT INTO prop_provision ({','.join(pvcols)}) "
                  f"VALUES ({','.join('?' * len(pvcols))})", [row[c] for c in pvcols])

    # roll open items with the delivery's declared severities
    rolled = 0
    for iid, sev, cat, desc in d.execute(
            "SELECT item_id, severity, category, description FROM own_nsc_open_item "
            "WHERE status='open'"):
        m.execute("INSERT INTO wh_open_item (module_id,item_id,category,description,severity,"
                  "source_table) VALUES ('own',?,?,?,?,'own_nsc_open_item')",
                  (f'NSC{iid}', cat, desc, sev))
        rolled += 1
    if rolled != 12:
        fail(f'rolled {rolled} open items != 12')

    # framework + module notes
    try:
        m.execute('INSERT INTO wh_framework (key, value) VALUES (?,?)',
                  ('merge_nsc_20260727',
                   'Normandale (KAINC-1015) merged: 6,376 provisions, 24 lease rows incl. 11 '
                   'terminated tenancies at full depth, 33 registry docs / 2,759 pages, prop layer '
                   f'3 docs / 25 prov (doc_id offset +{doc_offset}). Chain (7) / suite-history (8) '
                   'preserved verbatim as nsc_lease_chain / nsc_suite_history (richer than '
                   'canonical shapes). 12 open items rolled as NSC<id> with delivered severities '
                   '(HIGH: EASE.0 instrument, Calm Water holdover economics, Vixen Seventh '
                   'Amendment missing-instrument).'))
    except sqlite3.Error as e:
        print('note: wh_framework insert skipped ->', e)
    m.execute("UPDATE wh_module SET status = status || ' | Normandale (KAINC-1015) merged "
              "2026-07-27: 6,376 provisions, 24 leases, 33 docs — 17 properties.' "
              "WHERE module_id='own'")

    # survival + post checks
    post = dict(m.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    for pid, n in pre.items():
        if post.get(pid) != n:
            fail(f'survival: {pid} provisions changed {n} -> {post.get(pid)}')
    post_leases = dict(m.execute('SELECT property_id, count(*) FROM lease_lease GROUP BY property_id'))
    for pid, n in pre_leases.items():
        if post_leases.get(pid) != n:
            fail(f'survival: {pid} lease count changed {n} -> {post_leases.get(pid)}')

    checks = [
        ('NSC provisions', mq('SELECT count(*) FROM lease_provision WHERE property_id=?', (KEY,)), 6376),
        ('total provisions', mq('SELECT count(*) FROM lease_provision'), 63629),
        ('NSC leases', mq('SELECT count(*) FROM lease_lease WHERE property_id=?', (KEY,)), 24),
        ('registry rows', mq('SELECT count(*) FROM own_nsc_reg_document_registry'), 33),
        ('page text rows', mq('SELECT count(*) FROM own_nsc_reg_page_text'), 2759),
        ('chains', mq('SELECT count(*) FROM nsc_lease_chain'), 7),
        ('suite history', mq('SELECT count(*) FROM nsc_suite_history'), 8),
        ('NSC prop docs', mq('SELECT count(*) FROM prop_document WHERE property_id=?', (KEY,)), 3),
        ('NSC prop provisions', mq('SELECT count(*) FROM prop_provision pp JOIN prop_document pd '
                                   'ON pd.doc_id=pp.doc_id WHERE pd.property_id=?', (KEY,)), 25),
        ('doc_id unique', mq('SELECT count(*) - count(DISTINCT doc_id) FROM prop_document'), 0),
        ('NULL cites (NSC)', mq('SELECT count(*) FROM lease_provision WHERE property_id=? '
                                'AND (source IS NULL OR source_pages IS NULL)', (KEY,)), 0),
        ('orphan keys (NSC)', mq("""SELECT count(DISTINCT p.tenant_key) FROM lease_provision p
                                    LEFT JOIN lease_lease l ON l.property_id=p.property_id
                                    AND l.tenant_key=p.tenant_key
                                    WHERE p.property_id=? AND p.tenant_key IS NOT NULL
                                    AND l.tenant_key IS NULL""", (KEY,)), 0),
        ('ownership span', mq('SELECT count(DISTINCT property_id) FROM lease_provision'), 18),
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
    print('\nMERGE COMMITTED — Normandale applied.')
    print('Next: have Claude re-baseline the verify suite and run it (expect ALL PASS).')


if __name__ == '__main__':
    main()
