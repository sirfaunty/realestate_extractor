# Track B: Analytical Warehouse — Module integration in build order

## Status
**Phase 2 COMPLETE.** All 5 modules built and registered. DuckDB warehouse fully loaded: 815M rows (189K properties, 607M z-scores, 207M peer stats, 23K sales, 27K cap rates, 4.8K pricing, 37K ownership). 371 markets across all tables. Flask blueprints live for all modules.

**Stale WAL fix:** Delete `data/warehouse.duckdb` + `data/warehouse.duckdb.wal`, then run `python3 warehouse/load_initial_data.py` to rebuild.

## Build Order

### 1. Inventory Module ✅
- Module: `modules/inventory/` — `__init__.py`, `engine.py`, `routes.py`
- Blueprint at `/inventory` with property z-score lookup, peer group explorer
- Data: 189K properties, 21 peer cuts, ~150-265 metrics per property
- Backed by 607M z-score rows + 207M peer stats in warehouse

### 2. Sales Comps Module ✅
- Module: `modules/sales_comps/` — `__init__.py`, `engine.py`, `routes.py`
- Blueprint at `/comps` with transaction search, comp finder
- 23K pre-computed transactions, cap rate data

### 3. Submarket Scorecard ✅
- Module: `modules/scorecard/` — `__init__.py`, `engine.py`, `tilt_engine.py`, `routes.py`
- Blueprint at `/scorecard` — 11-step tilt engine scoring pipeline
- Scoring CLI: `python3 warehouse/score_markets.py` (run once to populate fact_market_score)
- 359 scoreable markets (cap rates ∩ sales)

### 4. Lease Analysis ✅
- Module: `modules/lease_analysis/` — `__init__.py`, `engine.py`, `models.py`, `routes.py`
- Blueprint at `/leases` — 7-layer pricing model
- Break-even floor → scarcity/velocity/gap/seasonality → capped premium → posture
- PricingResult + PricingAssumptions dataclasses in models.py

### 5. Market Intelligence ✅
- Module: `modules/market_intel/` — `__init__.py`, `engine.py`, `routes.py`
- Blueprint at `/market-intel` — dashboard with 371 markets, market brief, comparison
- 9 data surfaces, signal generation engine, market name resolution
- Integrates with scorecard when scores exist (gracefully handles un-scored state)

## Architecture
- **Module pattern:** `modules/` dir, `AbstractModule` base class, `ModuleRegistry` auto-discovery
- **INSTALLED_MODULES** list in `modules/__init__.py` (6 modules: proforma, inventory, sales_comps, scorecard, lease_analysis, market_intel)
- **Warehouse engine:** `warehouse/engine.py` — `WarehouseEngine` class with bulk loaders + query API
- **Property identity bridge:** `MD5(address+city+state)` links SQLite ↔ CoStar ↔ warehouse
- **Bitemporal Zone A/B/C:** Every fact row has `knowledge_date` + `ingestion_id` provenance

## Data Loading
- **Full load:** `python3 warehouse/load_initial_data.py` — loads everything (~15min for 350+ markets)
- **Supplement missing tables:** `python3 warehouse/load_missing_data.py` — fills dim_property, sales, cap rates, pricing, ownership without re-running z-scores
- **Score markets:** `python3 warehouse/score_markets.py` — run tilt engine across all 359 markets
- Data files: `data/multifamily_properties.parquet` (189K), `data/sales_comps_outputs/` (transactions, cap rates, pricing, ownership)

## Dev Commands
- `CAPACTIVE_DEV_MODE=1 python3 run.py --port 8080`
- Always use `python3` not `python`
- Clear stale pyc: `find modules/ -name __pycache__ -exec rm -rf {} +`
