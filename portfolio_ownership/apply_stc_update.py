#!/usr/bin/env python3
"""
Apply the Southtown update-harvest (ADDITIVE_SUPPLEMENT, delivered 2026-07-22)
to the KA master portfolio_warehouse.db — run this ON WINDOWS with:

    python portfolio_ownership\\apply_stc_update.py

Run from the realestate_extractor folder. It is transactional: it verifies
the delivery, applies the merge, runs all post-merge checks, and only then
commits. If anything mismatches, it rolls back and reports.

Merge spec (per SOUTHTOWN_UPDATE_REPORT.md + aggregator handoff §7/§8):
  - Mode B: additive; prior STC rows preserved except the four documented
    re-extracted tenants (delete-and-reinsert): dicks_house_of_sport,
    schulers, centurylink, tj_maxx.
    (Note: the report believed tj_maxx was already at 195 in the master;
    the master actually held 158 — verified discrepancy; replacement is
    covered by the report's own "safest integration" instruction.)
  - 10 lease_lease tenant_key patches (NULL -> slug, matched on trade_name).
  - Registry rows + page text replaced for the 4 superseded scans
    (file_ids 2, 13, 14, 29).
  - NEW prop layer: 28 prop_document (+doc_id offset 36), 3 page-text rows,
    8 prop_provision (source rewritten to prop_doc_<newid>).
  - NEW own_stc_encumbrance_schedule (28 rows; 1 captured + 27
    referenced-not-provided).
  - 8 open items rolled into wh_open_item as STCU1-8 (severity not declared
    by the delivery; left NULL).
  - wh_framework + wh_module notes.
Expected end state: STC provisions 4,445 · total 48,368 · STC NULL keys 0.
"""

import os
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')
DELIVERY_ZIP = os.path.join(HERE, 'inbox', 'Lease Modules',
                            'Southtown_Lease_Module_UPDATE_20260722.zip')
DB_MEMBER = 'Part1_CORE/southtown_lease_module.db'

REPL = ('dicks_house_of_sport', 'schulers', 'centurylink', 'tj_maxx')
REPL_FIDS = (2, 13, 14, 29)

ITEMS = [
 ('STCU1', 'roster', "lease_lease 45 baseline vs 43 in delivery fork; two tenancy rows unaccounted for in the delivery working copy (reason for Mode B). Master roster remains authoritative."),
 ('STCU2', 'encumbrance', "27 recorded instruments referenced-not-provided on Exhibit B title schedule; pull from Hennepin County. Priority: 5858332 parking easement, 5858330/31 access easements, 7865761 condemnation, A10413528 CUP, 6576737 McDonald's covenant."),
 ('STCU3', 'document_gap', "Kohl's Exhibit A legal description not recovered from scan; needed for parking denominator."),
 ('STCU4', 'boundary', '"Shopping Center" boundary differs by lease (TJ Maxx vs Five Below definitions); title/survey overlay required before parking ratios computed.'),
 ('STCU5', 'boundary', "Kohl's Tract boundary moved (1st Amendment s6 parcel release, s5 site plan replacement); 439-stall minimum must be re-tested."),
 ('STCU6', 'co_tenancy', "Co-tenancy status is textual analysis, not confirmed default. Petco requires first-quality NATIONAL retail tenant; Slumberland (former BB&B premises) classified regional elsewhere. Verify against rent roll."),
 ('STCU7', 'reconciliation', "Southtown MRI roster (1026/1027/1028) not in master fin_rent_roll_snapshot; occupancy reconciliation blocked."),
 ('STCU8', 'status_unverified', "Gamestop 2017 expiry / Massage Envy litigation carried forward from correspondence only; not verified against a document."),
]


def main():
    if not os.path.exists(MASTER):
        sys.exit(f'Master not found: {MASTER}')
    if not os.path.exists(DELIVERY_ZIP):
        sys.exit(f'Delivery zip not found: {DELIVERY_ZIP}')
    if os.path.exists(MASTER + '-journal'):
        sys.exit('A hot journal exists next to the master. Restore the master '
                 'from the pristine zip first (delete portfolio_warehouse.db AND '
                 'portfolio_warehouse.db-journal, then re-extract), then re-run.')

    # extract delivery DB to a temp file
    tmpdir = tempfile.mkdtemp(prefix='stc_update_')
    with zipfile.ZipFile(DELIVERY_ZIP) as z:
        z.extract(DB_MEMBER, tmpdir)
    deliv_path = os.path.join(tmpdir, DB_MEMBER.replace('/', os.sep))

    m = sqlite3.connect(MASTER)
    d = sqlite3.connect(f'file:{deliv_path.replace(os.sep, "/")}?mode=ro', uri=True)
    mq = lambda s, p=(): m.execute(s, p).fetchone()[0]

    # idempotency: already applied?
    if mq("SELECT count(*) FROM lease_provision WHERE property_id='STC'") == 4445:
        print('STC already at 4,445 provisions — update appears applied. Nothing to do.')
        return

    print('Integrity check on master…')
    ic = m.execute('PRAGMA integrity_check').fetchone()[0]
    if ic != 'ok':
        sys.exit(f'Master integrity check failed: {ic} — restore from zip first.')

    # pre-state
    base = mq("SELECT count(*) FROM lease_provision WHERE property_id='STC'")
    if base != 4165:
        sys.exit(f'Unexpected STC baseline {base} (expected 4,165) — investigate before merging.')

    pre = dict(m.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    pre_leases = dict(m.execute('SELECT property_id, count(*) FROM lease_lease GROUP BY property_id'))

    def fail(msg):
        m.execute('ROLLBACK')
        sys.exit(f'ABORT (rolled back): {msg}')

    m.execute('BEGIN')

    # 1. tenant_key patch
    patched = 0
    for tk, trade in d.execute('SELECT tenant_key, trade_name FROM lease_lease_stc_update'):
        cur = m.execute("""UPDATE lease_lease SET tenant_key=? WHERE property_id='STC'
                           AND trade_name=? AND (tenant_key IS NULL OR tenant_key='')""",
                        (tk, trade))
        patched += cur.rowcount
    if patched != 10:
        fail(f'tenant_key patch count {patched} != 10')

    # 2. provisions: replace the four re-extracted tenants
    delc = m.execute(
        f"DELETE FROM lease_provision WHERE property_id='STC' AND tenant_key IN {REPL}").rowcount
    if delc != 695:
        fail(f'deleted {delc} != 695')
    ins = 0
    for r in d.execute(f"""SELECT property_id, tenant_key, category, detail, source, source_pages
                           FROM lease_provision_stc WHERE tenant_key IN {REPL}"""):
        m.execute("""INSERT INTO lease_provision
                     (property_id, tenant_key, category, detail, source, source_pages)
                     VALUES (?,?,?,?,?,?)""", r)
        ins += 1
    if ins != 975:
        fail(f'inserted {ins} != 975')

    # 3. registry rows for superseded scans
    rcols = [r[1] for r in d.execute('PRAGMA table_info(own_stc_reg_document_registry)')]
    m.execute(f'DELETE FROM own_stc_reg_document_registry WHERE file_id IN {REPL_FIDS}')
    for r in d.execute(f"SELECT {','.join(rcols)} FROM own_stc_reg_document_registry "
                       f"WHERE file_id IN {REPL_FIDS}"):
        m.execute(f"INSERT INTO own_stc_reg_document_registry ({','.join(rcols)}) "
                  f"VALUES ({','.join('?' * len(rcols))})", r)

    # 4. page text for those docs
    pcols = ['file_id', 'page_no', 'method', 'chars', 'text', 'property_id']
    pdel = m.execute(f'DELETE FROM own_stc_reg_page_text WHERE file_id IN {REPL_FIDS}').rowcount
    if pdel != 350:
        fail(f'page_text deleted {pdel} != 350')
    pins = 0
    for r in d.execute(f"SELECT {','.join(pcols)} FROM own_stc_reg_page_text "
                       f"WHERE file_id IN {REPL_FIDS}"):
        m.execute(f"INSERT INTO own_stc_reg_page_text ({','.join(pcols)}) "
                  f"VALUES ({','.join('?' * len(pcols))})", r)
        pins += 1
    if pins != 396:
        fail(f'page_text inserted {pins} != 396')

    # 5. prop layer (+doc_id offset; integer PKs excluded so SQLite assigns)
    offset = mq('SELECT max(doc_id) FROM prop_document')
    if offset != 36:
        fail(f'unexpected master prop_document max doc_id {offset}')
    dcols = [r[1] for r in d.execute('PRAGMA table_info(prop_document)')]
    for r in d.execute(f"SELECT {','.join(dcols)} FROM prop_document"):
        row = dict(zip(dcols, r))
        row['doc_id'] += offset
        m.execute(f"INSERT INTO prop_document ({','.join(dcols)}) "
                  f"VALUES ({','.join('?' * len(dcols))})", [row[c] for c in dcols])
    ptcols = ['doc_id', 'property_id', 'page_no', 'method', 'chars', 'text']
    for r in d.execute(f"SELECT {','.join(ptcols)} FROM prop_document_page_text"):
        row = dict(zip(ptcols, r))
        row['doc_id'] += offset
        m.execute(f"INSERT INTO prop_document_page_text ({','.join(ptcols)}) "
                  f"VALUES ({','.join('?' * len(ptcols))})", [row[c] for c in ptcols])
    pvcols = [r[1] for r in d.execute('PRAGMA table_info(prop_provision)') if r[1] != 'prov_id']
    for r in d.execute(f"SELECT {','.join(pvcols)} FROM prop_provision"):
        row = dict(zip(pvcols, r))
        row['doc_id'] += offset
        row['source'] = f"prop_doc_{row['doc_id']}"
        m.execute(f"INSERT INTO prop_provision ({','.join(pvcols)}) "
                  f"VALUES ({','.join('?' * len(pvcols))})", [row[c] for c in pvcols])

    # 6. encumbrance schedule (new table, copied verbatim)
    sch = d.execute("SELECT sql FROM sqlite_master WHERE name='own_stc_encumbrance_schedule'"
                    ).fetchone()[0]
    m.execute(sch)
    ecols = [r[1] for r in d.execute('PRAGMA table_info(own_stc_encumbrance_schedule)')]
    for r in d.execute(f"SELECT {','.join(ecols)} FROM own_stc_encumbrance_schedule"):
        m.execute(f"INSERT INTO own_stc_encumbrance_schedule ({','.join(ecols)}) "
                  f"VALUES ({','.join('?' * len(ecols))})", r)

    # 7. open items
    for iid, cat, desc in ITEMS:
        m.execute("INSERT INTO wh_open_item (module_id,item_id,category,description,severity,"
                  "source_table) VALUES ('own',?,?,?,NULL,'SOUTHTOWN_UPDATE_REPORT.md')",
                  (iid, cat, desc))

    # 8. framework + module notes
    try:
        m.execute('INSERT INTO wh_framework (key, value) VALUES (?,?)',
                  ('merge_stc_update_20260722',
                   'STC ADDITIVE_SUPPLEMENT merged: 4 tenant re-extractions replaced '
                   '(dhos 537/schulers 221/centurylink 22/tj_maxx 195; report claimed tj_maxx '
                   'already at 195 in master but master held 158 - replaced), 10 NULL '
                   'tenant_keys patched, prop layer added (28 docs/8 prov, doc_id offset +36), '
                   'encumbrance schedule (1 captured + 27 referenced-not-provided), '
                   '8 open items STCU1-8.'))
    except sqlite3.Error as e:
        print('note: wh_framework insert skipped ->', e)
    m.execute("UPDATE wh_module SET status = status || ' | STC update-harvest "
              "(ADDITIVE_SUPPLEMENT, 2026-07-22) merged: 4,445 provisions, prop layer 28 docs, "
              "encumbrance schedule started.' WHERE module_id='own'")

    # survival + post-merge checks
    post = dict(m.execute('SELECT property_id, count(*) FROM lease_provision GROUP BY property_id'))
    for pid, n in pre.items():
        if pid != 'STC' and post.get(pid) != n:
            fail(f'survival: {pid} provisions changed {n} -> {post.get(pid)}')
    post_leases = dict(m.execute('SELECT property_id, count(*) FROM lease_lease GROUP BY property_id'))
    for pid, n in pre_leases.items():
        if post_leases.get(pid) != n:
            fail(f'survival: {pid} lease count changed {n} -> {post_leases.get(pid)}')

    checks = [
        ('STC provisions', mq("SELECT count(*) FROM lease_provision WHERE property_id='STC'"), 4445),
        ('STC leases', mq("SELECT count(*) FROM lease_lease WHERE property_id='STC'"), 45),
        ('STC NULL keys', mq("SELECT count(*) FROM lease_lease WHERE property_id='STC' "
                             "AND (tenant_key IS NULL OR tenant_key='')"), 0),
        ('registry rows', mq('SELECT count(*) FROM own_stc_reg_document_registry'), 35),
        ('page_text rows', mq('SELECT count(*) FROM own_stc_reg_page_text'), 2933),
        ('STC prop_document', mq("SELECT count(*) FROM prop_document WHERE property_id='STC'"), 28),
        ('STC prop_provision', mq("SELECT count(*) FROM prop_provision WHERE property_id='STC'"), 8),
        ('encumbrance rows', mq('SELECT count(*) FROM own_stc_encumbrance_schedule'), 28),
        ('doc_id unique', mq('SELECT count(*) - count(DISTINCT doc_id) FROM prop_document'), 0),
        ('NULL cites (STC)', mq("SELECT count(*) FROM lease_provision WHERE property_id='STC' "
                                'AND (source IS NULL OR source_pages IS NULL)'), 0),
        ('orphan keys (STC)', mq("""SELECT count(DISTINCT p.tenant_key) FROM lease_provision p
                                    LEFT JOIN lease_lease l ON l.property_id=p.property_id
                                    AND l.tenant_key=p.tenant_key
                                    WHERE p.property_id='STC' AND p.tenant_key IS NOT NULL
                                    AND l.tenant_key IS NULL"""), 0),
        ('total provisions', mq('SELECT count(*) FROM lease_provision'), 48368),
    ]
    ok = True
    for name, got, exp in checks:
        s = 'PASS' if got == exp else 'FAIL'
        if got != exp:
            ok = False
        print(f'  [{s}] {name}: {got} (expect {exp})')
    if not ok:
        fail('post-merge checks failed')

    m.execute('COMMIT')
    print('\nMERGE COMMITTED — STC update applied.')
    print('Next: have Claude run the full verify suite against the master (expect ALL PASS).')


if __name__ == '__main__':
    main()
