"""
external_data_integrator.py — External Data Integration Module

Bridges FRED (building permits) and Census ACS (age cohort demographics)
data into the CoStar Market Scorecard scoring pipeline.

The CoStar engine's z_score_engine expects quarterly metric data as
market × quarter DataFrames. This module:

  1. Loads FRED SF permit data (monthly → quarterly, ~108 markets)
  2. Loads Census ACS renter-weighted population data (~284 markets, annual → Q4)
  3. Pivots both into market × quarter matrices
  4. Caps extreme Census YoY values (CBSA boundary redefinitions cause artifacts)
  5. Returns a dict of {metric_key: DataFrame} ready for z_score_engine injection

Supported external metrics:
  - sf_permits_yoy:          SF building permit YoY change (supply pressure)
  - renter_weighted_pop_yoy: Renter-weighted population YoY change (demand signal)
  - pop_20_34_share:         Share of population aged 20-34 (peak renter cohort)

Usage:
    from external_data_integrator import load_external_data
    ext_data = load_external_data(fred_api_key="your_key")
    # ext_data is {metric_key: DataFrame(market × quarter)}
    # Pass to build_tilt_engine_input(external_quarterly_data=ext_data)
"""

import logging
import numpy as np
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Cap extreme Census YoY values at ±50%.
# CBSA boundary redefinitions (e.g., Clarksville TN +796%) create artifacts
# that would dominate Z-scores if uncapped.
CENSUS_YOY_CAP = 0.50


# ---------------------------------------------------------------------------
# FRED Permit Data — pivot to market × quarter matrix
# ---------------------------------------------------------------------------

def _load_fred_permit_matrices(fred_api_key: str) -> dict[str, pd.DataFrame]:
    """
    Load FRED SF permit data and pivot to market × quarter matrices.

    Returns dict with:
      - "sf_permits":     Quarterly SF permit counts (market × quarter)
      - "sf_permits_yoy": SF permit YoY % change (market × quarter)
    """
    from fred_data_loader import FREDDataLoader

    loader = FREDDataLoader(fred_api_key)
    df = loader.load_all_permit_data()

    if df is None or len(df) == 0:
        logger.warning("No FRED permit data loaded")
        return {}

    results = {}

    # Pivot each concept into a market × quarter matrix
    for concept, metric_key in [
        ("SF Building Permits", "sf_permits"),
        ("SF Permits YoY Change", "sf_permits_yoy"),
    ]:
        subset = df[df["concept"] == concept].copy()
        if len(subset) == 0:
            continue

        matrix = subset.pivot(index="market", columns="quarter", values="value")
        # Sort columns chronologically
        matrix = matrix.reindex(sorted(matrix.columns), axis=1)
        results[metric_key] = matrix
        logger.info(f"  FRED {metric_key}: {matrix.shape[0]} markets × "
                    f"{matrix.shape[1]} quarters")

    return results


# ---------------------------------------------------------------------------
# Census ACS Data — pivot to market × quarter matrix
# ---------------------------------------------------------------------------

def _load_census_matrices(
    census_api_key: Optional[str] = None,
    start_year: int = 2015,
    end_year: int = 2023,
) -> dict[str, pd.DataFrame]:
    """
    Load Census ACS age cohort data and pivot to market × quarter matrices.

    Census data is annual, mapped to Q4 of each reference year.
    YoY changes are capped at ±CENSUS_YOY_CAP to handle CBSA boundary
    redefinition artifacts.

    Returns dict with:
      - "renter_weighted_pop":     Renter-weighted population level
      - "renter_weighted_pop_yoy": Renter-weighted population YoY change (capped)
      - "pop_20_34_share":         Share of pop aged 20-34 (level)
      - "pop_20_34_share_yoy":     Share of pop aged 20-34 YoY change (capped)
    """
    from census_acs_loader import CensusACSLoader

    loader = CensusACSLoader(api_key=census_api_key)
    df = loader.load_multi_year(start_year=start_year, end_year=end_year)

    if df is None or len(df) == 0:
        logger.warning("No Census ACS data loaded")
        return {}

    results = {}

    concept_map = {
        "Renter-Weighted Population": "renter_weighted_pop",
        "Renter-Weighted Pop YoY Change": "renter_weighted_pop_yoy",
        "Share Age 20-34": "pop_20_34_share",
        "Share Age 20-34 YoY Change": "pop_20_34_share_yoy",
        "Population Age 20-34": "pop_20_34",
        "Pop Age 20-34 YoY Change": "pop_20_34_yoy",
    }

    for concept, metric_key in concept_map.items():
        subset = df[df["concept"] == concept].copy()
        if len(subset) == 0:
            continue

        matrix = subset.pivot(index="market", columns="quarter", values="value")
        matrix = matrix.reindex(sorted(matrix.columns), axis=1)

        # Cap YoY values to remove CBSA boundary artifacts
        if "yoy" in metric_key.lower():
            pre_cap = matrix.copy()
            matrix = matrix.clip(lower=-CENSUS_YOY_CAP, upper=CENSUS_YOY_CAP)
            n_capped = (pre_cap != matrix).sum().sum()
            if n_capped > 0:
                logger.info(f"  Census {metric_key}: capped {n_capped} extreme values "
                            f"at ±{CENSUS_YOY_CAP:.0%}")

        results[metric_key] = matrix
        logger.info(f"  Census {metric_key}: {matrix.shape[0]} markets × "
                    f"{matrix.shape[1]} quarters")

    return results


# ---------------------------------------------------------------------------
# Main entry point — load all external data
# ---------------------------------------------------------------------------

def load_external_data(
    fred_api_key: str,
    census_api_key: Optional[str] = None,
    census_start_year: int = 2015,
    census_end_year: int = 2023,
    include_fred: bool = True,
    include_census: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Load all external data sources and return as market × quarter matrices.

    This is the main entry point for integrating external data into the
    scoring pipeline. The returned dict can be passed directly to
    build_tilt_engine_input(external_quarterly_data=...).

    Parameters
    ----------
    fred_api_key      : FRED API key (required for permit data)
    census_api_key    : Census API key (optional, increases rate limits)
    census_start_year : First ACS year to load (default 2015)
    census_end_year   : Last ACS year to load (default 2023)
    include_fred      : Whether to load FRED permit data
    include_census    : Whether to load Census ACS data

    Returns
    -------
    dict: {metric_key: DataFrame(market × quarter)}
        Metric keys match the tilt_engine metric key convention.
        DataFrames have market names as index, quarter strings as columns.
    """
    all_data = {}

    if include_fred and fred_api_key:
        logger.info("Loading FRED permit data...")
        fred_data = _load_fred_permit_matrices(fred_api_key)
        all_data.update(fred_data)

    if include_census:
        logger.info("Loading Census ACS data...")
        census_data = _load_census_matrices(
            census_api_key,
            start_year=census_start_year,
            end_year=census_end_year,
        )
        all_data.update(census_data)

    # Summary
    total_markets = set()
    for key, matrix in all_data.items():
        total_markets.update(matrix.index.tolist())

    logger.info(f"\nExternal data summary:")
    logger.info(f"  Metrics loaded: {len(all_data)}")
    logger.info(f"  Total unique markets: {len(total_markets)}")
    for key, matrix in all_data.items():
        logger.info(f"    {key}: {matrix.shape[0]} markets, "
                    f"{matrix.shape[1]} quarters "
                    f"({sorted(matrix.columns)[0] if len(matrix.columns) > 0 else 'N/A'} → "
                    f"{sorted(matrix.columns)[-1] if len(matrix.columns) > 0 else 'N/A'})")

    return all_data


# ---------------------------------------------------------------------------
# Utility: get external metric coverage for a set of markets
# ---------------------------------------------------------------------------

def get_coverage_report(
    external_data: dict[str, pd.DataFrame],
    costar_markets: list[str],
) -> dict:
    """
    Report how many CoStar markets have external data coverage.

    Parameters
    ----------
    external_data  : output from load_external_data()
    costar_markets : list of CoStar market names (e.g., from classifications)

    Returns
    -------
    dict with coverage stats per metric
    """
    report = {}
    costar_set = set(costar_markets)

    for metric_key, matrix in external_data.items():
        ext_markets = set(matrix.index.tolist())
        overlap = costar_set & ext_markets
        report[metric_key] = {
            "external_markets": len(ext_markets),
            "costar_markets": len(costar_set),
            "overlap": len(overlap),
            "coverage_pct": len(overlap) / len(costar_set) * 100 if costar_set else 0,
            "missing": sorted(costar_set - ext_markets)[:10],  # first 10 missing
        }

    return report


# ---------------------------------------------------------------------------
# Main — standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    api_key = sys.argv[1] if len(sys.argv) > 1 else None
    if not api_key:
        print("Usage: python3 external_data_integrator.py <FRED_API_KEY>")
        sys.exit(1)

    print("Loading external data sources...")
    ext_data = load_external_data(fred_api_key=api_key)

    print(f"\nLoaded {len(ext_data)} metrics:")
    for key, matrix in ext_data.items():
        print(f"  {key}: {matrix.shape[0]} markets × {matrix.shape[1]} quarters")
        # Show a sample
        if len(matrix) > 0 and len(matrix.columns) > 0:
            last_col = sorted(matrix.columns)[-1]
            sample = matrix[last_col].dropna().head(5)
            print(f"    Sample ({last_col}):")
            for market, val in sample.items():
                print(f"      {market}: {val:.4f}")
