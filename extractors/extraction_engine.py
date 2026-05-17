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
    DocumentTemplate, ExtractionMode, FieldDefinition, FieldPriority, get_template
)
from .llm_client import LocalLLMClient

logger = logging.getLogger(__name__)


class ExtractionEngine:
    """Main extraction engine that routes documents through the appropriate pipeline."""

    LLM_CHECK_TTL = 60  # re-check LLM availability every 60 seconds

    def __init__(self, llm_client: Optional[LocalLLMClient] = None):
        self.llm = llm_client or LocalLLMClient()
        self._llm_available = None
        self._llm_checked_at = 0  # epoch timestamp of last check

    @property
    def llm_available(self) -> bool:
        import time
        now = time.time()
        if self._llm_available is None or (now - self._llm_checked_at) > self.LLM_CHECK_TTL:
            self._llm_available = self.llm.is_available()
            self._llm_checked_at = now
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
            # Rule-based + prose + inference (no LLM gap-fill)
            rule_terms = self._extract_financial_rules(doc, template)
            found_fields = {t['term_type'] for t in rule_terms}

            prose_terms = self._extract_prose_patterns(doc, template, found_fields, rule_terms)
            rule_terms.extend(prose_terms)

            return rule_terms

    def _extract_financial_llm(self, doc: DocumentContent,
                                template: DocumentTemplate) -> List[Dict]:
        """
        Use local LLM for financial term extraction.

        Pipeline:
        1. Rule-based pattern extraction (values, dates, entities)
        2. Prose-pattern extraction (regex patterns for text fields)
        3. LLM gap-fill for remaining CRITICAL/IMPORTANT fields only
        """
        # Step 1: Rule-based extraction for structured values
        rule_terms = self._extract_financial_rules(doc, template)
        found_fields = {t['term_type'] for t in rule_terms}

        # Step 2: Prose-pattern extraction for text fields
        prose_terms = self._extract_prose_patterns(doc, template, found_fields, rule_terms)
        found_fields.update(t['term_type'] for t in prose_terms)
        rule_terms.extend(prose_terms)

        all_fields = {f.name for f in template.financial_fields}
        missing_fields = all_fields - found_fields

        logger.info(
            f"Rule + prose extraction found {len(rule_terms)} terms. "
            f"Missing fields: {len(missing_fields)}"
        )

        if not missing_fields:
            return rule_terms

        # Step 3: LLM gap-fill — only for CRITICAL and IMPORTANT missing fields
        important_missing = [
            f for f in template.financial_fields
            if f.name in missing_fields
            and f.priority in (FieldPriority.CRITICAL, FieldPriority.IMPORTANT)
        ]

        if not important_missing:
            logger.info("Only OPTIONAL fields missing — skipping LLM call")
            return rule_terms

        missing_field_list = "\n".join(
            f"- {f.name}: {f.description}"
            for f in important_missing
        )

        # Use the LLM prompt from the template, or fall back to a focused prompt
        excerpt = self._clean_pdf_text(doc.full_text.replace('\n', ' '))[:2500]

        prompt = template.llm_extraction_prompt.format(
            field_list=missing_field_list,
            document_text=excerpt
        ) if '{field_list}' in template.llm_extraction_prompt else (
            f"Extract ONLY these terms from the document. "
            f"Return JSON array with keys: term_type, value_raw, value_numeric, confidence.\n"
            f"If a field is NOT in the document, set value_raw to null.\n\n"
            f"Fields:\n{missing_field_list}\n\nDocument:\n{excerpt}"
        )

        logger.info(f"LLM extraction for {len(important_missing)} missing important fields...")
        result = self.llm.generate_structured(
            prompt, template.llm_system_prompt
        )

        llm_terms = []
        if result:
            if isinstance(result, list):
                llm_terms = result
            elif isinstance(result, dict):
                llm_terms = result.get('terms', result.get('financial_terms', [result]))
                if not isinstance(llm_terms, list):
                    llm_terms = [llm_terms]

        # Validate LLM results — filter out garbage
        field_descriptions = {f.name: f.description.lower() for f in template.financial_fields}
        validated_llm = []
        for t in llm_terms:
            if not isinstance(t, dict):
                continue
            term_type = t.get('term_type', '')
            value_raw = t.get('value_raw')

            # Skip if not a valid field
            if term_type not in missing_fields:
                continue
            # Skip if value is null/empty
            if not value_raw or str(value_raw).strip().lower() in ('null', 'none', 'n/a', 'not found', 'not_found', ''):
                continue
            # Skip if value is just the field description echoed back
            val_lower = str(value_raw).lower().strip()
            desc = field_descriptions.get(term_type, '')
            if val_lower == desc or val_lower in desc or desc in val_lower:
                logger.warning(f"LLM echoed description for {term_type}: '{value_raw}' — skipping")
                continue
            # Skip suspiciously low confidence
            if (t.get('confidence') or 0) < 0.2:
                continue

            validated_llm.append(t)

        # Merge: rules take priority, LLM fills gaps
        all_terms = rule_terms + validated_llm
        return self._deduplicate_terms(all_terms)

    def _extract_prose_patterns(self, doc: DocumentContent,
                                 template: DocumentTemplate,
                                 already_found: set,
                                 rule_terms: List[Dict] = None) -> List[Dict]:
        """
        Extract text fields using prose_patterns defined on FieldDefinitions.

        These catch things like "fixed rate", "non-recourse", "NNN lease"
        that appear in running text rather than as labeled values.
        """
        terms = []
        text = self._clean_pdf_text(doc.full_text.replace('\n', ' '))

        for field_def in template.financial_fields:
            if field_def.name in already_found:
                continue
            if not field_def.prose_patterns:
                continue

            for pattern in field_def.prose_patterns:
                m = re.search(pattern, text)
                if m:
                    # Use the matched group (first capture group, or full match)
                    value = m.group(1) if m.lastindex else m.group(0)
                    value = value.strip()
                    terms.append({
                        "term_type": field_def.name,
                        "term_label": field_def.name.replace('_', ' '),
                        "value_raw": value,
                        "confidence": 0.80,
                    })
                    break  # first match wins

        # Inference-based extraction for fields that can be deduced
        terms.extend(self._infer_fields(doc, template, already_found, terms, rule_terms or []))

        return terms

    def _infer_fields(self, doc: DocumentContent, template: DocumentTemplate,
                       already_found: set, prose_terms: List[Dict],
                       all_terms: List[Dict] = None) -> List[Dict]:
        """
        Infer field values that aren't explicitly stated but can be deduced.

        For example:
        - rate_type = "Fixed" if no variable-rate indicators appear
        - loan_term can be calculated from origination + maturity dates
        - default_rate from "Default Interest Rate" mentions
        - recourse = infer from document structure
        """
        inferred = []
        all_terms = all_terms or []
        found = already_found | {t['term_type'] for t in prose_terms}
        text_lower = doc.full_text.lower().replace('\n', ' ')

        # Rate type: if no SOFR/LIBOR/variable/adjustable/floating, it's fixed
        if 'rate_type' not in found:
            has_field = any(f.name == 'rate_type' for f in template.financial_fields)
            if has_field:
                variable_indicators = ['sofr', 'libor', 'prime rate', 'adjustable',
                                       'variable rate', 'floating rate', 'index rate']
                if not any(ind in text_lower for ind in variable_indicators):
                    inferred.append({
                        "term_type": "rate_type",
                        "term_label": "rate type",
                        "value_raw": "Fixed",
                        "confidence": 0.75,
                    })

        # Loan term: calculate from origination and maturity dates
        if 'loan_term' not in found:
            orig_term = next((t for t in all_terms if t['term_type'] == 'origination_date'), None)
            mat_term = next((t for t in all_terms if t['term_type'] == 'maturity_date'), None)
            if orig_term and mat_term:
                term_str = self._calculate_loan_term(
                    orig_term.get('value_raw', ''),
                    mat_term.get('value_raw', '')
                )
                if term_str:
                    inferred.append({
                        "term_type": "loan_term",
                        "term_label": "loan term",
                        "value_raw": term_str,
                        "confidence": 0.85,
                    })

        # Recourse: infer from document context
        if 'recourse' not in found:
            has_field = any(f.name == 'recourse' for f in template.financial_fields)
            if has_field:
                # Check for non-recourse indicators
                if re.search(r'(?i)non[- ]?recourse', text_lower):
                    inferred.append({
                        "term_type": "recourse",
                        "term_label": "recourse",
                        "value_raw": "Non-recourse",
                        "confidence": 0.80,
                    })
                # If there's a separate guaranty document referenced, likely recourse
                elif re.search(r'(?i)guaranty|guarantor|personal\s*(?:liability|guarantee)', text_lower):
                    inferred.append({
                        "term_type": "recourse",
                        "term_label": "recourse",
                        "value_raw": "Recourse (guaranty referenced)",
                        "confidence": 0.70,
                    })

        # Prepayment: if prose pattern matched something generic, try to improve it
        prepay_term = next((t for t in prose_terms if t['term_type'] == 'prepayment_terms'), None)
        if prepay_term and 'stated therein' in prepay_term.get('value_raw', '').lower():
            # The match is just a reference to the Note — replace with what we can find
            # Look for the actual prepayment structure mentioned
            m = re.search(
                r'(?i)(?:prepayment\s+(?:premium|penalty|fee))'
                r'[^.]*?'
                r'((?:yield\s*maintenance|defeasance|lockout|'
                r'(?:\d+%?\s*(?:of|the)\s*)?(?:outstanding|unpaid|principal)'
                r')[^.]{0,100})',
                text_lower
            )
            if m:
                prepay_term['value_raw'] = m.group(0).strip()[:150]
            else:
                # Check for lockout period or premium percentage
                if 'lockout' in text_lower:
                    prepay_term['value_raw'] = "Lockout period applies (see Note)"
                elif re.search(r'(?i)prepayment\s+premium', text_lower):
                    prepay_term['value_raw'] = "Prepayment premium required (per Note terms)"
                else:
                    prepay_term['value_raw'] = "Subject to prepayment premium per Note"
                prepay_term['confidence'] = 0.65

        # Default interest rate: look for the written-out rate near
        # "Default Interest Rate" definition
        if 'default_rate' not in found:
            has_field = any(f.name == 'default_rate' for f in template.financial_fields)
            if has_field:
                # Look for pattern: "rate of X percent ... Default Interest Rate"
                m = re.search(
                    r'rate\s+of\s+([\w\s]+?(?:and\s+\d+/\d+\s+)?percent)[^"]*?'
                    r'default\s+interest\s+rate',
                    text_lower
                )
                if m:
                    written = m.group(1).strip()
                    numeric = self._parse_written_number(
                        written.replace('percent', '').strip()
                    )
                    inferred.append({
                        "term_type": "default_rate",
                        "term_label": "default rate",
                        "value_raw": written,
                        "value_numeric": numeric,
                        "value_unit": "%",
                        "confidence": 0.80,
                    })

        # Late fee: look for late charge section and extract the terms
        if 'late_fee' not in found:
            has_field = any(f.name == 'late_fee' for f in template.financial_fields)
            if has_field:
                # Pattern: "late charge" followed by description in same sentence/section
                m = re.search(
                    r'late\s+charge[.\s]+\w+.{10,200}?'
                    r'((?:five|four|three|two|one|\d+)\s*(?:cents?|%|percent)[^.]{0,60})',
                    text_lower
                )
                if m:
                    inferred.append({
                        "term_type": "late_fee",
                        "term_label": "late fee",
                        "value_raw": m.group(1).strip(),
                        "confidence": 0.75,
                    })

        return inferred

    @staticmethod
    def _clean_pdf_text(text: str) -> str:
        """Clean common PDF extraction artifacts from pdfplumber output."""
        import re
        # Fix garbled ordinals BEFORE camelCase split: "iA day" → "1st day"
        text = re.sub(r'\biA\b', '1st', text)

        # Fix run-together words from bad PDF encoding: "camelCase" splits
        text = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text)

        # Fix common run-together prepositions: "ofthe", "onthe", "dayof",
        # "ofland", "ifnot", etc.
        text = re.sub(
            r'\b(of|and|or|in|to|by|at|on|the|for|is|as|be|if|day|not|due|all|sum)'
            r'(the|this|that|all|any|such|said|each|of|in|on|which|not|land|'
            r'April|May|June|July|August|September|October|November|December|'
            r'January|February|March)\b',
            r'\1 \2', text, flags=re.IGNORECASE
        )

        # Fix split ordinal suffixes: "2n d" → "2nd", "1s t" → "1st"
        text = re.sub(r'(\d)\s*s\s*t\b', r'\1st', text)
        text = re.sub(r'(\d)\s*n\s*d\b', r'\1nd', text)
        text = re.sub(r'(\d)\s*r\s*d\b', r'\1rd', text)
        text = re.sub(r'(\d)\s*t\s*h\b', r'\1th', text)

        return text

    # ── Written-out number words ──
    _WORD_NUMS = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
        'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
        'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40,
        'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
    }
    _WORD_MULTIPLIERS = {
        'hundred': 100, 'thousand': 1_000, 'million': 1_000_000,
        'billion': 1_000_000_000,
    }

    @classmethod
    def _parse_written_number(cls, text: str) -> Optional[float]:
        """Parse written-out numbers like 'THREE MILLION' or 'seven and 26/100'."""
        text = text.lower().strip()
        # Handle fractional part "and XX/100"
        fractional = 0.0
        frac_match = re.search(r'and\s+(\d+)/(\d+)', text)
        if frac_match:
            fractional = int(frac_match.group(1)) / int(frac_match.group(2))
            text = text[:frac_match.start()].strip()

        words = re.findall(r'[a-z]+', text)
        if not words:
            return fractional if fractional else None

        # Parse word-based numbers
        total = 0
        current = 0
        for word in words:
            if word in cls._WORD_NUMS:
                current += cls._WORD_NUMS[word]
            elif word in cls._WORD_MULTIPLIERS:
                mult = cls._WORD_MULTIPLIERS[word]
                if current == 0:
                    current = 1
                current *= mult
                if mult >= 1000:
                    total += current
                    current = 0
            # skip 'and', 'of', etc.

        total += current
        if total == 0 and fractional == 0:
            return None
        return total + fractional

    def _extract_financial_rules(self, doc: DocumentContent,
                                  template: DocumentTemplate) -> List[Dict]:
        """
        Rule-based financial term extraction.

        Strategy: VALUE-FIRST extraction — scan full text for value patterns
        (dollar amounts, percentages, dates, entities), then match to fields
        based on surrounding context keywords.

        Key improvement: parse WRITTEN-OUT amounts (e.g. 'THREE MILLION and
        00/100 Dollars') which are more reliable than garbled PDF numerals.
        """
        terms = []
        raw_text = doc.full_text
        # Flatten newlines for matching but keep raw for reference
        text = raw_text.replace('\n', ' ')
        text = self._clean_pdf_text(text)

        # ── Pass 1A: Written-out dollar amounts (most reliable) ──
        written_dollar_pattern = re.compile(
            r'(.{0,150}?)'
            r'((?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|'
            r'twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
            r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|'
            r'thousand|million|billion)[\s\-]+)+'
            r'(?:and\s+\d+/\d+\s+)?'
            r'(?:dollars|dollar))',
            re.IGNORECASE
        )
        dollar_matches = []
        for m in written_dollar_pattern.finditer(text):
            context_before = m.group(1).strip()
            written_amount = m.group(2).strip()
            numeric = self._parse_written_number(written_amount)
            if numeric and numeric > 0:
                dollar_matches.append({
                    'before': context_before,
                    'after': '',
                    'raw': written_amount.title(),
                    'numeric': numeric,
                    'full_context': f'{context_before} {written_amount}',
                    'source': 'written',
                })

        # ── Pass 1B: Numeric dollar amounts as fallback ──
        dollar_num_pattern = re.compile(
            r'(.{0,120}?)'
            r'\$\s*'
            r'([\d,.\s]{3,20})'
            r'(.{0,60})',
            re.DOTALL
        )
        for m in dollar_num_pattern.finditer(text):
            context_before = m.group(1).strip()
            raw_num = re.sub(r'[,.\s]+$', '', m.group(2)).strip()
            context_after = m.group(3).strip()
            # Try to parse - but these are often garbled
            cleaned = re.sub(r'[^\d.]', '', raw_num)
            # Handle garbled patterns like "3000,.00000" -> likely 3,000,000
            # Heuristic: if it looks like repeated zeros, try to interpret
            numeric = self._parse_garbled_dollar(raw_num)
            dollar_matches.append({
                'before': context_before,
                'after': context_after,
                'raw': f'${raw_num}',
                'numeric': numeric,
                'full_context': f'{context_before} ${raw_num} {context_after}',
                'source': 'numeric',
            })

        # ── Pass 2A: Written-out percentages (most reliable) ──
        written_pct_pattern = re.compile(
            r'(.{0,150}?)'
            r'((?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|'
            r'twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
            r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)[\s\-]+)*'
            r'(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|'
            r'twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
            r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred))'
            r'(?:\s+and\s+\d+/\d+)?'
            r')\s*(?:percent|per\s*cent)',
            re.IGNORECASE
        )
        pct_matches = []
        for m in written_pct_pattern.finditer(text):
            context = m.group(1).strip()
            raw_pct = m.group(2).strip()
            numeric = self._parse_written_number(raw_pct)
            if numeric is not None:
                pct_matches.append({
                    'context': context,
                    'raw': raw_pct,
                    'numeric': numeric,
                    'source': 'written',
                })

        # ── Pass 2B: Numeric percentages ──
        # Rate-related keywords — if these appear nearby, treat as an interest rate
        # (apply garble correction for values > 20%)
        rate_keywords = {'interest', 'rate', 'coupon', 'spread', 'margin', 'floor', 'cap', 'default'}

        num_pct_pattern = re.compile(
            r'(.{0,120}?)'
            r'([\d]+[.\s]*[\d/]*)\s*(?:percent|%|per\s*(?:cent|annum))',
            re.IGNORECASE
        )
        for m in num_pct_pattern.finditer(text):
            context = m.group(1).strip()
            raw_pct = m.group(2).strip()
            ctx_lower = context.lower()
            is_rate = any(kw in ctx_lower for kw in rate_keywords)
            numeric = self._parse_percentage(raw_pct, is_rate_field=is_rate)
            pct_matches.append({
                'context': context,
                'raw': raw_pct,
                'numeric': numeric,
                'source': 'numeric',
            })

        # ── Pass 3: Extract dates with context ──
        date_pattern = re.compile(
            r'(.{0,120}?)'
            r'(\d{1,2}(?:st|nd|rd|th)?\s+day\s+of\s+\w+,?\s*\d{4}'
            r'|(?:first|second|third|1st|2nd|3rd)\s+day\s+of\s+\w+,?\s*\d{4}'
            r'|\b(?:January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+\d{1,2},?\s*\d{4}'
            r'|\d{1,2}/\d{1,2}/\d{2,4})',
            re.IGNORECASE
        )
        date_matches = []
        for m in date_pattern.finditer(text):
            context = m.group(1).strip()
            raw_date = m.group(2).strip()
            date_matches.append({
                'context': context,
                'raw': raw_date,
            })

        # ── Pass 4: Extract named entities (company/partnership names) ──
        # More flexible pattern - handles newlines in names
        entity_pattern = re.compile(
            r'(.{0,100}?)'
            r'([A-Z][A-Z\s,.\-&\']{4,80}?'
            r'(?:COMPANY|CORPORATION|PARTNERSHIP|LLC|LP|INC|TRUST|BANK|L\.P\.))',
        )
        entity_matches = []
        for m in entity_pattern.finditer(text):
            context = m.group(1).strip()
            entity = re.sub(r'\s+', ' ', m.group(2)).strip().rstrip(',.')
            # Skip false positives that are too short or look like headers
            if len(entity) < 8:
                continue
            entity_matches.append({
                'context': context,
                'raw': entity,
            })

        # ── Now match extracted values to template fields ──
        # Sort dollar matches: prefer written-out amounts (more reliable)
        dollar_matches.sort(key=lambda x: 0 if x.get('source') == 'written' else 1)
        pct_matches.sort(key=lambda x: 0 if x.get('source') == 'written' else 1)

        for field_def in template.financial_fields:
            field_keywords = [field_def.name.replace('_', ' ')] + \
                             [a.lower() for a in field_def.aliases]

            # Currency fields
            if field_def.field_type == 'currency':
                best = self._find_best_dollar_match(field_def, dollar_matches, field_keywords)
                if best:
                    terms.append(best)

            # Number fields (like DSCR)
            elif field_def.field_type == 'number':
                # Try dollar matches first (some number fields have $ values)
                best = self._find_best_dollar_match(field_def, dollar_matches, field_keywords)
                if best:
                    terms.append(best)

            # Percentage fields
            elif field_def.field_type == 'percentage':
                best = self._find_best_pct_match(field_def, pct_matches, field_keywords)
                if best:
                    terms.append(best)

            # Date fields
            elif field_def.field_type == 'date':
                # Build extended keywords for date fields
                date_keywords = list(field_keywords)
                if field_def.name == 'maturity_date':
                    date_keywords.extend(['payable on', 'due and payable', 'final installment'])
                elif field_def.name == 'origination_date':
                    date_keywords.extend(['made this', 'executed', 'effective', 'dated as of', 'closing'])

                for dm in date_matches:
                    ctx = dm['context'].lower()
                    if any(kw in ctx for kw in date_keywords):
                        raw_date = dm['raw']
                        normalized = self._normalize_date(raw_date)
                        terms.append({
                            "term_type": field_def.name,
                            "term_label": field_def.name.replace('_', ' '),
                            "value_raw": normalized or raw_date,
                            "confidence": 0.85 if normalized else 0.75,
                        })
                        break

            # Text/entity fields (borrower, lender, etc.)
            elif field_def.field_type == 'text':
                best = self._find_best_entity_match(field_def, entity_matches, field_keywords, text)
                if best:
                    terms.append(best)

        return terms

    def _find_best_dollar_match(self, field_def, dollar_matches, field_keywords) -> Optional[Dict]:
        """Find the best dollar amount match for a field, preferring written amounts."""
        for dm in dollar_matches:
            ctx = dm['full_context'].lower()
            if any(kw in ctx for kw in field_keywords):
                conf = 0.90 if dm.get('source') == 'written' else 0.65
                return {
                    "term_type": field_def.name,
                    "term_label": field_def.name.replace('_', ' '),
                    "value_raw": dm['raw'],
                    "value_numeric": dm['numeric'],
                    "confidence": conf,
                }
        return None

    def _find_best_pct_match(self, field_def, pct_matches, field_keywords) -> Optional[Dict]:
        """Find the best percentage match for a field, preferring written amounts."""
        for pm in pct_matches:
            ctx = pm['context'].lower()
            if any(kw in ctx for kw in field_keywords):
                conf = 0.90 if pm.get('source') == 'written' else 0.65
                return {
                    "term_type": field_def.name,
                    "term_label": field_def.name.replace('_', ' '),
                    "value_raw": f"{pm['raw']}%" if '%' not in pm['raw'] else pm['raw'],
                    "value_numeric": pm['numeric'],
                    "value_unit": "%",
                    "confidence": conf,
                }
        return None

    def _find_best_entity_match(self, field_def, entity_matches, field_keywords, full_text) -> Optional[Dict]:
        """Find the best entity match for a field using contextual patterns."""
        text_lower = full_text.lower()

        # Special handling for borrower/lender — look for structural patterns
        if field_def.name == 'borrower':
            # In mortgages: "between X ... (hereinafter designated as Mortgagor)"
            m = re.search(
                r'between\s+([A-Z][A-Z\s,.\-&\']+?(?:PARTNERSHIP|COMPANY|CORPORATION|LLC|LP|INC|TRUST))',
                full_text[:3000]
            )
            if m:
                entity = re.sub(r'\s+', ' ', m.group(1)).strip()
                return {
                    "term_type": "borrower",
                    "term_label": "borrower",
                    "value_raw": entity,
                    "confidence": 0.95,
                }
            # Also try "Mortgagor" pattern
            m = re.search(
                r'([A-Z][A-Z\s,.\-&\']+?(?:PARTNERSHIP|COMPANY|CORPORATION|LLC|LP|INC|TRUST))'
                r'[^"]*?(?:Mortgagor|Borrower)',
                full_text[:3000]
            )
            if m:
                entity = re.sub(r'\s+', ' ', m.group(1)).strip()
                return {
                    "term_type": "borrower",
                    "term_label": "borrower",
                    "value_raw": entity,
                    "confidence": 0.90,
                }

        if field_def.name == 'lender':
            # In mortgages: second entity, "hereinafter designated as Mortgagee"
            m = re.search(
                r'([A-Z][A-Z\s,.\-&\']+?(?:COMPANY|CORPORATION|BANK|TRUST|INC|LLC))'
                r'[^"]*?(?:Mortgagee|Lender)',
                full_text[:3000]
            )
            if m:
                entity = re.sub(r'\s+', ' ', m.group(1)).strip()
                return {
                    "term_type": "lender",
                    "term_label": "lender",
                    "value_raw": entity,
                    "confidence": 0.95,
                }

        # For collateral/security — extract the legal property description
        if field_def.name in ('collateral', 'security'):
            # First try: legal description like "Lot X, Block Y..."
            legal_m = re.search(
                r'(Lot\s+\d+[^.]*?(?:Addition|Subdivision|Plat|Survey|Section)[^.]*\.)',
                full_text[:8000], re.DOTALL
            )
            if legal_m:
                desc = re.sub(r'\s+', ' ', legal_m.group(1)).strip()
                # Prepend county/state if we can find it
                loc_m = re.search(
                    r'(?:County\s+of\s*)?(\w+),\s*State\s+of\s*(\w+)',
                    full_text[:8000]
                )
                location = ''
                if loc_m:
                    location = f'{loc_m.group(1)} County, {loc_m.group(2)} — '
                return {
                    "term_type": field_def.name,
                    "term_label": field_def.name.replace('_', ' '),
                    "value_raw": f'{location}{desc}'[:250],
                    "confidence": 0.90,
                }

            # Fallback: "situated in County of X, State of Y"
            prop_m = re.search(
                r'(?:situated|located)\s+in\s+(?:the\s+)?'
                r'(?:County\s+of\s*)?'
                r'(.{10,200}?)(?:\.\s|\bTOGETHER\b)',
                full_text[:8000], re.DOTALL
            )
            if prop_m:
                return {
                    "term_type": field_def.name,
                    "term_label": field_def.name.replace('_', ' '),
                    "value_raw": re.sub(r'\s+', ' ', prop_m.group(0)).strip()[:250],
                    "confidence": 0.85,
                }

        # General entity matching via keywords in context
        for em in entity_matches:
            ctx = em['context'].lower()
            if any(kw in ctx for kw in field_keywords):
                return {
                    "term_type": field_def.name,
                    "term_label": field_def.name.replace('_', ' '),
                    "value_raw": em['raw'],
                    "confidence": 0.80,
                }
        return None

    # Month name lookup for date normalization
    _MONTH_NAMES = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
    }
    _ORDINAL_WORDS = {
        'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
        'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
    }

    @staticmethod
    def _calculate_loan_term(origination_raw: str, maturity_raw: str) -> Optional[str]:
        """Calculate loan term from origination and maturity dates."""
        from datetime import datetime as dt
        try:
            # Parse MM/DD/YYYY format (our normalized output)
            def parse_date(s):
                for fmt in ('%m/%d/%Y', '%m/%d/%y'):
                    try:
                        return dt.strptime(s.strip(), fmt)
                    except ValueError:
                        continue
                return None

            orig = parse_date(origination_raw)
            mat = parse_date(maturity_raw)
            if not orig or not mat:
                return None

            # Calculate difference in months
            months = (mat.year - orig.year) * 12 + (mat.month - orig.month)
            if months <= 0:
                return None

            years = months // 12
            remaining_months = months % 12

            # In CRE, if both dates are on the 1st and remainder is 1 month,
            # it's typically just the next round year (e.g., 03/01 to 04/01 = 12 years, not 12y 1m)
            if remaining_months == 1 and orig.day <= 1 and mat.day <= 1:
                years += 0  # keep as-is but present as "~12 years"
                return f"{years} {'year' if years == 1 else 'years'}"

            if remaining_months == 0:
                return f"{years} {'year' if years == 1 else 'years'}"
            elif years == 0:
                return f"{remaining_months} {'month' if remaining_months == 1 else 'months'}"
            else:
                return f"{years} {'year' if years == 1 else 'years'}, {remaining_months} {'month' if remaining_months == 1 else 'months'}"
        except Exception:
            return None

    @classmethod
    def _normalize_date(cls, raw: str) -> Optional[str]:
        """
        Normalize various date formats to MM/DD/YYYY.

        Handles:
        - "first day of April, 2027" → "04/01/2027"
        - "1st day of March, 2015" → "03/01/2015"
        - "April 15, 1996" → "04/15/1996"
        - "3/15/2010" → "03/15/2010"
        """
        raw = raw.strip()

        # Pattern: "Xth day of Month, Year" or "first day of Month, Year"
        m = re.match(
            r'(?:(\w+)|(\d{1,2})(?:st|nd|rd|th)?)\s+day\s+of\s+(\w+),?\s*(\d{4})',
            raw, re.IGNORECASE
        )
        if m:
            if m.group(1):
                day = cls._ORDINAL_WORDS.get(m.group(1).lower(), 1)
            else:
                day = int(m.group(2))
            month = cls._MONTH_NAMES.get(m.group(3).lower(), 0)
            year = int(m.group(4))
            if month:
                return f'{month:02d}/{day:02d}/{year}'

        # Pattern: "Month DD, YYYY"
        m = re.match(
            r'(\w+)\s+(\d{1,2}),?\s*(\d{4})', raw, re.IGNORECASE
        )
        if m:
            month = cls._MONTH_NAMES.get(m.group(1).lower(), 0)
            if month:
                day = int(m.group(2))
                year = int(m.group(3))
                return f'{month:02d}/{day:02d}/{year}'

        # Pattern: "M/D/YY" or "MM/DD/YYYY"
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', raw)
        if m:
            month = int(m.group(1))
            day = int(m.group(2))
            year = int(m.group(3))
            if year < 100:
                year += 2000 if year < 50 else 1900
            return f'{month:02d}/{day:02d}/{year}'

        return None

    @classmethod
    def _parse_garbled_dollar(cls, raw: str) -> Optional[float]:
        """
        Parse garbled dollar amounts from PDF extraction.

        Handles patterns like:
        - "3000,.00000.," → $3,000,000 (garbled by pdfplumber)
        - "10 00,.00000" → $1,000,000
        - "30,000.00" → $30,000.00 (normal)
        """
        # Remove spaces
        s = raw.replace(' ', '')

        # Check for garbled pattern: digits followed by repeated zeros with
        # misplaced commas/periods.  These come from pdfplumber encoding bugs
        # where "$3,000,000.00" becomes "$3000,.00000.,"
        # Strip all trailing commas/periods/zeros that look like garbled padding
        garbled = re.match(r'^(\d{1,6})[,.]([0,.\s]+)$', s)
        if garbled:
            base_str = garbled.group(1)
            padding = re.sub(r'[^0]', '', garbled.group(2))  # count only the zeros
            n_zeros = len(padding)
            base = int(base_str)
            if n_zeros >= 4:
                # "3000" + 5 zeros → the actual number is base * 10^(n_zeros - len(base_digits) + 1)-ish
                # Simpler: "3000,.00000" in the doc represents "3,000,000.00"
                # The written-out form says THREE MILLION, so base=3000 * 1000 = 3M
                # "100,.00000" represents 100,000 — base=100 * 1000 = 100K
                return float(base) * 1000
            return float(base)

        # Normal parse — remove garbled chars, try standard float
        cleaned = re.sub(r'[^\d.]', '', s)
        # If multiple decimal points, keep only last one
        if cleaned.count('.') > 1:
            parts = cleaned.rsplit('.', 1)
            cleaned = parts[0].replace('.', '') + '.' + parts[1]
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    @staticmethod
    def _parse_percentage(raw: str, is_rate_field: bool = False) -> Optional[float]:
        """
        Parse a percentage from text like '7.26', '72.6', 'seven and 26/100'.

        Args:
            raw: The raw string containing the number
            is_rate_field: If True, apply sanity-check heuristics for interest
                           rates (typically 0-15%). Garbled values like "72.6"
                           get corrected to "7.26".
        """
        # Try direct numeric parse
        cleaned = re.sub(r'[^\d./]', '', raw)
        if '/' in cleaned:
            # Handle "X and Y/Z" fractional format like "726/100" -> could be garbled
            parts = re.match(r'(\d+)/(\d+)', cleaned)
            if parts:
                numer = int(parts.group(1))
                denom = int(parts.group(2))
                if denom > 0:
                    return numer / denom
            # Or "X" + "Y/Z" where X is whole part
            parts = re.match(r'(\d+?)(\d+)/(\d+)', cleaned)
            if parts:
                whole = int(parts.group(1))
                numer = int(parts.group(2))
                denom = int(parts.group(3))
                if denom > 0:
                    return whole + numer / denom
        try:
            val = float(cleaned)
            # For rate fields (interest rate, spread, cap, floor), values > 20
            # are almost certainly garbled — "72.6" means "7.26"
            if is_rate_field and val > 20:
                s = cleaned.replace('.', '')
                if len(s) >= 3:
                    val = float(s[0] + '.' + s[1:])
                else:
                    val = val / 10
            return val
        except (ValueError, ZeroDivisionError):
            return None

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
        """
        Use local LLM for legal clause extraction.

        Strategy: rule-based section detection first, then LLM only to
        summarize/classify clauses the rules couldn't match.
        """
        # Step 1: Rule-based extraction gets most clauses via section headers
        rule_clauses = self._extract_legal_rules(doc, template)
        found_types = {c['clause_type'] for c in rule_clauses}

        logger.info(
            f"Rule-based clause extraction found {len(rule_clauses)} clauses "
            f"({len(found_types)} types)"
        )

        missing_types = set(template.clause_types) - found_types

        if not missing_types:
            return rule_clauses

        # Step 2: One focused LLM call to find missing clause types
        missing_list = ", ".join(
            ct.replace('_', ' ') for ct in missing_types
        )

        # Use first ~2500 chars for speed
        excerpt = doc.full_text[:2500]

        prompt = (
            f"Does this document contain any of these clause types? "
            f"{missing_list}\n\n"
            f"For each one found, return JSON array with: clause_type, "
            f"clause_title, summary (1 sentence), section_ref, confidence.\n\n"
            f"Document:\n{excerpt}"
        )

        logger.info(f"LLM clause search for {len(missing_types)} missing types...")
        result = self.llm.generate_structured(prompt)

        llm_clauses = []
        if result:
            if isinstance(result, list):
                llm_clauses = result
            elif isinstance(result, dict):
                llm_clauses = result.get('clauses', result.get('legal_clauses', [result]))
                if not isinstance(llm_clauses, list):
                    llm_clauses = [llm_clauses]

        # Merge
        all_clauses = rule_clauses + [
            c for c in llm_clauses
            if isinstance(c, dict) and c.get('clause_type') not in found_types
        ]

        return self._deduplicate_clauses(all_clauses)

    def _extract_legal_rules(self, doc: DocumentContent,
                              template: DocumentTemplate) -> List[Dict]:
        """Rule-based clause extraction using section headers."""
        clauses = []
        text = doc.full_text

        # Multiple section header patterns for different document formats
        section_patterns = [
            # "ARTICLE IV — Title" or "Section 12.3 - Title"
            re.compile(
                r'(?:ARTICLE|SECTION|Article|Section)\s+[\dIVXivx]+[.\s]*'
                r'[-–—]?\s*([A-Z][^\n]+)',
                re.MULTILINE
            ),
            # "12. Title." or "3. Title of Section."  (numbered paragraphs)
            re.compile(
                r'^\s*(\d{1,2})\.\s+([A-Z][A-Za-z\s,;:\-&\']+?)(?:\.|$)',
                re.MULTILINE
            ),
        ]

        sections = []
        for pat in section_patterns:
            matches = list(pat.finditer(text))
            if matches:
                # Use whichever pattern found more sections
                if len(matches) > len(sections):
                    sections = matches

        if not sections:
            return clauses

        # Build clause-type keyword map for matching
        # Use PHRASES not single words to avoid false matches
        clause_keyword_map = {
            'events_of_default': ['events of default', 'event of default', 'default event',
                                  'shall constitute a default', 'default under this'],
            'remedies': ['remedies', 'acceleration', 'foreclosure', 'rights and remedies',
                         'appointment of receiver'],
            'representations_warranties': ['representations', 'warranties', 'represents and warrants'],
            'covenants': ['covenants', 'covenant', 'miscellaneous covenants'],
            'insurance_requirements': ['insurance', 'coverage'],
            'environmental': ['environmental', 'hazardous substance', 'hazardous material'],
            'transfer_restrictions': ['transfer', 'assignment of', 'conveyance', 'sale or transfer'],
            'due_on_sale': ['due on sale', 'due-on-sale', 'transfer of property',
                            'prohibition on transfer'],
            'subordination': ['subordination', 'subordinate'],
            'cross_default': ['cross default', 'cross-default'],
            'reporting_requirements': ['reporting', 'financial statements', 'books and records'],
            'cash_management': ['cash management', 'cash sweep'],
            'lockbox': ['lockbox', 'lock box', 'cash collateral'],
        }

        # Fill in any clause types not explicitly mapped
        for clause_type in template.clause_types:
            if clause_type not in clause_keyword_map:
                clause_keyword_map[clause_type] = [clause_type.replace('_', ' ')]

        for i, match in enumerate(sections):
            # Extract title — depends on which pattern matched
            if match.lastindex and match.lastindex >= 2:
                section_num = match.group(1)
                title = match.group(2).strip().rstrip('.')
                section_ref = f"Section {section_num}"
            else:
                title = match.group(1).strip().rstrip('.')
                section_ref = match.group(0).split('\n')[0].strip()[:80]

            start = match.start()
            end = sections[i+1].start() if i+1 < len(sections) else min(start + 3000, len(text))
            section_text = text[start:end].strip()

            # Clean title for matching
            title_lower = self._clean_pdf_text(title).lower()

            # Match to clause types
            for clause_type, keywords in clause_keyword_map.items():
                if any(kw in title_lower for kw in keywords):
                    clauses.append({
                        "clause_type": clause_type,
                        "section_ref": section_ref,
                        "clause_title": self._clean_pdf_text(title),
                        "full_text": section_text[:2000],  # cap at 2K chars
                        "summary": None,
                        "confidence": 0.70,
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

        # Second try: text-based parsing for operating statements / budgets
        if not all_rows and template.document_type == 'operating_statement':
            all_rows = self._extract_opstat_from_text(doc)
            if all_rows:
                logger.info(f"Text-based operating statement parser found {len(all_rows)} rows")

        # Second try (rent rolls): text-based parsing for flowing-text rent rolls
        if not all_rows and template.document_type == 'rent_roll':
            all_rows = self._extract_rent_roll_from_text(doc)
            if all_rows:
                logger.info(f"Text-based rent roll parser found {len(all_rows)} rows")

        # Last resort: LLM
        if not all_rows and self.llm_available:
            all_rows = self._extract_tabular_llm(doc, template)

        return all_rows

    def _extract_opstat_from_text(self, doc: DocumentContent) -> List[Dict]:
        """
        Parse operating statement / budget data directly from text.

        Handles property-management exports (Yardi, MRI, etc.) where pdfplumber
        can't parse the table structure but the text has a clear line-item format:

            -RentalIncome 1,165,707.46 0.00 1,211,422.50 0.00 ...
            TOTALREVENUE  1,232,534.38 0.00 ...
        """
        amount_re = re.compile(r'\(?\d[\d,]*\.\d{2}\)?')

        all_rows = []
        current_category = 'other'

        # Detect period columns from header lines
        periods = []
        lines = doc.full_text.split('\n')

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Look for year header like "2023 2024 2025 2025 2026"
            year_match = re.match(r'^(\d{4}(?:\s+\d{4})+)\s*$', stripped)
            if year_match:
                years = re.findall(r'\d{4}', stripped)
                # Next line should have Actual/Budget/Reforecast labels
                if i + 1 < len(lines):
                    label_line = lines[i + 1].strip()
                    labels = re.findall(r'(Actual|Budget|Reforecast|Forecast|Projected)', label_line, re.IGNORECASE)
                    if labels and len(labels) == len(years):
                        periods = [f"{y} {l}" for y, l in zip(years, labels)]
                break

        # Skip header patterns
        skip_prefixes = ('database:', 'entity:', 'kraus', 'engelsman', 'accrual',
                         'actual', 'budget', 'reforecast', 'page:')

        seen_lines = set()  # deduplicate across pages

        for page in doc.pages:
            page_text = page.text if hasattr(page, 'text') else ''
            page_lines = page_text.split('\n') if page_text else []

            for line in page_lines:
                stripped = line.strip()
                if not stripped:
                    continue

                lower = stripped.lower()
                # Skip headers and year rows
                if any(lower.startswith(p) for p in skip_prefixes):
                    continue
                if re.match(r'^\d{4}(\s+\d{4})+\s*$', stripped):
                    continue

                # Deduplicate
                if stripped in seen_lines:
                    continue
                seen_lines.add(stripped)

                # Find all dollar amounts in the line
                amounts = amount_re.findall(stripped)

                if not amounts:
                    # Pure section header — update category
                    clean = re.sub(r'\s+', '', stripped).lower()
                    if clean in ('revenue',):
                        current_category = 'revenue'
                    elif clean in ('expense', 'expenses'):
                        current_category = 'expense'
                    elif 'adjustmentstocash' in clean:
                        current_category = 'other'
                    continue

                # Extract name: everything before the first amount
                first_amt_pos = stripped.index(amounts[0])
                name_raw = stripped[:first_amt_pos].strip()

                # Clean the name: remove leading dash, add spaces between camelCase
                name = name_raw.lstrip('-').strip()
                if not name:
                    continue
                name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
                name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', name)
                name = name.replace('&', ' & ')
                name = re.sub(r'\s+', ' ', name).strip()

                # Detect totals/subtotals
                clean_key = re.sub(r'\s+', '', name_raw).lower()
                is_total = clean_key.startswith('net')
                is_subtotal = 'total' in clean_key and not is_total

                # Update category from line context (order matters — check specific before general)
                if 'netoperatingincome' in clean_key or clean_key == 'noi':
                    line_cat = 'noi'
                elif 'fundsfromoperations' in clean_key or clean_key == 'ffo':
                    line_cat = 'noi'
                elif clean_key.startswith('netincome') or clean_key == 'netcashflow':
                    line_cat = 'other'
                elif 'debtservice' in clean_key or 'mortgage' in clean_key:
                    line_cat = 'debt_service'
                elif 'interest' in clean_key:
                    line_cat = 'debt_service'
                elif 'depreciation' in clean_key or 'amortization' in clean_key:
                    line_cat = 'other'
                elif 'capital' in clean_key or 'improvement' in clean_key:
                    line_cat = 'capital'
                elif 'revenue' in clean_key or 'income' in clean_key:
                    line_cat = 'revenue'
                elif 'expense' in clean_key or 'operating' in clean_key:
                    line_cat = 'expense'
                elif is_total or is_subtotal:
                    line_cat = current_category
                else:
                    line_cat = current_category

                # If this is a section header with amounts, update category
                if is_subtotal and 'revenue' in clean_key:
                    current_category = 'revenue'
                elif clean_key in ('expense', 'expenses') or (is_subtotal and 'expense' in clean_key):
                    current_category = 'expense'

                # Parse amount value
                def _parse_amt(s):
                    neg = s.startswith('(') and s.endswith(')')
                    s = s.replace('(', '').replace(')', '').replace(',', '')
                    try:
                        v = float(s)
                        return -v if neg else v
                    except ValueError:
                        return None

                # Amounts come in pairs: amount, $PSF for each period
                # Use the LAST period (most recent budget)
                if periods and len(amounts) >= len(periods) * 2:
                    amount_val = _parse_amt(amounts[-2])
                    psf_val = _parse_amt(amounts[-1])
                    period = periods[-1]
                elif len(amounts) >= 2:
                    amount_val = _parse_amt(amounts[-2])
                    psf_val = _parse_amt(amounts[-1])
                    period = periods[-1] if periods else None
                elif len(amounts) == 1:
                    amount_val = _parse_amt(amounts[0])
                    psf_val = None
                    period = periods[-1] if periods else None
                else:
                    continue

                row = {
                    'line_item': name,
                    'category': line_cat,
                    'amount': amount_val,
                    'amount_psf': psf_val if psf_val and psf_val != 0 else None,
                    'period': period,
                    'is_total': is_total,
                    'is_subtotal': is_subtotal,
                    'page_number': page.page_number,
                }
                all_rows.append(row)

        return all_rows

    def _extract_rent_roll_from_text(self, doc: DocumentContent) -> List[Dict]:
        """
        Parse rent roll data directly from text when pdfplumber can't
        find structured tables.

        Supports two common property-management formats:

        Format A — Fixed-length records (e.g., senior living / simple rent rolls):
            100 One BR - 724   ← unit + type + sqft
            Holmgren, Odel M.  ← tenant
            0                  ← sec deposit
            2,290.00           ← rent
            0                  ← other

        Format B — "Total"-delimited records (e.g., Yardi rent rolls):
            6720-01            ← unit number
            CP-A               ← unit type
            650.00 t0002067    ← sqft + tenant ID
            Ahmad Doleh        ← tenant name
            859.00 rent        ← market rent + charge code
            915.00             ← actual rent
            500.00             ← deposit
            0.00 07/30/2019    ← other deposit + move-in
            08/29/2019         ← lease expiration
            0.00               ← balance
            Total              ← record delimiter
            915.00             ← total rent
        """
        # Combine all page text, skipping repeated headers
        all_lines = []
        header_keywords = {'unit', 'resident', 'tenant', 'rent', 'sec dep',
                           'unit type', 'second', 'deposit', 'name', 'market',
                           'charge', 'balance', 'sq ft', 'expiration', 'move'}
        page_meta_re = re.compile(
            r'(rent roll|page \d|as of|month year|current/notice|notice/vacant)',
            re.IGNORECASE
        )

        page_numbers = {}  # line_index -> page_number

        for page in doc.pages:
            text = page.text if hasattr(page, 'text') else ''
            if not text:
                continue

            lines = [l.strip() for l in text.split('\n') if l.strip()]

            # Skip header block at start of each page
            data_start = 0
            for i, line in enumerate(lines):
                lower = line.lower()
                if any(kw in lower for kw in header_keywords):
                    data_start = i + 1
                else:
                    break

            for line in lines[data_start:]:
                # Skip page metadata lines
                if page_meta_re.search(line):
                    continue
                idx = len(all_lines)
                page_numbers[idx] = page.page_number
                all_lines.append(line)

        if not all_lines:
            return []

        # Detect format: look for "Total" delimiters
        has_totals = any(l.lower() == 'total' for l in all_lines)

        # Detect unit number patterns
        # Format A: "100 One BR - 724" (digits + space + descriptive text)
        unit_re_a = re.compile(r'^(\d+)\s+(.+)$')
        # Format B: "6720-01" or "B125" (standalone unit IDs with a
        #           unit-type line like "CP-A", "1BR-B" on the next line)
        unit_re_b = re.compile(r'^([A-Z]?\d{3,}-\d{2,}|[A-Z]\d{2,})$')
        # Format C: plain integer unit (101, 102) followed by an alphanumeric
        #           type code (4302b12p) then "sqft tenantID" line
        unit_re_c = re.compile(r'^(\d{1,4})$')
        # Unit type patterns
        unit_type_re_b = re.compile(
            r'^[A-Z0-9]+-[A-Z0-9]+$|^(CP|BR|Studio|Alcove|SRO)',
            re.IGNORECASE
        )
        unit_type_re_c = re.compile(r'^[a-zA-Z0-9]{4,}$')  # e.g. "4302b12p"
        sqft_tenant_re = re.compile(r'^[\d,.]+\s+\S+')  # e.g. "1,070.00 t0001450"

        # Count format matches
        format_b_matches = 0
        for i, l in enumerate(all_lines):
            if unit_re_b.match(l) and i + 1 < len(all_lines) and unit_type_re_b.match(all_lines[i + 1]):
                format_b_matches += 1

        format_c_matches = 0
        for i, l in enumerate(all_lines):
            if unit_re_c.match(l) and i + 2 < len(all_lines):
                next_l = all_lines[i + 1]
                next_next = all_lines[i + 2]
                if (unit_type_re_c.match(next_l)
                        and not any(kw in next_l.lower() for kw in header_keywords)
                        and sqft_tenant_re.match(next_next)):
                    format_c_matches += 1

        format_a_matches = sum(1 for l in all_lines if unit_re_a.match(l))

        if has_totals and format_b_matches > 2:
            return self._parse_rent_roll_format_b(all_lines, page_numbers)
        elif format_c_matches > 2:
            return self._parse_rent_roll_format_c(all_lines, page_numbers)
        elif format_a_matches > 2:
            return self._parse_rent_roll_format_a(all_lines, page_numbers)
        else:
            if has_totals:
                return self._parse_rent_roll_format_b(all_lines, page_numbers)
            return []

    def _parse_rent_roll_format_a(self, lines: List[str],
                                   page_numbers: Dict[int, int]) -> List[Dict]:
        """
        Parse fixed-length rent roll records (e.g., Arbors-style).
        Records: unit+type, name, sec_dep, rent, other.
        """
        all_rows = []
        unit_re = re.compile(r'^(\d+)\s+(.+)$')
        amount_re = re.compile(r'^[\d,]+\.?\d*$')

        # Find record start positions
        record_starts = [i for i, line in enumerate(lines) if unit_re.match(line)]
        if len(record_starts) < 2:
            return []

        record_len = record_starts[1] - record_starts[0]
        if record_len < 2 or record_len > 10:
            return []

        for start in record_starts:
            record = lines[start:start + record_len]
            if len(record) < 2:
                continue

            m = unit_re.match(record[0])
            if not m:
                continue
            unit_number = m.group(1)
            unit_type_raw = m.group(2).strip()

            # Extract sqft from unit type ("One BR - 724" → 724)
            sqft = None
            unit_type = unit_type_raw
            sqft_match = re.search(r'[-\s]?\s*(\d{3,5})\s*$', unit_type_raw)
            if sqft_match:
                sqft = sqft_match.group(1)
                unit_type = unit_type_raw[:sqft_match.start()].rstrip(' -').strip()

            # Tenant name
            tenant_name = record[1] if len(record) > 1 else ''
            if amount_re.match(tenant_name):
                continue

            # Numeric fields
            numeric_values = []
            for field_line in record[2:]:
                cleaned = field_line.replace(',', '').strip()
                try:
                    numeric_values.append(float(cleaned))
                except ValueError:
                    numeric_values.append(None)

            sec_dep = numeric_values[0] if len(numeric_values) > 0 else None
            monthly_rent = numeric_values[1] if len(numeric_values) > 1 else None

            if sec_dep == 0:
                sec_dep = None

            row = {
                'unit_number': unit_number,
                'tenant_name': tenant_name,
                'square_footage': float(sqft) if sqft else None,
                'monthly_rent': monthly_rent,
                'status': 'occupied' if tenant_name and monthly_rent and monthly_rent > 0 else 'vacant',
                'notes': unit_type if unit_type else None,
                'page_number': page_numbers.get(start, 1),
                'metadata': {
                    'unit_type': unit_type,
                    'security_deposit': sec_dep,
                },
            }

            if unit_number and tenant_name:
                all_rows.append(row)

        return all_rows

    def _parse_rent_roll_format_b(self, lines: List[str],
                                   page_numbers: Dict[int, int]) -> List[Dict]:
        """
        Parse "Total"-delimited rent roll records (e.g., Yardi / Chamberlain-style).

        Each record runs from a unit number line to a "Total" line + total amount.
        Fields within a record are identified by content pattern rather than
        fixed position, since records can vary in length (subsidy lines, etc.).
        """
        all_rows = []
        unit_re = re.compile(r'^([A-Z]?\d{3,}-\d{2,}|[A-Z]\d{2,})$')
        unit_type_re = re.compile(
            r'^[A-Z0-9]+-[A-Z0-9]+$|^(CP|BR|Studio|Alcove|SRO)',
            re.IGNORECASE
        )
        date_re = re.compile(r'\d{2}/\d{2}/\d{4}')
        amount_re = re.compile(r'^-?[\d,]+\.\d{2}$')

        # Split into records using unit numbers as start markers
        # Require the next line to be a unit type to avoid false positives
        record_starts = []
        for i, line in enumerate(lines):
            if unit_re.match(line) and i + 1 < len(lines) and unit_type_re.match(lines[i + 1]):
                record_starts.append(i)
        if not record_starts:
            return []

        for ri, start in enumerate(record_starts):
            # Record ends at next unit start or end of lines
            end = record_starts[ri + 1] if ri + 1 < len(record_starts) else len(lines)
            record = lines[start:end]

            if len(record) < 4:
                continue

            unit_number = record[0]
            unit_type = record[1] if len(record) > 1 else None

            # Line 3: sqft + tenant ID or "sqft VACANT"
            sqft = None
            tenant_id = None
            is_vacant = False
            if len(record) > 2:
                sqft_line = record[2]
                if 'VACANT' in sqft_line.upper():
                    is_vacant = True
                    sqft_match = re.match(r'([\d,.]+)', sqft_line)
                    if sqft_match:
                        try:
                            sqft = float(sqft_match.group(1).replace(',', ''))
                        except ValueError:
                            pass
                else:
                    parts = sqft_line.split()
                    if parts:
                        try:
                            sqft = float(parts[0].replace(',', ''))
                        except ValueError:
                            pass
                        if len(parts) > 1:
                            tenant_id = parts[1]

            # Line 4: tenant name
            tenant_name = ''
            if len(record) > 3:
                name_candidate = record[3]
                # Make sure it's not a number
                if not amount_re.match(name_candidate.replace(',', '').replace('-', '')):
                    tenant_name = name_candidate

            if is_vacant:
                tenant_name = 'VACANT'

            # Find the "Total" line and the total rent after it
            total_rent = None
            for j, line in enumerate(record):
                if line.lower() == 'total' and j + 1 < len(record):
                    try:
                        total_rent = float(record[j + 1].replace(',', ''))
                    except ValueError:
                        pass
                    break

            # Parse amounts between name and Total
            # Line 5 is typically "market_rent rent" or just market_rent
            market_rent = None
            monthly_rent = None
            deposit = None
            move_in = None
            lease_end = None
            balance = None

            # Scan remaining lines for amounts and dates
            amount_lines = []
            for j in range(4, len(record)):
                line = record[j]
                if line.lower() == 'total':
                    break
                if line.lower() in ('subsidy', 'concession', 'credit'):
                    continue  # skip charge-type labels

                # Extract date if present
                date_match = date_re.search(line)
                if date_match:
                    date_val = date_match.group()
                    # Line with both amount and date (e.g., "0.00 07/30/2019")
                    amt_part = line[:date_match.start()].strip()
                    if not move_in:
                        move_in = date_val
                        # The amount before the date is "other deposit"
                    elif not lease_end:
                        lease_end = date_val
                    continue

                # Pure date line
                if re.match(r'^\d{2}/\d{2}/\d{4}$', line):
                    if not move_in:
                        move_in = line
                    elif not lease_end:
                        lease_end = line
                    continue

                # Amount line (possibly with charge code like "859.00 rent")
                parts = line.split()
                if parts:
                    amt_str = parts[0].replace(',', '').replace('-', '', 1) if parts[0].startswith('-') else parts[0].replace(',', '')
                    # Handle negative amounts
                    raw = parts[0].replace(',', '')
                    try:
                        val = float(raw)
                        amount_lines.append(val)
                    except ValueError:
                        pass

            # Map amounts by position:
            # [0]=market_rent, [1]=actual_rent, [2]=deposit, [3]=other, [4]=balance
            if len(amount_lines) >= 1:
                market_rent = amount_lines[0]
            if len(amount_lines) >= 2:
                monthly_rent = amount_lines[1]
            if len(amount_lines) >= 3:
                deposit = amount_lines[2]
            # balance is typically last amount before Total
            if len(amount_lines) >= 4:
                balance = amount_lines[-1]

            # Use total_rent as the definitive monthly rent if we have it
            # (handles cases with subsidy/concession where individual lines
            # don't add up simply)
            if total_rent is not None:
                monthly_rent = total_rent

            row = {
                'unit_number': unit_number,
                'tenant_name': tenant_name if tenant_name != 'VACANT' else '',
                'square_footage': sqft,
                'monthly_rent': monthly_rent,
                'lease_start': move_in,
                'lease_end': lease_end,
                'status': 'vacant' if is_vacant or not tenant_name or tenant_name == 'VACANT' else 'occupied',
                'notes': unit_type,
                'page_number': page_numbers.get(start, 1),
                'metadata': {
                    'unit_type': unit_type,
                    'market_rent': market_rent,
                    'security_deposit': deposit,
                    'tenant_id': tenant_id,
                    'balance': balance,
                },
            }

            all_rows.append(row)

        return all_rows

    def _parse_rent_roll_format_c(self, lines: List[str],
                                   page_numbers: Dict[int, int]) -> List[Dict]:
        """
        Parse fixed-length rent roll records where unit number, unit type,
        and sqft are on separate lines (e.g., 430 Oak Grove / Yardi style).

        Record structure (~10 lines, no "Total" delimiter):
            101              ← unit number (plain integer)
            4302b12p         ← unit type code
            1,070.00 t001450 ← sqft + tenant ID (or "sqft VACANT")
            Gowri Kabbur     ← tenant name
            1,885.00         ← market rent
            1,855.00         ← actual rent
            300.00           ← deposit
            0.00 05/17/2018  ← other deposit + move-in date
            05/31/2021       ← lease expiration
            0.00             ← balance
        """
        all_rows = []
        unit_re = re.compile(r'^(\d{1,4})$')
        type_re = re.compile(r'^[a-zA-Z0-9]{4,}$')
        date_re = re.compile(r'\d{2}/\d{2}/\d{4}')
        sqft_line_re = re.compile(r'^([\d,.]+)\s+(.+)$')

        # Find record starts: unit number + type code + sqft line
        header_keywords = {'unit', 'resident', 'tenant', 'rent', 'deposit',
                           'name', 'market', 'actual', 'balance', 'sq ft',
                           'expiration', 'move', 'lease', 'total'}
        record_starts = []
        for i, line in enumerate(lines):
            if unit_re.match(line) and i + 2 < len(lines):
                next_l = lines[i + 1]
                next_next = lines[i + 2]
                if (type_re.match(next_l)
                        and next_l.lower() not in header_keywords
                        and sqft_line_re.match(next_next)):
                    record_starts.append(i)

        if len(record_starts) < 2:
            return []

        # Determine record length
        record_len = record_starts[1] - record_starts[0]
        if record_len < 4 or record_len > 15:
            return []

        for start in record_starts:
            record = lines[start:start + record_len]
            if len(record) < 4:
                continue

            unit_number = record[0]
            unit_type = record[1]

            # Parse sqft + tenant ID / VACANT
            sqft = None
            tenant_id = None
            is_vacant = False
            sqft_m = sqft_line_re.match(record[2])
            if sqft_m:
                try:
                    sqft = float(sqft_m.group(1).replace(',', ''))
                except ValueError:
                    pass
                id_part = sqft_m.group(2).strip()
                if 'VACANT' in id_part.upper():
                    is_vacant = True
                else:
                    tenant_id = id_part

            # Tenant name
            tenant_name = record[3] if len(record) > 3 else ''
            if is_vacant:
                tenant_name = ''

            # Parse remaining lines for amounts and dates
            move_in = None
            lease_end = None
            amounts = []

            for j in range(4, len(record)):
                line = record[j]
                if line.lower() in ('total', 'subtotal'):
                    break

                # Check for date
                dm = date_re.search(line)
                if dm:
                    date_val = dm.group()
                    if not move_in:
                        move_in = date_val
                    elif not lease_end:
                        lease_end = date_val
                    # Also parse amount before the date if present
                    amt_part = line[:dm.start()].strip()
                    if amt_part:
                        try:
                            amounts.append(float(amt_part.replace(',', '')))
                        except ValueError:
                            pass
                    continue

                # Pure date line
                if re.match(r'^\d{2}/\d{2}/\d{4}$', line):
                    if not move_in:
                        move_in = line
                    elif not lease_end:
                        lease_end = line
                    continue

                # Amount line
                try:
                    amounts.append(float(line.replace(',', '')))
                except ValueError:
                    pass

            # Map amounts: [0]=market, [1]=actual_rent, [2]=deposit, [3..]=other/balance
            market_rent = amounts[0] if len(amounts) > 0 else None
            monthly_rent = amounts[1] if len(amounts) > 1 else None
            deposit = amounts[2] if len(amounts) > 2 else None
            balance = amounts[-1] if len(amounts) > 3 else None

            row = {
                'unit_number': unit_number,
                'tenant_name': tenant_name,
                'square_footage': sqft,
                'monthly_rent': monthly_rent,
                'lease_start': move_in,
                'lease_end': lease_end,
                'status': 'vacant' if is_vacant or not tenant_name else 'occupied',
                'notes': unit_type,
                'page_number': page_numbers.get(start, 1),
                'metadata': {
                    'unit_type': unit_type,
                    'market_rent': market_rent,
                    'security_deposit': deposit,
                    'tenant_id': tenant_id,
                    'balance': balance,
                },
            }

            all_rows.append(row)

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
    """
    Classify document type using a layered approach:
      1. Filename pattern matching (highest priority, fast)
      2. Title/header keyword matching (first 500 chars, high weight)
      3. Body keyword scoring (first 5000 chars)
      4. LLM fallback (only when confidence is very low)

    Supports 13 document types covering the full CRE document universe.
    """

    # ── Filename patterns ──────────────────────────────────────────
    # Checked first.  Tuple of (regex_pattern, doc_type, confidence).
    # Patterns are tested against the lowercased filename.
    FILENAME_PATTERNS = [
        # Partnership / LLC
        (r'llc.?agreement|limited.?liability.?company.?agreement', 'partnership_agreement', 0.92),
        (r'amendment.{0,10}llc|llc.{0,10}amendment', 'partnership_agreement', 0.90),
        (r'exhibit.*llc|llc.*exhibit|llc.?section|llc.?definition', 'partnership_agreement', 0.88),
        # HUD forms
        (r'hud.?cost.?cert', 'hud_form', 0.92),
        (r'hud.?final.?endors', 'hud_form', 0.92),
        (r'hud.?max.?insur', 'loan', 0.90),  # HUD mortgage schedule is a loan doc
        (r'hud.?escrow.?release|hud.?offsite|hud.?wc.?escrow', 'hud_form', 0.90),
        # Due diligence
        (r'diagnostic.?memo|due.?diligence|forensic.?review', 'due_diligence', 0.90),
        # Proforma / valuation
        (r'proforma|valuation.?proforma|portfolio.?proforma', 'proforma', 0.90),
        (r'investment.?summary|investment.?overview', 'proforma', 0.85),
        # Equity / waterfall
        (r'equity.?return.?calc|jv.?equity|equity.?waterfall', 'equity_waterfall', 0.90),
        (r'equity.?account|equity.?detail', 'equity_waterfall', 0.88),
        # Operating / budget
        (r'budget.?overview|operating.?budget|detailed.?budget', 'operating_statement', 0.90),
        (r'property.?overview.?summary', 'operating_statement', 0.85),
        (r'leadership.?rollup|cash.?activity', 'operating_statement', 0.85),
        # Closing / sources-uses
        (r'closing.?proceeds|settlement.?statement', 'closing', 0.90),
        (r'sources.?and.?uses|sources.?uses', 'closing', 0.88),
        (r'development.?agreement', 'closing', 0.88),
        # Loan
        (r'surplus.?cash.?note', 'loan', 0.88),
        (r'loan.?interest.?calc|project.?loan', 'loan', 0.88),
        (r'promissory.?note|mortgage.?schedule', 'loan', 0.90),
        # Guarantee
        (r'guarantee|guaranty', 'guarantee', 0.90),
        # Rent roll
        (r'rent.?roll', 'rent_roll', 0.92),
        # GL
        (r'general.?ledger|gl.?detail', 'general_ledger', 0.90),
        # Org chart
        (r'organizational.?chart|org.?chart|borrower.?org', 'organizational', 0.88),
        # Reference / context
        (r'context\.md$|context\.txt$|readme', 'reference', 0.85),
    ]

    # ── Keyword sets for body-text scoring ─────────────────────────
    # Each entry: (keywords, negative_keywords)
    # Negative keywords REDUCE score when found in the title, preventing
    # cross-referential text from causing misclassification.
    TYPE_KEYWORDS = {
        "lease": {
            "positive": [
                "lease agreement", "landlord", "tenant", "lessee", "lessor",
                "leased premises", "term of lease", "base rent",
                "common area maintenance", "cam charges", "security deposit",
                "lease commencement", "triple net", "nnn lease",
            ],
            "negative": [
                "llc agreement", "limited liability", "promissory note",
                "loan agreement", "hud", "proforma", "budget overview",
                "diagnostic memo", "equity return",
            ],
        },
        "loan": {
            "positive": [
                "promissory note", "loan agreement", "borrower", "lender",
                "principal balance", "interest rate", "maturity date", "mortgage",
                "amortization", "debt service", "collateral", "note holder",
                "loan amount", "prepayment", "default", "surplus cash note",
                "insurable mortgage", "mortgage schedule",
            ],
            "negative": [
                "llc agreement", "limited liability", "lease agreement",
                "rent roll", "diagnostic memo", "equity return",
            ],
        },
        "closing": {
            "positive": [
                "purchase and sale", "closing statement", "settlement statement",
                "buyer", "seller", "purchase price", "earnest money",
                "title insurance", "closing costs", "closing proceeds",
                "sources and uses", "development agreement", "contract for",
                "private development",
            ],
            "negative": [
                "llc agreement", "limited liability", "rent roll",
                "proforma", "budget overview",
            ],
        },
        "guarantee": {
            "positive": [
                "guarantee agreement", "guaranty agreement", "guarantor",
                "guaranteed obligations", "unconditional guarantee",
                "irrevocable", "personal guarantee", "carve-out guaranty",
                "completion guaranty",
            ],
            "negative": [
                "llc agreement", "limited liability", "lease agreement",
            ],
        },
        "rent_roll": {
            "positive": [
                "rent roll", "unit number", "occupied", "vacant",
                "monthly rent", "lease expiration", "square feet",
                "unit type", "market rent", "in-place rent",
                "move-in date", "lease start",
            ],
            "negative": [
                "llc agreement", "promissory note", "closing",
                "diagnostic memo", "proforma", "budget overview",
                "equity return", "investment summary",
            ],
        },
        "operating_statement": {
            "positive": [
                "operating statement", "income and expense",
                "operating expenses", "net operating income", "noi",
                "property tax", "maintenance", "utilities expense",
                "budget overview", "detailed budget", "variance",
                "actual vs budget", "controllable expenses",
                "non-controllable", "effective gross income",
                "total revenue", "total expenses", "cash activity",
                "leadership rollup", "operating budget",
                "reforecast", "annualized",
            ],
            "negative": [
                "llc agreement", "promissory note", "lease agreement",
                "proforma year", "capitalized value", "residual cap",
            ],
        },
        "general_ledger": {
            "positive": [
                "general ledger", "gl detail", "account code", "debit",
                "credit", "journal entry", "chart of accounts", "posting",
                "account register", "beginning balance", "ending balance",
            ],
            "negative": [
                "equity account", "equity return", "equity summary",
            ],
        },
        "partnership_agreement": {
            "positive": [
                "llc agreement", "limited liability company agreement",
                "operating agreement", "amendment no", "amendment to",
                "managing member", "membership interest", "capital account",
                "distributions of cash flow", "capital contribution",
                "section 3.2", "section 5.2", "member",
                "percentage ownership", "company agreement",
            ],
            "negative": [],
        },
        "due_diligence": {
            "positive": [
                "diagnostic memo", "due diligence", "forensic review",
                "equity reconciliation", "decision framework",
                "findings", "recommendation", "risk assessment",
                "compliance review", "document type: diagnostic",
            ],
            "negative": [],
        },
        "proforma": {
            "positive": [
                "proforma", "pro forma", "proforma summary",
                "valuation", "capitalized value", "residual cap",
                "discount rate", "exit cap", "irr",
                "investment summary", "investment overview",
                "cost basis", "net sale proceeds", "year 11 noi",
                "trailing 12", "annualized t6", "annualized t3",
                "interactive proforma",
            ],
            "negative": [
                "budget overview", "detailed budget",
            ],
        },
        "equity_waterfall": {
            "positive": [
                "equity return", "equity return calc", "jv equity",
                "surplus cash calculation", "equity contribution",
                "equity balance", "return to each partner",
                "compounded monthly", "preferred return",
                "equity period", "equity summary", "net invested equity",
                "distributions", "amount due to",
            ],
            "negative": [
                "llc agreement",
            ],
        },
        "hud_form": {
            "positive": [
                "u.s. department of housing", "urban development",
                "federal housing commissioner", "hud", "omb approval",
                "mortgagor's certificate", "cost certification",
                "final endorsement", "credit instrument",
                "escrow release", "fha",
            ],
            "negative": [],
        },
        "organizational": {
            "positive": [
                "organizational chart", "org chart", "borrower org",
                "mortgagor organizational", "corporate structure",
                "entity structure", "ownership chart",
            ],
            "negative": [],
        },
        "reference": {
            "positive": [
                "context bundle", "master prompt", "batch 1 of",
                "readme", "instructions", "overview document",
                "context file", "reference document",
            ],
            "negative": [],
        },
    }

    # Document types that have extraction templates (Phase 2 analysis)
    EXTRACTABLE_TYPES = {
        'lease', 'loan', 'closing', 'guarantee',
        'rent_roll', 'operating_statement', 'general_ledger',
    }

    def __init__(self, llm_client: Optional[LocalLLMClient] = None):
        self.llm = llm_client
        # Pre-compile filename patterns
        self._filename_patterns = [
            (re.compile(pat, re.IGNORECASE), dtype, conf)
            for pat, dtype, conf in self.FILENAME_PATTERNS
        ]

    def classify(self, doc: DocumentContent,
                 use_llm: bool = True) -> Tuple[str, float]:
        """
        Classify document type using layered analysis.

        Strategy:
          1. Filename pattern match (fast, high confidence)
          2. Title + body keyword scoring with negative penalties
          3. LLM fallback if confidence is very low

        Returns (document_type, confidence) tuple.
        """
        # ── Layer 1: Filename patterns ──
        filename_lower = doc.filename.lower()
        for pattern, doc_type, confidence in self._filename_patterns:
            if pattern.search(filename_lower):
                logger.info(f"Classifier: filename match → {doc_type} ({confidence:.0%})")
                return doc_type, confidence

        # ── Layer 2: Keyword scoring ──
        text_lower = doc.full_text[:5000].lower()
        title_text = doc.full_text[:500].lower()

        scores = {}
        for doc_type, kw_config in self.TYPE_KEYWORDS.items():
            positive_kws = kw_config["positive"]
            negative_kws = kw_config["negative"]

            score = 0
            for kw in positive_kws:
                if kw in title_text:
                    score += 3  # strong signal: keyword in title/header
                elif kw in text_lower:
                    score += 1  # weaker signal: keyword in body

            # Negative keywords in the title suppress this type
            for neg_kw in negative_kws:
                if neg_kw in title_text:
                    score -= 4  # strong penalty: contradicting keyword in title
                elif neg_kw in text_lower[:1000]:
                    score -= 1  # mild penalty in early body

            # Normalize by positive keyword count, clamp at 0
            scores[doc_type] = max(0, score) / len(positive_kws) if positive_kws else 0

        if not scores or max(scores.values()) == 0:
            if use_llm and self.llm and self.llm.is_available():
                return self._classify_llm(doc)
            return "unknown", 0.0

        best_type = max(scores, key=scores.get)
        best_score = scores[best_type]

        # If score is very low and LLM is allowed, use LLM for classification
        if use_llm and best_score < 0.15 and self.llm and self.llm.is_available():
            return self._classify_llm(doc)

        return best_type, min(best_score * 2, 1.0)  # scale up confidence

    def _classify_llm(self, doc: DocumentContent) -> Tuple[str, float]:
        """Use LLM to classify ambiguous documents."""
        type_list = "\n".join(
            f"- {t}" for t in sorted(self.TYPE_KEYWORDS.keys())
        )
        prompt = f"""Classify this real estate document into one of these types:
{type_list}

Return JSON: {{"document_type": "<type>", "confidence": <0-1>}}

Filename: {doc.filename}

First 2000 characters of the document:
{doc.full_text[:2000]}"""

        result = self.llm.generate_structured(prompt)
        if result and isinstance(result, dict):
            doc_type = result.get('document_type', 'unknown')
            # Validate the type is one we recognize
            if doc_type not in self.TYPE_KEYWORDS and doc_type != 'unknown':
                logger.warning(f"LLM returned unknown type '{doc_type}', using 'unknown'")
                doc_type = 'unknown'
            return doc_type, result.get('confidence', 0.5)

        return "unknown", 0.0
