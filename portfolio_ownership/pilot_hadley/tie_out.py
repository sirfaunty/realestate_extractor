#!/usr/bin/env python3
"""
#77 Pilot tie-out: Capactive engine extraction vs Riley's master (Hadley Five).

Reads the pilot extraction DB (data/pilot_hadley.db) and the KA master
(portfolio_ownership/.../portfolio_warehouse.db, property OSBORN-3007) and
reports, per tenancy:

  1. Field tie-out — financial_terms vs master lease_lease (expiration, SF,
     tenant identity). The master's Hadley row set carries expiration + SF for
     current tenants; rent fields are NULL there, so rent is reported as
     "extracted (no tie-out value)" rather than scored — flag, don't fabricate.
  2. Coverage — clause count + term count per document vs master provision
     depth per tenant (context for depth parity, not a pass/fail).

Read-only on both databases. Run from realestate_extractor:
    python portfolio_ownership/pilot_hadley/tie_out.py
"""

import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
PILOT_DB = os.path.join(ROOT, 'data', 'pilot_hadley.db')
MASTER = os.path.join(ROOT, 'portfolio_ownership',
                      'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')

# filename fragment -> master tenant_key
DOC_TENANT = {
    'cbd + vape': 'eastgate_tobacco',
    'subway': 'subway',
    "lee's liquor": 'lees_liquor',
    'ilove nails': 'ilove_nails',
    'great clips': 'great_clips',
    'maple leaf': 'maple_leaf_massage',
}

# Fields the STAGED DOCUMENT cannot support — verified by inspection.
# The Osborne-form leases define commencement as "earlier of opening or
# 90 days after delivery" (Exhibit D): a contingent, operational fact, so no
# calendar expiration is derivable from the instrument alone (Riley resolved
# these from the rent roll / financial source data). Lee's Liquor operates
# under an assignment that is not in the staged set — the instrument names
# only the original tenant (Kee Ho Han / MGM Liquor Warehouse).
NOT_DOC_DERIVABLE = {
    ('eastgate_tobacco', 'expiration'): 'contingent commencement (Exhibit D)',
    ('ilove_nails', 'expiration'): 'contingent commencement (Exhibit D)',
    ('lees_liquor', 'expiration'): 'assignment/renewal instrument not in staged set',
    ('lees_liquor', 'identity'): "assignment to Lee's not in staged set; "
                                 'instrument names original tenant',
}


def norm_date(s):
    """Normalize assorted date strings to (Y, M, D) or None."""
    if not s:
        return None
    s = str(s).strip()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        return (int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2})\b', s)
    if m:
        y = int(m.group(3));  y += 2000 if y < 50 else 1900
        return (y, int(m.group(1)), int(m.group(2)))
    return None


def main():
    if not os.path.exists(PILOT_DB):
        sys.exit(f'Pilot DB not found: {PILOT_DB} — run the extraction first.')
    p = sqlite3.connect(f'file:{PILOT_DB.replace(os.sep, "/")}?mode=ro', uri=True)
    m = sqlite3.connect(f'file:{MASTER.replace(os.sep, "/")}?mode=ro', uri=True)
    p.row_factory = sqlite3.Row

    docs = [dict(r) for r in p.execute(
        "SELECT id, filename, document_type FROM documents")]
    if not docs:
        sys.exit('Pilot DB has no documents — extraction may have failed.')

    truth = {r[0]: {'trade_name': r[1], 'status': r[2], 'sf': r[3], 'expiration': r[4]}
             for r in m.execute(
        "SELECT tenant_key, trade_name, status, sf, expiration FROM lease_lease "
        "WHERE property_id='OSBORN-3007'")}
    depth = dict(m.execute(
        "SELECT tenant_key, count(*) FROM lease_provision "
        "WHERE property_id='OSBORN-3007' GROUP BY tenant_key"))

    print('=' * 72)
    print('HADLEY FIVE PILOT TIE-OUT — Capactive engine vs Riley master')
    print('=' * 72)

    total_checks = 0
    total_hits = 0
    blocked = []   # misses that the staged documents cannot support

    for doc in docs:
        fname = doc['filename'].lower()
        tk = next((v for k, v in DOC_TENANT.items() if k in fname), None)
        print(f"\n--- {doc['filename']}  (doc_id {doc['id']}, "
              f"type={doc['document_type']}) -> tenant: {tk or 'UNMAPPED'}")
        if tk is None or tk not in truth:
            print('    no master tenancy mapped — skipped')
            continue
        t = truth[tk]

        terms = [dict(r) for r in p.execute(
            "SELECT term_type, term_label, value_raw, value_numeric, value_unit, "
            "expiration_date, effective_date, confidence, run_id "
            "FROM financial_terms WHERE document_id=?", (doc['id'],))]
        clauses_n = p.execute("SELECT count(*) FROM clauses WHERE document_id=?",
                              (doc['id'],)).fetchone()[0]

        def find_terms(*types):
            return [x for x in terms if x['term_type'] in types]

        # Strict mode: if the segmented extractor ran, score ONLY its
        # declared governing values — one answer per field, no credit for a
        # matching value buried in a bag of candidates. Detected by run_id
        # (pilot script) OR by segment term types (analyzer path, which
        # assigns its own numeric run ids).
        _SEG_TYPES = {'tenant_identity', 'governing_expiration',
                      'square_footage', 'instrument_expiration',
                      'commencement_contingent'}
        seg = [x for x in terms
               if x['run_id'] == 'seg_v1' or x['term_type'] in _SEG_TYPES]
        strict = bool(seg)
        if strict and doc is docs[0]:
            print('    [strict scoring: segmented run present]')

        # 1. tenant identity
        trade = t['trade_name'] or ''
        token = re.sub(r'[^a-z]', '', trade.split()[0].lower()) if trade else ''
        if strict:
            blob = ' '.join((x['value_raw'] or '')
                            for x in seg if x['term_type'] == 'tenant_identity').lower()
        else:
            blob = ' '.join((x['value_raw'] or '') + ' ' + (x['term_label'] or '')
                            for x in terms).lower()
        ident = bool(token) and token in re.sub(r'[^a-z ]', '', blob)
        total_checks += 1
        total_hits += 1 if ident else 0
        note = ''
        if not ident and (tk, 'identity') in NOT_DOC_DERIVABLE:
            blocked.append((tk, 'identity'))
            note = f"  [not doc-derivable: {NOT_DOC_DERIVABLE[(tk, 'identity')]}]"
        print(f"    tenant identity ({trade!r}): "
              f"{'MATCH' if ident else 'not found in terms'}{note}")

        # 2. expiration tie-out (master carries it for current tenants)
        if t['expiration']:
            want = norm_date(t['expiration'])
            if strict:
                gov = [x for x in seg if x['term_type'] == 'governing_expiration']
                got_dates = {norm_date(x['expiration_date']) for x in gov} - {None}
            else:
                got_dates = set()
                for x in terms:
                    for field in ('expiration_date', 'value_raw'):
                        d = norm_date(x[field] if field in x.keys() else None)
                        if d:
                            got_dates.add(d)
            hit = want in got_dates
            total_checks += 1
            total_hits += 1 if hit else 0
            note = ''
            if not hit and (tk, 'expiration') in NOT_DOC_DERIVABLE:
                blocked.append((tk, 'expiration'))
                note = f"  [not doc-derivable: {NOT_DOC_DERIVABLE[(tk, 'expiration')]}]"
            print(f"    expiration {t['expiration']}: "
                  f"{'MATCH' if hit else f'MISS (extracted dates: {sorted(got_dates)[:6]})'}"
                  f"{note}")
        else:
            print('    expiration: no tie-out value in master (terminated tenancy)')

        # 3. SF tie-out
        if t['sf']:
            pool = ([x for x in seg if x['term_type'] == 'square_footage']
                    if strict else terms)
            sf_vals = {round(x['value_numeric']) for x in pool
                       if x['value_numeric'] and 100 < x['value_numeric'] < 100000}
            hit = round(t['sf']) in sf_vals
            total_checks += 1
            total_hits += 1 if hit else 0
            print(f"    square footage {t['sf']:.0f}: "
                  f"{'MATCH' if hit else f'MISS (numeric values seen: {sorted(sf_vals)[:8]})'}")
        else:
            print('    square footage: no tie-out value in master')

        # 4. rent — extracted but not scoreable (master rent is NULL for Hadley)
        rents = find_terms('base_rent')
        if rents:
            r0 = rents[0]
            print(f"    base rent extracted: {r0['value_raw']!r} "
                  f"(no master tie-out value — informational)")
        else:
            print('    base rent: none extracted')

        # 5. depth context
        print(f"    coverage: {len(terms)} terms + {clauses_n} clauses "
              f"vs master {depth.get(tk, 0)} provisions")

    print('\n' + '=' * 72)
    pct = (100.0 * total_hits / total_checks) if total_checks else 0.0
    print(f'SCOREABLE FIELD TIE-OUT: {total_hits}/{total_checks} ({pct:.0f}%)')
    if blocked:
        deriv = total_checks - len(blocked)
        dpct = (100.0 * total_hits / deriv) if deriv else 0.0
        print(f'DOCUMENT-DERIVABLE SUBSET: {total_hits}/{deriv} ({dpct:.0f}%) — '
              f'{len(blocked)} field(s) require sources outside the staged '
              f'instruments:')
        for tk, f in blocked:
            print(f'    {tk}.{f}: {NOT_DOC_DERIVABLE[(tk, f)]}')
        print('  (Riley resolved these from rent roll / assignment instruments —')
        print('   the campaign closes them by ingesting the Portfolio Financial')
        print('   Source Data folder, per the aggregator two-source discipline.)')
    print('(Identity + expiration + SF where the master carries values. Rent and')
    print(' provision-level depth are reported as context — the master is the')
    print(' page-cited source of truth; parity there is the long-term bar.)')


if __name__ == '__main__':
    main()
