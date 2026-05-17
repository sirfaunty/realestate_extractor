"""
Financial Synthesis — Reconciled property-level financial summaries.

Takes extracted operating_statement_items for a property and produces:
  1. Period-by-period income/expense/NOI breakdown
  2. Source document citations for every figure
  3. Discrepancy detection when multiple documents disagree
  4. NOI timeline across all available periods

Designed for scale — works generically for any property_id, no
property-specific logic. Handles multiple NOI definitions
(with/without TIF, cash vs accrual, etc.) by tracking source
authority and flagging divergence.
"""

import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Source Authority ────────────────────────────────────────────────
# Higher number = more authoritative for financial data.
# When two documents report the same line item for the same period,
# the higher-authority source wins.

DOC_TYPE_AUTHORITY = {
    'general_ledger': 5,     # GL is the book of record
    'operating_statement': 4, # formal operating statements
    'rent_roll': 4,           # formal rent rolls
    'closing': 3,             # closing docs with actuals
    'due_diligence': 2,       # work product / memos
    'proforma': 2,            # projections / models
    'equity_waterfall': 2,
    'loan': 2,
    'guarantee': 1,
    'lease': 1,
    'hud_form': 3,
    'cost_certification': 3,
    'partnership_agreement': 1,
    'organizational': 0,
    'reference': 0,
}

# Tolerance for "same amount" when comparing across sources.
# Two amounts within this fraction of each other are considered matching.
AMOUNT_MATCH_TOLERANCE = 0.005  # 0.5%

# Period normalization — strip suffixes for comparison
_PERIOD_SORT_RE = re.compile(r'^(\d{4})\s*([A-Za-z]*)')


def _period_sort_key(period: str) -> Tuple[int, int]:
    """Sort periods chronologically: year first, then suffix order."""
    m = _PERIOD_SORT_RE.match(period or '')
    if not m:
        return (9999, 99)
    year = int(m.group(1))
    suffix = (m.group(2) or '').upper()
    suffix_order = {'A': 0, 'ACTUAL': 0, 'ACTUALS': 0,
                    'B': 1, 'BUDGET': 1,
                    'F': 2, 'FORECAST': 2, 'REFORECAST': 2,
                    'P': 3, 'PROFORMA': 3}
    return (year, suffix_order.get(suffix, 10))


def _amounts_match(a: float, b: float) -> bool:
    """Check if two amounts are close enough to be considered the same."""
    if a == b:
        return True
    if a == 0 or b == 0:
        return False
    return abs(a - b) / max(abs(a), abs(b)) < AMOUNT_MATCH_TOLERANCE


class FinancialSynthesizer:
    """
    Produce a reconciled financial summary for a property from
    extracted operating_statement_items.
    """

    def __init__(self, db):
        """
        Args:
            db: Database instance (already connected)
        """
        self.db = db

    # ─── Main Entry Point ────────────────────────────────────────────

    def synthesize(self, property_id: int) -> Dict:
        """
        Build a full financial synthesis for a property.

        Returns:
            {
                "property_id": int,
                "property_name": str,
                "periods": ["2021A", "2022A", ...],   # sorted
                "period_summaries": {
                    "2023A": {
                        "total_income": float,
                        "total_expenses": float,
                        "calculated_noi": float,
                        "reported_noi": [
                            {"amount": float, "line_item": str,
                             "source": {"doc_id": int, "filename": str, "page": int}}
                        ],
                        "income_items": [{...}],
                        "expense_items": [{...}],
                        "sources": [{"doc_id": int, "filename": str,
                                     "doc_type": str, "authority": int}],
                        "discrepancies": [str],
                    }
                },
                "noi_timeline": [
                    {"period": str, "calculated_noi": float,
                     "reported_noi": float|None,
                     "primary_source": {"doc_id": int, "filename": str},
                     "source_count": int}
                ],
                "document_sources": [
                    {"doc_id": int, "filename": str, "doc_type": str,
                     "authority": int, "item_count": int}
                ],
                "synthesis_notes": [str],
            }
        """
        # Fetch all opstat items for this property, joined with doc info
        items = self._fetch_items(property_id)
        if not items:
            return self._empty_result(property_id)

        # Get property name from the first item
        property_name = items[0]['property_name'] or ''

        # Build per-period summaries
        periods_data = self._group_by_period(items)
        sorted_periods = sorted(periods_data.keys(), key=_period_sort_key)

        period_summaries = {}
        for period in sorted_periods:
            period_summaries[period] = self._synthesize_period(
                period, periods_data[period])

        # Split periods by quality: primary (high/medium) vs secondary (low)
        primary_periods = [p for p in sorted_periods
                           if period_summaries[p].get('quality') in ('high', 'medium')]
        secondary_periods = [p for p in sorted_periods
                              if period_summaries[p].get('quality') == 'low']

        # Build NOI timeline (primary periods only by default)
        noi_timeline = self._build_noi_timeline(primary_periods, period_summaries)

        # Collect document source list
        doc_sources = self._collect_doc_sources(items)

        # Generate synthesis-level notes
        notes = self._generate_notes(primary_periods, period_summaries, doc_sources)
        if secondary_periods:
            notes.append(
                f"{len(secondary_periods)} additional period(s) with sparse data "
                f"not shown: {', '.join(secondary_periods)}."
            )

        return {
            'property_id': property_id,
            'property_name': property_name,
            'periods': primary_periods,
            'all_periods': sorted_periods,
            'secondary_periods': secondary_periods,
            'period_summaries': period_summaries,
            'noi_timeline': noi_timeline,
            'document_sources': doc_sources,
            'synthesis_notes': notes,
        }

    # ─── Data Fetching ───────────────────────────────────────────────

    def _fetch_items(self, property_id: int) -> List[Dict]:
        """Fetch all operating_statement_items for a property with doc metadata."""
        cur = self.db.conn.execute("""
            SELECT
                os.id, os.document_id, os.property_name, os.period,
                os.category, os.subcategory, os.line_item,
                os.amount, os.amount_psf,
                os.is_subtotal, os.is_total, os.page_number,
                d.filename, d.document_type
            FROM operating_statement_items os
            JOIN documents d ON os.document_id = d.id
            WHERE d.property_id = ?
            ORDER BY os.period, os.category, os.line_item
        """, (property_id,))

        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _empty_result(self, property_id: int) -> Dict:
        """Return an empty synthesis when no data exists."""
        prop = self.db.get_property(property_id) if hasattr(self.db, 'get_property') else None
        name = prop.get('name', '') if prop else ''
        return {
            'property_id': property_id,
            'property_name': name,
            'periods': [],
            'period_summaries': {},
            'noi_timeline': [],
            'document_sources': [],
            'synthesis_notes': ['No operating statement data found for this property.'],
        }

    # ─── Period Grouping ─────────────────────────────────────────────

    def _group_by_period(self, items: List[Dict]) -> Dict[str, List[Dict]]:
        """Group items by period, filtering out far-future amortization periods."""
        import datetime
        current_year = datetime.date.today().year
        max_year = current_year + 12  # Allow up to 12 years of proforma

        by_period = defaultdict(list)
        for item in items:
            period = item.get('period') or 'Unknown'
            # Filter out far-future periods (loan amortization schedules)
            m = _PERIOD_SORT_RE.match(period)
            if m:
                year = int(m.group(1))
                if year > max_year:
                    continue
            by_period[period].append(item)
        return dict(by_period)

    # ─── Per-Period Synthesis ────────────────────────────────────────

    def _synthesize_period(self, period: str, items: List[Dict]) -> Dict:
        """
        Build a reconciled summary for a single period.

        Strategy: select the primary document (most non-subtotal detail
        items) and use ONLY its income/expense data. Secondary documents
        contribute reported NOI values for cross-reference but not line
        items, since CRE documents use different names for the same
        economic concepts ("MARKET RENT RESIDENTIAL" vs "Gross Potential
        Apt Rent") which causes double-counting in line-by-line merging.
        """
        # Find the primary document for this period (most detail items)
        doc_item_counts = defaultdict(int)
        for i in items:
            if not i.get('is_subtotal') and not i.get('is_total') \
                    and i['category'] in ('income', 'revenue', 'expense'):
                doc_item_counts[i['document_id']] += 1

        primary_doc_id = max(doc_item_counts, key=doc_item_counts.get) \
            if doc_item_counts else None

        # Use only primary document's line items for income/expense
        income_raw = [i for i in items
                      if i['category'] in ('income', 'revenue')
                      and not i.get('is_subtotal') and not i.get('is_total')
                      and (primary_doc_id is None or i['document_id'] == primary_doc_id)]
        expense_raw = [i for i in items
                       if i['category'] == 'expense'
                       and not i.get('is_subtotal') and not i.get('is_total')
                       and (primary_doc_id is None or i['document_id'] == primary_doc_id)]

        # Collect ALL subtotals from ALL documents (for cross-reference)
        subtotals = [i for i in items if i.get('is_subtotal') or i.get('is_total')]

        # Format line items with citations (no cross-doc reconciliation needed)
        income_items = [self._format_line_item(i) for i in income_raw]
        expense_items = [self._format_line_item(i) for i in expense_raw]
        income_items.sort(key=lambda x: -(x.get('amount') or 0))
        expense_items.sort(key=lambda x: -(x.get('amount') or 0))

        # Calculate totals from reconciled items
        total_income = sum(i['amount'] for i in income_items if i['amount'])
        total_expenses = sum(i['amount'] for i in expense_items if i['amount'])

        # Fallback: if no detail items exist for a category, use best subtotal
        # This handles documents that only report summary totals (e.g., Property
        # Overview with "Total OPEX" but no individual expense line items).
        if total_expenses == 0 and not expense_raw:
            opex_subtotal = self._find_best_subtotal(subtotals, 'expense')
            if opex_subtotal is not None:
                total_expenses = opex_subtotal

        if total_income == 0 and not income_raw:
            income_subtotal = self._find_best_subtotal(subtotals, 'income')
            if income_subtotal is not None:
                total_income = income_subtotal

        calculated_noi = total_income - total_expenses

        # Collect reported NOI from subtotals
        reported_noi = self._extract_reported_noi(subtotals)

        # Sources contributing to this period
        doc_ids_seen = set()
        sources = []
        for item in items:
            did = item['document_id']
            if did not in doc_ids_seen:
                doc_ids_seen.add(did)
                sources.append({
                    'doc_id': did,
                    'filename': item['filename'],
                    'doc_type': item['document_type'],
                    'authority': DOC_TYPE_AUTHORITY.get(
                        item['document_type'], 0),
                })
        sources.sort(key=lambda s: -s['authority'])

        # Detect discrepancies
        discrepancies = self._detect_discrepancies(
            period, calculated_noi, reported_noi, income_raw, expense_raw)

        # Quality score: how complete/trustworthy is this period's data?
        quality = self._score_period_quality(
            income_items, expense_items, total_income, total_expenses,
            reported_noi, sources)

        return {
            'total_income': round(total_income, 2),
            'total_expenses': round(total_expenses, 2),
            'calculated_noi': round(calculated_noi, 2),
            'reported_noi': reported_noi,
            'income_items': income_items,
            'expense_items': expense_items,
            'sources': sources,
            'discrepancies': discrepancies,
            'quality': quality,
        }

    def _score_period_quality(self, income_items, expense_items,
                               total_income, total_expenses,
                               reported_noi, sources) -> str:
        """
        Rate period data quality as 'high', 'medium', or 'low'.

        high:   Both income AND expense present with plausible amounts
        medium: At least one side has detail, but other side may be thin
        low:    Sparse data, zero totals, extreme imbalance, or only noise
        """
        n_income = len(income_items)
        n_expense = len(expense_items)
        has_reported_noi = len(reported_noi) > 0

        # Negative income is a red flag — summary rows with offsets only
        if total_income < 0:
            return 'low'

        # Extreme imbalance: one side has big numbers, other near-zero
        # (e.g., $11M income but $1K expenses — clearly incomplete)
        if total_income > 0 and total_expenses > 0:
            ratio = min(total_income, total_expenses) / max(total_income, total_expenses)
            if ratio < 0.10:  # Less than 10% of the larger side
                return 'low'

        # Both sides have detail items → high
        if n_income >= 3 and n_expense >= 3:
            return 'high'

        # One side has detail + subtotal fallback on other, reasonable ratio
        if (n_income >= 3 or n_expense >= 3) and total_income > 0 and total_expenses > 0:
            return 'high'

        # Subtotal-only but both are nonzero and balanced
        if total_income > 0 and total_expenses > 0 and has_reported_noi:
            return 'medium'

        # Only one side has data
        if total_income > 0 or total_expenses > 0:
            if n_income + n_expense >= 3:
                return 'medium'
            return 'low'

        # Only reported NOI, no calculated data
        if has_reported_noi:
            return 'low'

        return 'low'

    def _reconcile_line_items(self, items: List[Dict]) -> List[Dict]:
        """
        When multiple documents report the same line item, keep the
        version from the highest-authority source.

        Returns deduplicated, cited line items.
        """
        # Group by normalized line item name
        by_name = defaultdict(list)
        for item in items:
            key = item['line_item'].strip().lower()
            by_name[key].append(item)

        reconciled = []
        for key, group in by_name.items():
            if len(group) == 1:
                # Single source — straightforward
                i = group[0]
                reconciled.append(self._format_line_item(i))
            else:
                # Multiple sources — pick highest authority, note others
                group.sort(key=lambda x: -DOC_TYPE_AUTHORITY.get(
                    x['document_type'], 0))
                primary = group[0]
                entry = self._format_line_item(primary)

                # Record alternate sources
                alt_sources = []
                for alt in group[1:]:
                    if alt['amount'] and primary['amount']:
                        if not _amounts_match(alt['amount'], primary['amount']):
                            alt_sources.append({
                                'amount': round(alt['amount'], 2),
                                'source': {
                                    'doc_id': alt['document_id'],
                                    'filename': alt['filename'],
                                    'page': alt.get('page_number'),
                                },
                            })
                if alt_sources:
                    entry['alternate_values'] = alt_sources

                reconciled.append(entry)

        # Sort: largest amounts first for readability
        reconciled.sort(key=lambda x: -(x.get('amount') or 0))
        return reconciled

    def _format_line_item(self, item: Dict) -> Dict:
        """Format a single line item with source citation."""
        return {
            'line_item': item['line_item'],
            'amount': round(item['amount'], 2) if item['amount'] else None,
            'subcategory': item.get('subcategory'),
            'source': {
                'doc_id': item['document_id'],
                'filename': item['filename'],
                'page': item.get('page_number'),
            },
        }

    def _find_best_subtotal(self, subtotals: List[Dict],
                            category: str) -> Optional[float]:
        """
        Find the best subtotal value for a category when no detail items exist.

        For expenses, looks for "Total OPEX", "Total Operating Expenses", etc.
        For income, looks for "Total Revenue", "Total Income", etc.
        Prefers higher-authority documents. Returns the amount or None.
        """
        candidates = []
        for item in subtotals:
            if item['category'] != category:
                continue
            line_lower = (item['line_item'] or '').lower()
            amount = item.get('amount', 0)
            if not amount or amount <= 0:
                continue

            # Score by how "total"-like the line item name is
            score = 0
            authority = DOC_TYPE_AUTHORITY.get(item['document_type'], 0)

            if category == 'expense':
                if 'total' in line_lower and ('opex' in line_lower
                        or 'operating' in line_lower
                        or 'expense' in line_lower):
                    score = 10  # Best: "Total OPEX" or "Total Operating Expenses"
                elif 'total' in line_lower and 'expense' in line_lower:
                    score = 8
                elif 'opex' in line_lower and 'total' not in line_lower:
                    score = 5   # Partial: "OPEX" without "Total"
            elif category in ('income', 'revenue'):
                if 'total' in line_lower and ('revenue' in line_lower
                        or 'income' in line_lower):
                    score = 10
                elif 'total' in line_lower:
                    score = 5

            if score > 0:
                candidates.append((score, authority, amount, item))

        if not candidates:
            return None

        # Best = highest score, then highest authority
        candidates.sort(key=lambda c: (-c[0], -c[1]))
        return candidates[0][2]

    def _extract_reported_noi(self, subtotals: List[Dict]) -> List[Dict]:
        """Extract explicitly reported NOI values from subtotal rows."""
        noi_reports = []
        for item in subtotals:
            line_lower = (item['line_item'] or '').lower()
            if 'noi' in line_lower or 'net operating' in line_lower:
                if item['amount']:
                    noi_reports.append({
                        'amount': round(item['amount'], 2),
                        'line_item': item['line_item'],
                        'source': {
                            'doc_id': item['document_id'],
                            'filename': item['filename'],
                            'page': item.get('page_number'),
                        },
                    })
        return noi_reports

    # ─── Discrepancy Detection ───────────────────────────────────────

    def _detect_discrepancies(self, period: str, calculated_noi: float,
                               reported_noi: List[Dict],
                               income_items: List[Dict],
                               expense_items: List[Dict]) -> List[str]:
        """Flag significant discrepancies in a period's data."""
        notes = []

        # Check if reported NOI values disagree with each other
        if len(reported_noi) >= 2:
            amounts = [r['amount'] for r in reported_noi]
            min_noi, max_noi = min(amounts), max(amounts)
            if not _amounts_match(min_noi, max_noi):
                sources_str = ', '.join(
                    f"${r['amount']:,.0f} ({r['source']['filename']})"
                    for r in reported_noi
                )
                notes.append(
                    f"Multiple NOI values reported for {period}: {sources_str}. "
                    f"Differences may reflect different accounting treatments "
                    f"(TIF inclusion, cash vs accrual, management fee netting)."
                )

        # Check if calculated NOI diverges from reported
        if reported_noi and calculated_noi:
            closest = min(reported_noi, key=lambda r: abs(r['amount'] - calculated_noi))
            if not _amounts_match(closest['amount'], calculated_noi):
                diff = calculated_noi - closest['amount']
                notes.append(
                    f"Calculated NOI (${calculated_noi:,.0f}) differs from "
                    f"closest reported NOI (${closest['amount']:,.0f} from "
                    f"{closest['source']['filename']}) by ${abs(diff):,.0f}. "
                    f"This may indicate missing line items or different "
                    f"inclusion/exclusion rules."
                )

        # Check if multiple docs contributed income items with divergent totals
        doc_income = defaultdict(float)
        for item in income_items:
            if item.get('amount') and not item.get('is_subtotal'):
                doc_income[item['document_id']] += item['amount']

        if len(doc_income) >= 2:
            totals = list(doc_income.values())
            if not _amounts_match(min(totals), max(totals)):
                notes.append(
                    f"Income totals differ across {len(doc_income)} source "
                    f"documents for {period}. The reconciled view uses the "
                    f"highest-authority source for each line item."
                )

        return notes

    # ─── NOI Timeline ────────────────────────────────────────────────

    def _build_noi_timeline(self, sorted_periods: List[str],
                             period_summaries: Dict) -> List[Dict]:
        """Build a chronological NOI timeline with source citations."""
        timeline = []
        for period in sorted_periods:
            ps = period_summaries[period]

            # Pick "best" reported NOI if available
            best_reported = None
            if ps['reported_noi']:
                # Prefer the one closest to our calculated value
                if ps['calculated_noi']:
                    best_reported = min(
                        ps['reported_noi'],
                        key=lambda r: abs(r['amount'] - ps['calculated_noi']))
                else:
                    best_reported = ps['reported_noi'][0]

            # Primary source = highest-authority doc for this period
            primary_source = ps['sources'][0] if ps['sources'] else None

            timeline.append({
                'period': period,
                'calculated_noi': ps['calculated_noi'],
                'reported_noi': best_reported['amount'] if best_reported else None,
                'reported_noi_source': best_reported['source'] if best_reported else None,
                'primary_source': {
                    'doc_id': primary_source['doc_id'],
                    'filename': primary_source['filename'],
                } if primary_source else None,
                'source_count': len(ps['sources']),
                'has_discrepancies': len(ps['discrepancies']) > 0,
            })
        return timeline

    # ─── Document Sources ────────────────────────────────────────────

    def _collect_doc_sources(self, items: List[Dict]) -> List[Dict]:
        """List all source documents that contributed financial data."""
        doc_map = {}
        for item in items:
            did = item['document_id']
            if did not in doc_map:
                doc_map[did] = {
                    'doc_id': did,
                    'filename': item['filename'],
                    'doc_type': item['document_type'],
                    'authority': DOC_TYPE_AUTHORITY.get(
                        item['document_type'], 0),
                    'item_count': 0,
                }
            doc_map[did]['item_count'] += 1

        return sorted(doc_map.values(), key=lambda d: (-d['authority'], -d['item_count']))

    # ─── Notes / Insights ────────────────────────────────────────────

    def _generate_notes(self, sorted_periods: List[str],
                         period_summaries: Dict,
                         doc_sources: List[Dict]) -> List[str]:
        """Generate high-level synthesis notes."""
        notes = []

        if not sorted_periods:
            notes.append('No financial periods found.')
            return notes

        notes.append(
            f"Financial data spans {len(sorted_periods)} periods: "
            f"{sorted_periods[0]} through {sorted_periods[-1]}."
        )

        notes.append(
            f"Data sourced from {len(doc_sources)} document(s)."
        )

        # Count periods with discrepancies
        disc_count = sum(
            1 for p in sorted_periods
            if period_summaries[p]['discrepancies']
        )
        if disc_count:
            notes.append(
                f"{disc_count} of {len(sorted_periods)} periods have "
                f"discrepancies between source documents. See period-level "
                f"notes for details."
            )

        # Check for budget/forecast periods
        budget_periods = [
            p for p in sorted_periods
            if any(s in p.upper() for s in ('B', 'BUDGET', 'F', 'FORECAST'))
        ]
        if budget_periods:
            notes.append(
                f"Budget/forecast periods detected: {', '.join(budget_periods)}. "
                f"These contain projected figures, not actuals."
            )

        return notes

    # ─── Convenience: NOI comparison against validated data ──────────

    def compare_noi(self, property_id: int,
                    validated_noi: Dict[str, float]) -> List[Dict]:
        """
        Compare synthesized NOI against a set of validated figures.

        Args:
            validated_noi: {"2023A": 4092328.31, "2024A": 2402102.01, ...}

        Returns:
            List of comparison records:
            [{
                "period": "2023A",
                "validated": 4092328.31,
                "calculated": 3627246.00,
                "reported": 4096642.00,
                "calc_diff": -465082.31,
                "calc_diff_pct": -11.4,
                "closest_match": "reported",
                "closest_diff": 4313.69,
                "closest_diff_pct": 0.1,
                "closest_source": {"doc_id": 16, "filename": "..."},
            }]
        """
        synthesis = self.synthesize(property_id)
        results = []

        for period, val_noi in sorted(validated_noi.items(), key=lambda x: _period_sort_key(x[0])):
            ps = synthesis['period_summaries'].get(period)
            if not ps:
                results.append({
                    'period': period,
                    'validated': val_noi,
                    'calculated': None,
                    'reported': None,
                    'note': f'No extracted data for period {period}',
                })
                continue

            calc = ps['calculated_noi']
            calc_diff = (calc - val_noi) if calc else None
            calc_pct = (calc_diff / val_noi * 100) if calc_diff and val_noi else None

            # Find closest reported NOI to validated
            closest = None
            closest_diff = None
            closest_pct = None
            closest_src = None
            for rnoi in ps['reported_noi']:
                d = abs(rnoi['amount'] - val_noi)
                if closest_diff is None or d < abs(closest_diff):
                    closest = rnoi['amount']
                    closest_diff = rnoi['amount'] - val_noi
                    closest_pct = (closest_diff / val_noi * 100) if val_noi else None
                    closest_src = rnoi['source']

            results.append({
                'period': period,
                'validated': val_noi,
                'calculated': calc,
                'reported': closest,
                'calc_diff': round(calc_diff, 2) if calc_diff else None,
                'calc_diff_pct': round(calc_pct, 1) if calc_pct else None,
                'closest_match': 'reported' if closest else 'calculated',
                'closest_diff': round(closest_diff, 2) if closest_diff else None,
                'closest_diff_pct': round(closest_pct, 1) if closest_pct else None,
                'closest_source': closest_src,
            })

        return results
