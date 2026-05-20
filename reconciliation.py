"""
Cross-Document Term Reconciliation for Capactive.

When the same term type (e.g. loan_amount, borrower) appears in multiple
documents, the raw extraction data is often noisy:
  - Multiple legitimate values from different instruments
  - Time-varying values from amendment sequences
  - Extraction errors (template text, wrong entity, OCR noise)
  - Duplicate values from overlapping documents

This module reconciles those into a clean property-level summary:
  1. Groups raw terms by type
  2. Deduplicates identical values
  3. Filters extraction noise
  4. Resolves conflicts using document recency + confidence
  5. Produces a canonical "property fact sheet" with provenance

All reconciliation is deterministic — no LLM needed.
"""

import json
import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


# ─── Noise Filters ──────────────────────────────────────────────────

# Patterns that indicate extraction errors, not real values
_NOISE_PATTERNS = [
    re.compile(r'(?i)^insert\s+(if|the|your|name)'),
    re.compile(r'(?i)^include\s+only\s+if'),
    re.compile(r'(?i)^(n/a|none|see\s+|refer\s+to|per\s+)'),
    re.compile(r'(?i)^transactions?\s+where'),
    re.compile(r'(?i)^(exhibit|schedule|section|article)\s+[a-z0-9]'),
    re.compile(r'(?i)^(the|a|an)\s+(borrower|lender|mortgag)'),
    # Template / placeholder text
    re.compile(r'(?i)^(project\s+name\s+|date\s+of\s+)'),
    # Sentence fragments (starts with lowercase preposition/conjunction)
    re.compile(r'^(of|and|or|the|in|at|to|for|from|with|on|by)\s+\w'),
    # Very long strings are usually paragraph fragments, not values
    re.compile(r'^.{120,}$'),
]


def _is_noise(value_raw: str) -> bool:
    """Check if a raw value looks like extraction noise."""
    if not value_raw or not value_raw.strip():
        return True
    v = value_raw.strip()
    for pat in _NOISE_PATTERNS:
        if pat.search(v):
            return True
    return False


def _remove_numeric_outliers(entries: List[Dict]) -> List[Dict]:
    """
    Remove entries whose numeric value is an extreme outlier.
    Uses a simple heuristic: if a value is <1% or >100x the median
    of all numeric values, it's likely an extraction error.
    Only applies when there are 3+ numeric entries.
    """
    numerics = []
    for e in entries:
        num = _normalise_numeric(e['value_raw'], e.get('value_numeric'))
        if num is not None and num > 0:
            numerics.append(num)

    if len(numerics) < 3:
        return entries

    numerics.sort()
    median = numerics[len(numerics) // 2]

    if median == 0:
        return entries

    kept = []
    for e in entries:
        num = _normalise_numeric(e['value_raw'], e.get('value_numeric'))
        if num is not None and num > 0 and median > 0:
            ratio = num / median
            if ratio < 0.01 or ratio > 100:
                logger.info(
                    f"Outlier filtered: {e['term_type']} = {e['value_raw']} "
                    f"(ratio {ratio:.2f}x median) from {e['filename']}"
                )
                continue
        kept.append(e)

    return kept if kept else entries  # never filter everything


def _prefer_numeric_format(entries: List[Dict]) -> Dict:
    """
    Among entries with equal confidence, prefer values in numeric
    format (e.g. '2.31%') over written-out form (e.g. 'two%').
    """
    if len(entries) <= 1:
        return entries[0] if entries else {}

    best_conf = max(e['confidence'] for e in entries)
    top_tier = [e for e in entries if e['confidence'] >= best_conf - 0.01]

    if len(top_tier) <= 1:
        return top_tier[0] if top_tier else entries[0]

    # Among top confidence, prefer values starting with a digit or $
    for e in top_tier:
        raw = (e['value_raw'] or '').strip()
        if raw and (raw[0].isdigit() or raw[0] == '$'):
            return e

    return top_tier[0]


# ─── Term Type Categorisation ───────────────────────────────────────

# Terms where multiple legitimate values are expected (different instruments)
_MULTI_INSTRUMENT_TERMS = {
    'loan_amount', 'interest_rate', 'maturity_date', 'origination_date',
    'loan_term', 'annual_debt_service', 'prepayment_terms', 'recourse',
}

# Terms that should have one canonical property-level value
_CANONICAL_TERMS = {
    'property_name', 'property_address', 'fha_project_number',
    'total_units', 'entity_type', 'formation_state',
    'rate_type',
}

# Terms that vary over time (amendments, refinancing) — latest wins
_TIME_VARYING_TERMS = {
    'mortgage_amount', 'escrow_amount', 'mip_rate',
    'replacement_reserves', 'endorsement_date',
}

# Terms with multiple legitimate values per member/party (not conflicts)
_PER_PARTY_TERMS = {
    'membership_interest_pct',
}

# Entity terms that need case-normalised dedup
_ENTITY_TERMS = {
    'borrower', 'lender', 'managing_member', 'investor_member',
}

# Distribution / waterfall terms — period-specific, keep all
_PERIOD_TERMS = {
    'grand_total_distributions', 'ka_total_distributions',
    'idp_total_distributions', 'initial_equity_contribution',
}


# ─── Document Recency Heuristic ─────────────────────────────────────

# Filename patterns that hint at document chronology.
# Higher number = more recent.
_AMENDMENT_ORDER = re.compile(
    r'(?i)(?:amendment|amend(?:ed)?)\s*(?:no\.?\s*|#\s*)?(\d+)',
)
_RESTATED_BONUS = re.compile(r'(?i)restat(?:ed|ement)')
_CLOSING_BOOK = re.compile(r'(?i)closing\s*book')


def _recency_score(filename: str, doc_type: str) -> int:
    """
    Estimate how 'recent' a document is from its filename.
    Higher = more recent.  Used to break ties.
    """
    score = 0
    m = _AMENDMENT_ORDER.search(filename)
    if m:
        score += int(m.group(1)) * 10
    if _RESTATED_BONUS.search(filename):
        score += 5
    if _CLOSING_BOOK.search(filename):
        score += 20  # closing book transcripts reference final values
    return score


# ─── Core Reconciliation ────────────────────────────────────────────

class ReconciledTerm:
    """A single reconciled property-level term."""

    __slots__ = (
        'term_type', 'canonical_value', 'canonical_numeric',
        'confidence', 'source_doc_id', 'source_filename',
        'source_doc_type', 'all_values', 'conflict', 'notes',
    )

    def __init__(self, term_type: str):
        self.term_type = term_type
        self.canonical_value: Optional[str] = None
        self.canonical_numeric: Optional[float] = None
        self.confidence: float = 0.0
        self.source_doc_id: Optional[int] = None
        self.source_filename: Optional[str] = None
        self.source_doc_type: Optional[str] = None
        self.all_values: List[Dict] = []  # all raw extractions
        self.conflict: bool = False  # True if multiple distinct values
        self.notes: str = ''

    def to_dict(self) -> Dict:
        return {
            'term_type': self.term_type,
            'canonical_value': self.canonical_value,
            'canonical_numeric': self.canonical_numeric,
            'confidence': self.confidence,
            'source_doc_id': self.source_doc_id,
            'source_filename': self.source_filename,
            'source_doc_type': self.source_doc_type,
            'conflict': self.conflict,
            'notes': self.notes,
            'source_count': len(self.all_values),
        }


def _normalise_entity(value: str) -> str:
    """Normalise entity names for comparison (case, whitespace, LLC/Inc)."""
    v = value.strip().upper()
    v = re.sub(r'\s+', ' ', v)
    v = re.sub(r',\s*$', '', v)
    return v


def _normalise_numeric(value_raw: str, value_numeric: Optional[float]) -> Optional[float]:
    """Get the best numeric value for comparison."""
    if value_numeric is not None:
        return value_numeric
    # Try to parse from raw
    m = re.search(r'[\$]?([\d,]+\.?\d*)', value_raw or '')
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except ValueError:
            pass
    return None


def reconcile_terms(db, property_id: int) -> Dict[str, Any]:
    """
    Reconcile financial terms across all documents for a property.

    Returns:
        {
            'property_id': int,
            'canonical_terms': {term_type: ReconciledTerm.to_dict()},
            'multi_instrument': {term_type: [{value, docs, ...}]},
            'noise_filtered': int,
            'conflicts': [term_type, ...],
            'stats': {...},
        }
    """
    # Pull all terms with doc metadata
    rows = db.execute('''
        SELECT ft.id, ft.term_type, ft.value_raw, ft.value_numeric,
               ft.confidence, ft.document_id,
               d.filename, d.document_type
        FROM financial_terms ft
        JOIN documents d ON ft.document_id = d.id
        WHERE d.property_id = ?
        ORDER BY ft.term_type, ft.confidence DESC
    ''', (property_id,)).fetchall()

    # Group by term_type
    by_type: Dict[str, List[Dict]] = defaultdict(list)
    noise_count = 0

    for r in rows:
        raw_val = r[2] or ''

        # Filter noise
        if _is_noise(raw_val):
            noise_count += 1
            logger.debug(f"Noise filtered: {r[1]} = '{raw_val[:60]}' from {r[6]}")
            continue

        by_type[r[1]].append({
            'id': r[0],
            'term_type': r[1],
            'value_raw': raw_val,
            'value_numeric': r[3],
            'confidence': r[4] or 0,
            'doc_id': r[5],
            'filename': r[6],
            'doc_type': r[7],
        })

    canonical: Dict[str, Dict] = {}
    multi_instrument: Dict[str, List] = {}
    conflicts: List[str] = []

    for term_type, entries in sorted(by_type.items()):
        if not entries:
            continue

        rt = ReconciledTerm(term_type)
        rt.all_values = entries

        if len(entries) == 1:
            # Single source — trivial
            e = entries[0]
            rt.canonical_value = e['value_raw']
            rt.canonical_numeric = e['value_numeric']
            rt.confidence = e['confidence']
            rt.source_doc_id = e['doc_id']
            rt.source_filename = e['filename']
            rt.source_doc_type = e['doc_type']
            canonical[term_type] = rt.to_dict()
            continue

        # ── Multi-instrument terms → group by instrument, don't merge ──
        if term_type in _MULTI_INSTRUMENT_TERMS:
            # Filter outliers before grouping
            cleaned = _remove_numeric_outliers(entries)
            groups = _group_by_instrument(cleaned, term_type)
            multi_instrument[term_type] = groups
            # Pick canonical: prefer numeric format among top confidence
            best = _prefer_numeric_format(cleaned)
            rt.canonical_value = best['value_raw']
            rt.canonical_numeric = best['value_numeric']
            rt.confidence = best['confidence']
            rt.source_doc_id = best['doc_id']
            rt.source_filename = best['filename']
            rt.source_doc_type = best['doc_type']
            rt.conflict = len(groups) > 1
            rt.notes = f'{len(groups)} instruments'
            if rt.conflict:
                conflicts.append(term_type)
            canonical[term_type] = rt.to_dict()
            continue

        # ── Per-party terms → keep all, each represents a different member ──
        if term_type in _PER_PARTY_TERMS:
            cleaned = _remove_numeric_outliers(entries)
            groups = _group_by_instrument(cleaned, term_type)
            multi_instrument[term_type] = groups
            # Pick the highest value as canonical (majority owner)
            best = max(cleaned, key=lambda e: (
                _normalise_numeric(e['value_raw'], e.get('value_numeric')) or 0,
                e['confidence'],
            ))
            rt.canonical_value = best['value_raw']
            rt.canonical_numeric = best['value_numeric']
            rt.confidence = best['confidence']
            rt.source_doc_id = best['doc_id']
            rt.source_filename = best['filename']
            rt.source_doc_type = best['doc_type']
            rt.conflict = len(groups) > 1
            rt.notes = f'{len(groups)} members'
            if rt.conflict:
                conflicts.append(term_type)
            canonical[term_type] = rt.to_dict()
            continue

        # ── Period-specific terms → keep all, note the range ──
        if term_type in _PERIOD_TERMS:
            # Dedup and keep all distinct values
            seen = {}
            for e in entries:
                num = _normalise_numeric(e['value_raw'], e['value_numeric'])
                key = num if num is not None else e['value_raw'].strip()
                if key not in seen:
                    seen[key] = e
            multi_instrument[term_type] = [
                {'value': e['value_raw'], 'numeric': e['value_numeric'],
                 'doc': e['filename'], 'doc_id': e['doc_id']}
                for e in seen.values()
            ]
            best = max(entries, key=lambda e: e['confidence'])
            rt.canonical_value = best['value_raw']
            rt.canonical_numeric = best['value_numeric']
            rt.confidence = best['confidence']
            rt.source_doc_id = best['doc_id']
            rt.source_filename = best['filename']
            rt.source_doc_type = best['doc_type']
            rt.notes = f'{len(seen)} periods'
            canonical[term_type] = rt.to_dict()
            continue

        # ── Entity terms → case-normalised dedup ──
        if term_type in _ENTITY_TERMS:
            entity_groups = _dedup_entities(entries)
            best_group = max(entity_groups, key=lambda g: g['total_confidence'])
            rt.canonical_value = best_group['canonical']
            rt.confidence = best_group['best_confidence']
            rt.source_doc_id = best_group['best_doc_id']
            rt.source_filename = best_group['best_filename']
            rt.source_doc_type = best_group['best_doc_type']
            rt.conflict = len(entity_groups) > 1
            if rt.conflict:
                rt.notes = f'{len(entity_groups)} distinct entities'
                conflicts.append(term_type)
            canonical[term_type] = rt.to_dict()
            continue

        # ── Time-varying terms → most recent document wins ──
        if term_type in _TIME_VARYING_TERMS:
            best = max(entries, key=lambda e: (
                _recency_score(e['filename'], e['doc_type']),
                e['confidence'],
            ))
            rt.canonical_value = best['value_raw']
            rt.canonical_numeric = best['value_numeric']
            rt.confidence = best['confidence']
            rt.source_doc_id = best['doc_id']
            rt.source_filename = best['filename']
            rt.source_doc_type = best['doc_type']
            distinct = _count_distinct(entries)
            rt.conflict = distinct > 1
            rt.notes = f'latest of {len(entries)} values' if distinct > 1 else ''
            if rt.conflict:
                conflicts.append(term_type)
            canonical[term_type] = rt.to_dict()
            continue

        # ── Canonical terms → highest confidence, majority wins on tie ──
        if term_type in _CANONICAL_TERMS:
            best = _pick_canonical(entries)
            rt.canonical_value = best['value_raw']
            rt.canonical_numeric = best['value_numeric']
            rt.confidence = best['confidence']
            rt.source_doc_id = best['doc_id']
            rt.source_filename = best['filename']
            rt.source_doc_type = best['doc_type']
            distinct = _count_distinct(entries)
            rt.conflict = distinct > 1
            if rt.conflict:
                conflicts.append(term_type)
            canonical[term_type] = rt.to_dict()
            continue

        # ── Default: highest confidence wins, prefer numeric format ──
        best = _prefer_numeric_format(entries)
        rt.canonical_value = best['value_raw']
        rt.canonical_numeric = best['value_numeric']
        rt.confidence = best['confidence']
        rt.source_doc_id = best['doc_id']
        rt.source_filename = best['filename']
        rt.source_doc_type = best['doc_type']
        distinct = _count_distinct(entries)
        rt.conflict = distinct > 1
        if rt.conflict:
            conflicts.append(term_type)
        canonical[term_type] = rt.to_dict()

    return {
        'property_id': property_id,
        'canonical_terms': canonical,
        'multi_instrument': multi_instrument,
        'noise_filtered': noise_count,
        'conflicts': conflicts,
        'stats': {
            'total_raw_terms': len(rows) + noise_count,
            'after_noise_filter': len(rows),
            'unique_term_types': len(by_type),
            'canonical_count': len(canonical),
            'conflict_count': len(conflicts),
            'multi_instrument_count': len(multi_instrument),
        },
    }


# ─── Helpers ─────────────────────────────────────────────────────────

def _count_distinct(entries: List[Dict]) -> int:
    """Count distinct values (by numeric if available, else raw)."""
    seen = set()
    for e in entries:
        num = _normalise_numeric(e['value_raw'], e['value_numeric'])
        if num is not None:
            seen.add(num)
        else:
            seen.add(e['value_raw'].strip().upper())
    return len(seen)


def _pick_canonical(entries: List[Dict]) -> Dict:
    """
    Pick the canonical value using majority voting + confidence.
    If multiple values exist, the most common wins. Ties broken by confidence.
    """
    # Group by normalised value
    value_groups: Dict[str, List[Dict]] = defaultdict(list)
    for e in entries:
        num = _normalise_numeric(e['value_raw'], e['value_numeric'])
        if num is not None:
            key = str(num)
        else:
            key = e['value_raw'].strip().upper()
        value_groups[key].append(e)

    if len(value_groups) == 1:
        # All agree
        best = max(entries, key=lambda e: e['confidence'])
        return best

    # Majority voting: most occurrences wins, then highest confidence
    best_group = max(
        value_groups.values(),
        key=lambda group: (len(group), max(e['confidence'] for e in group)),
    )
    return max(best_group, key=lambda e: e['confidence'])


def _group_by_instrument(entries: List[Dict], term_type: str) -> List[Dict]:
    """
    Group multi-instrument terms by their source document/instrument.
    Returns a list of instrument groups with deduped values.
    """
    # For loan terms, group by document (each doc = one instrument)
    # Dedup by numeric value when possible
    seen_values = {}
    groups = []

    for e in entries:
        num = _normalise_numeric(e['value_raw'], e['value_numeric'])
        key = num if num is not None else e['value_raw'].strip()

        if key not in seen_values:
            seen_values[key] = {
                'value': e['value_raw'],
                'numeric': e['value_numeric'],
                'confidence': e['confidence'],
                'docs': [{'id': e['doc_id'], 'filename': e['filename'], 'doc_type': e['doc_type']}],
            }
        else:
            seen_values[key]['docs'].append(
                {'id': e['doc_id'], 'filename': e['filename'], 'doc_type': e['doc_type']}
            )
            if e['confidence'] > seen_values[key]['confidence']:
                seen_values[key]['confidence'] = e['confidence']
                seen_values[key]['value'] = e['value_raw']

    return list(seen_values.values())


def _dedup_entities(entries: List[Dict]) -> List[Dict]:
    """
    Deduplicate entity names by case-normalised comparison.
    Returns groups of equivalent entity names.
    """
    groups: Dict[str, Dict] = {}

    for e in entries:
        norm = _normalise_entity(e['value_raw'])
        if norm not in groups:
            groups[norm] = {
                'canonical': e['value_raw'],
                'normalised': norm,
                'total_confidence': e['confidence'],
                'best_confidence': e['confidence'],
                'best_doc_id': e['doc_id'],
                'best_filename': e['filename'],
                'best_doc_type': e['doc_type'],
                'count': 1,
            }
        else:
            g = groups[norm]
            g['count'] += 1
            g['total_confidence'] += e['confidence']
            if e['confidence'] > g['best_confidence']:
                g['best_confidence'] = e['confidence']
                g['best_doc_id'] = e['doc_id']
                g['best_filename'] = e['filename']
                g['best_doc_type'] = e['doc_type']
                g['canonical'] = e['value_raw']  # prefer higher-confidence spelling

    return list(groups.values())


# ─── Pretty Print ────────────────────────────────────────────────────

def print_reconciliation(result: Dict) -> None:
    """Print a human-readable reconciliation report."""
    stats = result['stats']
    print(f"\n{'='*70}")
    print(f"TERM RECONCILIATION — Property {result['property_id']}")
    print(f"{'='*70}")
    print(f"  Raw terms:          {stats['total_raw_terms']}")
    print(f"  After noise filter: {stats['after_noise_filter']}")
    print(f"  Unique term types:  {stats['unique_term_types']}")
    print(f"  Canonical values:   {stats['canonical_count']}")
    print(f"  Conflicts:          {stats['conflict_count']}")
    print(f"  Multi-instrument:   {stats['multi_instrument_count']}")
    print()

    # Canonical terms (clean)
    clean = {k: v for k, v in result['canonical_terms'].items() if not v['conflict']}
    if clean:
        print(f"  RESOLVED ({len(clean)} terms — single canonical value)")
        print(f"  {'-'*66}")
        for tt in sorted(clean.keys()):
            v = clean[tt]
            val = v['canonical_value']
            if len(val) > 50:
                val = val[:47] + '...'
            print(f"    {tt:30s}  {val:50s}  [{v['source_doc_type']}]")
        print()

    # Conflicts
    conflicted = {k: v for k, v in result['canonical_terms'].items() if v['conflict']}
    if conflicted:
        print(f"  CONFLICTS ({len(conflicted)} terms — multiple distinct values)")
        print(f"  {'-'*66}")
        for tt in sorted(conflicted.keys()):
            v = conflicted[tt]
            val = v['canonical_value']
            if len(val) > 50:
                val = val[:47] + '...'
            notes = v.get('notes', '')
            print(f"    {tt:30s}  {val:50s}  {notes}")
        print()

    # Multi-instrument detail
    if result['multi_instrument']:
        print(f"  MULTI-INSTRUMENT DETAIL")
        print(f"  {'-'*66}")
        for tt, groups in sorted(result['multi_instrument'].items()):
            print(f"    {tt} ({len(groups)} values):")
            for g in groups:
                if isinstance(g, dict) and 'docs' in g:
                    doc_names = ', '.join(d['filename'][:30] for d in g['docs'])
                    print(f"      {g['value']:>30s}  from {doc_names}")
                elif isinstance(g, dict):
                    print(f"      {g.get('value', '?'):>30s}  from {g.get('doc', '?')[:30]}")
            print()

    if result['noise_filtered'] > 0:
        print(f"  Filtered {result['noise_filtered']} noisy extractions")
    print()
