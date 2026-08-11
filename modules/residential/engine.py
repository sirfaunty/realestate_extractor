"""
Residential Portfolio Engine — read-only access to the KA residential
handoff package (portfolio_ownership/residential/handoff_package).

Data doctrine (from the package's discrepancy review, May 2026):
KA internal accounting is authoritative for actuals; the proforma is used
only for forward-looks (2027F/2028F). Surfaces label forecast numbers as
such and expose the discrepancy report verbatim.

All loads are mtime-cached per file; the engine never writes.
"""

import csv
import json
import os
import threading
import logging

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
PKG = os.path.join(_REPO_ROOT, 'portfolio_ownership', 'residential', 'handoff_package')

# JSON asset keys ↔ display names ↔ CSV property names
ASSETS = [
    ('HQ',          'HQ Apartments',   'HQ'),
    ('Larking',     'The Larking',     'Larking'),
    ('Chamberlain', 'Chamberlain',     'Chamberlain'),
    ('Moda',        'Moda on Raymond', 'MODA'),
    ('Five90Park',  'Five 90 Park',    'Five 90 Park'),
    ('430OakGrove', '430 Oak Grove',   '430 Oak Grove'),
    ('Arbors',      'Arbors at Ridges', 'Arbors'),
]
KEY_TO_NAME = {k: n for k, n, _ in ASSETS}
KEY_TO_CSV = {k: c for k, _, c in ASSETS}
CSV_TO_KEY = {c: k for k, _, c in ASSETS}

CAP_RATES = [0.050, 0.0525, 0.055, 0.0575, 0.060]

# Curated facts from HANDOFF.md (provenance: handoff_package/HANDOFF.md)
VALUE_PROGRAMS = [
    ('4D Affordable — Five 90 Park', '~$250K/yr', '590 Park qualifies first; 2023 effort stalled, being re-examined'),
    ('4D Affordable — Chamberlain / Moda / Larking', 'TBD', 'Sequencing follows 590 Park playbook'),
    ('HQ shared parking program', 'TBD', 'Underutilized capacity during business hours'),
    ('Bulk internet stabilization (2027)', '~$600K/yr', 'Already in motion; ramping through 2026'),
]
HEADLINES = [
    ('NOI 2024A → 2028F', '$12.6M → $17.7M (+40%)'),
    ('Annualized NER 2024A → 2028F', '$22.8M → $27.8M (+22%)'),
    ('Implied value @ 5.5% cap', '$230M → $322M (+$92M)'),
]


class ResidentialEngine:

    def __init__(self, pkg_path: str = None):
        self.pkg = pkg_path or PKG
        self._cache = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return os.path.exists(os.path.join(self.pkg, 'HANDOFF.md'))

    # ── cached loaders ──────────────────────────────────────────────

    def _load(self, rel, loader):
        path = os.path.join(self.pkg, rel)
        if not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        with self._lock:
            hit = self._cache.get(rel)
            if hit and hit[0] == mtime:
                return hit[1]
        data = loader(path)
        with self._lock:
            self._cache[rel] = (mtime, data)
        return data

    def _json(self, rel):
        return self._load(rel, lambda p: json.load(open(p, encoding='utf-8')))

    def _csv(self, rel):
        return self._load(rel, lambda p: list(csv.DictReader(open(p, encoding='utf-8-sig'))))

    def _text(self, rel):
        return self._load(rel, lambda p: open(p, encoding='utf-8').read())

    # ── portfolio surfaces ──────────────────────────────────────────

    def roster(self):
        budget = self._json('source_data/budgets/budget_summary.json') or {}
        summary = {r['Property']: r for r in
                   (self._csv('source_data/rent_files/csv_data/property_summary.csv') or [])}
        out = []
        for key, name, csvname in ASSETS:
            b = budget.get(key, {})
            s = summary.get(csvname, {})
            out.append({
                'key': key, 'name': name,
                'units': b.get('units'),
                'noi_2026b': b.get('noi'),
                'gpr_2026b': b.get('gpr'),
                'physical_pct': b.get('physical_pct'),
                'end_occ_pct': s.get('End_Occ_Pct'),
                'mgmt_history': s.get('Management_History'),
                'latest_specials': s.get('Latest_Specials'),
            })
        return out

    def noi_bridge(self):
        f = self._json('build_chains/operational_memo/forecast_2027_2028.json') or {}
        rows = []
        for key, name, _ in ASSETS:
            b = (f.get('2026b') or {}).get(key, {})
            y7 = (f.get('2027f') or {}).get(key, {})
            y8 = (f.get('2028f') or {}).get(key, {})
            if not (b or y7 or y8):
                continue
            rows.append({
                'key': key, 'name': name,
                'noi_2026b': b.get('noi_2026b'),
                'noi_2027f': y7.get('noi_2027f') or y7.get('noi'),
                'noi_2028f': y8.get('noi_2028f') or y8.get('noi'),
            })
        return rows, (f.get('totals') or {})

    def quarterly(self, key=None):
        rows = self._csv('source_data/rent_files/csv_data/quarterly_metrics_v2.csv') or []
        if key:
            csvname = KEY_TO_CSV.get(key)
            rows = [r for r in rows if r['property'] == csvname]
        return rows

    def concessions_latest(self):
        rows = self._csv('source_data/rent_files/csv_data/concession_history.csv') or []
        latest = {}
        for r in rows:
            p = r['property']
            if p not in latest or r['year_month'] > latest[p]['year_month']:
                latest[p] = r
        return latest

    # ── per-asset surfaces ──────────────────────────────────────────

    def asset(self, key):
        if key not in KEY_TO_NAME:
            return None
        budget = (self._json('source_data/budgets/budget_summary.json') or {}).get(key, {})
        scenario = (self._json('build_chains/leadership_doc/scenario_data.json') or {}).get(key, {})
        proj = (self._json('build_chains/leadership_doc/projections.json') or {}).get(key, {})
        summary = {r['Property']: r for r in
                   (self._csv('source_data/rent_files/csv_data/property_summary.csv') or [])}
        return {
            'key': key, 'name': KEY_TO_NAME[key],
            'budget': budget, 'scenario': scenario, 'projections': proj,
            'summary': summary.get(KEY_TO_CSV.get(key), {}),
        }

    def valuation_matrix(self, key):
        """NOI ÷ cap rate across the package's standard cap ladder — the
        methodology used in the source deliverables. Forecast NOI is labeled
        proforma-derived per the data doctrine."""
        scenario = (self._json('build_chains/leadership_doc/scenario_data.json') or {}).get(key)
        if not scenario:
            return None
        rows = []
        for label, src, tag in [
            ('2026B', 'baseline_2026b', 'budget'),
            ('2027F', 'scenario_2027f', 'proforma forecast'),
            ('2028F', 'scenario_2028f', 'proforma forecast'),
        ]:
            s = scenario.get(src) or {}
            noi = s.get('noi')
            if noi is None:
                continue
            rows.append({
                'period': label, 'tag': tag, 'noi': noi,
                'values': {c: noi / c for c in CAP_RATES},
            })
        return rows

    def comps(self, key, limit=10):
        allc = (self._json('source_data/comps/comps_scored.json') or {}).get(key) or []
        return allc[:limit]

    def discrepancy_report(self):
        return self._text('context/DISCREPANCY_REPORT.md')

    def handoff(self):
        return self._text('HANDOFF.md')
