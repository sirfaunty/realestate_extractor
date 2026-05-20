#!/bin/bash
# Run this from the realestate_extractor directory
# Usage: bash git_push.sh

set -e
cd "$(dirname "$0")"

# Clean up stale lock if present
rm -f .git/index.lock

echo "=== Commit 1: Module system + proforma + chamberlain engine ==="
git add .gitignore
git add modules/__init__.py modules/base.py
git add modules/proforma/__init__.py modules/proforma/bridge.py modules/proforma/routes.py
git add chamberlain/
git add web/templates/proforma_dashboard.html
git add database.py requirements.txt webapp.py
git add web/templates/properties.html web/templates/property_detail.html
git commit -m "Add module system, chamberlain engine, and proforma module

- Module pattern: AbstractModule base class + ModuleRegistry auto-discovery
- Vendored chamberlain proforma engine (Pydantic models, citation system)
- Proforma module with citation bridge (SQLite → chamberlain Cited[T])
- Drillback UI: every number traces to source document + verbatim text
- Updated webapp.py with module registration, property tabs
- Fixed sqlite3.Row .get() errors, column name mismatches in bridge"

echo ""
echo "=== Commit 2: DuckDB analytical warehouse ==="
git add warehouse/__init__.py warehouse/schema.sql warehouse/engine.py
git add warehouse/routes.py warehouse/load_initial_data.py
git commit -m "Add DuckDB analytical warehouse with bitemporal schema

Zone A/B/C architecture:
- Zone A: raw_ingestion_log (provenance for every data load)
- Zone B: dim_property (188K), fact_property_zscore (5.6M),
  fact_peer_group_stats (2.5M), fact_sales_transaction (23K),
  fact_cap_rate_aggregate (27K), fact_pricing_aggregate (4.8K),
  fact_ownership (37K)
- Zone C: convenience views (v_property_master, v_current_cap_rates)

Property identity bridge: MD5(address+city+state) links SQLite ↔ CoStar
Warehouse dashboard + REST API at /warehouse (11 endpoints)
Bulk loaders for parquet + CSV with DuckDB native read_parquet/read_csv"

echo ""
echo "=== Commit 3: Inventory + Sales Comps modules ==="
git add modules/inventory/__init__.py modules/inventory/engine.py modules/inventory/routes.py
git add modules/sales_comps/__init__.py modules/sales_comps/engine.py modules/sales_comps/routes.py
git commit -m "Add inventory and sales comps modules

Inventory module (/inventory, 12 routes):
- Property z-score benchmarking across 150+ metrics, 21 peer cuts
- Outlier detection (strengths/weaknesses split)
- Peer group explorer, market stats, property search
- Identity bridge API (Capactive SQLite ↔ CoStar warehouse)

Sales Comps module (/comps, 17 routes):
- Transaction search with multi-filter (market, year, price, class, buyer/seller)
- Comparable property finder (radius matching by units/vintage/class)
- Cap rate trend explorer (national + market, yearly + quarterly)
- Pricing analytics ($/unit, $/SF by class/vintage)
- Ownership history + owner portfolio views
- Market deep-dive with YoY trends, top buyers/sellers"

echo ""
echo "=== Commit 4: Track briefings ==="
git add TRACK_A_EXTRACTOR.md TRACK_B_WAREHOUSE.md
git commit -m "Add track briefing docs for parallel development

Track A: Document extractor training & accuracy
Track B: Warehouse module integration (build order mapped)"

echo ""
echo "=== Push to origin/main ==="
git push origin main

echo ""
echo "=== Create feature branches for parallel work ==="
git branch track-a/extractor-training
git branch track-b/module-integration
git push origin track-a/extractor-training
git push origin track-b/module-integration

echo ""
echo "Done. Three branches ready:"
echo "  main                        — current baseline (all code)"
echo "  track-a/extractor-training  — for extraction work"
echo "  track-b/module-integration  — for warehouse/module work"
echo ""
echo "To start working on a track:"
echo "  git checkout track-a/extractor-training"
echo "  git checkout track-b/module-integration"
