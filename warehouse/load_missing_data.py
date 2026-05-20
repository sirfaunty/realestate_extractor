#!/usr/bin/env python3
"""
Supplemental loader — loads dim_property + sales/cap/pricing/ownership
into an existing warehouse that already has z-scores and peer stats.

Usage:
    python3 warehouse/load_missing_data.py
"""

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warehouse.engine import WarehouseEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('load_missing')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DATE = '2024-08-31'
SALES_KNOWLEDGE_DATE = '2025-05-01'


def run():
    wh = WarehouseEngine()
    wh.connect()

    # Check current state
    s = wh.summary()
    logger.info("Current warehouse state:")
    for k, v in s.items():
        logger.info(f"  {k:40s} {v:>12,}")

    # ─── 1. Master inventory → dim_property ─────────────────────────
    if s.get('dim_property', 0) == 0:
        master = os.path.join(PROJECT_ROOT, 'data', 'multifamily_properties.parquet')
        if os.path.exists(master):
            logger.info(f"Loading master inventory: {master}")
            n = wh.load_inventory_parquet(master, KNOWLEDGE_DATE, 'Aug 2024')
            logger.info(f"  → {n:,} properties loaded into dim_property")
        else:
            logger.warning(f"Master parquet not found at {master}")
    else:
        logger.info(f"dim_property already has {s['dim_property']:,} rows — skipping")

    # ─── 2. Sales comps data ────────────────────────────────────────
    sales_dir = os.path.join(PROJECT_ROOT, 'data', 'sales_comps_outputs')
    if not os.path.isdir(sales_dir):
        logger.warning(f"Sales dir not found: {sales_dir}")
        logger.info("Skipping sales, cap rates, pricing, ownership")
    else:
        # Transactions
        if s.get('fact_sales_transaction', 0) == 0:
            txn_csv = os.path.join(sales_dir, 'merged_warehouse', 'transactions.csv')
            if os.path.exists(txn_csv):
                logger.info(f"Loading transactions: {txn_csv}")
                n = wh.load_sales_comps_csv(txn_csv, SALES_KNOWLEDGE_DATE)
                logger.info(f"  → {n:,} transactions loaded")

        # Cap rates
        if s.get('fact_cap_rate_aggregate', 0) == 0:
            cap_dir_clean = os.path.join(sales_dir, 'cap_rate_aggregates_clean')
            cap_dir_all = os.path.join(sales_dir, 'cap_rate_aggregates_all')
            cap_loads = [
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
            total = 0
            for path, gran, ptype, clean in cap_loads:
                if os.path.exists(path):
                    n = wh.load_cap_rate_csv(path, SALES_KNOWLEDGE_DATE, gran, ptype, clean)
                    total += n
                    label = 'clean' if clean else 'all'
                    logger.info(f"  cap_rate {label} {gran}/{ptype}: {n:,} rows")
            logger.info(f"  → Total cap rate rows: {total:,}")

        # Pricing
        if s.get('fact_pricing_aggregate', 0) == 0:
            pricing_dir = os.path.join(sales_dir, 'pricing_layer')
            pricing_loads = [
                (os.path.join(pricing_dir, 'national_year.csv'), 'national'),
                (os.path.join(pricing_dir, 'market_year.csv'), 'market'),
                (os.path.join(pricing_dir, 'market_class_year.csv'), 'market_by_class'),
                (os.path.join(pricing_dir, 'market_vintage_year.csv'), 'market_by_vintage'),
                (os.path.join(pricing_dir, 'national_class_vintage.csv'), 'national_by_class_vintage'),
                (os.path.join(pricing_dir, 'submarket_class_year.csv'), 'submarket_by_class'),
            ]
            total = 0
            for path, gran in pricing_loads:
                if os.path.exists(path):
                    n = wh.load_pricing_csv(path, SALES_KNOWLEDGE_DATE, gran)
                    total += n
                    logger.info(f"  pricing {gran}: {n:,} rows")
            logger.info(f"  → Total pricing rows: {total:,}")

        # Ownership
        if s.get('fact_ownership', 0) == 0:
            ownership_csv = os.path.join(sales_dir, 'ownership_layer', 'ownership_history.csv')
            if os.path.exists(ownership_csv):
                logger.info(f"Loading ownership: {ownership_csv}")
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

    # ─── Final summary ──────────────────────────────────────────────
    s = wh.summary()
    logger.info("=" * 60)
    logger.info("WAREHOUSE STATE AFTER SUPPLEMENT")
    logger.info("=" * 60)
    for k, v in s.items():
        logger.info(f"  {k:40s} {v:>12,}")

    wh.close()
    logger.info("Done.")


if __name__ == '__main__':
    run()
