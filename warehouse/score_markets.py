#!/usr/bin/env python3
"""
Score all markets using the tilt engine against warehouse data.

Usage:
    python3 warehouse/score_markets.py [--dry-run]

Requires the warehouse to be loaded with cap rates + sales transactions.
Results are stored in fact_market_score and printed to stdout.
"""

import os
import sys
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warehouse.engine import WarehouseEngine
from modules.scorecard.engine import ScorecardEngine
from modules.scorecard.tilt_engine import ScorecardConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('score_markets')


def run(dry_run: bool = False):
    wh = WarehouseEngine()
    wh.connect()

    se = ScorecardEngine(wh)

    # Check prerequisites
    cap_count = wh.conn.execute(
        "SELECT count(DISTINCT market) FROM fact_cap_rate_aggregate "
        "WHERE granularity = 'market'"
    ).fetchone()[0]
    sales_count = wh.conn.execute(
        "SELECT count(DISTINCT market) FROM fact_sales_transaction "
        "WHERE market IS NOT NULL"
    ).fetchone()[0]
    logger.info(f"Cap rate markets: {cap_count}")
    logger.info(f"Sales markets: {sales_count}")

    scoreable = se._get_scoreable_markets()
    logger.info(f"Scoreable (cap ∩ sales): {len(scoreable)}")

    if not scoreable:
        logger.error("No scoreable markets found — load cap rates and sales first")
        wh.close()
        return

    if dry_run:
        logger.info("Dry run — not scoring. Markets that would be scored:")
        for i, m in enumerate(scoreable, 1):
            logger.info(f"  {i:3d}. {m}")
        wh.close()
        return

    # Run scoring
    logger.info("Running tilt engine scoring...")
    result = se.score_from_warehouse()

    if 'error' in result:
        logger.error(f"Scoring failed: {result['error']}")
        wh.close()
        return

    logger.info(f"Scored {result['markets_scored']} markets")
    logger.info("")
    logger.info("=" * 70)
    logger.info("TOP 25 MARKETS")
    logger.info("=" * 70)
    logger.info(f"{'Rank':>4}  {'Market':<35} {'Score':>8}  {'D&S':>7}  {'Occ':>7}  {'Rent':>7}")
    logger.info("-" * 70)

    for entry in result.get('top_10', [])[:25]:
        logger.info(
            f"{entry.get('rank', '?'):>4}  "
            f"{entry.get('market_id', '?'):<35} "
            f"{entry.get('final_score', 0):>8.3f}  "
            f"{entry.get('ds_score', 0):>7.3f}  "
            f"{entry.get('occ_score', 0):>7.3f}  "
            f"{entry.get('rent_score', 0):>7.3f}"
        )

    # Verify storage
    stored = wh.conn.execute(
        "SELECT count(*) FROM fact_market_score WHERE score_type = 'final'"
    ).fetchone()[0]
    logger.info(f"\nStored {stored} final scores in fact_market_score")

    wh.close()
    logger.info("Done.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Score markets from warehouse data')
    parser.add_argument('--dry-run', action='store_true',
                        help='List scoreable markets without running scoring')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
