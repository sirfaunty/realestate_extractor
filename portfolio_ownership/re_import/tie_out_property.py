#!/usr/bin/env python3
"""
#77 Re-import campaign — property-agnostic tie-out harness.

Generalizes the validated Hadley pilot harness: scores a Capactive pilot
extraction DB against the KA master's verified lease layer for any property.

- Docs are auto-mapped to master tenant_keys by normalized filename token
  matching (longest key first); unmapped docs and roster tenancies with no
  staged doc are reported, not silently dropped.
- STRICT scoring when the segment-first extractor ran: only its declared
  governing values count (tenant_identity / governing_expiration /
  square_footage), one answer per field.
- Master truth values that aren't clean dates ("vacated ~2025") score as
  "no tie-out value" — flag, don't fabricate, both directions.

Read-only on both databases. Run:
    venv/Scripts/python portfolio_ownership/re_import/tie_out_property.py \
        --db data/pilot_maplewood.db --property-id ENGELS-2010
"""

import argparse
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
MASTER = os.path.join(ROOT, 'portfolio_ownership',
                      'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')
# Riley's verified rent-roll module (page-cited MRI extractions, ties EXACT)
# — the operational second source of the aggregator two-source discipline.
OPS_DB = os.path.join(ROOT, 'portfolio_ownership', 'inbox',
                      'Portfolio Financial Source Data & Modules',
                      'Financial Modules', 'Final Portfolio Rent Roll Module_7.10.26',
                      'database', 'portfolio_rentroll.db')

SEG_TYPES = {'tenant_identity', 'governing_expiration', 'square_footage',
             'instrument_expiration', 'commencement_contingent'}


def norm_date(s):
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
        y = int(m.group(3))
        y += 2000 if y < 50 else 1900
        return (y, int(m.group(1)), int(m.group(2)))
    return None


def alpha(s):
    return re.sub(r'[^a-z]', '', (s or '').lower())


def map_docs(docs, roster):
    """filename -> tenant_key by normalized token containment."""
    keys = sorted((k for k in roster if k), key=len, reverse=True)
    out = {}
    for doc in docs:
        fnorm = alpha(doc['filename'])
        hit = None
        for tk in keys:
            if alpha(tk) and alpha(tk) in fnorm:
                hit = tk
                break
        if hit is None:  # try distinctive trade-name tokens (both leading)
            for tk in keys:
                trade = roster[tk]['trade_name'] or ''
                toks = [alpha(w) for w in re.split(r'[^A-Za-z]+', trade)
                        if len(w) >= 4]
                if toks and all(t in fnorm for t in toks[:2]):
                    hit = tk
                    break
        if hit is None:
            # any single distinctive token, accepted only if it identifies
            # exactly one tenancy ("CBD + Vape" -> eastgate_tobacco via 'vape')
            generic = {'store', 'stores', 'shop', 'center', 'health', 'club'}
            cands = set()
            for tk in keys:
                trade = roster[tk]['trade_name'] or ''
                for w in re.split(r'[^A-Za-z]+', trade):
                    a = alpha(w)
                    if len(a) >= 4 and a not in generic and a in fnorm:
                        cands.add(tk)
                        break
            if len(cands) == 1:
                hit = cands.pop()
        out[doc['id']] = hit
    return out


def load_ops(ops_db, property_id, roster):
    """Latest-snapshot rent-roll rows for the property, mapped to master
    tenant_keys by occupant-name tokens. Returns {tenant_key: row} or {}."""
    if not os.path.exists(ops_db):
        return {}
    o = sqlite3.connect(f'file:{ops_db.replace(os.sep, "/")}?mode=ro', uri=True)
    snap = o.execute("SELECT MAX(snapshot_date) FROM rent_roll "
                     "WHERE property_key=?", (property_id,)).fetchone()[0]
    if not snap:
        return {}
    out = {}
    for occ, exp, sqft, rent, suite in o.execute(
            "SELECT occupant, expiration, sqft, monthly_base_rent, suite "
            "FROM rent_roll WHERE property_key=? AND snapshot_date=?",
            (property_id, snap)):
        if not occ or occ.strip().lower() == 'vacant':
            continue
        onorm = alpha(occ)
        for tk, t in roster.items():
            toks = [alpha(w) for w in re.split(r'[^A-Za-z]+', t['trade_name'] or '')
                    if len(alpha(w)) >= 4]
            if (alpha(tk) and alpha(tk) in onorm) or \
                    (toks and all(x in onorm for x in toks[:2])):
                out[tk] = {'occupant': occ, 'expiration': exp, 'sqft': sqft,
                           'rent': rent, 'suite': suite, 'snapshot': snap}
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--property-id', required=True, help='e.g. ENGELS-2010')
    ap.add_argument('--master', default=MASTER)
    ap.add_argument('--ops', default=OPS_DB,
                    help="Riley's rent-roll module DB (operational source)")
    ap.add_argument('--no-ops', action='store_true',
                    help='paper-only scoring (skip the operational source)')
    args = ap.parse_args()

    p = sqlite3.connect(f'file:{args.db.replace(os.sep, "/")}?mode=ro', uri=True)
    m = sqlite3.connect(f'file:{args.master.replace(os.sep, "/")}?mode=ro', uri=True)
    p.row_factory = sqlite3.Row

    docs = [dict(r) for r in p.execute(
        "SELECT id, filename, document_type FROM documents")]
    if not docs:
        sys.exit('Pilot DB has no documents — run the ingest first.')

    roster = {r[0]: {'trade_name': r[1], 'status': r[2], 'sf': r[3],
                     'expiration': r[4]}
              for r in m.execute(
        "SELECT tenant_key, trade_name, status, sf, expiration FROM lease_lease "
        "WHERE property_id=?", (args.property_id,))}
    if not roster:
        sys.exit(f'No master lease roster for property {args.property_id}')
    depth = dict(m.execute(
        "SELECT tenant_key, count(*) FROM lease_provision "
        "WHERE property_id=? GROUP BY tenant_key", (args.property_id,)))

    doc_map = map_docs(docs, roster)
    ops = {} if args.no_ops else load_ops(args.ops, args.property_id, roster)

    print('=' * 72)
    print(f'RE-IMPORT TIE-OUT — {args.property_id} — Capactive vs Riley master')
    if ops:
        snap = next(iter(ops.values()))['snapshot']
        print(f'[two-source mode: rent-roll module joined, snapshot {snap}, '
              f'{len(ops)} tenancies mapped]')
    print('=' * 72)

    total = hits = 0
    ops_closed = 0
    covered = set()
    blocked = []   # misses whose truth value is absent from the document text

    # Per-TENANT aggregation: chain corpora (Cottage Grove) ship each
    # tenant's lease + amendments as separate FILES — score the union of a
    # tenancy's documents, not each file against the same truth.
    by_tenant = {}
    unmapped = []
    for doc in docs:
        tk = doc_map.get(doc['id'])
        if tk is None or tk not in roster:
            unmapped.append(doc)
        else:
            by_tenant.setdefault(tk, []).append(doc)
    for doc in unmapped:
        print(f"\n--- {doc['filename']}  (doc_id {doc['id']}) -> UNMAPPED")
        print('    no master tenancy mapped — skipped')

    for tk, tdocs in by_tenant.items():
        names = ', '.join(d['filename'][:44] for d in tdocs[:4])
        extra = f' (+{len(tdocs) - 4} more)' if len(tdocs) > 4 else ''
        print(f"\n--- tenant {tk}: {len(tdocs)} document(s) — {names}{extra}")
        covered.add(tk)
        t = roster[tk]
        doc = tdocs[0]   # citation anchor for per-doc fields

        ids = [d['id'] for d in tdocs]
        qmarks = ','.join('?' * len(ids))
        terms = [dict(r) for r in p.execute(
            f"SELECT term_type, term_label, value_raw, value_numeric, "
            f"expiration_date, run_id FROM financial_terms "
            f"WHERE document_id IN ({qmarks})", ids)]
        clauses_n = p.execute(
            f"SELECT count(*) FROM clauses WHERE document_id IN ({qmarks})",
            ids).fetchone()[0]
        seg = [x for x in terms if x['term_type'] in SEG_TYPES]
        strict = bool(seg)

        # identity — token-set match against the master's FULL trade_name,
        # including its parenthetical legal entity ("Pixie Nails (Enlightened
        # Beauty Nails Inc)"): identifying the paper's legal tenant IS
        # identifying the tenancy.
        trade = t['trade_name'] or ''
        stop = {'former', 'the', 'store', 'inc', 'llc', 'corp', 'company',
                'holdings', 'reorganized', 'subtenant', 'of', 'and'}
        tokens = [alpha(w) for w in re.split(r'[^A-Za-z]+', trade)
                  if len(alpha(w)) >= 4 and alpha(w) not in stop]
        if not tokens and not trade:
            # bare roster (no trade_name recorded): derive tokens from the
            # tenant_key itself — 'cub_foods' -> cub, foods
            tokens = [alpha(w) for w in (tk or '').split('_')
                      if len(alpha(w)) >= 4 and alpha(w) not in stop]
            trade = tk
        # short brand names (UPS, GNC) — word-boundary match on raw text
        shorts = [alpha(w) for w in re.split(r'[^A-Za-z]+', trade)
                  if 2 <= len(alpha(w)) <= 3 and alpha(w) not in {'the', 'inc', 'llc', 'of'}]
        pool = seg if strict else terms
        raw_blob = ' '.join((x['value_raw'] or '') for x in pool
                            if not strict or x['term_type'] == 'tenant_identity')
        blob = alpha(raw_blob)

        def name_match(alpha_text, raw_text):
            return (any(tok in alpha_text for tok in tokens) or
                    any(re.search(rf'(?i)\b{t}\b', raw_text) for t in shorts))

        ident = name_match(blob, raw_blob)
        src = 'paper'
        if not ident and tk in ops:
            occ = ops[tk]['occupant']
            if name_match(alpha(occ), occ):
                ident, src = True, 'ops'
                ops_closed += 1
        total += 1
        hits += 1 if ident else 0
        tag = ' [closed by rent roll]' if ident and src == 'ops' else ''
        print(f"    tenant identity ({trade!r}): "
              f"{'MATCH' if ident else 'not found'}{tag}")

        # expiration — on a MISS, check whether the truth date exists in the
        # document text at all: if not, no extractor can derive it from the
        # paper (contingent commencement / ops-data value) — annotate, and
        # track separately, mirroring the aggregator two-source discipline.
        want = norm_date(t['expiration'])
        if want:
            if strict:
                got = {norm_date(x['expiration_date']) for x in seg
                       if x['term_type'] == 'governing_expiration'} - {None}
            else:
                got = {norm_date(x['expiration_date']) for x in terms} - {None}
            hit = want in got
            src = 'paper'
            # two-source: the rent roll closes what the paper cannot state
            ops_exp = norm_date(ops[tk]['expiration']) if tk in ops else None
            if not hit and ops_exp == want:
                hit, src = True, 'ops'
                ops_closed += 1
            # conflict flag: paper and ops disagree — surface, don't pick
            if got and ops_exp and ops_exp not in got:
                print(f"    NOTE: paper says {sorted(got)[:2]} but rent roll "
                      f"says {ops[tk]['expiration']} — reconcile")
            total += 1
            hits += 1 if hit else 0
            note = ' [closed by rent roll]' if hit and src == 'ops' else ''
            if not hit:
                doc_text = '\n'.join(r[0] for r in p.execute(
                    f"SELECT content FROM document_fulltext "
                    f"WHERE CAST(document_id AS INTEGER) IN ({qmarks}) "
                    f"ORDER BY document_id, CAST(page_number AS INTEGER)",
                    ids))
                y, mth, d = want
                name3 = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul',
                         'aug', 'sep', 'oct', 'nov', 'dec'][mth - 1]
                present = bool(
                    re.search(rf'{mth}\s*[/\-.]\s*0?{d}\s*[/\-.]\s*{y}', doc_text) or
                    re.search(rf'(?i){name3}\w*\.?\s+0?{d}(?:st|nd|rd|th)?\s*,?\s*{y}',
                              doc_text) or
                    re.search(rf'(?i)0?{d}(?:st|nd|rd|th)?\s+day\s+of\s+{name3}\w*\s*,?\s*{y}',
                              doc_text))
                if not present:
                    blocked.append((tk, 'expiration'))
                    note = ('  [truth date not present in document text — '
                            'requires operational sources]')
            print(f"    expiration {t['expiration']}: "
                  f"{'MATCH' if hit else f'MISS (got {sorted(got)[:4]})'}{note}")
        else:
            print(f"    expiration: no clean tie-out value in master "
                  f"({t['expiration']!r})")

        # SF — paper first; rent roll closes when the paper answer is absent
        if t['sf']:
            pool2 = ([x for x in seg if x['term_type'] == 'square_footage']
                     if strict else terms)
            sf_vals = {round(x['value_numeric']) for x in pool2
                       if x['value_numeric'] and 100 < x['value_numeric'] < 200000}
            hit = round(t['sf']) in sf_vals
            src = 'paper'
            if not hit and tk in ops and ops[tk].get('sqft'):
                try:
                    if round(float(ops[tk]['sqft'])) == round(t['sf']):
                        hit, src = True, 'ops'
                        ops_closed += 1
                except (TypeError, ValueError):
                    pass
            total += 1
            hits += 1 if hit else 0
            tag = ' [closed by rent roll]' if hit and src == 'ops' else ''
            print(f"    square footage {t['sf']:.0f}: "
                  f"{'MATCH' if hit else f'MISS (seen {sorted(sf_vals)[:6]})'}{tag}")
        else:
            print('    square footage: no tie-out value in master')

        print(f"    coverage: {len(terms)} terms + {clauses_n} clauses "
              f"vs master {depth.get(tk, 0)} provisions")

    missing = sorted((set(k for k in roster if k)) - covered)
    if missing:
        print('\nroster tenancies with no staged document:')
        for tk in missing:
            t = roster[tk]
            print(f"    {tk} ({t['trade_name']}, {t['status']}) — "
                  f"{depth.get(tk, 0)} master provisions")

    print('\n' + '=' * 72)
    pct = 100.0 * hits / total if total else 0.0
    print(f'SCOREABLE FIELD TIE-OUT: {hits}/{total} ({pct:.0f}%) '
          f'[strict scoring where the segmented extractor ran]')
    if ops_closed:
        print(f'  of which {ops_closed} field(s) closed by the operational '
              f'source (Riley rent-roll module, page-cited) — two-source '
              f'discipline: paper first, ops where the paper cannot state it')
    if blocked:
        deriv = total - len(blocked)
        dpct = 100.0 * hits / deriv if deriv else 0.0
        print(f'DOCUMENT-DERIVABLE SUBSET: {hits}/{deriv} ({dpct:.0f}%) — '
              f'{len(blocked)} truth value(s) absent from the staged text:')
        for tk, f in blocked:
            print(f'    {tk}.{f}')
        print('  (Riley resolved these from operational sources — rent roll /')
        print('   assignments; the campaign closes them by ingesting the')
        print('   Portfolio Financial Source Data folder.)')
    print('(Identity + expiration + SF where the master carries clean values.')
    print(' The master is the page-cited source of truth.)')


if __name__ == '__main__':
    main()
