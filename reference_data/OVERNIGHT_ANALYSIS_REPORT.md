# Overnight Analysis Report — Chamberlain Batch 1
**Date:** May 15, 2026  
**Scope:** Chamberlain Apartments Batch 1 ingestion results, extraction quality, comparison against partner's validated financials, and prioritized fix list.

---

## 1. Ingestion Status

**33 of 40 files ingested successfully.** 7 MSG (Outlook email) files failed because `extract-msg` wasn't installed when the server was running. These need a re-ingest after `pip install extract-msg` and server restart.

| Metric | Value |
|---|---|
| Documents ingested | 33 |
| Fulltext pages stored | 436 |
| Total text (chars) | 2,227,787 |
| Raw tables stored | 311 |
| Failed (MSG) | 7 |
| File types | PDF (14), XLSX (11), PNG (7), MD (1) |

Fulltext storage is working correctly for all formats (FTS5 virtual table). Every ingested document has searchable page-level text. Table extraction also works — the XLSX files especially produce rich structured tables (the Budget Overview alone has 571+ rows on one sheet).

---

## 2. Phase 2 Analysis — Stalled

The PropertyAnalyzer run started at 03:16 UTC and stalled after processing only **1 of 33 documents** (the Diagnostic Memo, doc 2). The analysis_runs record is stuck at status "running" with no error recorded.

**Root cause:** Most likely the server was shut down (user signed off for the night) or Ollama was unavailable. The analysis continues to use the LLM for gap-filling even though it's designed to degrade gracefully — the `_llm_available` property caches its first check and never refreshes, so if Ollama went down mid-run, the stale cache would keep trying to call it, hitting timeouts.

**What was extracted from the single document processed:**

- 16 clauses (9 from doc 2 via rules, 7 via LLM) — but all have empty `full_text` fields and generic "no relevant clauses found" summaries. The clause extractor matched section headers but couldn't extract meaningful content.
- 4 financial terms from doc 2: earnest money ($1,500,000) and closing costs ($957,325) — both correct values confirmed against the Diagnostic Memo text.
- 0 rent roll entries, 0 operating statement items, 0 GL entries.

**Fix required:** Reset the stalled analysis run, fix the LLM cache staleness bug, add timeout/retry logic, then re-run.

---

## 3. Document Classification — 17% Accuracy

This is the most critical issue. The keyword-based classifier is producing near-random results. Only 1 of 6 testable classifications was correct.

### Misclassification Examples

| Document | Classified As | Should Be |
|---|---|---|
| CHAMBERLAIN_CONTEXT.md | rent_roll | reference/context |
| Diagnostic Memo | closing | due_diligence |
| LLC Agreement amendments | loan, guarantee | partnership_agreement |
| Surplus Cash Note | rent_roll | loan |
| HUD Cost Certification | lease | cost_certification |
| HUD Final Endorsement | general_ledger | loan/endorsement |
| Closing Proceeds Summary | rent_roll | closing |
| JV Equity Return Calcs | lease | equity/waterfall |
| Valuation Proforma | rent_roll | proforma |
| Leadership Rollup | rent_roll | financial_summary |

### Why It's Failing

The classifier scores keyword hits in the first 5000 chars of text, with 3x weight for the first 500 chars. The problem is:

1. **Taxonomy too narrow.** Only 7 categories (lease, loan, closing, guarantee, rent_roll, operating_statement, general_ledger) for a document universe that includes partnership agreements, org charts, proformas, equity waterfalls, cost certifications, escrow releases, HUD forms, diagnostic memos, and reference docs. Documents that don't fit any category get assigned the highest-scoring wrong category.

2. **Keyword overlap.** CRE documents are cross-referential — a loan document mentions "rent," "lease," "operating," etc. because it's describing the property. The keyword scorer can't distinguish whether a document IS a rent roll vs. DISCUSSES rents.

3. **No structural signals.** A rent roll has a specific table structure (unit, tenant, rent, dates). The classifier only looks at text keywords, not table shape.

### Recommended Fix (Priority 1)

Add new document types to the taxonomy: `partnership_agreement`, `due_diligence`, `proforma`, `equity_waterfall`, `cost_certification`, `hud_form`, `reference`, `organizational`. Then refine keywords for each and add negative keywords (e.g., "LLC Agreement" in first 500 chars → NOT a lease/loan).

---

## 4. Comparison Against Validated Data

### 4A. NOI Figures

The validated data (from partner's `historical_pl.json`) shows Chamberlain NOI as:

| Period | Validated NOI | Budget Overview (doc 15) | Property Overview (doc 16) |
|---|---|---|---|
| 2021A | — | $2,860,198 | $1,191,975 |
| 2022A | — | $3,216,155 | $3,141,770 |
| 2023A | $4,092,328 | $2,770,997 | $3,627,246 |
| 2024A | $2,402,102 | $2,518,023 | $4,096,642 |
| 2025A | $2,578,446 | $2,812,263 | $3,118,125 |
| 2026B | $2,751,242 | — | $3,464,062 |

**Key observations:**

- **None of the document NOI figures match the validated figures exactly.** This is expected and explained by the partner's handoff documentation: different NOI views exist (as-reported, AMF-adjusted, cleansed, property NOI). The validated `historical_pl.json` appears to use a different definition than what's in these documents.

- **The Property Overview (doc 16) shows 2023 NOI of $4,096,642** — close to the validated $4,092,328, suggesting this view may include utility income that the other views exclude.

- **The Budget Overview (doc 15) shows consistently different figures** — likely a cash-basis or forecast-adjusted view rather than accrual actuals.

- **The dollar-exact P&L line items from the validated data (rental_income $4,976,028.42, etc.) do not appear in any Batch 1 document.** These likely come from MRI/Yardi GL exports or monthly financial statements that were not included in Batch 1.

### 4B. Unit Mix

The validated unit mix (333 total units: 6 Studio, 99 Alcove, 62 1BR, 86 2BR, 3 3BR, 77 Affordable) is referenced in the Budget Overview and Context documents but not in a structured rent-roll format. No rent roll document was included in Batch 1 — the actual rent roll data would come from MRI/Yardi exports.

### 4C. Capital Structure

From the Portfolio Proforma (doc 33) and Diagnostic Memo (doc 2):
- Total Project Cost: ~$52.67M (matches partner context)
- HUD Loan: ~$47.76M original 221(d)(4) note (matches)
- KA Inc. permanent equity: ~$2.65M (matches)
- IDP equity: ~$1.58M (matches)
- Current loan balance (3/31/25): $49,835,966.92 (from Property Overview)

### 4D. Below-the-Line Items

The validated AMF figures ($56.3K in 2023A, $54.4K in 2024A) are not directly found in document text. The Budget Overview shows "Cash-3rd Party Manager" of $328,270.71 which is the total management fee, not just the AMF (asset management fee) component.

### 4E. TIF / Tax Data

TIF is extensively discussed in the Diagnostic Memo (32 of 60 pages mention TIF). The TIF mechanics, IDP dispute, and 15-error analysis documented in the partner's handoff are present in the memo text but haven't been structurally extracted yet.

---

## 5. What's Missing from Batch 1

To do a proper extraction-vs-validated comparison, the following source documents are needed but were NOT in Batch 1:

1. **Monthly/annual P&L statements** — The validated historical_pl.json figures come from these. Without them, we can't test P&L line-item extraction.
2. **Rent roll exports** — No actual unit-level rent roll was included. The validated per_asset_unit_mix.json can't be compared.
3. **GL detail exports** — The Equity Account Details (doc 18) has GL-like data but it's the equity sub-ledger, not the operating GL.
4. **3rd-party operating statements** — The validated figures include 3rd-party year-end numbers for 2025A that would come from separate audit/review docs.

---

## 6. Code Issues Found — Prioritized Fix List

### Priority 1: Critical

1. **Document classifier accuracy (17%)** — Add new document types, refine keywords, add negative keywords, use structural signals (table shape). Without correct classification, the Phase 2 analyzer routes documents to the wrong extraction pipeline.

2. **Reset stalled analysis run** — UPDATE analysis_runs SET status='failed', error='stalled - server shutdown' WHERE id=1. Then the user can re-trigger from the UI.

3. **LLM availability cache is stale** — `_llm_available` in ExtractionEngine is set once and never refreshed. If Ollama starts/stops during a session, the engine won't notice. Add a TTL (e.g., check every 60 seconds).

### Priority 2: Important

4. **Operating statement text parser doesn't handle XLSX pipe-delimited format** — The Budget Overview (doc 15) has rich financial data in pipe-delimited text (from the XLSX → text conversion), but `_extract_opstat_from_text` expects camelCase line items from Yardi/MRI exports. Need a new parser variant for the pipe-delimited spreadsheet format.

5. **Column-year mapping for financial spreadsheets** — CRE financial spreadsheets have a consistent pattern: rows are line items, columns are years (2021A, 2022A, ..., 2026B). The extraction engine doesn't have a parser for this common format. This is the single most impactful improvement for financial extraction.

6. **Clause extraction produces empty results** — All 16 extracted clauses have empty `full_text` fields. The rule-based clause extractor finds section headers but doesn't capture the actual clause content that follows. The `_extract_legal_rules` method needs to grab text between section headers, not just match the header itself.

7. **`_extract_legal_llm` sends raw (uncleaned) text** — Unlike the financial extraction path, the legal LLM path sends raw PDF text without running `_clean_pdf_text` first, reducing LLM extraction quality.

### Priority 3: Nice-to-Have

8. **No retry logic in LLM client** — A single transient Ollama failure kills the entire LLM path for that document. Add 1-2 retries with backoff.

9. **JSON bracket parser doesn't handle strings** — `_extract_json` in llm_client.py counts bracket depth without accounting for brackets inside JSON string values. Edge case but could cause parse failures.

10. **Entity extraction regex is ALL-CAPS only** — Mixed-case entity names like "Goldman Sachs Bank USA" won't be caught.

11. **`llm_clause_prompt` field in templates is dead code** — The `_extract_legal_llm` method builds its own prompt and ignores the template's `llm_clause_prompt`. Either use it or remove it.

---

## 7. Recommended Next Steps (Morning)

1. **Install extract-msg and re-ingest the 7 MSG files.** Quick win: `pip install extract-msg`, restart server, re-upload the MSG files.

2. **Reset the stalled analysis run.** Direct DB fix or add a "cancel/reset" button to the UI.

3. **Fix document classifier** before re-running analysis. Incorrect classification means the wrong extraction pipeline runs on each document, producing garbage results. This is the highest-leverage fix.

4. **Add a column-year financial parser** for XLSX spreadsheets. The Budget Overview and Property Overview have the exact NOI/income/expense data we need — it's just in a year-column format the parser doesn't understand yet.

5. **Get the missing source documents** (P&L statements, rent rolls, GL exports) into Batch 2. The validated data can't be properly compared until we have the documents those numbers came from.

6. **Re-run Phase 2 analysis** after fixes 3 and 4 are in place.

---

## 8. Things I Did NOT Change

Per your instruction to "make obvious changes" but "queue up anything that requires input," I held off on code changes for the following reasons:

- **Classifier taxonomy expansion** requires a decision on which document types to add and how to handle documents that don't fit any category (assign "unknown" vs. best-guess).
- **Column-year parser** requires understanding the specific spreadsheet layouts across different properties — Chamberlain may not be representative.
- **Analysis run reset** — I can do this via SQL but wanted to flag it since it affects the UI state the user will see.

These are queued for your review in the morning.
