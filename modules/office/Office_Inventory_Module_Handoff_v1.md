# Office Inventory Z-Score Module — Handoff (v1)

**Comprehensive Ownership Platform · Office Asset Class · Minneapolis-St. Paul MSA**
**Date: 2026-05-19 · Status: built, internally verified · Supersedes: nothing (new asset-class layer)**

*First non-multifamily asset class in the comprehensive platform. Parallel to the multifamily inventory engine: same Z-score methodology, same binding rules, same bitemporal-ready fact structure. Two grains — office property (supply) and office tenant (demand) — joined on `PropertyID`. No platform numbers modified; all figures traceable to the two source ingestions.*

---

## 0. The 60-second orientation

The office module mirrors the multifamily inventory engine one asset class over. It scores office properties **tier-relative within Building Class A/B/C** (the office analog of the multifamily quality tier), across **seven co-equal peer lenses** (none privileged), with a **demographic Z-layer kept per-cohort and uncollapsed** (mirroring the multifamily unit-mix co-equal treatment). It adds a genuinely new grain multifamily does not have: a **tenant/occupancy layer** matched to buildings at 99.6%, which is the office demand-side picture.

Everything is observational/statistical. No recommendations, no invented metrics, gaps `src`-flagged not papered over — the multifamily binding rules carried verbatim.

---

## A. The two source ingestions (as received)

| Source | File | Rows | Role | knowledge_date |
|---|---|---|---|---|
| Office property | `Minneapolis_Office_Inventory_5_19_26.xlsx` | 5,033 | Supply grain — one row per building | 2026-05-19 |
| Office tenant | `Costar_Export__5_.zip` (9 identical-schema batches) | 4,197 → 4,100 | Demand grain — one row per tenant-in-space, ≥5,800 SF floor | 2026-05-19 |

Both recorded in `raw_ingestion_log` with sha256 file hashes (immutability proof, per the national-schema Zone-A contract). 97 exact batch-overlap duplicate tenant rows removed on full-row match.

**The user's highlight scheme (property file), decoded:** green (77) + dark-green (2) = reference / peer-grouping keys databased for all properties; blue (133) = Z-score targets; orange (2, leasing company) = reference. Theme colors resolved from the workbook theme XML (accent1 `#4F81BD` = blue, accent3 `#9BBB59` = green).

---

## B. Architecture (user-confirmed decisions, 2026-05-19)

| Decision | Resolution |
|---|---|
| **Primary tier** | Building Class A/B/C, tier-relative, no cross-class composite. F + missing (n=13) = flagged residual cohort, context-only — NOT folded into C |
| **Peer lenses** | Seven, co-equal, independent (none privileged): Building Status, Construction Material, Tenancy, Secondary/Center type, Submarket, owner-occupied band (tenant-derived), sector-concentration band (tenant-derived). Every property carries one Z per lens per measure |
| **Building Status** | Every cohort scored **within its own cohort** (Existing vs Existing, Proposed vs Proposed) — a peer lens, not an inclusion filter. This is the more honest treatment |
| **Demographics** | ~83 CoStar trade-area columns (2020/2025/2030 within 1 mi) scored **per-cohort, NOT collapsed to a composite** (mirrors MF unit-mix co-equal cuts). 2030 tagged `projected`, 2020/2025 `observed` — so a future backtest never treats a projection as having been knowable |
| **Min-N collapse** | Universal. Cell with n < 30 collapses up to Class-only and is flagged `collapsed__*=1`. A thin cell yields a clearly-labelled wider-peer Z, never a noisy fake one. This is the platform's existing James–Stein / `MIN_N` discipline, not a new invention |
| **Nulls** | Construction Material (480) and Tenancy (160) → explicit `(unknown)` peer cell, `src`-flagged, never imputed |

---

## C. The Z-engine (faithful to platform methodology)

Replicated, not reinvented: **Signal Z** = within-peer standardized level `(x − μ)/σ` (sample σ; n<2 or σ=0 → NULL by construction, honest not zero-filled). Each measure's Signal Z within a lens is its category score; lenses are co-equal. Verified valid: rent Z within status has mean −0.003, std 0.998 (≈0/≈1 as required).

Eight performance measures scored: rent $/SF/yr midpoint, SF-derived vacancy, parking ratio, stories, typical floor size, taxes/SF, tenant-occupied-SF (demand grain, ≥5,800 floor), tenant sector HHI. The property rent string (`"$14.00 - 15.00 (Est.)"`) is parsed to lo/hi/mid with the **CoStar `(Est.)` flag preserved** (86% of parsed rents are estimated — divergence disclosed, not collapsed). Sparse source `Vacancy %` (16.9%) is reported **alongside** an SF-derived vacancy, not replaced (dollar-vs-ratio-style divergence disclosed per binding rule).

---

## D. What the warehouse contains — `office_inventory.duckdb` (7 tables)

| Table | Rows | What it is |
|---|---|---|
| `office_property` | 5,033 | Supply grain + all Z-scores, peer-n, collapse flags, parsed rent, tenant rollups |
| `office_tenant` | 2,768 | Demand grain — office tenants matched to office properties |
| `office_tenant_rollup` | 1,579 | Per-building tenant aggregates (≥5,800 SF FLOOR — never a full census) |
| `office_demographic_z` | 417,739 | Per-cohort demographic Z, long/tidy, `vintage_kind` observed/projected |
| `fact_office_panel` | 27,706 | Bitemporal long/tidy facts (period + knowledge_date) — same shape as MF PoC `fact_property_panel` |
| `office_peer_health` | 161 | Transparency artifact: every Class×lens cell with n and engine-stable flag |
| `raw_ingestion_log` | 2 | Zone-A audit spine with file hashes |

---

## E. Coverage & honest constraints (flagged, not papered over)

- **Tenant join: 2,822 / 2,834 office tenant rows = 99.6%** onto the property DB. The non-office ~1,375 tenant rows (Industrial/Flex/Retail/Specialty/Health Care) are the *other asset classes to come* — correct exclusion, not a join failure.
- **1,603 / 5,033 office buildings (31.8%) have ≥1 matched tenant.** The other 68% are **sub-floor by construction** (the tenant file's hard 5,800 SF cutoff excludes small/owner-occupied/sub-floor space). Every tenant-derived building metric is a **floor, never a complete occupancy census**, and is labelled as such.
- **Thin-cell reality is real and quantified.** Class A = 347 props, sparse under any second split. Of 161 Class×lens cells, 83 are engine-stable (n≥30), 78 require collapse. Collapse rates by lens are stored per-row (`collapsed__*`) and summarized in `office_peer_health`. Vacancy-based Z's inherit the 16.9–20% source fill — coverage is stated per measure×lens, never inflated.
- **2030 demographics are CoStar projections**, isolated as `vintage_kind='projected'` (171,122 rows) vs `observed` (246,617). Scoreable but never silently treated as knowable history.
- Residual tier (13 F/none props) flagged `tier_is_residual`, context-only.

---

## F. How this connects to the comprehensive platform

This is the office instance of the same two-grain structure the national-schema spec defines (a property panel + a finer panel joined by a shared key). The tenant layer is the office analog of the multifamily Tier-S dependent-variable upgrade — it adds occupancy/demand reality (sector mix, owner-occ share, rollover) that the asset-only export cannot see. The demographic layer is built **append-only**: the forthcoming office-specific demographic data and any census/FRED-style external joins attach to `office_demographic_z` without re-architecting — the same additive, supersession-disciplined principle as the bitemporal Zone-B design. As industrial / retail / flex / medical / hospitality / land follow the same pattern, the platform converges toward the full-market picture that feeds the multifamily predictive layer.

---

## G. Open items / next steps

1. **External office demographic + census/FRED layer** — the user-flagged additional office-specific demographics attach to `office_demographic_z` as new `demo_col` rows (append-only, no schema change).
2. **Bitemporal migration** — `fact_office_panel` is already long/tidy with period+knowledge_date; it slots into the same Zone-A/B/C migration the national schema spec defines for multifamily. Currently one snapshot (no time depth yet — same honest constraint as the MF panel).
3. **Tenant lease-economics enrichment** — `Signed`/`Commencement`/`Future Move` are sparse (41–55%); rollover-exposure metrics are derivable but coverage-limited and should be labelled as such when built.
4. Next asset class (industrial/retail/flex) reuses `build_office_module.py` as the template — only the peer-lens config and measure map change.

---

## H. Geo/macro layer — INTEGRATED (added 2026-05-19)

The full office-specific external set was pulled by Claude Code (network-enabled) and integrated. Six long/tidy bitemporal sources, 126,058 rows, stacked into `office_geo_panel`.

| Source | Rows | Span | knowledge_date | Honest constraint (disclosed, not hidden) |
|---|---|---|---|---|
| FRED (DGS10, DFF, BAA10Y, CRE proxy, MSP CPI) | 63,310 | 1917→2026 | initial-release realtime_start | CRE series flagged national proxy, not MSP |
| ACS B19013 tract income | 5,853 | 4 vintages 2017–2024 | **actual release dates looked up** (2020-24 = 2026-01-08, not nominal Dec) | full tract resolution retained; NO cross-vintage differencing |
| Census CBP estab/emp by NAICS | 1,870 | 2021–2023 | vintage release where obtainable | 1,246 KD NULL (not authoritatively datable) — flagged not faked |
| BLS QCEW emp by NAICS | 52,595 | 2021–2025 | CSV-slice Last-Modified | single-vintage (slices don't expose prelim→final history) |
| BLS LAUS unemployment | 2,142 | 2021–2026 | **NULL** (BLS v1 has no release field; schedule page 403'd) | not fabricated — the correct call |
| Census BPS permits | 288 | 2017–2025 | file Last-Modified | county-grain (same limit MF carries) |

**Property→geo linkage: county-grain, 5,033/5,033 mapped (100%, zero attrition).** All six macro sources are county/MSA-native, so county integration loses nothing for them. A name-format bug (`"St  Croix"` vs `"St. Croix"`) that would have silently dropped 118 in-MSP Wisconsin properties was caught and fixed via normalization — exactly the coverage-gap class the binding rules exist to prevent.

**SUPERSESSION (FULLY RESOLVED 2026-05-19):** the property→**tract-true** point-in-polygon join is **DONE for the entire MSA**. After the initial MN shapefile (4,783/4,915) and the WI supplement (TIGER `tl_2023_55_tract` + ACS state-55 counties 55093/55109), placement is now **4,915 / 4,915 distinct properties = 100.0%, zero attrition** — matching the multifamily `property_tract` gold standard exactly. Every property has `tract_join_status='tract_true'`; the 132 Wisconsin properties (Pierce + St. Croix) moved from county-grain fallback to tract-true. Tract-true **supersedes** county-grain; `office_property_county` is retained as the documented fallback. **Payoff realized full-MSA:** property→tract→ACS exposes an **11.6× intra-Hennepin** office income gradient ($21K–$250K) and tighter **1.9×/1.7× St.Croix/Pierce** WI gradients (exurban homogeneity — sensible). A first-run row inflation (236 rows from the known 118-duplicate-PropertyID condition) and a WI-county mislabel were caught by source-verification and fixed. The WI supplement was a closed scope gap (not a silent backfill): knowledge_dates reused verbatim from the original ACS releases, schema/dtype-identical, sha256-verified zip, logged as ingestion #5.

New tables: `office_geo_panel`, `office_property_county`, `office_property_tract` (tract-true), `office_acs_income_county`, `office_tenant_naics_bridge`, `office_bldg_sector_exposure`. `raw_ingestion_log` now 4 ingestions (property, tenant, geo, tract-true). The geo layer is append-only: forthcoming office-specific demographic data attaches with zero re-architecting.

**No open structural items remain.** Honest carried limits (per binding rules, documented not hidden): QCEW single-vintage (no prelim→final depth — same "no time depth yet" constraint as the MF panel); LAUS knowledge_date NULL (BLS v1 has no release field); BPS/QCEW/LAUS/CBP county-grain (no intra-county variation — same limit MF carries for BPS). The previously-noted WI tract gap is **CLOSED** (supplement integrated; 100% tract placement). Remaining limits are not fixable from available sources; all flagged, none fabricated.

---

## I. Final warehouse contents (13 tables, 595,829 rows)

| Table | Rows | Grain / role |
|---|---|---|
| `office_property` | 5,033 | Supply grain + all Z-scores, peer-n, collapse flags, parsed rent |
| `office_property_tract` | 4,915 | **Tract-true** property→census-tract, 100% placement (MN+WI) |
| `office_property_county` | 5,033 | County-grain fallback (retained, documented) |
| `office_tenant` | 2,768 | Demand grain — office tenants matched to properties |
| `office_tenant_rollup` | 1,579 | Per-building tenant aggregates (≥5,800 SF floor) |
| `office_tenant_naics_bridge` | 2,797 | Tenant↔county-sector-employment link (84.7% matched) |
| `office_bldg_sector_exposure` | 1,579 | SF-weighted sector-employment-trend per building |
| `office_demographic_z` | 417,739 | Per-cohort demographic Z; observed/projected tagged |
| `office_geo_panel` | 126,158 | 6-source bitemporal macro panel (FRED/ACS incl. WI/CBP/QCEW/LAUS/BPS) |
| `office_acs_income_county` | 356 | ACS county rollup per vintage incl. WI (no differencing) |
| `fact_office_panel` | 27,706 | Bitemporal long/tidy facts (period + knowledge_date) |
| `office_peer_health` | 161 | Transparency: every Class×lens cell, n, engine-stable flag |
| `raw_ingestion_log` | 5 | Append-only audit spine w/ knowledge dates |

---

*Module build only. No platform numbers modified or asserted. Built against the inspected source ingestions + the Claude-Code geo pull (provenance audited against its MANIFEST). Z-methodology faithful to the platform engine; binding rules carried (no invented metrics; observational; gaps src-flagged; tier-relative; co-equal cuts; supersession explicit; knowledge-date governance preserved including honest NULLs).*
