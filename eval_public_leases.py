"""
Public-lease tie-out: publicly filed commercial leases (SEC EDGAR exhibits,
rendered to PDF under data/blind_test/, gitignored) vs a hand-verified answer
key. Measures how the engine does on lease forms it was NOT built on.

    venv/Scripts/python eval_public_leases.py [--model none|llama3.1:8b] [--only 05]
    KEY=data/blind_test/holdout_key.json PDF_DIR=data/blind_test/holdout_pdf \
        OUT_TAG=holdout venv/Scripts/python eval_public_leases.py

Key values: null = not stated in the lease; "contingent" = not scored (date set
by a future event, term-only end, total not stated). A non-zero value where the
lease states none counts as "invented".
"""
import json
import os
import sys
import time
from datetime import date

REPO = os.getcwd()
sys.path.insert(0, os.path.dirname(REPO))
sys.path.insert(0, REPO)
from eval_demo_leases import _date, _num, _norm  # noqa: E402
from realestate_extractor.document_ingestion import ingest_document  # noqa: E402
from realestate_extractor.templates.document_templates import get_template  # noqa: E402
from realestate_extractor.extractors.extraction_engine import ExtractionEngine  # noqa: E402
from realestate_extractor.extractors.llm_client import LocalLLMClient  # noqa: E402


class _Off(LocalLLMClient):
    def is_available(self):
        return False


FIELDS = [
    ('tenant', ['tenant_identity', 'tenant_name']),
    ('landlord', ['landlord_name', 'landlord']),
    ('square_feet', ['square_footage', 'square_feet', 'rentable_sf']),
    ('commencement', ['lease_commencement', 'commencement_date']),
    ('expiration', ['governing_expiration', 'expiration_date', 'lease_expiration']),
    ('base_rent_monthly', ['base_rent', 'monthly_rent']),
    ('security_deposit', ['security_deposit']),
]


def ok(field, v, k):
    alt = k.get(field + '_alt')
    alts = [k.get(field)] + (alt if isinstance(alt, list) else [alt] if alt is not None else [])
    for t in alts:
        if field in ('tenant', 'landlord'):
            if _norm(t) in _norm(v):
                return True
        elif field in ('commencement', 'expiration'):
            if _date(v) == date.fromisoformat(t):
                return True
        elif field == 'square_feet':
            if _num(v) == t:
                return True
        else:
            n = _num(v)
            if n is not None and abs(n - t) < 1.0:
                return True
    return False


def main():
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    key = json.load(open(os.environ.get('KEY','data/blind_test/key.json')))['leases']
    model = sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else 'none'
    llm = _Off() if model == 'none' else LocalLLMClient(model=model)
    if model != 'none' and not llm.is_available():
        sys.exit(f'{model} not available in Ollama')
    engine = ExtractionEngine(llm)
    template = get_template('lease')
    tally = {'correct': 0, 'missed': 0, 'WRONG': 0, 'invented': 0, 'skipped': 0}
    per_field = {f: {'correct': 0, 'missed': 0, 'WRONG': 0, 'invented': 0} for f, _ in FIELDS}
    rows = []
    for k in key:
        if only and k['file'] != only:
            continue
        t0 = time.time()
        doc = ingest_document(os.path.join(os.environ.get('PDF_DIR','data/blind_test/pdf'), k['file'] + '.pdf'))
        out = engine.extract(doc, template) or {}
        terms = {}
        for t in out.get('financial_terms', []):
            terms.setdefault(t['term_type'], t.get('value_raw') if t.get('value_raw') not in (None, '')
                             else t.get('value_numeric'))
        res = {}
        for field, types in FIELDS:
            v = next((terms[t] for t in types if t in terms), None)
            truth = k.get(field)
            if truth == 'contingent':
                verdict = 'skipped'
            elif truth is None or truth == 0:
                # not in the lease ("None" / "N/A"): any non-zero value is invented
                verdict = 'skipped' if v is None or not _num(v) else 'invented'
            elif v is None:
                verdict = 'missed'
            else:
                verdict = 'correct' if ok(field, v, k) else 'WRONG'
            tally[verdict] += 1
            if verdict != 'skipped':
                per_field[field][verdict] += 1
            res[field] = (verdict, None if v is None else str(v)[:40])
        rows.append((k, res, time.time() - t0))
        bad = {f: r[1] for f, r in res.items() if r[0] in ('WRONG', 'invented')}
        miss = [f for f, r in res.items() if r[0] == 'missed']
        print(f"{k['file']} {k['label'][:44]:44} {time.time() - t0:5.1f}s  "
              f"ok={sum(r[0] == 'correct' for r in res.values())}  miss={miss or '-'}  bad={bad or '-'}",
              flush=True)
    scored = tally['correct'] + tally['missed'] + tally['WRONG'] + tally['invented']
    print(f"\nALL: {tally['correct']}/{scored} correct ({tally['correct'] / max(scored, 1):.0%}), "
          f"missed {tally['missed']}, WRONG {tally['WRONG']}, invented {tally['invented']}, "
          f"not scored {tally['skipped']} (contingent / not stated)")
    for f, c in per_field.items():
        n = sum(c.values())
        print(f"  {f:18} {c['correct']}/{n}  missed {c['missed']}  WRONG {c['WRONG']}  invented {c['invented']}")
    json.dump([{'file': k['file'], 'label': k['label'], 'seconds': round(s, 1), 'fields': r}
               for k, r, s in rows], open(f"data/eval/{os.environ.get('OUT_TAG','blind_test')}_{model.replace(':', '_')}.json", 'w'), indent=1)


if __name__ == '__main__':
    main()
