#!/usr/bin/env python3
"""
Apply the Gateway Business Park harvest (GW I/II/III, delivered 2026-07-21)
to the KA master portfolio_warehouse.db — run ON WINDOWS from the
realestate_extractor folder:

    python portfolio_ownership/apply_gateway.py

Multi-building delivery per the Maplewood pattern: one module DB, three real
spine keys (KAINC-1008 / KAINC-1009 / KAINC-1010), never a consolidated key.
Delivery verified pre-merge (all assertions pass; 23/23 perturbations fire;
16/16 roster reconciliations exact).

Merge spec (handoff §8):
  - lease_lease (41) + lease_provision (8,885): column intersection,
    integer PKs excluded.
  - own_gbp_reg_document_registry (95; incl. 1 honest NULL-building Farmers
    doc) + own_gbp_reg_page_text (3,934): new tables, verbatim.
  - Chain (6) + suite-history (27): delivered schemas are RICHER than the
    master's canonical shapes (relocation web: from/to building+suite,
    instruments). Copied verbatim as gbp_lease_chain / gbp_suite_history to
    avoid destroying data via intersection; framework note records this.
  - prop layer (1 doc / 3 provisions): doc_id offset +64, source rewrite.
  - gbp_open_item (43) copied verbatim for provenance; the 38 OPEN items
    rolled into wh_open_item as GBP<id> (severity not declared -> NULL).
Expected end state: total provisions 57,253 · GW-I 2,443 · GW-II 2,734 ·
GW-III 3,708 · 16 ownership properties.
"""

import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')
OUTER_ZIP = os.path.join(HERE, 'inbox', 'Lease Modules', 'Gateway Lease Module.zip')
INNER_ZIP = 'GBP_LEASE_MODULE_HARVEST_PART1.zip'
DB_MEMBER = 'gbp_module.db'

KEYS = ('KAINC-1008', 'KAINC-1009', 'KAINC-1010')
EXPECT = {'KAINC-1008': 2443, 'KAINC-1009': 2734, 'KAINC-1010': 3708}
DOC_OFFSET_EXPECT = 64


def main():
    if not os.path.exists(MASTER):
        sys.exit(f'Master not found: {MASTER}')
    if os.path.exists(MASTER + '-journal'):
        sys.exit('Hot journal present — restore master from the pristine zip first.')
    if not os.path.exists(OUTER_ZIP):
        sys.exit(f'Delivery not found: {OUTER_ZIP}')

    tmpdir = tempfile.mkdtemp(prefix='gbp_')
    with zipfile.ZipFile(OUTER_ZIP) as z:
        z.extract(INNER_ZIP, tmpdir)
    with zipfile.ZipFile(os.path.join(tmpdir, INNER_ZIP)) as z:
        z.extract(DB_MEMBER, tmpdir)
    deliv = os.path.join(tmpdir, DB_MEMBER)

    m = sqlite3.connect(MASTER)
    d = sqlite3.connect(f'file:{deliv.replace(os.sep, "/")}?mode=ro', uri=True)
    mq = lambda s, p=(): m.execute(s, p).fetchone()[0]
    cols = lambda c, t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]

    # idempotency
    if mq("SELECT count(*) FROM lease_provision WHERE property_id='KAINC-1008'") > 0:
        print('GBP rows already present — merge appears applied. Nothing to do.')
        return

    print('Integrity check on master…')
    ic = m.execute('PRAGMA integrity_check').fetchone()[0]
    if ic != 'ok':
        sys.exit(f'Master integrity check failed: {ic}')

    # delivery sanity (verify from the DB, never the report)
    dq = lambda s: d.execute(s).fetchone()[0]
    got = dict(d.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    if got != EXPECT:
        sys.exit(f'Delivery counts {got} != expected {EXPECT}')
    if dq("SELECT count(*) FROM lease_provision WHERE source IS NULL OR source_pages IS NULL"):
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

    # 1. canonical tables on column intersection, integer PKs excluded
    for table, pk in (('lease_lease', 'lease_id'), ('lease_provision', 'prov_id')):
        shared = [c for c in cols(d, table) if c in cols(m, table) and c != pk]
        n = 0
        for r in d.execute(f"SELECT {','.join(shared)} FROM {table}"):
            m.execute(f"INSERT INTO {table} ({','.join(shared)}) "
                      f"VALUES ({','.join('?' * len(shared))})", r)
            n += 1
        print(f'  {table}: +{n}')

    # 2. registries + rich supplemental tables: verbatim under module names
    for src_name, dst_name in (
            ('own_gbp_reg_document_registry', 'own_gbp_reg_document_registry'),
            ('own_gbp_reg_page_text', 'own_gbp_reg_page_text'),
            ('lease_lease_chain', 'gbp_lease_chain'),
            ('lease_suite_history', 'gbp_suite_history'),
            ('gbp_open_item', 'gbp_open_item')):
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

    # 3. prop layer with doc_id offset
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

    # 4. roll OPEN items into wh_open_item as GBP<id>
    rolled = 0
    for iid, pid, tk, cat, detail in d.execute(
            "SELECT item_id, property_id, tenant_key, category, detail FROM gbp_open_item "
            "WHERE status='open'"):
        ctx = ' / '.join(x for x in (pid, tk) if x)
        m.execute("INSERT INTO wh_open_item (module_id,item_id,category,description,severity,"
                  "source_table) VALUES ('own',?,?,?,NULL,'gbp_open_item')",
                  (f'GBP{iid}', cat, (f'[{ctx}] ' if ctx else '') + (detail or '')))
        rolled += 1
    if rolled != 38:
        fail(f'rolled {rolled} open items != 38')

    # 5. framework + module notes
    try:
        m.execute('INSERT INTO wh_framework (key, value) VALUES (?,?)',
                  ('merge_gbp_20260721',
                   'Gateway Business Park I/II/III merged (3 spine keys, one delivery, Maplewood '
                   'pattern): 8,885 provisions, 41 leases, 95 registry docs (1 honest NULL-building '
                   'Farmers doc, zero provisions), 3,934 pages, prop layer +1 doc/+3 prov (offset '
                   '+64). Chain (6) and suite-history (27) schemas are richer than canonical '
                   'lease_lease_chain/lease_suite_history — preserved verbatim as gbp_lease_chain/'
                   'gbp_suite_history rather than lossy intersection. 38 open items rolled as '
                   'GBP<id>; gbp_open_item copied for provenance. Stronghouse identity remains '
                   'candidate-only (legal_tenant Capital Construction, LLC).'))
    except sqlite3.Error as e:
        print('note: wh_framework insert skipped ->', e)
    m.execute("UPDATE wh_module SET status = status || ' | Gateway I/II/III (KAINC-1008/1009/1010) "
              "merged 2026-07-27: 8,885 provisions, 41 leases, 95 docs — 16 properties.' "
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
        ('GW-I provisions', mq("SELECT count(*) FROM lease_provision WHERE property_id='KAINC-1008'"), 2443),
        ('GW-II provisions', mq("SELECT count(*) FROM lease_provision WHERE property_id='KAINC-1009'"), 2734),
        ('GW-III provisions', mq("SELECT count(*) FROM lease_provision WHERE property_id='KAINC-1010'"), 3708),
        ('total provisions', mq('SELECT count(*) FROM lease_provision'), 57253),
        ('GBP leases', mq(f"SELECT count(*) FROM lease_lease WHERE property_id IN {KEYS}"), 41),
        ('registry rows', mq('SELECT count(*) FROM own_gbp_reg_document_registry'), 95),
        ('page text rows', mq('SELECT count(*) FROM own_gbp_reg_page_text'), 3934),
        ('chains', mq('SELECT count(*) FROM gbp_lease_chain'), 6),
        ('suite history', mq('SELECT count(*) FROM gbp_suite_history'), 27),
        ('GBP prop docs', mq(f"SELECT count(*) FROM prop_document WHERE property_id IN {KEYS}"), 1),
        ('doc_id unique', mq('SELECT count(*) - count(DISTINCT doc_id) FROM prop_document'), 0),
        ('NULL cites (GBP)', mq(f"SELECT count(*) FROM lease_provision WHERE property_id IN {KEYS} "
                                'AND (source IS NULL OR source_pages IS NULL)'), 0),
        ('orphan keys (GBP)', mq(f"""SELECT count(DISTINCT p.tenant_key) FROM lease_provision p
                                     LEFT JOIN lease_lease l ON l.property_id=p.property_id
                                     AND l.tenant_key=p.tenant_key
                                     WHERE p.property_id IN {KEYS} AND p.tenant_key IS NOT NULL
                                     AND l.tenant_key IS NULL"""), 0),
        ('ownership span', mq('SELECT count(DISTINCT property_id) FROM lease_provision'), 17),
    ]
    # NOTE on span: 17 distinct property_ids = 16 ownership properties
    # (Maplewood I+II = two keys, one property) + Gateway adds three keys.
    ok = True
    for name, got_v, exp in checks:
        s = 'PASS' if got_v == exp else 'FAIL'
        if got_v != exp:
            ok = False
        print(f'  [{s}] {name}: {got_v} (expect {exp})')
    if not ok:
        fail('post-merge checks failed')

    m.execute('COMMIT')
    print('\nMERGE COMMITTED — Gateway I/II/III applied.')
    print('Next: have Claude re-baseline the verify suite and run it (expect ALL PASS).')


if __name__ == '__main__':
    main()
