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

### 3. Submarket Scorecard
- Partner code: `outputs/scorecard/`
- **NOTE:** All 14 files are mislabeled (shifted by one filename). Z-score functions merged into tilt_engine code.
- Depends on inventory module

### 4. Lease Analysis
- Partner code: `outputs/lease_analysis_tool/`
- 7 modules missing from partner
- Hedonic intrinsic model can run on 883 Larking leases
- Depends on inventory

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
