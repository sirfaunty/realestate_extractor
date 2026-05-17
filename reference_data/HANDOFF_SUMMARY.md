# KA Residential — Chat Handoff Summary

**Purpose of this document:** Hand off context from a document-generation chat to a partner building a broader platform for **extracting source documents** (financial statements, budgets, rent rolls, loan docs) for Kraus-Anderson's residential / senior-housing portfolio. This summarizes what was built, the data methodology that emerged, and the source-document landscape — so the partner's Claude project can be primed on what "good" extraction looks like for this portfolio.

**Date of handoff:** May 2026
**Chat scope:** Final iteration (v7+) of the KA Residential Leadership Board document, plus a new standalone Arbors at Ridges strategic overview.

---

## 1. What this chat produced

Three deliverables (all in `/outputs`):

| File | Status | Description |
|---|---|---|
| `KA_Residential_Leadership_Board_Q2_2026.docx` | **Final** | 6-page board doc + 1-page addendum. Multifamily-only (6 assets). Operating overview, valuation, loan overview, outlook/3-yr takeaways, NOI reconciliation addendum. |
| `KA_Arbors_Strategic_Overview_Q2_2026.docx` | **Draft** | 3-page standalone strategic overview for Arbors at Ridges (62-unit senior housing asset). Operating trajectory, valuation, operator strategy. |
| `KA_Residential_Operations_Update_Q2_2026.docx` | Stable (built earlier) | 5-page operational memo. Not modified in this chat. |

Build scripts (Node.js + `docx` library):
- `leadership_board_build/build_board.js` (~1,240 lines)
- `arbors_overview_build/build_arbors.js` (~620 lines)

---

## 2. The core methodology: source-of-truth NOI reconciliation

**This is the most important takeaway for the platform.** The single biggest analytical effort across this project was establishing a *consistent, defensible NOI series* for each asset. The challenge: KA's internal accounting and third-party property-manager reports disagree, often materially, and for structural reasons.

### 2.1 Source hierarchy by period (the "source of truth" rule)

| Period | Authoritative source | Why |
|---|---|---|
| 2021A–2024A | **KA internal accounting actuals** | Leadership's books; accrual-basis; complete |
| 2025A | **Third-party year-end financial statements** (VG, Cushman, Level 10, Ebenezer) | Most recent audited/finalized 3P numbers |
| 2026B | **Property-level budget files** (final approved) | Forward budget, property-by-property |
| 2027F–2028F | **Stabilized-rent proforma growth** | +3%/yr revenue & opex assumptions |

### 2.2 "Property NOI" definition (critical — used consistently everywhere)

> **Property NOI = NOI before asset management fees (AMF), capital expenditures, and non-operating items.**

AMF, CapEx, intercompany interest/AR, and non-operating items are tracked **separately, below the line**, and reconciled period-by-period. This definition makes the asset-level operating picture comparable across years even though the underlying source documents change.

### 2.3 Why internal accounting and 3P reports diverge (recurring extraction problem)

The platform will repeatedly hit these discrepancy drivers. Documenting them here because **any extraction tool needs to recognize and flag these**:

1. **RE tax appeals / refunds** — booked on KA internal books but frequently *not* on 3P books, or in a different period. Examples found:
   - FNP (Five 90 Park) 2024A: **$471,031** prior-year RE tax refund (May/Sep 2024 entries) inflated KA internal NOI. Pulled below the line.
   - Larking 2022A: 3P (Cushman) missed a **~$1.08M RE tax accrual** that KA had booked; caught up in next year's opening entries.
   - 430 Oak Grove 2024A: small **$50K** RE tax refund + structurally lower 2024 assessment.
2. **Refinance entries** — capital injections, paydowns, and intercompany notes booked as operating or capex when they're actually financing items. Examples:
   - HQ 2023A: **~$17.2M** reclassified out of "Routine CapEx" as BoA→MetLife refi-related costs, including a $4M KA equity injection that became the KAFSG intercompany note.
   - Moda: Principal Life → Jackson National refi (Oct 2025) — partial-year reporting not comparable.
   - 430 Oak Grove: Chase loan + KAFSG intercompany.
3. **Non-operating / capital / internal fees** — AMF, intercompany loans, project management fees mixed into operating lines.
4. **Accrual vs. cash timing** — 3P managers often book on payment basis; KA accrues monthly.
5. **Account-code roll-up mismatch** — KA's internal chart of accounts is geared toward *commercial* assets and does not roll up residential account codes the same way 3P managers do. **3P reports have materially better residential line-item detail and back-up schedules.** Translating 3P → KA internal is currently a manual ownership-representation function. **This is precisely the gap the platform is meant to close.**

### 2.4 2026 alignment goal (the platform's mandate, essentially)

Establish a consistent reconciliation cadence so both books reflect the same operating picture, with KA internal pushing infrequent ownership-level entries (RE tax appeals, refi true-ups, intercompany activity) to third-party bookkeepers via standardized journal entries. Historically not a priority because ownership representation hadn't used 3P financials for projections — that has changed, and the analytical depth now required surfaces these disconnects constantly.

---

## 3. The portfolio (asset roster)

### 3.1 Multifamily (6 assets, 1,350 units) — the Leadership Board doc

| Asset | Units | KA % ownership | Submarket | 3P Property Manager |
|---|---|---|---|---|
| HQ Apartments | 306 | 62.25% (ELP 33.00%, 1976 Trust 4.75%) | Mpls Downtown | VG |
| The Larking | 341 | 50% (Kin Properties 50%) | Mpls Downtown | VG (since 2/2026; Cushman prior) |
| Chamberlain | 316 | 75% (IDP 25%) | Richfield NE | VG |
| Moda on Raymond | 220 | 100% | StP Raymond/University | VG |
| Five 90 Park | 92 | 0% (ELP 100%) | St. Paul | VG |
| 430 Oak Grove | 75 | 100% | Mpls Loring Park | Level 10 (since 9/2025) |

### 3.2 Senior housing (1 asset) — the Arbors standalone doc

| Asset | Units | KA % | Location | 3P Operator |
|---|---|---|---|---|
| Arbors at Ridges | 62 | 100% (Arbors II Senior Living, LLC) | Burnsville, MN | Ebenezer (Fairview) |

Arbors was **pulled out of the Leadership Board doc entirely** mid-project and given its own standalone deliverable — different asset class, different operator dynamics, active strategic situation (potential sale).

---

## 4. Validated financial data (the numbers that survived reconciliation)

### 4.1 Multifamily Property NOI ($K) — FINAL, as used in the board doc

| Property | 2023A | 2024A | 2025A | 2026B | 2027F | 2028F |
|---|---|---|---|---|---|---|
| Chamberlain | 3,997 | 2,998 | 2,633 | 2,808 | 3,107 | 3,309 |
| Moda | 299 | 1,843 | 2,322 | 2,414 | 2,831 | 3,002 |
| HQ | 3,123 | 3,634 | 3,514 | 3,807 | 4,439 | 4,698 |
| Larking | 882 | 3,715 | 4,340 | 4,663 | 4,918 | 5,196 |
| Five 90 Park | 98 | 322 | 389 | 393 | 605 | 647 |
| 430 Oak Grove | 469 | 642 | 621 | 938 | 1,001 | 1,042 |
| **Multifamily Total** | **8,868** | **13,154** | **13,819** | **15,023** | **17,201** | **18,194** |

> Note: Five 90 Park 2024A shows **$322K** in the final board doc (KA internal $626-639K was inflated by the $471K RE tax refund — pulled below the line). The validated JSON files in the package show some intermediate values; `property_noi_FINAL.json` has a stale `638.5` for FNP 2024A — the **$322K figure in this table is the corrected final.**

### 4.2 Portfolio P&L ($K) — FINAL

| Year | Apt Rev | Other Inc | Total Rev | OpEx | Property NOI | Margin |
|---|---|---|---|---|---|---|
| 2023A | 18,587 | 3,206 | 21,793 | (12,925) | 8,868 | 40.7% |
| 2024A | 22,786 | 3,392 | 26,178 | (13,024) | 13,154 | 50.2% |
| 2025A | 23,537 | 3,611 | 27,148 | (13,329) | 13,819 | 50.9% |
| 2026B | 24,483 | 3,911 | 28,394 | (13,371) | 15,023 | 52.9% |
| 2027F | 26,467 | 3,977 | 30,444 | (13,243) | 17,201 | 56.5% |
| 2028F | 27,833 | 4,019 | 31,852 | (13,658) | 18,194 | 57.1% |

### 4.3 430 Oak Grove — KA internal actuals (5 yrs, all verified clean)

From `oak_final.json`. 2021A NOI $483K / 2022A $534K / 2023A $454K / 2024A $626K / 2025A $604K. (2024A bump from $50K RE tax refund + structurally lower assessment.)

### 4.4 Larking — the messiest reconciliation (see `larking_third_party_vs_ka.json`)

KA internal vs Cushman 3P diverged every year for different reasons. Highlights: 2022A KA -$1,624K vs Cushman -$404K (KA correct on accrual basis — Cushman missed the RE tax accrual). 2024A Cushman $3,715K used over KA roll-up $3,594K (KA was interim snapshot). 2025A Cushman $4,340K combined res+retail.

### 4.5 Arbors at Ridges — annual series 2018A–2026B ($K)

| Year | Revenue | OpEx | NOI | NOI Margin | Nursing Exp |
|---|---|---|---|---|---|
| 2018A | 4,241 | (2,825) | 1,416 | 33% | (1,068) |
| 2019A | 4,627 | (2,973) | 1,654 | 36% | (1,083) |
| 2020A | 4,398 | (3,186) | 1,212 | 28% | (1,210) |
| 2021A | 4,038 | (3,298) | 740 | 18% | (1,257) |
| 2022A | 4,483 | (3,593) | 890 | 20% | (1,440) |
| 2023A | 5,036 | (3,815) | 1,221 | 24% | (1,479) |
| 2024A | 5,569 | (4,301) | 1,268 | 23% | (1,686) |
| 2025A | 5,656 | (4,739) | 917 | 16% | (1,868) |
| 2026B | 6,045 | (4,859) | 1,185 | 20% | (1,906) |

Source: KA internal (2018A–2024A); SLIB BOV TTM Nov 2025 (2025A); Ebenezer 2026 Plan (2026B). 2018→2025 CAGRs: revenue +4.2%, OpEx +7.7%, NOI -6.0%, salaries +9.0%, nursing +8.3%. Service revenue mix 48%→62%.

---

## 5. Capital structure (multifamily — for context)

- **Third-party senior debt:** $204.7M across 6 loans, 4.65% weighted avg rate
- **FSG intercompany loans:** ~$20.5M (HQ $17.72M + 430 Oak $2.78M + Five 90 Park amount per Loan Doc Overview) — KA Financial Services Group; subordinated/standstill to senior
- **Implied value @ 5.00% cap on 2026B Property NOI:** $300.5M
- **Third-party LTV:** 68.1%

Per-loan: Chamberlain Colliers HUD 223(f) $52.97M 2.33% to 2056; Moda Jackson National $31.85M SOFR+2.50% to 2028; HQ MetLife $38.00M 6.30% to 2033; Larking Pacific Life $66.60M 4.90% to 2049; Five 90 Park Bridgewater $8.77M 5.00% to 2027; 430 Oak Grove JPMorgan Chase $6.50M 6.63% to 2054.

---

## 6. Source-document landscape (what the platform will be extracting)

This is the inventory of source-document *types* encountered, by asset. The platform should expect all of these formats.

### 6.1 Document types seen

| Type | Format | Notes |
|---|---|---|
| Property budget files | `.xlsx` | Multi-tab. Key tab often "Historical Comp." with year columns. Per-property. Some password-protected. |
| Historical financials | `.xlsx` | KA internal accounting roll-ups; year columns at fixed offsets |
| Monthly owner's reports | `.pdf` | 3P manager reports (Cushman, VG, Level 10). PDF — needs OCR/table extraction. Sometimes truncated. |
| Financial packages | `.msg` | Outlook emails *with attachments* — package files delivered by 3P managers |
| Rent roll analyses | `.xlsx` | Unit-level; used for unit mix + in-place rents |
| Valuation analyses | `.xlsx` | Internal KA valuation models |
| Broker Opinion of Value (BOV) | `.pdf` | Third-party brokerage (e.g., SLIB for Arbors) |
| Strategic overviews | `.pdf` | Internal narrative docs |
| Loan documents | `.pdf` | Loan agreements — covenant terms, guaranties, DSCR/Debt Yield definitions |

### 6.2 Known extraction gotchas

- **`.msg` files** contain the real payload as attachments — must extract attachments, not just body text. (Body text *can* matter though — e.g., the Arbors update email had the current strategic state in the body.)
- **Mislabeled files** — one "430 2022 Financials" file was actually wrong; a `revised_430_2022_actuals.xlsx` superseded it. Platform should support supersede/version logic.
- **Truncated PDFs** — some Cushman monthly reports came through truncated; extraction needs to detect incompleteness.
- **Password-protected xlsx** — at least one budget file (`2026_CBL_Budget_FINAL_11_10_25_protected.xlsx`).
- **Fixed-offset year columns** — historical financial xlsx files put year data at regular column offsets (e.g., every 4 columns); not always with clean headers.
- **Partial-year reporting** — refi years produce partial-year 3P statements that aren't comparable to full years; must be flagged and annualized or excluded.
- **Combined res + retail** — some assets (Larking) have retail; 3P reports sometimes combine, sometimes separate. Must track which.

### 6.3 Source files referenced in this chat (uploads)

Budget / financials: `2026_Budget_HQA_*.xlsx`, `2026_CBL_Budget_FINAL_*_protected.xlsx`, `FNP_2026_Budget_*.xlsx`, `MOR_2026_Budget_*.xlsx`, `430_Oak_Grove_2026_Budget*.xlsx`, `430_20{21,23,24}_Financials.xlsx`, `430_Oak_Grove_2025_Financials.xlsx`, `revised_430_2022_actuals.xlsx`, `VG_Larking_w-HQ_Split_*.xlsx`, `Arbors_Senior_-_Historical_Financials.xlsx`, `Arbors_at_Ridges_2026_Plan_-_FINAL_APPROVED.xlsx`

Valuation: `HQ_Apartments_-_Valuation_Analysis_*.xlsx`, `Arbors_at_Ridges_-_Valuation_Analysis_*.xlsx`, `Arbors_at_Ridges_BOV.pdf`

Rent rolls: `Chamberlain_Rent_Roll_Analysis_*.xlsx`

3P monthly reports / packages: `December_20{21,22,23,24}_Monthly_*Report*_The_Larking.pdf`, `The_Larking_December_20{24,25}_Financial_Package.msg`, `430_Oak_Owner__LLC_-_Q1_2026_Financials.msg`, `Weekly_Residential___Sr__Housing_Reports_4-26-26.msg`

Narrative / strategic: `Arbors_Strategic_Overview_12_29_25.pdf`, `p_ARBORS_AT_RIDGES.pdf`, `RE__Arbors.msg`, `2022_Budget-Arbors.pdf`

Bundles: `KA_Financials.zip`, plus prior handoff zips.

---

## 7. Arbors strategic situation (context only — active, sensitive)

The Arbors doc is a *strategic* document, not just a financial overview. The platform doesn't need to act on this, but the partner should understand the asset is in an active decision process:

- **NOI deterioration** since 2018 (margin 33%→16%) driven by service-revenue shift and nursing-cost growth.
- **Bremer Bank loan** has a hard 1.25x DSCR covenant projected to be breached on 2025 financials.
- **SLIB BOV (Dec 2025):** $13.0M–$13.9M. Fairview Buyer Investment floor ~$12.7–12.8M. KA working target ~$14.5M.
- **Operator situation:** Ebenezer disengaged on owner-driven NOI work; Ebenezer planning a purchase LOI in 2026. Operator alternatives evaluated — Lifespark/Vincent (won't 3P manage, would only buy with equity), Silvercrest (pitched ~$600K NOI uplift, viewed skeptically).
- **Ebenezer purchase rights expire EOY 2027** — equity transfer before then triggers them.
- **Recommended sequencing:** (1) get Ebenezer's number, (2) internal valuation huddle, (3) approach Lifespark/Vincent, (4) management transition only if needed.

---

## 8. Document voice / style constraints (if the partner generates docs too)

- Audience: KA Inc. **and** Engelsma (ELP) fund participants — neutral framing; "ownership/guarantor exposure" generically, not "KA Inc. exposure" alone.
- Tables and brief bullets over narrative prose.
- Don't over-footnote — definitions and one-time-item explanations belong in an addendum, not stacked under every table.
- KA red branding: `KA_RED=B22234`, `KA_RED_DARK=8B1A28`; Arial; 0.7" margins; portrait Letter.
- The deliverables should read as the asset manager's own work product — clean, professional, not obviously machine-generated.

---

## 9. Files included in this handoff zip

- `HANDOFF_SUMMARY.md` — this file
- `build_board.js` — Leadership Board doc build script (reference for table structures + final numbers)
- `build_arbors.js` — Arbors overview build script
- `KA_Residential_Leadership_Board_Q2_2026.docx` — final board doc
- `KA_Arbors_Strategic_Overview_Q2_2026.docx` — draft Arbors doc
- `validated_data/` — the reconciliation JSON files (`property_noi_FINAL.json`, `oak_final.json`, `larking_third_party_vs_ka.json`, `validated_per_asset_noi.json`, `historical_comp_validated.json`, `property_btl_items.json`, `per_asset_unit_mix.json`, `historical_pl.json`, `final_property_noi.json`, `property_noi_cleansed.json`, `property_noi_final.json`)
- `journal.txt` — chronological journal of prior chat sessions (v1–v7 build history)

> **Caveat on validated JSONs:** these are *intermediate working files* from across the project. Where they conflict with the FINAL tables in Sections 4.1–4.2 of this document, **the tables in this document win.** The most notable known discrepancy: Five 90 Park 2024A is $322K final (the JSONs may show ~$626-639K pre-reconciliation).
