# KA Portfolio Warehouse → Capactive Integration Assessment
2026-07-27 · Written after package verification (214/214 ALL PASS, hash-verified landing)

## What Riley built (survey findings)

`portfolio_warehouse.db` (543 MB SQLite, 249 tables) is not just the lease/ownership layer the
handoff focuses on — it is a **13-module portfolio data warehouse**, registered in `wh_module`:

| # | Module | Prefix | Scale |
|---|--------|--------|-------|
| 1 | Rent Roll | `rr_` | portfolio-wide |
| 2 | Budgets | `bud_` | 280K budget lines |
| 3 | CAM Reconciliation | `recon_`/`cam_` | 5,871 controls, 13.7K expenses |
| 4 | Property Tax | `tax_`/`mn_` | statements + MN specifics |
| 5 | Insurance | `ins_` | |
| 6 | Overview/Aggregation | `ovr_` | 228K fund-consolidation rows, CoStar pulls |
| 7 | Capex & Amortization | `capex_` | 36K amort schedule rows |
| 8 | Rent Statements | `rr_stmt_` | 150K statement lines |
| 9 | Bad Debt | `bd_` | |
| 10 | Vacancy | `vac_` | 7.5K suites, 4.8K property rows |
| 11 | **Ownership Documents** | `own_`/`lease_`/`prop_` | 13 properties, 48,088 page-cited provisions |
| 12 | Historical Financials | `fin_` | 1.6M GL transactions, 640K monthly op-statement rows, 66 properties × 10 FY |
| 13 | Loans / Closing Files | `loan_` | 38 properties, 60 instruments, 923 docs |

Spine: `dim_property` (85 rows: property_key ↔ entity_code ↔ owning entity, fund, product type,
lender, manager) with `dim_alias` (284-row crosswalk) bridging the two-key system.
Open items: `wh_open_item` (6,779 rows — honest-gap register, severity vocabulary is inconsistent:
HIGH/MED/low/warn/DEFECT/blocker… normalize at display time, never rewrite the data).

Lease layer shape (the module we integrate first):
- `lease_lease` (34 cols: tenant_key, suite, trade/legal names, status, sf, dates, base rent…)
- `lease_provision` (property_id, tenant_key, category, detail, source, source_pages…) — categories
  are both article-level verbatim (Article 1: PREMISES…) and a ~23-category provision sweep
  (ROFR, exclusives, co-tenancy, opex caps, early termination, parking regimes…), every row page-cited
- supplemental: rent steps, renewal options, lease chains, suite occupancy/history, terminations
- property-level docs: `prop_document` / `prop_document_page_text` / `prop_provision` (REAs,
  easements, ground/master leases)

## Integration approach (recommended phasing)

**Principle: Capactive reads the master READ-ONLY.** All writes happen through Riley's
aggregation workflow (merge harnesses + verify suite) on the file directly. The app never
touches it — that keeps the 214-check guarantee intact and the two workflows decoupled.

**Phase A — Portfolio Ownership module (start here).**
New Capactive module following the established office-module pattern (own engine + blueprint +
standalone dark UI):
- Engine: SQLite read-only against the master; per-request/thread-local connections
  (same concurrency lesson as the DuckDB engines).
- `/portfolio-ownership/` — property list off `dim_property`, showing which properties have the
  lease layer populated (13 today, grows per merge).
- Property page — lease roster (current/former/development status), provision browser filtered by
  tenant/category with page citations, near-term rollovers, property-level document layer.
- Portfolio open-items register — `wh_open_item` with normalized severity, filterable by module
  and property. This alone is a high-value surface (6.8K tracked gaps incl. the §5 HIGH items).
- Provision search across properties (48K provisions, e.g. "every exclusive-use clause portfolio-wide").

**Phase B — additional module surfaces.** Each wh_module maps to a page/tab as needed:
financial trends (fin_), loan abstracts (loan_), vacancy (vac_), budgets vs actuals (bud_ + fin_),
CAM recon (recon_). Prioritize by what KA (or the eventual client) asks to see.

**Phase C — registry alignment.** Map the 85-property spine into Capactive's
Fund/Sub-fund/Portfolio/Property registry (the Phase-0 hierarchy work), honoring the two-key
system via dim_alias. This makes KA a "deal group" alongside Barrington/Southtown/Midway rather
than a silo.

**Refresh model:** the module re-opens the DB per request (or watches file mtime) so a completed
merge (Gateway, Southtown update…) appears in the UI without code changes.

## Tie-in: per-client module activation (tiers)

Patrick's requirement: not all modules for all clients; paid tiers; admin-controlled activation.
Groundwork already in Capactive: per-org `FeatureFlags` in config.py (`get_org_features`),
`/admin/license` + `/admin/permissions` routes, `NOTES_multi_tenant_licensing.md`,
`SPEC_layer3_admin_panel.md`. The KA warehouse's own `wh_module` registry shows the natural
gating unit: module IDs. Proposed direction (to be designed when ready — task #68):
- A module manifest in Capactive (id, name, blueprint, tier) covering ALL modules
  (market analytics, deal modules, portfolio ownership surfaces…).
- Org-level activation set stored with the org profile; enforced at blueprint route level
  (decorator) + nav rendering (hide inactive).
- Admin UI: per-org checklist grouped by tier; changes audit-logged.

## Risks / notes
- 543 MB SQLite read-only is fine for serving; index checks needed per query pattern
  (e.g. lease_provision(property_id, tenant_key), wh_open_item(module_id)).
- Never ship this DB in the repo (.gitignore already excludes *.db).
- The aggregation duties (handoff §8) are a separate operating role from the app: merges,
  guards, ALL PASS gates. In-flight: Gateway (3 spine keys), Southtown update (mode-declared),
  5 queued builds that predate the rotated-scan OCR gate.
- Severity/case normalization for wh_open_item display; do not mutate source rows.
