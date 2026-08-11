#!/usr/bin/env python3
"""
Apply the Plaza 94 harvest (KAINC-1018, v2 supplemental-integrated) to the KA
master — run ON WINDOWS from realestate_extractor:

    python portfolio_ownership/apply_plaza94.py

Delivery verified pre-merge: 10,557 provisions / 29 leases / 90 registry docs /
3,816 pages, zero NULL cites, zero orphans, single-key domain; delivery suite
27/27 with 10/10 perturbations firing.

Aggregator note (recorded in wh_framework): the report's summary table says
32 chain rows / 9 suite-history rows / 16 current·13 former; the DELIVERED DB
holds 31 / 11 / 17·12 and its own assertion suite passes against those — the
DB is the artifact of record; the report prose miscounts.

Merge spec (§8): canonical tables on intersection (integer PKs excluded);
own_p94_* registries verbatim; chain (31) + suite-history (11) preserved
verbatim as p94_lease_chain / p94_suite_history; prop layer doc_id offset;
13 OPEN items rolled as P94<id> (no severity declared -> NULL);
own_p94_reg_open_item copied verbatim (incl. 5 resolved, for provenance).

Expected end state: total provisions 74,186 · 18 ownership properties.
"""

import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')
OUTER_ZIP = os.path.join(HERE, 'inbox', 'Lease Modules', 'Plaza 94 Lease Module.zip')
INNER_ZIP = 'PLAZA_94_HARVEST_v2_PART1_CORE.zip'
DB_MEMBER = 'CORE/p94_module.db'

KEY = 'KAINC-1018'
EXPECT_PROV = 10557
DOC_OFFSET_EXPECT = 68


def main():
    if not os.path.exists(MASTER):
        sys.exit(f'Master not found: {MASTER}')
    if os.path.exists(MASTER + '-journal'):
        sys.exit('Hot journal present — restore master from the pristine zip first.')
    if not os.path.exists(OUTER_ZIP):
        sys.exit(f'Delivery not found: {OUTER_ZIP}')

    tmpdir = tempfile.mkdtemp(prefix='p94_')
    with zipfile.ZipFile(OUTER_ZIP) as z:
        z.extract(INNER_ZIP, tmpdir)
    with zipfile.ZipFile(os.path.join(tmpdir, INNER_ZIP)) as z:
        z.extract(DB_MEMBER, tmpdir)
    deliv = os.path.join(tmpdir, DB_MEMBER.replace('/', os.sep))

    m = sqlite3.connect(MASTER)
    d = sqlite3.connect(f'file:{deliv.replace(os.sep, "/")}?mode=ro', uri=True)
    mq = lambda s, p=(): m.execute(s, p).fetchone()[0]
    dq = lambda s: d.execute(s).fetchone()[0]
    cols = lambda c, t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]

    if mq('SELECT count(*) FROM lease_provision WHERE property_id=?', (KEY,)) > 0:
        print('P94 rows already present — merge appears applied. Nothing to do.')
        return

    print('Integrity check on master…')
    if m.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        sys.exit('Master integrity check failed — restore from zip first.')

    if dq('SELECT count(*) FROM lease_provision') != EXPECT_PROV:
        sys.exit('Delivery provision count mismatch — abort.')
    if dq("SELECT count(*) FROM lease_provision WHERE property_id != 'KAINC-1018'"):
        sys.exit('Delivery domain violation — abort.')
    if dq('SELECT count(*) FROM lease_provision WHERE source IS NULL OR source_pages IS NULL'):
        sys.exit('Delivery has NULL citations — abort.')
    if mq('SELECT count(*) FROM dim_property WHERE property_key=?', (KEY,)) != 1:
        sys.exit(f'Spine missing {KEY} — abort.')

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
            ('own_p94_reg_document_registry', 'own_p94_reg_document_registry'),
            ('own_p94_reg_page_text', 'own_p94_reg_page_text'),
            ('lease_lease_chain', 'p94_lease_chain'),
            ('lease_suite_history', 'p94_suite_history'),
            ('own_p94_reg_open_item', 'own_p94_reg_open_item')):
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
    for iid, tk, cat, desc in d.execute(
            "SELECT item_id, tenant_key, category, description FROM own_p94_reg_open_item "
            "WHERE status='open'"):
        ctx = f'[{tk}] ' if tk else ''
        m.execute("INSERT INTO wh_open_item (module_id,item_id,category,description,severity,"
                  "source_table) VALUES ('own',?,?,?,NULL,'own_p94_reg_open_item')",
                  (f'P94{iid}', cat, ctx + (desc or '')))
        rolled += 1
    if rolled != 13:
        fail(f'rolled {rolled} open items != 13')

    try:
        m.execute('INSERT INTO wh_framework (key, value) VALUES (?,?)',
                  ('merge_p94_20260727',
                   'Plaza 94 (KAINC-1018) v2 merged: 10,557 provisions, 29 leases (17 current/12 '
                   'former per DB), 90 registry docs / 3,816 pages, prop layer 1 doc/1 prov '
                   f'(offset +{offset}). Chain (31)/suite-history (11) preserved verbatim as '
                   'p94_lease_chain/p94_suite_history. AGGREGATOR NOTE: report summary table '
                   'says 32 chains/9 hist/16 current-13 former; delivered DB (the artifact of '
                   'record, its own 27-assertion suite passing) holds 31/11/17-12 — report '
                   'prose miscount, DB adopted. Fresenius Art. 29.19 three-mile exclusive '
                   'captured verbatim; status ambiguity flagged for counsel. 13 open items '
                   'rolled as P94<id>.'))
    except sqlite3.Error as e:
        print('note: wh_framework insert skipped ->', e)
    m.execute("UPDATE wh_module SET status = status || ' | Plaza 94 (KAINC-1018) v2 merged "
              "2026-07-27: 10,557 provisions, 29 leases, 90 docs — 18 properties.' "
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
        ('P94 provisions', mq('SELECT count(*) FROM lease_provision WHERE property_id=?', (KEY,)), 10557),
        ('total provisions', mq('SELECT count(*) FROM lease_provision'), 74186),
        ('P94 leases', mq('SELECT count(*) FROM lease_lease WHERE property_id=?', (KEY,)), 29),
        ('registry rows', mq('SELECT count(*) FROM own_p94_reg_document_registry'), 90),
        ('page text rows', mq('SELECT count(*) FROM own_p94_reg_page_text'), 3816),
        ('chains', mq('SELECT count(*) FROM p94_lease_chain'), 31),
        ('suite history', mq('SELECT count(*) FROM p94_suite_history'), 11),
        ('P94 prop docs', mq('SELECT count(*) FROM prop_document WHERE property_id=?', (KEY,)), 1),
        ('doc_id unique', mq('SELECT count(*) - count(DISTINCT doc_id) FROM prop_document'), 0),
        ('NULL cites (P94)', mq('SELECT count(*) FROM lease_provision WHERE property_id=? '
                                'AND (source IS NULL OR source_pages IS NULL)', (KEY,)), 0),
        ('orphan keys (P94)', mq("""SELECT count(DISTINCT p.tenant_key) FROM lease_provision p
                                    LEFT JOIN lease_lease l ON l.property_id=p.property_id
                                    AND l.tenant_key=p.tenant_key
                                    WHERE p.property_id=? AND p.tenant_key IS NOT NULL
                                    AND l.tenant_key IS NULL""", (KEY,)), 0),
        ('ownership span', mq('SELECT count(DISTINCT property_id) FROM lease_provision'), 19),
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
    print('\nMERGE COMMITTED — Plaza 94 applied.')
    print('Next: have Claude re-baseline the verify suite and run it (expect ALL PASS).')


if __name__ == '__main__':
    main()
