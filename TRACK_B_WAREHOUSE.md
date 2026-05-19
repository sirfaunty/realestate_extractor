# Track B: Analytical Warehouse — Module integration in build order

## Status
Phase 1 DONE: DuckDB warehouse loaded with 8.4M rows (188K properties, 5.6M z-scores, 23K sales, 27K cap rates, 37K ownership). Flask blueprint live at `/warehouse` with 11 API endpoints + dashboard.

**Stale WAL fix:** Delete `data/warehouse.duckdb` + `data/warehouse.duckdb.wal`, then run `python3 warehouse/load_initial_data.py` to rebuild (~15s).

## Build Order

### 1. Inventory Module
Wire the national inventory z-score engine as a platform module.
- Partner code: `outputs/national_inventory/US National Inventory (Market sample)/`
- Key files: `zscore_engine.py`, `micro_market_zscores.py`
- Data: 189K properties, 21 peer cuts, ~150-265 metrics per property
- Register as `modules/inventory/` using AbstractModule pattern

### 2. Sales Comps Module
Wire the sales comp pipeline into the platform with query UI.
- Partner code: `outputs/sales_comps/package/handoff/`
- 9 pipeline scripts, 23K pre-computed transactions
- Query UI for comp search by market/property/date
- Register as `modules/sales_comps/`

### 3. Submarket Scorecard ✅
- Module: `modules/scorecard/` — registered in INSTALLED_MODULES
- `tilt_engine.py`: Full 11-step scoring pipeline ported from partner's code (ScorecardConfig, asymmetric adjustments, bounded multipliers, 3-category model: D&S/Occ/Rent, momentum decay, duration-weighted aggregation, batch scoring, rankings)
- `engine.py`: Warehouse-backed query + scoring layer (score_from_warehouse, market metrics builder, score storage, drill-down explanations, scenario comparison)
- `routes.py`: Flask blueprint at `/scorecard` — dashboard with rankings, market detail pages, config explorer, 8 API endpoints (rankings, market, explain, history, config, score, scenario)
- New warehouse table: `fact_market_score` (market scores with period/tier breakdown)
- Successfully scored 217 markets from warehouse data (cap rates + pricing + transactions)
- **Partner file mapping:** All 16 files had content shifted by one filename position (e.g., `minneapolis.json` contains `tilt_engine.py`, `submarket_orchestrator.py` contains `run_scorecard.py`, etc.)
- **Note:** Occupancy signals require CoStar quarterly exports (not yet loaded). D&S and Rent scoring work from existing warehouse data.

### 4. Lease Analysis ✅
- Module: `modules/lease_analysis/` — registered in INSTALLED_MODULES
- `models.py`: Reconstructed all 8 missing partner modules (1,100+ lines) — LeaseRecord, Assumption, downtime_table, BreakevenAssumptions/Result, breakeven_for_unit_type, AvailabilitySnapshot, snapshot_from_counts, ForwardExposureSnapshot, effective_exposure_pct, build_forward_exposure, VelocitySnapshot, compute_velocity, portfolio_velocity, AskingAchievedGap, compute_gap, SeasonalIndex, build_seasonality_table, PricingResult, PricingAssumptions, price_unit_type, price_all
- `engine.py`: SQLite-backed query layer — LeaseAnalysisEngine loads rent roll + financial terms, builds LeaseRecords, computes break-even floors, runs full 7-layer pricing pipeline (floor → scarcity → velocity → gap → seasonality → combine → cap)
- `routes.py`: Flask blueprint at `/leases` — dashboard with property summaries, property detail page, full pricing analysis page, 4 API endpoints (properties, summary, pricing, rent_roll)
- 7-layer pricing model: break-even floor + weighted scarcity/velocity/gap/seasonality signals → capped premium (±6%) → posture recommendation (push/hold/concede)
- **Note:** Hedonic intrinsic model (layer 7b) reserved for future — requires CoStar hedonic exports not yet loaded. Current pipeline uses 6 active layers.

### 5. Market Intelligence
- Partner code: `outputs/brokerage/warehouse/market_intel/`
- 8 pipeline scripts, 18 source PDFs, PPTX template
- Depends on scorecard

## Architecture
- **Module pattern:** `modules/` dir, `AbstractModule` base class, `ModuleRegistry` auto-discovery
- **INSTALLED_MODULES** list in `modules/__init__.py`
- **Warehouse engine:** `warehouse/engine.py` — `WarehouseEngine` class with bulk loaders + query API
- **Property identity bridge:** `MD5(address+city+state)` links SQLite ↔ CoStar ↔ warehouse
- **Bitemporal Zone A/B/C:** Every fact row has `knowledge_date` + `ingestion_id` provenance

## Dev Commands
- `CAPACTIVE_DEV_MODE=1 python3 run.py --port 8080`
- Always use `python3` not `python`
