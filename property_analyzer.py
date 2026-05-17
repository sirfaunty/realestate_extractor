"""
Property Analyzer — Phase 2 of the ingest-then-analyze pipeline.

After documents are ingested (Phase 1), this module runs structured
analysis on all documents associated with a property:

1. Rent Roll Analysis — parse tenant data, occupancy, rent totals
2. Operating Statement Analysis — income/expense breakdowns
3. Financial Term Extraction — key loan/lease terms
4. Legal Clause Extraction — important clauses from leases/loans

Uses stored fulltext + raw tables from the database (no PDF re-reading).
LLM (Ollama) is used only for gap-filling after rule-based extraction.
"""

import json
import logging
import re
import time
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from .database import Database
from .pdf_ingestion import DocumentContent, PageContent
from .extractors.extraction_engine import ExtractionEngine, DocumentClassifier
from .extractors.llm_client import LocalLLMClient
from .templates.document_templates import get_template

logger = logging.getLogger(__name__)


class PropertyAnalyzer:
    """
    Run structured analysis on all ingested documents for a property.

    Pulls text + tables from the database (no PDF re-reading needed),
    runs appropriate parsers based on document type, and stores
    structured results.
    """

    def __init__(self, db: Database, llm_client: Optional[LocalLLMClient] = None):
        self.db = db
        self.llm = llm_client or LocalLLMClient()
        self.engine = ExtractionEngine(self.llm)
        self._on_step = None  # callback(step, detail)

    def _emit(self, step: str, detail: str = ''):
        if self._on_step:
            try:
                self._on_step(step, detail)
            except Exception:
                pass

    def analyze_property(self, property_id: int) -> Dict:
        """
        Run full analysis on all documents for a property.

        Returns a summary dict with counts of extracted data.
        """
        start = time.time()

        # Get all documents for this property
        docs = self.db.conn.execute("""
            SELECT * FROM documents WHERE property_id = ?
            ORDER BY document_type, processed_at
        """, (property_id,)).fetchall()
        docs = [dict(d) for d in docs]

        if not docs:
            return {'error': 'No documents found for this property', 'doc_count': 0}

        # Start analysis run
        run_id = self.db.start_analysis_run(property_id, len(docs))
        self._emit('analyzing', f'Analyzing {len(docs)} documents...')

        summary = {
            'doc_count': len(docs),
            'rent_roll_entries': 0,
            'operating_items': 0,
            'financial_terms': 0,
            'clauses': 0,
            'by_type': {},
        }

        try:
            # Group documents by type
            by_type = {}
            for doc in docs:
                dt = doc.get('document_type', 'unknown')
                by_type.setdefault(dt, []).append(doc)

            # Process each type
            for doc_type, type_docs in by_type.items():
                self._emit('analyzing', f'Processing {len(type_docs)} {doc_type} document(s)...')
                type_summary = {'count': len(type_docs), 'extracted': 0}

                for doc_record in type_docs:
                    doc_id = doc_record['id']

                    # Reconstruct DocumentContent from stored data
                    doc_content = self._reconstruct_document(doc_id, doc_record)
                    if not doc_content:
                        continue

                    # Get template for this doc type
                    template = get_template(doc_type)

                    if doc_type == 'rent_roll':
                        count = self._analyze_rent_roll(doc_id, doc_content, doc_record)
                        summary['rent_roll_entries'] += count
                        type_summary['extracted'] += count

                    elif doc_type == 'operating_statement':
                        count = self._analyze_operating_statement(doc_id, doc_content, doc_record)
                        summary['operating_items'] += count
                        type_summary['extracted'] += count

                    elif template:
                        # For leases, loans, etc. — run the full extraction engine
                        counts = self._analyze_with_engine(doc_id, doc_content, template)
                        summary['financial_terms'] += counts.get('terms', 0)
                        summary['clauses'] += counts.get('clauses', 0)
                        type_summary['extracted'] += counts.get('terms', 0) + counts.get('clauses', 0)

                    else:
                        # No extraction template for this type (e.g., partnership_agreement,
                        # hud_form, proforma, etc.) — content is stored but structured
                        # extraction requires a future template.
                        logger.info(
                            f"No extraction template for type '{doc_type}' — "
                            f"skipping structured extraction for {doc_record['filename']}"
                        )

                    # Mark document as analyzed
                    self.db.mark_document_analyzed(doc_id)

                summary['by_type'][doc_type] = type_summary

            summary['time'] = round(time.time() - start, 1)
            self.db.complete_analysis_run(run_id, summary)
            self._emit('complete', f'Analysis complete — {summary["doc_count"]} documents processed')

        except Exception as e:
            logger.error(f"Analysis failed for property {property_id}: {e}", exc_info=True)
            self.db.fail_analysis_run(run_id, str(e))
            summary['error'] = str(e)
            self._emit('failed', str(e))

        return summary

    def _reconstruct_document(self, doc_id: int, doc_record: Dict) -> Optional[DocumentContent]:
        """
        Rebuild a DocumentContent object from stored fulltext + tables.
        This avoids re-reading the PDF file.
        """
        # Get stored fulltext pages
        rows = self.db.conn.execute("""
            SELECT page_number, content FROM document_fulltext
            WHERE CAST(document_id AS INTEGER) = ?
            ORDER BY CAST(page_number AS INTEGER)
        """, (doc_id,)).fetchall()

        if not rows:
            logger.warning(f"No fulltext found for document {doc_id}")
            return None

        # Get stored tables
        table_rows = self.db.get_document_tables(doc_id)
        tables_by_page = {}
        for tr in table_rows:
            pn = tr['page_number']
            tables_by_page.setdefault(pn, []).append(
                [tr['headers']] + tr['rows_json']
            )

        # Build DocumentContent
        pages = []
        for row in rows:
            pn = int(row['page_number'])
            pages.append(PageContent(
                page_number=pn,
                text=row['content'],
                tables=tables_by_page.get(pn, []),
                is_scanned=bool(doc_record.get('is_scanned')),
            ))

        doc = DocumentContent(
            filepath=doc_record.get('filepath', ''),
            filename=doc_record.get('filename', ''),
            pages=pages,
            page_count=len(pages),
            is_scanned=bool(doc_record.get('is_scanned')),
            file_hash=doc_record.get('file_hash', ''),
        )
        return doc

    def _analyze_rent_roll(self, doc_id: int, doc: DocumentContent,
                           doc_record: Dict) -> int:
        """Extract rent roll data using text-based parsers."""
        # Clear any existing rent roll data for this document
        self.db.conn.execute(
            "DELETE FROM rent_roll_entries WHERE document_id = ?", (doc_id,))

        # Use the extraction engine's text parser
        rows = self.engine._extract_rent_roll_from_text(doc)

        # Fall back to table column mapping if text parser found nothing
        if not rows:
            template = get_template('rent_roll')
            if template:
                rows = self.engine._extract_tabular(doc, template)

        # Store results
        property_name = doc_record.get('property_name')
        for row in rows:
            try:
                self.db.insert_rent_roll_entry(
                    document_id=doc_id,
                    property_name=property_name,
                    unit_number=row.get('unit_number'),
                    tenant_name=row.get('tenant_name'),
                    suite=row.get('suite'),
                    square_footage=self._safe_float(row.get('square_footage')),
                    lease_start=row.get('lease_start'),
                    lease_end=row.get('lease_end'),
                    monthly_rent=self._safe_float(row.get('monthly_rent')),
                    annual_rent=self._safe_float(row.get('annual_rent')),
                    rent_psf=self._safe_float(row.get('rent_psf')),
                    status=row.get('status'),
                    notes=row.get('notes'),
                    page_number=row.get('page_number'),
                    metadata=row.get('metadata'),
                )
            except Exception as e:
                logger.warning(f"Failed to store rent roll entry: {e}")

        logger.info(f"Rent roll: extracted {len(rows)} entries from {doc_record['filename']}")
        return len(rows)

    def _analyze_operating_statement(self, doc_id: int, doc: DocumentContent,
                                      doc_record: Dict) -> int:
        """Extract operating statement data.

        Tries three strategies in order:
          1. Columnar year-table parser (handles XLSX year-column layouts)
          2. Text-based camelCase parser (Yardi/MRI exports)
          3. Template-based tabular mapping
        """
        # Clear existing
        self.db.conn.execute(
            "DELETE FROM operating_statement_items WHERE document_id = ?", (doc_id,))

        # Strategy 1: Columnar year-table parser (best for XLSX financial reports)
        rows = self._extract_columnar_financials(doc_id, doc_record)

        # Strategy 2: Text-based parser
        if not rows:
            rows = self.engine._extract_opstat_from_text(doc)

        # Strategy 3: Template-based tabular mapping
        if not rows:
            template = get_template('operating_statement')
            if template:
                rows = self.engine._extract_tabular(doc, template)

        # Store results
        property_name = doc_record.get('property_name')
        for row in rows:
            try:
                self.db.insert_operating_statement_item(
                    document_id=doc_id,
                    category=row.get('category', 'unknown'),
                    line_item=row.get('line_item', ''),
                    property_name=property_name,
                    period=row.get('period'),
                    subcategory=row.get('subcategory'),
                    amount=self._safe_float(row.get('amount')),
                    amount_psf=self._safe_float(row.get('amount_psf')),
                    is_subtotal=row.get('is_subtotal', False),
                    is_total=row.get('is_total', False),
                    page_number=row.get('page_number'),
                )
            except Exception as e:
                logger.warning(f"Failed to store opstat item: {e}")

        logger.info(f"OpStat: extracted {len(rows)} items from {doc_record['filename']}")
        return len(rows)

    # ─── Columnar Year-Table Parser ─────────────────────────────────

    # Patterns to detect year/period columns in header rows
    _YEAR_PATTERN = re.compile(
        r'(20\d{2})\s*'
        r'(actual|actuals|budget|forecast|reforecast|proforma|a|b|f)?',
        re.IGNORECASE,
    )

    # Categories inferred from line item names
    _INCOME_KEYWORDS = {
        'gross potential', 'apartment revenue', 'rental income', 'rent revenue',
        'other income', 'utility income', 'tif revenue', 'tif funding',
        'total revenue', 'total income', 'effective gross income',
        'parking income', 'laundry income', 'misc income', 'noi with tif',
    }
    _EXPENSE_KEYWORDS = {
        'payroll', 'advertising', 'marketing', 'administrative', 'repairs',
        'maintenance', 'contract services', 'turnover', 'insurance',
        'property tax', 'real estate tax', 'utilities', 'management fee',
        'total expenses', 'total operating', 'controllable', 'non-controllable',
        'common area', 'landscaping', 'pest control', 'trash',
    }
    _SUBTOTAL_KEYWORDS = {
        'total revenue', 'total income', 'total expenses', 'total operating',
        'net operating income', 'net cash flow', 'effective gross',
        'total controllable', 'total non-controllable',
        # Note: 'apartment revenues' removed — substring matches "Gross Potential
        # Apartment Revenues" (not a subtotal). Exact match handled by _SUBTOTAL_PATTERN.
        # Note: 'noi' removed — too short, false positives on other strings.
    }
    # Below-the-line items that should NOT be categorized as income/expense
    _BELOW_LINE_KEYWORDS = {
        'debt service', 'mortgage', 'loan payment', 'principal',
        'interest expense', 'cash flow after', 'net cash flow',
        'replacement reserve', 'capital reserve', 'reserve deposit',
        'distributions', 'partner distribution', 'return on equity',
        'cash available', 'surplus cash', 'operating cash flow',
    }
    # Non-operating items that appear in CRE spreadsheets but should be
    # excluded from income/expense extraction entirely
    _SKIP_KEYWORDS = {
        'sale price', 'implied equity', 'exit cap', 'cap rate',
        'reversion', 'terminal value', 'irr', 'equity multiple',
        'loan balance', 'loan proceeds', 'refinance', 'payoff',
        'appraised value', 'net proceeds', 'gain on sale',
        'present value', 'discount rate', 'yield',
        # Renovation/scenario items — not operating income/expense
        'rent premium', 'unit upgrade', 'no unit upgrade',
        'capital improvement', 'non-operating',
        'economic occupancy',  # ratio, not a dollar amount
    }
    # CRE expense category headers — these short labels are section headers
    # (subtotals of the detail items below them), NOT actual line items.
    # The real items have more specific names like "REAL ESTATE" instead of
    # "TAXES", or "WATER/SEWER" instead of "UTILITIES".
    _EXPENSE_CATEGORY_HEADERS = {
        'taxes', 'utilities', 'payroll', 'insurance', 'maintenance',
        'marketing', 'administrative', 'management fees', 'contract services',
        'grounds', 'turnover', 'security', 'apartment turnover',
        'grounds / contract services', 'grounds/contract services',
        'controllable opex', 'non-controllable opex',
        'controllable expenses', 'non-controllable expenses',
        'controllable', 'non-controllable',
        'non-operating expenses', 'capital expenses',
        'expense ratio', 'operating expenses',
    }
    # Additional subtotal patterns — any line matching these is a subtotal
    _SUBTOTAL_PATTERN = re.compile(
        r'^total\s|'                      # starts with "TOTAL "
        r'\btotal\b.*\b(?:income|revenue|expense|rent|parking|laundry|'
        r'corporate|cam|reimbursement|other|controllable|operating)\b|'
        r'^effective gross|'              # EGI
        r'^net rental income|'
        r'^net operating|'
        r'^operating income|'             # "Operating Income Including TIF"
        # Note: "TOTAL GROSS POTENTIAL RENT" is caught by ^total\s above.
        # Do NOT match "Gross Potential Apartment Revenues" — it's the base GPR, not a subtotal.
        r'^gross potential\s.*rent$|'    # "Gross Potential Rent" (exact, no "Revenues")
        r'^other income$|'                # Category header "OTHER INCOME" (exact)
        r'^income adjustments$|'          # Adjustment subtotal
        r'^apartment revenues$|'          # Subtotal: GPR - offsets
        r'^total net rental|'
        r'\bsubtotal\b|'
        r'\bgrand total\b|'
        r'\bnoi\b',                       # "NOI" as whole word
        re.IGNORECASE,
    )

    def _extract_columnar_financials(self, doc_id: int,
                                      doc_record: Dict) -> List[Dict]:
        """
        Extract financial data from year-column table layouts common in
        CRE operating spreadsheets.

        Scans stored raw tables for rows that look like:
          [line_item, amount_2020, amount_2021, amount_2022, ...]
        with a header row identifying the year for each column.

        Deduplicates across sheets: when multiple sheets in the same
        workbook contain the same (line_item, period, amount) triple,
        only one copy is kept.
        """
        table_rows = self.db.get_document_tables(doc_id)
        if not table_rows:
            return []

        results = []

        # Process tables sheet by sheet
        pages = {}
        for tr in table_rows:
            pn = tr['page_number']
            pages.setdefault(pn, []).append(tr)

        for page_num, page_tables in pages.items():
            for tbl in page_tables:
                headers = tbl.get('headers') or []
                raw_rows = tbl.get('rows_json') or []
                if isinstance(headers, str):
                    import json
                    headers = json.loads(headers)
                if isinstance(raw_rows, str):
                    import json
                    raw_rows = json.loads(raw_rows)

                all_rows = [headers] + raw_rows
                page_results = self._parse_year_column_table(
                    all_rows, page_num
                )
                results.extend(page_results)

        # ── Sheet selection + deduplication ──
        # CRE workbooks repeat the same data across Summary, Detail,
        # Overview, and Export sheets at different granularity levels.
        # Strategy: for each period, pick the sheet with the most
        # non-subtotal detail line items (the most granular view),
        # then deduplicate within that selection.
        if results:
            before = len(results)

            # Step 1: For each period, find the best sheet (highest dollar coverage).
            # The most granular sheet will have the largest sum of absolute
            # non-subtotal amounts because it contains the actual revenue/expense
            # line items rather than just category subtotals.
            from collections import defaultdict
            period_page_dollars = defaultdict(lambda: defaultdict(float))
            period_page_counts = defaultdict(lambda: defaultdict(int))
            for r in results:
                if not r['is_subtotal'] and r['category'] in ('income', 'expense'):
                    period_page_dollars[r['period']][r['page_number']] += abs(r['amount'] or 0)
                    period_page_counts[r['period']][r['page_number']] += 1

            # For each period, pick the page with the highest dollar coverage.
            # Tie-break on item count (more items = more granular).
            best_page_for_period = {}
            for period, page_dollars in period_page_dollars.items():
                if page_dollars:
                    best_page = max(
                        page_dollars,
                        key=lambda p: (page_dollars[p], period_page_counts[period][p])
                    )
                    best_page_for_period[period] = best_page

            # Step 2: Filter — keep only items from the best sheet per period.
            # For periods with no clear best sheet (e.g., only subtotals),
            # keep all items and rely on triple-dedup below.
            filtered = []
            for r in results:
                period = r['period']
                if period in best_page_for_period:
                    if r['page_number'] == best_page_for_period[period]:
                        filtered.append(r)
                else:
                    filtered.append(r)  # No best sheet — keep it
            results = filtered

            # Step 3: Triple-dedup within the filtered set for any remaining dupes
            seen = set()
            deduped = []
            for r in results:
                key = (r['line_item'].strip().lower(), r['period'],
                       round(r['amount'], 2) if r['amount'] else None)
                if key not in seen:
                    seen.add(key)
                    deduped.append(r)
            results = deduped

            logger.info(
                f"Columnar parser: {before} raw → {len(filtered)} after "
                f"sheet selection → {len(results)} after dedup "
                f"({doc_record['filename']})"
            )

        return results

    # Words that indicate a column is a variance/delta, not an absolute amount
    _VARIANCE_KEYWORDS = re.compile(
        r'variance|var\.?\s|change|delta|diff|vs\.?\s|versus|cagr|'
        r'yoy|y-o-y|growth|incr|decr|%\s*inc|%\s*dec|'
        r'\bvs\b|\bchg\b|'
        r'20\d{2}\s*[-–]\s*20\d{2}',   # "2026-2025" variance columns
        re.IGNORECASE,
    )

    def _parse_year_column_table(self, all_rows: List[List[str]],
                                  page_number: int) -> List[Dict]:
        """
        Parse a table with year-column layout.

        Detects the header row (containing year patterns), maps columns to
        periods, then extracts line items with amounts for each period.
        Skips variance/delta columns (identified by keywords like
        'Variance', 'Change', 'vs', 'CAGR', etc.).
        """
        # Step 1: Find the header row containing year labels
        year_columns = {}  # col_index -> period string (e.g., "2023A")
        header_row_idx = None

        for row_idx, row in enumerate(all_rows):
            year_cols_found = {}
            for col_idx, cell in enumerate(row):
                cell_str = str(cell).strip()
                if not cell_str:
                    continue

                # Skip variance/delta columns
                if self._VARIANCE_KEYWORDS.search(cell_str):
                    continue

                # Also check the row above for variance indicators
                if row_idx > 0:
                    above_row = all_rows[row_idx - 1]
                    if col_idx < len(above_row):
                        above_str = str(above_row[col_idx] or '').strip()
                        if above_str and self._VARIANCE_KEYWORDS.search(above_str):
                            continue

                match = self._YEAR_PATTERN.search(cell_str)
                if match:
                    year = match.group(1)
                    suffix = (match.group(2) or '').strip().lower()

                    # If no suffix on the year itself, check the row above
                    # for type indicators (Actual/Budget/Forecast/Projected)
                    if not suffix and row_idx > 0:
                        above_row = all_rows[row_idx - 1]
                        if col_idx < len(above_row):
                            above_str = str(above_row[col_idx] or '').strip().lower()
                            if above_str in ('budget', 'budgeted'):
                                suffix = 'budget'
                            elif above_str in ('forecast', 'reforecast', 'projected'):
                                suffix = 'forecast'
                            elif above_str in ('proforma', 'pro forma'):
                                suffix = 'proforma'
                            # 'actual'/'actuals' → leave as '' (defaults to A)

                    # Normalize suffix — bare year treated as Actual
                    if suffix in ('actual', 'actuals', 'a', ''):
                        period = f"{year}A"
                    elif suffix in ('budget', 'budgeted', 'b'):
                        period = f"{year}B"
                    elif suffix in ('forecast', 'reforecast', 'projected', 'f'):
                        period = f"{year}F"
                    elif suffix in ('proforma', 'pro forma'):
                        period = f"{year}P"
                    else:
                        period = f"{year}A"

                    # Also skip if the same period was already found
                    # (second occurrence is likely a variance column)
                    if period not in year_cols_found.values():
                        year_cols_found[col_idx] = period

            # Need at least 2 year columns to be a valid header row
            if len(year_cols_found) >= 2:
                year_columns = year_cols_found
                header_row_idx = row_idx
                break

        if header_row_idx is None:
            return []

        # Step 2: Extract line items from rows below the header
        results = []
        current_category = 'unknown'
        below_the_line = False  # True once we pass NOI
        pending_below = False   # Deferred trigger — applies on next row
        past_total_opex = False  # True after TOTAL OPERATING EXPENSES — no resets

        for row_idx in range(header_row_idx + 1, len(all_rows)):
            # Apply deferred below-the-line trigger from previous row
            if pending_below:
                below_the_line = True
                pending_below = False
            row = all_rows[row_idx]
            if not row:
                continue

            # Find the line item name (first non-empty, non-numeric cell)
            line_item = ''
            for cell in row:
                if cell is None:
                    continue
                cell_str = str(cell).strip()
                if cell_str and not self._is_numeric_str(cell_str):
                    line_item = cell_str
                    break

            if not line_item:
                continue

            # Skip empty/separator rows
            line_lower = line_item.lower()
            if len(line_item) < 2 or line_lower in ('', '-', '—'):
                continue

            # Skip non-operating items (valuation, capital structure)
            if any(kw in line_lower for kw in self._SKIP_KEYWORDS):
                continue

            # Detect subtotals — use keyword set, regex pattern,
            # AND known expense category headers
            is_subtotal = (
                any(kw in line_lower for kw in self._SUBTOTAL_KEYWORDS)
                or bool(self._SUBTOTAL_PATTERN.search(line_item))
                or line_lower.strip() in self._EXPENSE_CATEGORY_HEADERS
            )

            # Detect below-the-line items (debt service, reserves, etc.)
            is_below_line = any(kw in line_lower for kw in self._BELOW_LINE_KEYWORDS)

            # Track when we pass the NOI line — everything after is below-the-line.
            # Use pending_below so the trigger line itself keeps its correct category.
            if 'noi' in line_lower or 'net operating' in line_lower:
                pending_below = True
            # Also trigger on "total operating expenses" — detail breakdowns
            # that follow are duplicates of the summary section above
            if is_subtotal and 'total operating' in line_lower and 'expense' in line_lower:
                pending_below = True
                past_total_opex = True

            if below_the_line:
                # Once below the line, stay there for data rows.
                # Only no-amount section headers (handled below) can reset this.
                current_category = 'debt_service'
            elif is_below_line:
                current_category = 'debt_service'
            elif any(kw in line_lower for kw in self._INCOME_KEYWORDS) \
                    or 'income' in line_lower or 'revenue' in line_lower:
                current_category = 'income'
            elif any(kw in line_lower for kw in self._EXPENSE_KEYWORDS) \
                    or 'expense' in line_lower:
                current_category = 'expense'
            elif 'noi' in line_lower or 'net operating' in line_lower:
                current_category = 'noi'

            # Extract amounts for each year column
            has_any_amount = False
            for col_idx, period in year_columns.items():
                if col_idx >= len(row):
                    continue
                amount = self._safe_float(row[col_idx])
                if amount is not None:
                    has_any_amount = True
                    results.append({
                        'category': current_category,
                        'line_item': line_item,
                        'period': period,
                        'amount': amount,
                        'is_subtotal': is_subtotal,
                        'is_total': is_subtotal and ('total' in line_lower or 'noi' in line_lower),
                        'page_number': page_number,
                    })

            # If this row had no numeric amounts, it might be a section header.
            # But NOT if we've already passed TOTAL OPERATING EXPENSES —
            # detail breakdowns that follow are duplicates, not new sections.
            if not has_any_amount and len(line_item) > 3 and not past_total_opex:
                if 'income' in line_lower or 'revenue' in line_lower:
                    current_category = 'income'
                    below_the_line = False
                elif 'expense' in line_lower or 'operating' in line_lower:
                    current_category = 'expense'
                    below_the_line = False
                elif any(kw in line_lower for kw in self._BELOW_LINE_KEYWORDS):
                    current_category = 'debt_service'

        return results

    @staticmethod
    def _is_numeric_str(s: str) -> bool:
        """Check if a string looks like a number (including formatted)."""
        cleaned = s.replace(',', '').replace('$', '').replace('-', '').replace('(', '').replace(')', '').strip()
        if not cleaned:
            return False
        try:
            float(cleaned)
            return True
        except ValueError:
            return False

    def _analyze_with_engine(self, doc_id: int, doc: DocumentContent,
                              template) -> Dict[str, int]:
        """Run the full extraction engine for lease/loan/closing docs."""
        # Clear existing extracted data for this document
        self.db.conn.execute("DELETE FROM financial_terms WHERE document_id = ?", (doc_id,))
        self.db.conn.execute("DELETE FROM clauses WHERE document_id = ?", (doc_id,))

        # Run extraction
        extraction = self.engine.extract(doc, template)

        counts = {'terms': 0, 'clauses': 0}

        # Store financial terms
        for term in extraction.get('financial_terms', []):
            try:
                self.db.insert_financial_term(
                    document_id=doc_id,
                    term_type=term.get('term_type', 'unknown'),
                    value_raw=term.get('value_raw'),
                    value_numeric=self._safe_float(term.get('value_numeric')),
                    value_unit=term.get('value_unit'),
                    term_label=term.get('term_label'),
                    effective_date=term.get('effective_date'),
                    expiration_date=term.get('expiration_date'),
                    escalation_type=term.get('escalation_type'),
                    escalation_detail=term.get('escalation_detail'),
                    section_ref=term.get('section_ref'),
                    page_number=term.get('page_number'),
                    confidence=self._safe_float(term.get('confidence')),
                )
                counts['terms'] += 1
            except Exception as e:
                logger.warning(f"Failed to store financial term: {e}")

        # Store clauses
        for clause in extraction.get('clauses', []):
            try:
                self.db.insert_clause(
                    document_id=doc_id,
                    clause_type=clause.get('clause_type', 'unknown'),
                    full_text=clause.get('full_text', ''),
                    section_ref=clause.get('section_ref'),
                    clause_title=clause.get('clause_title'),
                    summary=clause.get('summary'),
                    page_number=clause.get('page_number'),
                    confidence=self._safe_float(clause.get('confidence')),
                )
                counts['clauses'] += 1
            except Exception as e:
                logger.warning(f"Failed to store clause: {e}")

        # Store tabular data if extraction found any
        for row in extraction.get('tabular_data', []):
            try:
                if template.document_type == 'general_ledger':
                    self.db.insert_gl_entry(
                        document_id=doc_id,
                        account_code=row.get('account_code'),
                        account_name=row.get('account_name'),
                        entry_date=row.get('entry_date'),
                        description=row.get('description'),
                        debit=self._safe_float(row.get('debit')),
                        credit=self._safe_float(row.get('credit')),
                        balance=self._safe_float(row.get('balance')),
                        period=row.get('period'),
                        page_number=row.get('page_number'),
                    )
            except Exception as e:
                logger.warning(f"Failed to store tabular row: {e}")

        logger.info(f"Engine: {counts['terms']} terms, {counts['clauses']} clauses")
        return counts

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """Safely convert a value to float."""
        if value is None:
            return None
        try:
            if isinstance(value, str):
                value = value.replace(',', '').replace('$', '').strip()
            return float(value)
        except (ValueError, TypeError):
            return None
