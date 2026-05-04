"""
Extraction Engine for Real Estate Document Extractor.

Orchestrates the three extraction modes:
1. LEGAL — Clause extraction preserving full legal language
2. FINANCIAL — Structured key-value financial term extraction
3. TABULAR — Row/column data from rent rolls, operating statements, GL

Uses local LLM (via Ollama) for intelligent extraction.
Falls back to rule-based extraction when LLM is unavailable.
"""

import json
import re
import logging
from typing import List, Dict, Optional, Any, Tuple

from ..pdf_ingestion import DocumentContent, PageContent
from ..templates.document_templates import (
    DocumentTemplate, ExtractionMode, FieldDefinition, get_template
)
from .llm_client import LocalLLMClient

logger = logging.getLogger(__name__)


class ExtractionEngine:
    """Main extraction engine that routes documents through the appropriate pipeline."""

    def __init__(self, llm_client: Optional[LocalLLMClient] = None):
        self.llm = llm_client or LocalLLMClient()
        self._llm_available = None

    @property
    def llm_available(self) -> bool:
        if self._llm_available is None:
            self._llm_available = self.llm.is_available()
            if not self._llm_available:
                logger.warning(
                    "Local LLM not available. Falling back to rule-based extraction. "
                    "For best results, install Ollama and pull a model: "
                    "'ollama pull llama3.1:8b'"
                )
        return self._llm_available

    def extract(self, doc: DocumentContent, template: DocumentTemplate) -> Dict[str, Any]:
        """
        Run extraction on a document using the specified template.

        Returns a dict with keys:
        - financial_terms: list of extracted financial terms
        - clauses: list of extracted legal clauses
        - tabular_data: list of extracted row data
        - metadata: extraction metadata (mode used, confidence, etc.)
        """
        results = {
            "financial_terms": [],
            "clauses": [],
            "tabular_data": [],
            "metadata": {
                "document_type": template.document_type,
                "extraction_modes": [m.value for m in template.extraction_modes],
                "used_llm": self.llm_available,
                "page_count": doc.page_count,
            }
        }

        for mode in template.extraction_modes:
            if mode == ExtractionMode.DUAL:
                # Run both legal and financial extraction
                results["financial_terms"] = self._extract_financial(doc, template)
                results["clauses"] = self._extract_legal(doc, template)
            elif mode == ExtractionMode.LEGAL:
                results["clauses"] = self._extract_legal(doc, template)
            elif mode == ExtractionMode.FINANCIAL:
                results["financial_terms"] = self._extract_financial(doc, template)
            elif mode == ExtractionMode.TABULAR:
                results["tabular_data"] = self._extract_tabular(doc, template)

        return results

    # ─── Financial Term Extraction ───────────────────────────────────

    def _extract_financial(self, doc: DocumentContent,
                           template: DocumentTemplate) -> List[Dict]:
        """Extract structured financial terms from document."""
        if self.llm_available and template.llm_extraction_prompt:
            return self._extract_financial_llm(doc, template)
        else:
            return self._extract_financial_rules(doc, template)

    def _extract_financial_llm(self, doc: DocumentContent,
                                template: DocumentTemplate) -> List[Dict]:
        """Use local LLM for financial term extraction."""
        field_list = "\n".join(
            f"- {f.name}: {f.description} (type: {f.field_type})"
            + (f" [aliases: {', '.join(f.aliases)}]" if f.aliases else "")
            for f in template.financial_fields
        )

        all_terms = []
        text = doc.full_text

        # Chunk if needed (long documents)
        chunks = self.llm.chunk_text(text, max_chars=6000)

        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)} for financial terms")

            prompt = template.llm_extraction_prompt.format(
                field_list=field_list,
                document_text=chunk
            )

            result = self.llm.generate_structured(
                prompt, template.llm_system_prompt
            )

            if result:
                if isinstance(result, list):
                    all_terms.extend(result)
                elif isinstance(result, dict):
                    # Some models wrap in a container
                    terms = result.get('terms', result.get('financial_terms', [result]))
                    if isinstance(terms, list):
                        all_terms.extend(terms)

        # Deduplicate by term_type (keep highest confidence)
        return self._deduplicate_terms(all_terms)

    def _extract_financial_rules(self, doc: DocumentContent,
                                  template: DocumentTemplate) -> List[Dict]:
        """Rule-based fallback for financial term extraction."""
        terms = []
        text = doc.full_text

        for field_def in template.financial_fields:
            # Build search patterns from field name and aliases
            search_terms = [field_def.name.replace('_', ' ')] + field_def.aliases

            for search_term in search_terms:
                # Look for patterns like "Term: Value" or "Term ... $X"
                patterns = [
                    rf"(?i){re.escape(search_term)}[:\s]+([^\n]+)",
                    rf"(?i){re.escape(search_term)}[.\s]*?\$\s*([\d,]+\.?\d*)",
                    rf"(?i){re.escape(search_term)}[.\s]*?(\d{{1,2}}/\d{{1,2}}/\d{{2,4}})",
                ]

                for pattern in patterns:
                    matches = re.finditer(pattern, text)
                    for match in matches:
                        value_raw = match.group(1).strip()
                        value_numeric = self._parse_numeric(value_raw)

                        terms.append({
                            "term_type": field_def.name,
                            "term_label": search_term,
                            "value_raw": value_raw,
                            "value_numeric": value_numeric,
                            "confidence": 0.5,  # lower confidence for rule-based
                        })
                        break  # take first match per search term
                else:
                    continue
                break  # found a match for this field

        return terms

    # ─── Legal Clause Extraction ─────────────────────────────────────

    def _extract_legal(self, doc: DocumentContent,
                       template: DocumentTemplate) -> List[Dict]:
        """Extract legal clauses preserving full language."""
        if self.llm_available and template.llm_clause_prompt:
            return self._extract_legal_llm(doc, template)
        else:
            return self._extract_legal_rules(doc, template)

    def _extract_legal_llm(self, doc: DocumentContent,
                            template: DocumentTemplate) -> List[Dict]:
        """Use local LLM for legal clause extraction."""
        clause_list = "\n".join(
            f"- {ct.replace('_', ' ').title()}"
            for ct in template.clause_types
        )

        all_clauses = []
        text = doc.full_text
        chunks = self.llm.chunk_text(text, max_chars=6000)

        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)} for legal clauses")

            prompt = template.llm_clause_prompt.format(
                clause_list=clause_list,
                document_text=chunk
            )

            result = self.llm.generate_structured(
                prompt, template.llm_system_prompt
            )

            if result:
                if isinstance(result, list):
                    all_clauses.extend(result)
                elif isinstance(result, dict):
                    clauses = result.get('clauses', result.get('legal_clauses', [result]))
                    if isinstance(clauses, list):
                        all_clauses.extend(clauses)

        return self._deduplicate_clauses(all_clauses)

    def _extract_legal_rules(self, doc: DocumentContent,
                              template: DocumentTemplate) -> List[Dict]:
        """Rule-based fallback for clause extraction using section headers."""
        clauses = []
        text = doc.full_text

        # Common section header patterns
        section_pattern = re.compile(
            r'(?:ARTICLE|SECTION|Article|Section)\s+[\dIVXivx]+[.\s]*'
            r'[-–—]?\s*([A-Z][^\n]+)',
            re.MULTILINE
        )

        sections = list(section_pattern.finditer(text))

        for i, match in enumerate(sections):
            title = match.group(1).strip().rstrip('.')
            start = match.start()
            end = sections[i+1].start() if i+1 < len(sections) else min(start + 3000, len(text))
            section_text = text[start:end].strip()

            # Try to match to known clause types
            title_lower = title.lower()
            for clause_type in template.clause_types:
                clause_words = clause_type.replace('_', ' ').split()
                if any(word in title_lower for word in clause_words):
                    clauses.append({
                        "clause_type": clause_type,
                        "section_ref": match.group(0).split('\n')[0].strip(),
                        "clause_title": title,
                        "full_text": section_text,
                        "summary": None,
                        "confidence": 0.4,
                    })
                    break

        return clauses

    # ─── Tabular Data Extraction ─────────────────────────────────────

    def _extract_tabular(self, doc: DocumentContent,
                          template: DocumentTemplate) -> List[Dict]:
        """Extract tabular data (rent rolls, operating statements, GL)."""
        all_rows = []

        # First try: use tables extracted by pdfplumber
        for page in doc.pages:
            for table in page.tables:
                if len(table) < 2:
                    continue  # need at least header + 1 data row

                mapped = self._map_table_columns(table, template)
                if mapped:
                    for row in mapped:
                        row['page_number'] = page.page_number
                    all_rows.extend(mapped)

        # If no tables found via pdfplumber, try LLM
        if not all_rows and self.llm_available:
            all_rows = self._extract_tabular_llm(doc, template)

        return all_rows

    def _map_table_columns(self, table: List[List[str]],
                            template: DocumentTemplate) -> List[Dict]:
        """
        Map table columns to template fields using header matching.
        """
        if not table or len(table) < 2:
            return []

        headers = [str(h).strip().lower() for h in table[0]]

        # Build mapping: column index -> field name
        col_map = {}
        for col_idx, header in enumerate(headers):
            if not header:
                continue
            for field_def in template.table_columns:
                # Check exact match or alias match
                check_names = [field_def.name.replace('_', ' ')] + \
                              [a.lower() for a in field_def.aliases]
                if header in check_names or any(alias in header for alias in check_names):
                    col_map[col_idx] = field_def.name
                    break

        if not col_map:
            return []

        # Extract data rows
        rows = []
        for row_data in table[1:]:
            row = {}
            for col_idx, field_name in col_map.items():
                if col_idx < len(row_data):
                    value = str(row_data[col_idx]).strip()
                    if value and value.lower() not in ('', 'none', 'n/a', '-'):
                        row[field_name] = value
            if row:  # only add non-empty rows
                rows.append(row)

        return rows

    def _extract_tabular_llm(self, doc: DocumentContent,
                              template: DocumentTemplate) -> List[Dict]:
        """Use LLM to parse tabular data when pdfplumber can't find tables."""
        field_list = "\n".join(
            f"- {f.name}: {f.description}"
            + (f" [aliases: {', '.join(f.aliases)}]" if f.aliases else "")
            for f in template.table_columns
        )

        all_rows = []
        text = doc.full_text
        chunks = self.llm.chunk_text(text, max_chars=6000)

        for i, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)} for tabular data")

            prompt = template.llm_extraction_prompt.format(
                field_list=field_list,
                document_text=chunk
            )

            result = self.llm.generate_structured(
                prompt, template.llm_system_prompt
            )

            if result:
                if isinstance(result, list):
                    all_rows.extend(result)
                elif isinstance(result, dict):
                    rows = result.get('rows', result.get('entries', result.get('data', [])))
                    if isinstance(rows, list):
                        all_rows.extend(rows)

        return all_rows

    # ─── Helpers ─────────────────────────────────────────────────────

    def _deduplicate_terms(self, terms: List[Dict]) -> List[Dict]:
        """Deduplicate financial terms, keeping highest confidence."""
        seen = {}
        for term in terms:
            key = term.get('term_type', '')
            conf = term.get('confidence', 0) or 0
            if key not in seen or conf > (seen[key].get('confidence', 0) or 0):
                seen[key] = term
        return list(seen.values())

    def _deduplicate_clauses(self, clauses: List[Dict]) -> List[Dict]:
        """Deduplicate clauses, keeping longest text per type."""
        seen = {}
        for clause in clauses:
            key = clause.get('clause_type', '')
            text_len = len(clause.get('full_text', ''))
            if key not in seen or text_len > len(seen[key].get('full_text', '')):
                seen[key] = clause
        return list(seen.values())

    @staticmethod
    def _parse_numeric(value: str) -> Optional[float]:
        """Try to parse a numeric value from a string."""
        if not value:
            return None
        # Remove currency symbols, commas, spaces
        cleaned = re.sub(r'[$,\s]', '', value)
        # Handle percentage
        cleaned = cleaned.rstrip('%')
        try:
            return float(cleaned)
        except ValueError:
            return None


class DocumentClassifier:
    """Classify document type using content analysis."""

    # Keywords strongly associated with each document type
    TYPE_KEYWORDS = {
        "lease": [
            "lease agreement", "landlord", "tenant", "lessee", "lessor",
            "rent", "leased premises", "term of lease", "base rent",
            "common area", "cam", "security deposit"
        ],
        "loan": [
            "promissory note", "loan agreement", "borrower", "lender",
            "principal", "interest rate", "maturity", "mortgage",
            "amortization", "debt service", "collateral"
        ],
        "closing": [
            "purchase and sale", "closing statement", "settlement",
            "buyer", "seller", "purchase price", "earnest money",
            "title insurance", "closing costs", "settlement statement"
        ],
        "guarantee": [
            "guarantee", "guaranty", "guarantor", "guaranteed obligations",
            "unconditional", "irrevocable", "net worth", "personal guarantee"
        ],
        "rent_roll": [
            "rent roll", "unit", "tenant", "occupied", "vacant",
            "monthly rent", "lease expiration", "square feet"
        ],
        "operating_statement": [
            "operating statement", "income and expense", "revenue",
            "operating expenses", "net operating income", "noi",
            "property tax", "insurance", "maintenance", "utilities"
        ],
        "general_ledger": [
            "general ledger", "gl detail", "account code", "debit",
            "credit", "journal entry", "chart of accounts", "posting"
        ],
    }

    def __init__(self, llm_client: Optional[LocalLLMClient] = None):
        self.llm = llm_client

    def classify(self, doc: DocumentContent) -> Tuple[str, float]:
        """
        Classify document type based on content analysis.

        Returns (document_type, confidence) tuple.
        """
        text_lower = doc.full_text[:5000].lower()  # check first ~5000 chars

        scores = {}
        for doc_type, keywords in self.TYPE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            # Normalize by keyword count
            scores[doc_type] = score / len(keywords)

        if not scores:
            return "unknown", 0.0

        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        # If score is very low, use LLM for classification
        if best_score < 0.15 and self.llm and self.llm.is_available():
            return self._classify_llm(doc)

        return best_type, min(best_score * 3, 1.0)  # scale up confidence

    def _classify_llm(self, doc: DocumentContent) -> Tuple[str, float]:
        """Use LLM to classify ambiguous documents."""
        prompt = f"""Classify this real estate document into one of these types:
- lease: Lease agreement
- loan: Loan document / promissory note / mortgage
- closing: Purchase/closing document
- guarantee: Guarantee agreement
- rent_roll: Rent roll
- operating_statement: Operating statement / income & expense
- general_ledger: General ledger detail

Return JSON: {{"document_type": "<type>", "confidence": <0-1>}}

First 2000 characters of the document:
{doc.full_text[:2000]}"""

        result = self.llm.generate_structured(prompt)
        if result and isinstance(result, dict):
            return (
                result.get('document_type', 'unknown'),
                result.get('confidence', 0.5)
            )

        return "unknown", 0.0
