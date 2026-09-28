"""
Model-agnostic extraction tie-out harness.

Runs the 12 fictional demo leases through the REAL extraction path (the
same segment-first engine the app uses) with a chosen local model, and
scores every key field against the ground-truth manifest.json produced by
demo_portfolio.py. Built for model bake-offs: every time a new open model
ships, one command says whether to switch.

    venv/Scripts/python eval_demo_leases.py --model llama3.1:8b
    venv/Scripts/python eval_demo_leases.py --model qwen3:8b --model gemma4:12b
    venv/Scripts/python eval_demo_leases.py --model none      # deterministic only
    venv/Scripts/python eval_demo_leases.py --model qwen3:8b --only "Riverbend"

Scores:
  correct  — field extracted and matches truth
  missed   — field absent (honest: "don't know")
  WRONG    — field extracted but disagrees with truth (the dangerous kind)
  invented — a term the lease does not contain (e.g. a TI allowance)
Results also land in data/eval/<model>.json for comparison over time.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

MANIFEST = os.path.join(HERE, 'data', 'demo_portfolio', 'manifest.json')
OUTDIR = os.path.join(HERE, 'data', 'eval')

# term types that must NOT appear for these leases (none of the demo
# leases contains them) — any value is an invention
NEVER_PRESENT = {'ti_allowance', 'free_rent', 'percentage_rent'}


# ─── value normalizers ───────────────────────────────────────────────

def _date(v):
    """Parse the engine's date formats → date, else None."""
    if v is None:
        return None
    s = str(v).strip()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', s)
        if not m:
            return None
        mo, d, y = map(int, m.groups())
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _num(v):
    if v is None:
        return None
    m = re.search(r'-?[\d,]+(?:\.\d+)?', str(v).replace('$', ''))
    if not m:
        return None
    try:
        return float(m.group(0).replace(',', ''))
    except ValueError:
        return None


def _norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


# ─── field checks: (field, [term types to look in], checker) ─────────

def checks(truth, prop):
    owner = prop['owner']
    return [
        ('tenant', ['tenant_identity', 'tenant_name'],
         lambda v: _norm(truth['tenant']) in _norm(v)),
        ('landlord', ['landlord_name', 'landlord'],
         lambda v: _norm(owner.split(' LLC')[0]) in _norm(v)),
        ('square_feet', ['square_footage', 'square_feet', 'rentable_sf'],
         lambda v: _num(v) == truth['square_feet']),
        ('commencement', ['lease_commencement', 'commencement_date'],
         lambda v: _date(v) == date.fromisoformat(truth['commencement'])),
        ('expiration', ['governing_expiration', 'expiration_date',
                        'lease_expiration'],
         lambda v: _date(v) == date.fromisoformat(truth['expiration'])),
        ('base_rent_monthly', ['base_rent', 'monthly_rent'],
         lambda v: abs((_num(v) or -1) - truth['base_rent_monthly_year1']) < 1.0),
        ('security_deposit', ['security_deposit'],
         lambda v: abs((_num(v) or -1) - truth['security_deposit']) < 1.0),
        ('escalation', ['escalation_rate', 'escalation', 'rent_escalation'],
         lambda v: (_num(v) or -1) == truth['escalation_pct']),
    ]


# ─── run one model ───────────────────────────────────────────────────

def run(model, leases, props, verbose):
    from realestate_extractor.document_ingestion import ingest_document
    from realestate_extractor.templates.document_templates import get_template
    from realestate_extractor.extractors.extraction_engine import ExtractionEngine
    from realestate_extractor.extractors.llm_client import LocalLLMClient

    if model == 'none':
        class _Off(LocalLLMClient):
            def is_available(self):
                return False
        llm = _Off()
    else:
        llm = LocalLLMClient(model=model)
        if not llm.is_available():
            print(f'  ! {model} not available in Ollama — `ollama pull {model}`')
            return None
    engine = ExtractionEngine(llm)
    template = get_template('lease')

    tally = {'correct': 0, 'missed': 0, 'WRONG': 0, 'invented': 0}
    per_field = {}
    rows = []
    t_all = time.time()
    for truth in leases:
        prop = props[truth['property']]
        path = os.path.join(HERE, truth['file'])
        doc = ingest_document(path)
        t0 = time.time()
        out = engine.extract(doc, template) or {}
        secs = time.time() - t0
        terms = {}
        for t in out.get('financial_terms', []):
            terms.setdefault(t['term_type'], t.get('value_raw')
                             if t.get('value_raw') not in (None, '')
                             else t.get('value_numeric'))
        row = {'tenant': truth['tenant'], 'seconds': round(secs, 1),
               'fields': {}}
        for field, types, ok in checks(truth, prop):
            val = next((terms[t] for t in types if t in terms), None)
            if val is None:
                verdict = 'missed'
            else:
                try:
                    verdict = 'correct' if ok(val) else 'WRONG'
                except Exception:
                    verdict = 'WRONG'
            tally[verdict] += 1
            per_field.setdefault(field, {'correct': 0, 'missed': 0, 'WRONG': 0})
            per_field[field][verdict] += 1
            row['fields'][field] = {'verdict': verdict, 'value': str(val)[:60]}
        inv = {t: str(terms[t])[:40] for t in NEVER_PRESENT if t in terms}
        tally['invented'] += len(inv)
        row['invented'] = inv
        rows.append(row)
        if verbose:
            bad = {f: r['value'] for f, r in row['fields'].items()
                   if r['verdict'] == 'WRONG'}
            print(f"    {truth['tenant'][:28]:28} {secs:5.1f}s  "
                  f"wrong={bad or '-'}  invented={inv or '-'}")

    scored = tally['correct'] + tally['missed'] + tally['WRONG']
    result = {'model': model, 'leases': len(rows),
              'accuracy': round(tally['correct'] / scored, 3) if scored else 0,
              'wrong_rate': round(tally['WRONG'] / scored, 3) if scored else 0,
              'tally': tally, 'per_field': per_field,
              'seconds_total': round(time.time() - t_all, 1),
              'seconds_per_lease': round((time.time() - t_all) / max(len(rows), 1), 1),
              'rows': rows, 'run_at': time.strftime('%Y-%m-%d %H:%M')}
    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, re.sub(r'[^\w.-]', '_', model) + '.json'), 'w') as f:
        json.dump(result, f, indent=2)
    return result


def main():
    ap = argparse.ArgumentParser(description='Score local models on the demo leases.')
    ap.add_argument('--model', action='append', required=True,
                    help='Ollama model tag, repeatable; "none" = deterministic only')
    ap.add_argument('--only', help='substring filter on tenant name')
    ap.add_argument('-q', '--quiet', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        sys.exit('No manifest — run: venv/Scripts/python demo_portfolio.py')
    m = json.load(open(MANIFEST))
    props = {p['name']: p for p in m['properties']}
    # leases that received an amendment have a moving target — the truth
    # for the base lease PDF alone is still the base lease terms, so keep them
    leases = [l for l in m['leases']
              if not args.only or args.only.lower() in l['tenant'].lower()]

    import logging
    logging.basicConfig(level=logging.WARNING)

    results = []
    for model in args.model:
        print(f'\n=== {model} — {len(leases)} leases')
        r = run(model, leases, props, not args.quiet)
        if r:
            results.append(r)
            t = r['tally']
            print(f"  accuracy {r['accuracy']:.0%}  |  correct {t['correct']}  "
                  f"missed {t['missed']}  WRONG {t['WRONG']}  invented "
                  f"{t['invented']}  |  {r['seconds_per_lease']}s/lease")

    if len(results) > 1 or results:
        fields = [f for f, *_ in checks(leases[0], props[leases[0]['property']])]
        print('\n' + 'field'.ljust(20) + ''.join(r['model'][:16].rjust(18) for r in results))
        for f in fields:
            line = f.ljust(20)
            for r in results:
                pf = r['per_field'].get(f, {})
                line += f"{pf.get('correct', 0)}/{len(leases)} ({pf.get('WRONG', 0)} wrong)".rjust(18)
            print(line)
        print('invented'.ljust(20) + ''.join(str(r['tally']['invented']).rjust(18) for r in results))
        print('sec/lease'.ljust(20) + ''.join(str(r['seconds_per_lease']).rjust(18) for r in results))
        print(f'\nDetail: {OUTDIR}')


if __name__ == '__main__':
    main()
