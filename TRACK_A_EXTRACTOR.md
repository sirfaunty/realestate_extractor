# Track A: Document Extractor — Chamberlain completion + new property onboarding

## Session Focus
1. Finish Chamberlain document import — get all remaining docs ingested and extraction verified
2. Onboard a new property end-to-end — this will stress-test classifier, parsers, and synthesis against fresh docs
3. Fix whatever breaks — parser coverage gaps, classifier misses, extraction accuracy issues will surface naturally from the new property
4. Build validation tooling as needed — compare extraction output against hand-validated data

## Key Context
- Dev mode: `CAPACTIVE_DEV_MODE=1 python3 run.py --port 8080`
- DB path: `data/org_dev.db`
- Always use `python3` not `python`
- Sandbox can't reach localhost:11434 (Ollama), so LLM-dependent analysis must be run locally
- Proforma module is live but depends on `pydantic pyyaml numpy-financial python-dateutil`
- Known past issues: sqlite3.Row .get() errors, column name mismatches in bridge.py (all fixed), 2025A expense gaps, cross-sheet double-counting

## Key Files
- `webapp.py` — main Flask app (~2400 lines)
- `batch_processor.py` — document ingestion pipeline
- `property_analyzer.py` — property analysis engine
- `financial_synthesis.py` — period-level financial synthesis
- `extractors/extraction_engine.py` — rule-based + LLM extraction
- `templates/document_templates.py` — field templates for each doc type (10 templates)
- `modules/proforma/` — proforma module with citation bridge
- `warehouse/` — DuckDB analytical warehouse (just built, wired at /warehouse) — don't touch, Track B owns this

---

## Completed This Session (May 19, 2026)

### Round 1: Core Extraction Fixes in `property_analyzer.py`
1. **Category classifier state machine** — `below_the_line`/`past_total_opex` now reset when new INCOME/EXPENSE section headers appear. Fixes multi-scenario budget sheets where INCOME→EXPENSE→NOI repeats.
2. **Period year cap** — `_MAX_OPSTAT_YEAR = 2031` filters 2032–2085 proforma projection noise from OS items.
3. **Unit mix noise filter** — `_UNIT_MIX_KEYWORDS` (bedroom, studio, alcove, market rate, affordable, etc.) excluded from OS extraction.
4. **Year-as-amount filter** — Amounts equal to year numbers (2020.0–2099.0) dropped.
5. **Low-confidence classification gate** — Docs with `classification_confidence < 0.3` skip structured extraction (rent_roll, OS, GL). Prevents garbage from misclassified emails.
6. **Rent roll validation** — Rejects header rows ("Unit #", "Tenant"), property-name-as-unit, summary rows, and entries without meaningful data.
7. **Table-aware financial term extraction** — New `_extract_financial_from_tables()` method scans `document_tables` for key-value pairs matching template field aliases. Merges with engine extraction (prose terms take priority, table terms fill gaps). This is critical for XLSX docs where the rule-based prose extractor can't see data.

### Round 1: New Templates in `templates/document_templates.py`
- **Proforma** (27 fields) — property info, valuation, income/expense assumptions, return metrics, capital structure
- **Equity Waterfall** (20 fields) — partnership structure, capital contributions, distribution terms, return calculations
- **HUD Form** (18 fields) — FHA project IDs, mortgage terms, cost certification data, endorsement/escrow
- All wired into `TEMPLATES` dict and routed through `_analyze_with_engine()`

### Round 1 Results (Post-Run)
- **2047 OS items**, **70 financial terms**, **0 rent roll** (garbage filtered), **18 clauses**
- OS items: clean categories, no future-period noise, no unit-mix leakage
- Financial terms: proforma docs producing 21+ terms, but quality issues found (garbage from KA Portfolio multi-property spreadsheet)

### Round 2: Financial Term Quality Hardening

**`_extract_financial_from_tables()` hardened with 6 new guards:**
1. **Header row guard** — Rows with 5+ short non-empty cells (column headers for multi-property/multi-year tables) are skipped entirely. Fixes "City, State" → property_type, "KA Inc. %" → market.
2. **Word-boundary matching** — Short aliases (< 6 chars) require word-boundary match via regex. Prevents "units" matching inside "community units of ownership".
3. **Dominated alias guard** — If a matched alias is a substring of a longer alias from another field, the match is rejected. Fixes "equity" matching on "Equity Multiple" cell (should be equity_multiple, not total_equity).
4. **CRE currency minimums** — purchase_price must be > $100K, NOI > $10K, GPR > $10K, etc. Filters garbage like "$52.67" purchase price from multi-property summary rows.
5. **Positive-only validation** — purchase_price, loan_amount, GPR, total_equity etc. reject negative values.
6. **Number range checks** — total_units 1–50K, DSCR 0.1–10x, equity_multiple 0.5–50x, price_per_unit $5K–$5M.
7. **Single-word field names excluded from aliases** — Prevents "market", "irr", etc. from matching unrelated cells. Only multi-word field names + explicit aliases + description used.
8. **Date pattern rejection for text fields** — Values like "12/31/21" no longer match property_address.

**Template alias tightening (`document_templates.py`):**
- `property_address`: removed "address"/"location" (too broad) → "property address"/"street address"/"site address"
- `total_units`: removed "units" (too short) → "total units"/"unit count"/"number of units"
- `property_type`: removed "asset class" → "property type"/"asset type"
- `total_equity`: removed "equity" (conflicts with equity_multiple) → "total equity"/"KA equity"/"sponsor equity"/"LP equity"
- `purchase_price`: removed "total cost"/"cost basis" (matched per-unit rows) → "purchase price"/"total project cost"/"total development cost"
- `market`: removed "submarket" → "market area"/"metro area"
- `price_per_unit`: tightened to "price per unit"/"cost per unit"
- `other_income`: added "other income" explicit alias

**Result**: Doc 33 (KA Portfolio) went from 21 terms (many garbage) → 5 clean terms. Doc 23 (Chamberlain Valuation) went from 21 → 7 clean terms.

### Round 2: Below-the-Line Taxonomy Split

**New `_BTL_SUBCATEGORY_KEYWORDS` dict** classifies 306 below-the-line items into 6 subcategories:
- `debt_service` (83 items): Debt Service, Interest, Principal, Mortgage Insurance, Cash Surplus Note
- `capital_expense` (41 items): Capital Expenses, Appliance Replacements, Building/Land Improvements
- `reserve` (42 items): Capital Reserve, Replacement Reserve, Escrow, Prepaid Rents
- `admin_expense` (41 items): Asset Management Fee, Bank Fees, Legal, Tax/Audit, Accounting
- `financing` (51 items): Equity/Loan Funding, Notes Payable/Receivable, Owner Contributions/Withdrawals
- `noi_subtotal` (48 items): Net Operating Income, Net Cash Flow, Outstanding Debt Balance

**Also:**
- Added `'gpr'` to `_INCOME_KEYWORDS` — "GPR" abbreviation now classifies as income
- Added `^total$` to `_SUBTOTAL_PATTERN` — bare "Total" rows flagged as subtotals
- Added `'net equity'` to BTL financing keywords

### Needs Re-Run
```bash
python3 run_analysis.py --property-id 1
```

---

## Queued — Next Steps

### Priority 1: Re-run & Verify (BLOCKED — needs local run)
- Run `python3 run_analysis.py --property-id 1` locally to repopulate with Round 2 fixes
- Expected: OS items split into 6+ categories (not all debt_service), cleaner financial terms, GPR as income
- Check property page in web UI for correct synthesis output

### Priority 2: HUD PDF Text Handling
- **STILL OPEN** — PDFs produce garbage terms ("of", "Purchase,"). OCR text too fragmented for rule-based extraction.
- Options: (a) improve OCR preprocessing, (b) rely on LLM gap-fill when Ollama is available, (c) build HUD-specific parsing for tabular PDF layouts

### Priority 3: New Property Onboarding
- Feed in a fresh property's docs to stress-test the full pipeline
- Watch for: classifier misses on new doc formats, parser failures on different spreadsheet layouts, synthesis issues with different period structures

### Priority 4: Classifier Improvements
- Add `correspondence` doc type for emails that don't fit other categories
- Low-confidence emails (IDP_Meeting.msg at 0.14, Tax Appeal email at 0.11) currently get misfiled
- .msg files with `classification_confidence < 0.2` should default to `correspondence` or `reference`

### Priority 5: Equity Waterfall Coverage
- Only getting 3-6 terms from equity waterfall docs — needs better alias coverage for surplus cash calculation format
- May need specialized parsing for the JV return calculation spreadsheet layout

---

## Chamberlain Data Inventory (40 docs)

| Type | Count | Tables Stored | Text (chars) | Extraction Status |
|------|-------|--------------|-------------|-------------------|
| operating_statement | 3 | 31 sheets | 503K | Best coverage — 2022–2026 income/expense/NOI |
| proforma | 4 | 49 sheets | 995K | NEW: 21+ terms from table-aware extraction |
| partnership_agreement | 11 | 0 | 184K | No template (text-heavy legal docs) |
| equity_waterfall | 4 | 20 sheets | 135K | NEW: 3 terms, needs LLM for more |
| organizational | 4 | 0 | 13K | No template needed (emails + org chart) |
| hud_form | 4 | 45 tables | 62K | NEW template, minimal results (OCR quality) |
| closing | 3 | 2 sheets | 214K | Existing template, 13 terms |
| loan | 3 | 1 sheet | 40K | Existing template, 28 terms |
| due_diligence | 1 | 64 tables | 92K | No template (diagnostic memo) |
| general_ledger | 1 | 0 | 2K | Email, low value |
| rent_roll | 1 | 0 | 11K | Email, garbage data filtered by validation |
| reference | 1 | 0 | 16K | Context doc, no extraction needed |
