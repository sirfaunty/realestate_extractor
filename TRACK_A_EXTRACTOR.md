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

### Round 3: Re-Run Verification & Storage-Layer Fixes

**Re-run results (Run #15, 41 min):**
- **2047 OS items** — BTL taxonomy working: debt_service (85), reserve (73), noi_subtotal (48), capital_expense (41), admin_expense (34), financing (25), expense (1315), income (418), unknown (8)
- **40 financial terms** — down from 70 (Round 1) thanks to Round 2 quality hardening
- **18 clauses** — stable
- **0 rent roll** — garbage correctly filtered
- LLM timeouts: 4/4 equity_waterfall, 3/4 hud_form, 3/4 proforma, 1/3 loan (docs too long for Ollama)

**Issues found and fixed:**

1. **Universal CRE validation gate** (`_analyze_with_engine`) — CRE currency minimums, positive-only, and range checks only applied to `_extract_financial_from_tables()` path. Engine/prose extraction bypassed them, allowing garbage from KA Portfolio through (purchase_price=$52.67, GPR=$5.5, NOI=$1.0, total_equity=$1.86). Added a validation filter that applies to ALL terms before DB storage, regardless of extraction source. Drops 40→36 terms.

2. **OS items `property_id` NULL bug** — `insert_operating_statement_item()` didn't include `property_id` in its field list, so all 2047 OS items had `property_id = NULL`. Added `property_id` to the DB insert field list and passed it from `doc_record` in the caller. Queries joining through `documents` were unaffected, but direct `WHERE property_id = 1` queries returned nothing.

**Residual issues (not blocking):**
- `earnest_money = $5,000` has value_numeric=5.0 (engine parsing bug)
- `property_name = KA Headquarters Apartments` from portfolio spreadsheet (wrong property, needs property-level disambiguation)
- `dscr = $1.16` has `$` prefix in value_raw (cosmetic, numeric correct)

### Needs Re-Run
```bash
python3 run_analysis.py --property-id 1
```

---

## Queued — Next Steps

### Priority 1: Upload 26 Refinance Closing Docs
- Upload via batch upload UI after current re-run
- Classifier already prepped with 20+ new filename patterns and 70+ new keywords
- Watch for: classifier accuracy on new doc types, extraction coverage

### Priority 2: Equity Waterfall Coverage
- Only getting 1 term from equity waterfall docs (surplus_cash) — all 4 LLM calls timed out
- Needs better alias coverage for surplus cash calculation format
- May need specialized parsing for the JV return calculation spreadsheet layout

### Priority 3: New Property Onboarding
- Feed in a fresh property's docs to stress-test the full pipeline
- Watch for: classifier misses on new doc formats, parser failures on different spreadsheet layouts

### Priority 4: Engine Numeric Parsing
- `earnest_money = $5,000` parsed as value_numeric=5.0 instead of 5000.0
- Root cause in extraction_engine.py currency parsing, not in `_safe_float`

---

## Chamberlain Data Inventory (40 docs)

| Type | Count | Tables | Text | Financial Terms | Notes |
|------|-------|--------|------|-----------------|-------|
| operating_statement | 3 | 31 | 503K | 2047 OS items | BTL split into 6 subcategories |
| proforma | 4 | 49 | 995K | 20 terms (pre-gate) | Chamberlain Valuation clean; KA Portfolio has garbage |
| partnership_agreement | 11 | 0 | 184K | 0 | No template (text-heavy legal docs) |
| equity_waterfall | 4 | 20 | 135K | 1 (surplus_cash) | All 4 LLM timeouts, needs specialized parser |
| organizational | 4 | 0 | 13K | 0 | Emails + org chart, no extraction needed |
| hud_form | 4 | 45 | 62K | 8 | HUD parser working (FHA#, borrower, mortgage, etc.) |
| closing | 3 | 2 | 214K | 2 | purchase_price + earnest_money |
| loan | 3 | 1 | 40K | 9 | borrower, loan_amount, rate_type, interest_rate |
| due_diligence | 1 | 64 | 92K | 0 | No template (diagnostic memo) |
| general_ledger | 1 | 0 | 2K | 0 | Email, low value |
| rent_roll | 1 | 0 | 11K | 0 | Email, garbage data filtered |
| reference | 1 | 0 | 16K | 0 | Context doc |
