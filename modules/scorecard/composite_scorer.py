"""
composite_scorer.py — Module 6 of the CoStar Market Scorecard Engine

The final aggregation layer that produces composite market scores and rankings.

Pipeline (matches the Z Score Summary Pull workbook):

  For each property class (tier):
    For each market:
      For each period (Annual, 2Yr, 5Yr, 10Yr):
        1. Compute TOTAL Z per metric (signal × cat_adj × vol_adj)
        2. Compute Overall D&S (weighted avg → period adj)
        3. Compute Overall Rent Growth (weighted avg → period adj)
        4. Compute Overall MF Fundamental (momentum → blend D&S + Rent)
      Duration-weight the MF Fundamental across periods

  Then: tier-weight across property classes → final score → rank
"""

import pandas as pd
import numpy as np
from typing import Optional
from pathlib import Path


# ---------------------------------------------------------------------------
# Main pipeline: compute composite scores
# ---------------------------------------------------------------------------

def compute_composite_scores(
    all_detail_results: dict,
    classifications: pd.DataFrame,
    peer_groups: dict = None,      # kept for API compat, not used
    report_quarter: str = "2025 Q4",
    config=None,
    property_classes: list[str] | None = None,
    peer_group_markets: set = None,
    external_quarterly_data: dict = None,
) -> dict:
    """
    Run the full scoring pipeline from detail results to final rankings.

    This orchestrates:
      1. z_score_engine.build_tilt_engine_input() — per-tier Z-score prep
      2. tilt_engine.score_all_markets()           — per-market scoring
      3. tilt_engine.compute_final_rankings()       — tier-weighted ranking

    Parameters
    ----------
    all_detail_results      : dict of metric_key -> compute_detail() output
    classifications         : DataFrame from market_classifier
    peer_groups             : (unused, kept for backward compatibility)
    report_quarter          : the report quarter
    config                  : ScorecardConfig from tilt_engine (or None for defaults)
    property_classes        : list of tiers to score (default: all 4)
    peer_group_markets      : set of market names for Z-score peer group filtering
    external_quarterly_data : dict of {metric_key: DataFrame(market × quarter)}
                              from external_data_integrator.load_external_data().
                              Injects FRED/Census data into the scoring pipeline.

    Returns
    -------
    dict: {
        "final_scores": Series (market -> composite score),
        "rankings": DataFrame (ranked markets with scores and details),
        "tier_scores": dict of {tier: {market_id: MarketScore}},
        "tier_rankings": DataFrame per tier,
        "config": ScorecardConfig used,
    }
    """
    from .tilt_engine import (
        ScorecardConfig, DEFAULT_CONFIG,
        score_all_markets, compute_final_rankings,
    )
    from .z_score_engine import build_tilt_engine_input

    if config is None:
        config = DEFAULT_CONFIG

    if property_classes is None:
        property_classes = list(config.tier_weights.keys())

    # --- Score each tier ---
    all_tier_data = {}  # {tier: {market: {period: {...}}}}

    for prop_class in property_classes:
        print(f"\n  Scoring tier: {prop_class}")

        try:
            market_data = build_tilt_engine_input(
                all_detail_results, prop_class, config,
                peer_group_markets=peer_group_markets,
                external_quarterly_data=external_quarterly_data,
            )
            if market_data:
                all_tier_data[prop_class] = market_data
                print(f"    Prepared {len(market_data)} markets")
            else:
                print(f"    No market data for {prop_class}")
        except Exception as e:
            print(f"    WARNING: Failed to build input for {prop_class}: {e}")
            continue

    if not all_tier_data:
        print("  No tiers produced data — returning empty results")
        return {
            "final_scores": pd.Series(dtype=float),
            "rankings": pd.DataFrame(),
            "tier_scores": {},
            "config": config,
        }

    # --- Run tilt_engine scoring across all tiers ---
    print(f"\n  Running tilt engine across {len(all_tier_data)} tiers...")
    tier_scores = score_all_markets(all_tier_data, config)

    # --- Compute tier-weighted final rankings ---
    print(f"  Computing final rankings...")
    rankings_df = compute_final_rankings(tier_scores, config)

    # Extract final scores as a Series
    if not rankings_df.empty:
        final_scores = rankings_df.set_index("market_id")["final_score"]
    else:
        final_scores = pd.Series(dtype=float)

    # Merge classification data into rankings
    rankings = enrich_rankings(rankings_df, classifications)

    # Print summary
    if not rankings.empty:
        print(f"\n  Scored {len(rankings)} markets")
        print(f"  Score range: [{final_scores.min():.4f}, {final_scores.max():.4f}]")
        print(f"  Mean: {final_scores.mean():.4f}, Median: {final_scores.median():.4f}")

    return {
        "final_scores": final_scores,
        "rankings": rankings,
        "tier_scores": tier_scores,
        "config": config,
    }


# ---------------------------------------------------------------------------
# Rankings enrichment
# ---------------------------------------------------------------------------

def enrich_rankings(
    rankings_df: pd.DataFrame,
    classifications: pd.DataFrame,
) -> pd.DataFrame:
    """
    Add classification data (tier, region) to the rankings DataFrame.
    """
    if rankings_df.empty:
        return rankings_df

    # The tilt_engine rankings have: market_id, final_score, ds_score,
    # rent_score, tier_* columns, rank
    df = rankings_df.copy()

    # Add percentile
    if "final_score" in df.columns and len(df) > 0:
        df["percentile"] = df["final_score"].rank(pct=True)

    # Merge classification info
    if "market" in classifications.columns:
        class_cols = ["market"]
        for col in ["inventory_tier", "general_region", "specific_region"]:
            if col in classifications.columns:
                class_cols.append(col)

        df = df.merge(
            classifications[class_cols],
            left_on="market_id",
            right_on="market",
            how="left",
        )
        if "market" in df.columns and "market_id" in df.columns:
            df = df.drop(columns=["market"])

    return df


# ---------------------------------------------------------------------------
# Scenario comparison
# ---------------------------------------------------------------------------

def compare_scenarios(
    all_detail_results: dict,
    classifications: pd.DataFrame,
    scenarios: dict[str, dict],
    report_quarter: str = "2025 Q4",
) -> pd.DataFrame:
    """
    Run multiple scoring scenarios and compare the results side-by-side.

    Parameters
    ----------
    scenarios : dict of scenario_name -> dict with optional 'config' key
                containing a ScorecardConfig instance

    Returns
    -------
    DataFrame with market as index, columns for each scenario's score
    """
    results = {}

    for scenario_name, params in scenarios.items():
        print(f"\n--- Running scenario: {scenario_name} ---")
        output = compute_composite_scores(
            all_detail_results,
            classifications,
            report_quarter=report_quarter,
            config=params.get("config"),
        )
        results[scenario_name] = output["final_scores"]

    comparison = pd.DataFrame(results)
    if not comparison.empty:
        comparison = comparison.sort_values(
            comparison.columns[0], ascending=False
        )
        for col in comparison.columns:
            comparison[f"{col}_rank"] = comparison[col].rank(
                ascending=False
            ).astype(int)

    return comparison


# ---------------------------------------------------------------------------
# Score explanation (for UI "why this score?" feature)
# ---------------------------------------------------------------------------

def explain_score(
    market: str,
    all_detail_results: dict,
    classifications: pd.DataFrame,
    report_quarter: str = "2025 Q4",
    config=None,
    external_quarterly_data: dict = None,
) -> dict:
    """
    Generate a detailed explanation of how a market's composite score
    was computed, suitable for display in the web UI.

    Returns a structured breakdown at each level of aggregation.
    """
    from .tilt_engine import DEFAULT_CONFIG
    from .z_score_engine import build_tilt_engine_input

    if config is None:
        config = DEFAULT_CONFIG

    result = {"market": market, "tiers": {}}

    for prop_class in config.tier_weights:
        try:
            market_data = build_tilt_engine_input(
                all_detail_results, prop_class, config,
                external_quarterly_data=external_quarterly_data,
            )
        except Exception:
            continue

        if market not in market_data:
            continue

        from .tilt_engine import score_market_all_periods
        ms = score_market_all_periods(market_data[market], config)

        tier_detail = {
            "final_score": ms.final_score,
            "duration_weighted_ds": ms.duration_weighted_ds,
            "duration_weighted_rent": ms.duration_weighted_rent,
            "periods": {},
        }

        for period, ps in ms.period_scores.items():
            tier_detail["periods"][period] = {
                "ds_raw": ps.overall_ds_raw,
                "ds_adj": ps.overall_ds_adj,
                "rent_raw": ps.overall_rent_raw,
                "rent_adj": ps.overall_rent_adj,
                "mf_fundamental": ps.overall_mf,
                "tilt_value": ps.tilt_value,
                "ds_metrics": {
                    k: {
                        "signal_z": v.signal_z,
                        "volatility_z": v.volatility_z,
                        "category_z": v.category_z,
                        "total_z": v.total_z,
                    }
                    for k, v in ps.ds_metric_z.items()
                },
                "rent_metrics": {
                    k: {
                        "signal_z": v.signal_z,
                        "volatility_z": v.volatility_z,
                        "category_z": v.category_z,
                        "total_z": v.total_z,
                    }
                    for k, v in ps.rent_metric_z.items()
                },
            }

        result["tiers"][prop_class] = tier_detail

    return result


# ---------------------------------------------------------------------------
# Export to Excel for validation
# ---------------------------------------------------------------------------

def export_validation_workbook(
    output: dict,
    output_path: str = "scorecard_validation.xlsx",
):
    """
    Export the full scoring results to an Excel workbook for
    cell-by-cell comparison against the original workbooks.
    """
    output_path = Path(output_path)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        rankings = output.get("rankings", pd.DataFrame())
        if not rankings.empty:
            rankings.to_excel(writer, sheet_name="Rankings", index=False)

        final_scores = output.get("final_scores", pd.Series())
        if not final_scores.empty:
            final_scores.to_frame("score").to_excel(
                writer, sheet_name="Final Scores"
            )

    print(f"Validation workbook saved: {output_path}")


# ---------------------------------------------------------------------------
# Main — standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from .tilt_engine import ScorecardConfig

    print("Composite Scorer — Module 6 (Integrated with tilt_engine)")
    print("=" * 60)

    cfg = ScorecardConfig()
    print(f"\nTier weights: {cfg.tier_weights}")
    print(f"Period weights: {cfg.period_weights}")
    print(f"D&S weight: {cfg.ds_weight}, Rent weight: {cfg.rg_weight}")
    print(f"Category indicator: {cfg.category_indicator}")
    print(f"Volatility indicator: {cfg.volatility_indicator}")
    print(f"Period indicator: {cfg.period_indicator}")
    print(f"Momentum knob: {cfg.mom_knob}")

    # Validate weight sums
    tier_sum = sum(cfg.tier_weights.values())
    period_sum = sum(cfg.period_weights.values())
    cat_sum = cfg.ds_weight + cfg.rg_weight
    print(f"\nWeight validation:")
    print(f"  Tier weights sum:   {tier_sum:.2f} {'✓' if abs(tier_sum - 1.0) < 0.01 else '✗'}")
    print(f"  Period weights sum: {period_sum:.2f} {'✓' if abs(period_sum - 1.0) < 0.01 else '✗'}")
    print(f"  Category sum:       {cat_sum:.2f} {'✓' if abs(cat_sum - 1.0) < 0.01 else '✗'}")

    print("\nComposite scorer ready.")
