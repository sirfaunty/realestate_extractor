#!/usr/bin/env python3
"""
Initial data load for the Capactive analytical warehouse.

Loads:
  1. National inventory master parquet → dim_property (189K properties)
  2. 5 sample market z-scores → fact_property_zscore
  3. 5 sample market peer stats → fact_peer_group_stats
  4. Sales comp transactions → fact_sales_transaction
  5. Cap rate aggregates (clean + all) → fact_cap_rate_aggregate
  6. Pricing aggregates → fact_pricing_aggregate
  7. Ownership history → fact_ownership

Usage:
    python3 warehouse/load_initial_data.py [--data-dir outputs/national_inventory/...]
"""

import os
import sys
import glob
import logging

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warehouse.engine import WarehouseEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('load_initial_data')

# ─── Paths ─────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# National inventory sample
INVENTORY_DIR = os.environ.get(
    'INVENTORY_DIR',
    os.path.join(PROJECT_ROOT, 'outputs', 'national_inventory',
                 'US National Inventory (Market sample)')
)

# Sales comps outputs
SALES_DIR = os.environ.get(
    'SALES_DIR',
    os.path.join(PROJECT_ROOT, 'outputs', 'sales_comps',
                 'package', 'handoff', 'outputs')
)

KNOWLEDGE_DATE = '2024-08-31'  # Aug 2024 vintage
SALES_KNOWLEDGE_DATE = '2025-05-01'  # Sales comps pipeline date

SAMPLE_MARKETS = ['Abilene', 'Akron', 'Albany', 'Albuquerque', 'Alexandria']


def load_all():
    db_path = os.environ.get('WAREHOUSE_DB', None)
    wh = WarehouseEngine(db_path=db_path) if db_path else WarehouseEngine()
    wh.connect()

    # Clear any previous loads for a clean start
    logger.info("Clearing previous data for clean reload...")
    for table in ['fact_ownership', 'fact_pricing_aggregate', 'fact_cap_rate_aggregate',
                  'fact_sales_transaction', 'fact_peer_group_stats',
                  'fact_property_zscore', 'dim_property', 'raw_ingestion_log']:
        try:
            wh.conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    # Reset sequences
    for seq in ['seq_ingestion_id', 'seq_property_key', 'seq_market_key']:
        try:
            wh.conn.execute(f"DROP SEQUENCE IF EXISTS {seq}")
            wh.conn.execute(f"CREATE SEQUENCE {seq} START 1")
        except Exception:
            pass

    # ─── 1. Master inventory parquet ─────────────────────────────────
    master_parquet = os.path.join(INVENTORY_DIR, 'multifamily_properties.parquet')
    if os.path.exists(master_parquet):
        logger.info(f"Loading master inventory: {master_parquet}")
        n = wh.load_inventory_parquet(master_parquet, KNOWLEDGE_DATE, 'Aug 2024')
        logger.info(f"  → {n:,} properties loaded into dim_property")
    else:
        logger.warning(f"Master parquet not found: {master_parquet}")

    # ─── 2. Z-scores by market ───────────────────────────────────────
    total_zscores = 0
    for market in SAMPLE_MARKETS:
        zpath = os.path.join(INVENTORY_DIR, market, 'zscores_long.parquet')
        if os.path.exists(zpath):
            n = wh.load_zscore_parquet(zpath, KNOWLEDGE_DATE)
            total_zscores += n
            logger.info(f"  {market}: {n:,} z-score rows")
    logger.info(f"  → Total z-scores: {total_zscores:,}")

    # ─── 3. Peer group stats ────────────────────────────────────────
    total_peers = 0
    for market in SAMPLE_MARKETS:
        ppath = os.path.join(INVENTORY_DIR, market, 'peer_group_stats.parquet')
        if os.path.exists(ppath):
            n = wh.load_peer_stats_parquet(ppath, KNOWLEDGE_DATE)
            total_peers += n
            logger.info(f"  {market}: {n:,} peer stat rows")
    logger.info(f"  → Total peer stats: {total_peers:,}")

    # ─── 4. Sales transactions ──────────────────────────────────────
    txn_csv = os.path.join(SALES_DIR, 'merged_warehouse', 'transactions.csv')
    if os.path.exists(txn_csv):
        logger.info(f"Loading sales transactions: {txn_csv}")
        n = wh.load_sales_comps_csv(txn_csv, SALES_KNOWLEDGE_DATE)
        logger.info(f"  → {n:,} transactions loaded")
    else:
        logger.warning(f"Transactions CSV not found: {txn_csv}")

    # ─── 5. Cap rate aggregates ─────────────────────────────────────
    cap_dir_clean = os.path.join(SALES_DIR, 'cap_rate_aggregates_clean')
    cap_dir_all = os.path.join(SALES_DIR, 'cap_rate_aggregates_all')

    cap_loads = [
        # (path, granularity, period_type, is_clean)
        (os.path.join(cap_dir_clean, 'market_year.csv'), 'market', 'year', True),
        (os.path.join(cap_dir_clean, 'market_quarter.csv'), 'market', 'quarter', True),
        (os.path.join(cap_dir_clean, 'national_quarter.csv'), 'national', 'quarter', True),
        (os.path.join(cap_dir_clean, 'submarket_quarter.csv'), 'submarket', 'quarter', True),
        (os.path.join(cap_dir_clean, 'property_class.csv'), 'national_by_class', 'year', True),
        (os.path.join(cap_dir_all, 'market_year.csv'), 'market', 'year', False),
        (os.path.join(cap_dir_all, 'market_quarter.csv'), 'market', 'quarter', False),
        (os.path.join(cap_dir_all, 'national_quarter.csv'), 'national', 'quarter', False),
        (os.path.join(cap_dir_all, 'submarket_quarter.csv'), 'submarket', 'quarter', False),
        (os.path.join(cap_dir_all, 'property_class.csv'), 'national_by_class', 'year', False),
    ]

    total_caps = 0
    for path, gran, ptype, clean in cap_loads:
        if os.path.exists(path):
            n = wh.load_cap_rate_csv(path, SALES_KNOWLEDGE_DATE, gran, ptype, clean)
            total_caps += n
            label = 'clean' if clean else 'all'
            logger.info(f"  cap_rate {label} {gran}/{ptype}: {n:,} rows")
    logger.info(f"  → Total cap rate rows: {total_caps:,}")

    # ─── 6. Pricing aggregates ──────────────────────────────────────
    pricing_dir = os.path.join(SALES_DIR, 'pricing_layer')
    pricing_loads = [
        (os.path.join(pricing_dir, 'national_year.csv'), 'national'),
        (os.path.join(pricing_dir, 'market_year.csv'), 'market'),
        (os.path.join(pricing_dir, 'market_class_year.csv'), 'market_by_class'),
        (os.path.join(pricing_dir, 'market_vintage_year.csv'), 'market_by_vintage'),
        (os.path.join(pricing_dir, 'national_class_vintage.csv'), 'national_by_class_vintage'),
        (os.path.join(pricing_dir, 'submarket_class_year.csv'), 'submarket_by_class'),
    ]

    total_pricing = 0
    for path, gran in pricing_loads:
        if os.path.exists(path):
            n = wh.load_pricing_csv(path, SALES_KNOWLEDGE_DATE, gran)
            total_pricing += n
            logger.info(f"  pricing {gran}: {n:,} rows")
    logger.info(f"  → Total pricing rows: {total_pricing:,}")

    # ─── 7. Ownership history ───────────────────────────────────────
    ownership_csv = os.path.join(SALES_DIR, 'ownership_layer', 'ownership_history.csv')
    if os.path.exists(ownership_csv):
        logger.info(f"Loading ownership history: {ownership_csv}")
        ingestion_id = wh.register_ingestion(
            source='ownership_pipeline',
            knowledge_date=SALES_KNOWLEDGE_DATE,
            file_path=ownership_csv,
        )
        wh.conn.execute(f"""
            INSERT INTO fact_ownership
                (property_id, owner_canonical, acquisition_date, disposition_date,
                 acquisition_price, disposition_price, hold_months,
                 is_current, knowledge_date, ingestion_id)
            SELECT
                "property_id",
                "owner_company_canonical",
                TRY_CAST("acquired_date" AS DATE),
                TRY_CAST("disposed_date" AS DATE),
                TRY_CAST("acquisition_price" AS DOUBLE),
                TRY_CAST("disposition_price" AS DOUBLE),
                TRY_CAST("hold_months" AS INTEGER),
                CASE WHEN "is_current_owner" = 'True' THEN true ELSE false END,
                '{SALES_KNOWLEDGE_DATE}',
                {ingestion_id}
            FROM read_csv('{ownership_csv}', auto_detect=true, all_varchar=true)
        """)
        n = wh.conn.execute(f"""
            SELECT count(*) FROM fact_ownership WHERE ingestion_id = {ingestion_id}
        """).fetchone()[0]
        wh.conn.execute(
            "UPDATE raw_ingestion_log SET record_count = ? WHERE ingestion_id = ?",
            [n, ingestion_id]
        )
        logger.info(f"  → {n:,} ownership records loaded")

    # ─── Summary ────────────────────────────────────────────────────
    summary = wh.summary()
    logger.info("=" * 60)
    logger.info("WAREHOUSE LOAD COMPLETE")
    logger.info("=" * 60)
    for k, v in summary.items():
        label = k.replace('_', ' ').title()
        logger.info(f"  {label:40s} {v:>12,}")

    # Quick validation queries
    logger.info("")
    logger.info("─── Validation Queries ───")

    # Markets loaded
    markets = wh.conn.execute("""
        SELECT market, count(*) as n
        FROM dim_property
        WHERE market IS NOT NULL
        GROUP BY market
        ORDER BY n DESC
        LIMIT 10
    """).fetchall()
    logger.info(f"Top markets by property count:")
    for m, n in markets:
        logger.info(f"  {m:30s} {n:>8,}")

    # Z-score coverage
    zscore_markets = wh.conn.execute("""
        SELECT p.market, count(DISTINCT z.property_id) as scored_props
        FROM fact_property_zscore z
        JOIN dim_property p ON z.property_id = p.property_id
        GROUP BY p.market
    """).fetchall()
    logger.info(f"\nZ-score coverage by market:")
    for m, n in zscore_markets:
        logger.info(f"  {m:30s} {n:>8,} scored properties")

    # Sales comp markets
    sc_markets = wh.conn.execute("""
        SELECT market, count(*) as n,
               CAST(median(sale_price) AS BIGINT) as med_price
        FROM fact_sales_transaction
        WHERE market IS NOT NULL AND sale_price IS NOT NULL
        GROUP BY market
        ORDER BY n DESC
        LIMIT 10
    """).fetchall()
    logger.info(f"\nTop sales comp markets:")
    for m, n, p in sc_markets:
        logger.info(f"  {m:30s} {n:>6,} deals  median ${p:>12,}")

    wh.close()
    logger.info("\nDone. Warehouse file: data/warehouse.duckdb")


if __name__ == '__main__':
    load_all()
