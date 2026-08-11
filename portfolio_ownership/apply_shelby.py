#!/usr/bin/env python3
"""
Apply the Shelby Mall harvest (KAINC-1024) to the KA master — run ON WINDOWS
from realestate_extractor:

    python portfolio_ownership/apply_shelby.py

Delivery verified pre-merge: 3,468 lease provisions + 381 prop provisions ·
22 leases · 54 registry docs / 998 pages · 20 prop docs (4 referenced-not-
provided, zero provisions on them) · zero NULL cites · zero orphans ·
single-key domain; delivery suite ALL PASS with 8/8 perturbations firing
(including the no-fabricated-ground-lease guard).

Headline finding preserved: NO ground lease exists on the pad — the Shopko
parcel is FEE under the 1977 Cross-Easement/First-Refusal/Party-Wall REA;
succession by fee conveyance (Shopko -> Lacrosse Shopko Properties ->
2809 Losey Blvd LLC). Roster 0PAD.0/EASE.0 typed as REA income, NOT tenancies.

Merge spec (§8): canonical tables on intersection (integer PKs excluded);
own_shm_* registries verbatim; chain (7) preserved verbatim as
shm_lease_chain; prop layer doc_id offset; 13 open items rolled as SHM<id>
(no severity declared -> NULL); own_shm_open_item copied verbatim.

Expected end state: total provisions 81,971 · 21 ownership properties.
"""

import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')
OUTER_ZIP = os.path.join(HERE, 'inbox', 'Lease Modules', 'Shelby Mall Lease Module.zip')
INNER_ZIP = 'SHELBY_MALL_HARVEST_Part1_CORE.zip'
DB_MEMBER = 'shelby_mall_module.db'

KEY = 'KAINC-1024'
DOC_OFFSET_EXPECT = 74


def main():
    if not os.path.exists(MASTER):
        sys.exit(f'Master not found: {MASTER}')
    if os.path.exists(MASTER + '-journal'):
        sys.exit('Hot journal present — restore master from the pristine zip first.')
    if not os.path.exists(OUTER_ZIP):
        sys.exit(f'Delivery not found: {OUTER_ZIP}')

    tmpdir = tempfile.mkdtemp(prefix='shm_')
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

    if mq('SELECT count(*) FROM lease_provision WHERE property_id=?', (KEY,)) > 0:
        print('SHM rows already present — merge appears applied. Nothing to do.')
        return

    print('Integrity check on master…')
    if m.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        sys.exit('Master integrity check failed — restore from zip first.')

    if dq('SELECT count(*) FROM lease_provision') != 3468:
        sys.exit('Delivery provision count mismatch — abort.')
    if dq("SELECT count(*) FROM lease_provision WHERE property_id != 'KAINC-1024'"):
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
            ('own_shm_reg_document_registry', 'own_shm_reg_document_registry'),
            ('own_shm_reg_page_text', 'own_shm_reg_page_text'),
            ('lease_lease_chain', 'shm_lease_chain'),
            ('own_shm_open_item', 'own_shm_open_item')):
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
    for iid, cat, detail in d.execute(
            "SELECT item_id, category, detail FROM own_shm_open_item WHERE status='open'"):
        m.execute("INSERT INTO wh_open_item (module_id,item_id,category,description,severity,"
                  "source_table) VALUES ('own',?,?,?,NULL,'own_shm_open_item')",
                  (f'SHM{iid}', cat, detail or ''))
        rolled += 1
    if rolled != 13:
        fail(f'rolled {rolled} open items != 13')

    try:
        m.execute('INSERT INTO wh_framework (key, value) VALUES (?,?)',
                  ('merge_shm_20260727',
                   'Shelby Mall (KAINC-1024) merged: 3,468 lease + 381 prop provisions, 22 '
                   'leases, 54 registry docs / 998 pages, 20 prop docs incl. the 1977 REA chain '
                   f'(offset +{offset}; 4 referenced-not-provided carry zero provisions). '
                   'HEADLINE: no ground lease exists — Shopko pad is FEE under the 1977 REA; '
                   'succession by fee conveyance to 2809 Losey Blvd LLC; 0PAD.0/EASE.0 typed as '
                   'REA income, not tenancies. Chain (7) preserved verbatim as shm_lease_chain. '
                   '13 open items rolled as SHM<id>.'))
    except sqlite3.Error as e:
        print('note: wh_framework insert skipped ->', e)
    m.execute("UPDATE wh_module SET status = status || ' | Shelby Mall (KAINC-1024) merged "
              "2026-07-27: 3,468 provisions, 22 leases, 54 docs — 21 properties.' "
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
        ('SHM provisions', mq('SELECT count(*) FROM lease_provision WHERE property_id=?', (KEY,)), 3468),
        ('total provisions', mq('SELECT count(*) FROM lease_provision'), 81971),
        ('SHM leases', mq('SELECT count(*) FROM lease_lease WHERE property_id=?', (KEY,)), 22),
        ('registry rows', mq('SELECT count(*) FROM own_shm_reg_document_registry'), 54),
        ('page text rows', mq('SELECT count(*) FROM own_shm_reg_page_text'), 998),
        ('chains', mq('SELECT count(*) FROM shm_lease_chain'), 7),
        ('SHM prop docs', mq('SELECT count(*) FROM prop_document WHERE property_id=?', (KEY,)), 20),
        ('SHM prop provisions', mq('SELECT count(*) FROM prop_provision pp JOIN prop_document pd '
                                   'ON pd.doc_id=pp.doc_id WHERE pd.property_id=?', (KEY,)), 381),
        ('doc_id unique', mq('SELECT count(*) - count(DISTINCT doc_id) FROM prop_document'), 0),
        ('NULL cites (SHM)', mq('SELECT count(*) FROM lease_provision WHERE property_id=? '
                                'AND (source IS NULL OR source_pages IS NULL)', (KEY,)), 0),
        ('orphan keys (SHM)', mq("""SELECT count(DISTINCT p.tenant_key) FROM lease_provision p
                                    LEFT JOIN lease_lease l ON l.property_id=p.property_id
                                    AND l.tenant_key=p.tenant_key
                                    WHERE p.property_id=? AND p.tenant_key IS NOT NULL
                                    AND l.tenant_key IS NULL""", (KEY,)), 0),
        ('ownership span', mq('SELECT count(DISTINCT property_id) FROM lease_provision'), 22),
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
    print('\nMERGE COMMITTED — Shelby Mall applied.')
    print('Next: have Claude re-baseline the verify suite and run it (expect ALL PASS).')


if __name__ == '__main__':
    main()
