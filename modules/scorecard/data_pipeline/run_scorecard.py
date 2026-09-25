"""
run_scorecard.py — Main orchestrator for the CoStar Market Scorecard Engine

This is the entry point that runs the full scoring pipeline:
  1. Load CoStar data export (data_loader.py)
  2. Classify markets into tiers and regions (market_classifier.py)
  3. Compute all detail metrics with period averages (metric_calculator.py)
  4. For each tier: compute Signal Z, Volatility Z, Category Z (z_score_engine.py)
  5. Apply TOTAL Z, D&S/Rent aggregation, period/momentum tilts (tilt_engine.py)
  6. Tier-weight and rank (composite_scorer.py)

Usage:
    python run_scorecard.py <costar_export.xlsx> [--quarter "2025 Q4"]
"""

import sys
import time
import argparse
from pathlib import Path

import pandas as pd
import numpy as np


def run_full_pipeline(
    filepath: str,
    report_quarter: str = "2025 Q4",
    half_life: int = 8,
    output_dir: str = "output",
    property_classes: list[str] | None = None,
):
    """Run the complete scorecard pipeline."""

    start_time = time.time()

    # -------------------------------------------------------------------------
    # Step 1: Load data
    # -------------------------------------------------------------------------
    print("=" * 60)
    print("STEP 1: Loading CoStar Data Export")
    print("=" * 60)

    from data_loader import load_costar_export
    df = load_costar_export(filepath)

    # -------------------------------------------------------------------------
    # Step 2: Classify markets
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEP 2: Classifying Markets")
    print("=" * 60)

    from market_classifier import classify_markets
    classifications = classify_markets(df, report_quarter=report_quarter)

    print(f"\nClassified {len(classifications)} markets")

    # -------------------------------------------------------------------------
    # Step 3: Compute all detail metrics
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEP 3: Computing Detail Metrics")
    print("=" * 60)

    from metric_calculator import compute_detail, METRIC_DEFINITIONS

    all_detail_results = {}
    for metric_key in METRIC_DEFINITIONS:
        try:
            result = compute_detail(
                df, metric_key, classifications,
                report_quarter=report_quarter,
                half_life=half_life,
            )
            all_detail_results[metric_key] = result
        except Exception as e:
            print(f"\n  WARNING: Failed to compute {metric_key}: {e}")
            continue

    print(f"\nSuccessfully computed {len(all_detail_results)}/{len(METRIC_DEFINITIONS)} metrics")

    # -------------------------------------------------------------------------
    # Step 4-6: Score all tiers, apply tilts, produce rankings
    # -------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("STEPS 4-6: Scoring Pipeline (Z-scores → Tilts → Rankings)")
    print("=" * 60)

    from tilt_engine import ScorecardConfig
    from composite_scorer import compute_composite_scores

    config = ScorecardConfig()

    # Determine which property classes to score
    if property_classes is None:
        # Default: score all 4 tiers
        property_classes = list(config.tier_weights.keys())

    output = compute_composite_scores(
        all_detail_results,
        classifications,
        report_quarter=report_quarter,
        config=config,
        property_classes=property_classes,
    )

    # -------------------------------------------------------------------------
    # Results
    # -------------------------------------------------------------------------
    elapsed = time.time() - start_time

    print("\n" + "=" * 60)
    print(f"RESULTS — {report_quarter} Market Scorecard")
    print("=" * 60)

    rankings = output["rankings"]
    if not rankings.empty:
        # Display top and bottom markets
        display_cols = ["market_id", "final_score", "ds_score", "rent_score", "rank"]
        avail_cols = [c for c in display_cols if c in rankings.columns]
        if "percentile" in rankings.columns:
            avail_cols.append("percentile")

        print(f"\nTop 25 Markets:")
        print(rankings[avail_cols].head(25).to_string(index=False))

        print(f"\nBottom 10 Markets:")
        print(rankings[avail_cols].tail(10).to_string(index=False))

        print(f"\n--- Score Distribution ---")
        scores = output["final_scores"]
        print(f"  Count:  {len(scores)}")
        print(f"  Mean:   {scores.mean():.4f}")
        print(f"  Median: {scores.median():.4f}")
        print(f"  Std:    {scores.std():.4f}")
        print(f"  Min:    {scores.min():.4f}")
        print(f"  Max:    {scores.max():.4f}")

    print(f"\nTotal runtime: {elapsed:.1f} seconds")

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    if not rankings.empty:
        rankings_path = output_dir / f"scorecard_rankings_{report_quarter.replace(' ', '_')}.csv"
        rankings.to_csv(rankings_path, index=False)
        print(f"\nRankings exported: {rankings_path}")

    # Also export detailed validation workbook
    try:
        from composite_scorer import export_validation_workbook
        validation_path = output_dir / f"scorecard_validation_{report_quarter.replace(' ', '_')}.xlsx"
        export_validation_workbook(output, str(validation_path))
    except Exception as e:
        print(f"  Warning: Could not export validation workbook: {e}")

    return output


# ---------------------------------------------------------------------------
# Quick validation mode — just tests data loading and classification
# ---------------------------------------------------------------------------

def run_validation(filepath: str, report_quarter: str = "2025 Q4"):
    """Run a quick validation of the first 3 modules."""

    print("=" * 60)
    print("VALIDATION MODE")
    print("=" * 60)

    from data_loader import load_costar_export, lookup
    from market_classifier import classify_markets, get_summary_stats
    from metric_calculator import (
        compute_detail, METRIC_DEFINITIONS,
        quarter_to_index, index_to_quarter,
    )

    print("\n1. Loading data...")
    df = load_costar_export(filepath)

    print("\n2. Classifying markets...")
    classifications = classify_markets(df, report_quarter=report_quarter)
    stats = get_summary_stats(classifications)

    print(f"\n   Total markets: {stats['total_markets']}")
    for tier, info in stats["by_inventory_tier"].items():
        print(f"   {tier:15s}: {info['count']:3d} ({info['pct']:.1%})")

    print("\n3. Sample data validation (New York)...")
    ny = "New York - NY USA"

    checks = [
        ("Inventory Units", "All", 1_488_557),
        ("Vacancy Rate", "All", 0.033086),
        ("Population", "All", None),
        ("Market Asking Rent/Unit", "4 & 5 Star", 4571.79),
    ]

    for concept, prop_class, expected in checks:
        val = lookup(df, ny, prop_class, concept, report_quarter)
        status = ""
        if expected:
            if abs(val - expected) / expected < 0.01:
                status = "✓"
            else:
                status = f"✗ (expected {expected})"
        print(f"   {concept:30s} [{prop_class:12s}]: {val:>15,.2f} {status}")

    print("\n4. Computing Absorption Detail (All class only)...")
    absorption = compute_detail(
        df, "absorption", classifications,
        report_quarter=report_quarter,
        half_life=8,
    )

    # Validate period averages
    print("\n5. Period average validation (NY Absorption %)...")
    class_data = absorption["All"]
    period_avgs = class_data["period_averages"]

    if ny in period_avgs.index:
        for period in ["Yr 1", "Yrs 1-5", "Yrs 1-10", "Yrs 1-12"]:
            if period in period_avgs.columns:
                val = period_avgs.loc[ny, period]
                print(f"   {period:12s}: {val:.6f} ({val:.4%})")

    # Quick tilt engine smoke test
    print("\n6. Tilt engine smoke test...")
    from tilt_engine import ScorecardConfig, score_market_all_periods

    config = ScorecardConfig()
    test_data = {}
    for period in config.period_weights:
        test_data[period] = {
            "signal_indicators": {k: 1.0 for k in list(config.ds_metric_weights) + list(config.rent_metric_weights)},
            "volatility_indicators": {k: 0.5 for k in list(config.ds_metric_weights) + list(config.rent_metric_weights)},
            "ds_category_values": {k: [1.0] * 6 for k in config.ds_metric_weights},
            "rent_category_values": {k: [1.0] * 5 for k in config.rent_metric_weights},
            "ds_period_signal_z": 0.5,
            "rent_period_signal_z": 0.5,
            "tilt_value": 1.2,
        }

    ms = score_market_all_periods(test_data, config)
    print(f"   Test market MF score: {ms.final_score:.4f}")
    print(f"   D&S: {ms.duration_weighted_ds:.4f}, Rent: {ms.duration_weighted_rent:.4f}")

    print("\n✓ Validation complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CoStar Market Scorecard Engine"
    )
    parser.add_argument("filepath", help="Path to CoStar export Excel file")
    parser.add_argument(
        "--quarter", default="2025 Q4",
        help="Report quarter (default: 2025 Q4)"
    )
    parser.add_argument(
        "--half-life", type=int, default=8,
        help="Half-life parameter in quarters (default: 8)"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run validation mode only"
    )
    parser.add_argument(
        "--output", default="output",
        help="Output directory (default: output)"
    )
    parser.add_argument(
        "--tiers", nargs="+",
        default=None,
        help="Property classes to score (default: all 4 tiers)"
    )

    args = parser.parse_args()

    if args.validate:
        run_validation(args.filepath, args.quarter)
    else:
        run_full_pipeline(
            args.filepath,
            report_quarter=args.quarter,
            half_life=args.half_life,
            output_dir=args.output,
            property_classes=args.tiers,
        )
