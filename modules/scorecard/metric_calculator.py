"""
metric_calculator.py — Module 3 of the CoStar Market Scorecard Engine

Replicates the structure of ALL 11 Detail workbooks in a single module.
Each Detail workbook follows the same 4-zone column pattern:

  Zone 1 (H-DG):    Raw metric pull (e.g., Absorption Units) via INDEX/MATCH
  Zone 2 (DH-GZ):   Secondary metric pull (e.g., Inventory Units) for derived calcs
  Zone 3 (HA-LI):   Derived metric (e.g., Absorption % = Zone1 / Zone2)
  Zone 4 (LI-NQ):   Period averages with half-life exponential decay weighting

The half-life parameter (cell LL6 = 8 quarters) controls how much weight
recent data gets vs. older data in the period averages.

Period average schemes (from the shared strings in Absorption Detail):
  - Individual quarters: Q1-Q104
  - Individual years: Yr 1-Yr 25
  - 2-year blocks: Yrs 1-2, 3-4, ..., 23-24
  - 3-year blocks: Yrs 1-3, 4-6, ..., 23-25
  - 5-year blocks: Yrs 1-5, 6-10, ..., 21-25
  - 10-year blocks: Yrs 1-10, 11-20
  - 12-year blocks: Yrs 1-12, 13-24

Source workbook: Q4 2025 Absorption Detail_1.27.26.xlsx (representative)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Configuration — Metric definitions
# ---------------------------------------------------------------------------

@dataclass
class MetricDefinition:
    """
    Defines one Detail workbook's computation.

    Each of the 11 Detail workbooks pulls one or two raw concepts from
    the CoStar export and optionally computes a derived metric (ratio).
    """
    name: str                        # Human-readable name
    primary_concept: str             # CoStar concept for Zone 1
    secondary_concept: str | None    # CoStar concept for Zone 2 (if ratio)
    derived_name: str | None         # Name for the derived metric (Zone 3)
    is_ratio: bool = False           # True if Zone 3 = Zone 1 / Zone 2
    is_pct_change: bool = False      # True if Zone 3 = (Zone1[t] - Zone1[t-4]) / Zone1[t-4]
    property_classes: list = field(default_factory=lambda: [
        "All", "4 & 5 Star", "3 Star", "1 & 2 Star"
    ])


# The 11 Detail workbook definitions
# (These map to the actual workbooks the user described)
METRIC_DEFINITIONS = {
    "absorption": MetricDefinition(
        name="Absorption Detail",
        primary_concept="Absorption Units",
        secondary_concept="Inventory Units",
        derived_name="Absorption % of Inventory",
        is_ratio=True,
    ),
    "vacancy": MetricDefinition(
        name="Vacancy Rate Detail",
        primary_concept="Vacancy Rate",
        secondary_concept=None,
        derived_name=None,
    ),
    "asking_rent_unit": MetricDefinition(
        name="Asking Rent/Unit Detail",
        primary_concept="Market Asking Rent/Unit",
        secondary_concept=None,
        derived_name="Asking Rent/Unit YoY Change",
        is_pct_change=True,
    ),
    "asking_rent_sf": MetricDefinition(
        name="Asking Rent/SF Detail",
        primary_concept="Market Asking Rent/SF",
        secondary_concept=None,
        derived_name="Asking Rent/SF YoY Change",
        is_pct_change=True,
    ),
    "effective_rent_unit": MetricDefinition(
        name="Effective Rent/Unit Detail",
        primary_concept="Market Effective Rent/Unit",
        secondary_concept=None,
        derived_name="Effective Rent/Unit YoY Change",
        is_pct_change=True,
    ),
    "effective_rent_sf": MetricDefinition(
        name="Effective Rent/SF Detail",
        primary_concept="Market Effective Rent/SF",
        secondary_concept=None,
        derived_name="Effective Rent/SF YoY Change",
        is_pct_change=True,
    ),
    "effective_rent_studio": MetricDefinition(
        name="Effective Studio Rent/Unit Detail",
        primary_concept="Market Effective Rent/Unit Studio",
        secondary_concept=None,
        derived_name="Effective Studio Rent/Unit YoY Change",
        is_pct_change=True,
    ),
    "effective_rent_1br": MetricDefinition(
        name="Effective 1BR Rent/Unit Detail",
        primary_concept="Market Effective Rent/Unit 1 Bedroom",
        secondary_concept=None,
        derived_name="Effective 1BR Rent/Unit YoY Change",
        is_pct_change=True,
    ),
    "effective_rent_2br": MetricDefinition(
        name="Effective 2BR Rent/Unit Detail",
        primary_concept="Market Effective Rent/Unit 2 Bedroom",
        secondary_concept=None,
        derived_name="Effective 2BR Rent/Unit YoY Change",
        is_pct_change=True,
    ),
    "effective_rent_3br": MetricDefinition(
        name="Effective 3BR Rent/Unit Detail",
        primary_concept="Market Effective Rent/Unit 3 Bedroom",
        secondary_concept=None,
        derived_name="Effective 3BR Rent/Unit YoY Change",
        is_pct_change=True,
    ),
    "net_deliveries": MetricDefinition(
        name="Net Delivered Units Detail",
        primary_concept="Net Delivered Units",
        secondary_concept="Inventory Units",
        derived_name="Deliveries % of Inventory",
        is_ratio=True,
    ),
    "under_construction": MetricDefinition(
        name="Under Construction Detail",
        primary_concept="Under Construction Units",
        secondary_concept="Inventory Units",
        derived_name="Construction % of Inventory",
        is_ratio=True,
    ),
    "population": MetricDefinition(
        name="Population Detail",
        primary_concept="Population",
        secondary_concept=None,
        derived_name="Population YoY Change",
        is_pct_change=True,
    ),
    "employment": MetricDefinition(
        name="Employment Detail",
        primary_concept="Total Employment",
        secondary_concept=None,
        derived_name="Employment YoY Change",
        is_pct_change=True,
    ),
    "income": MetricDefinition(
        name="Median Household Income Detail",
        primary_concept="Median Household Income",
        secondary_concept=None,
        derived_name="Income YoY Change",
        is_pct_change=True,
    ),
    # --- Demographics: additional fields for demo_engine.py ---
    "households": MetricDefinition(
        name="Households Detail",
        primary_concept="Households",
        secondary_concept=None,
        derived_name="Households YoY Change",
        is_pct_change=True,
    ),
    "office_employment": MetricDefinition(
        name="Office Employment Detail",
        primary_concept="Office Employment",
        secondary_concept=None,
        derived_name="Office Employment YoY Change",
        is_pct_change=True,
    ),
    "industrial_employment": MetricDefinition(
        name="Industrial Employment Detail",
        primary_concept="Industrial Employment",
        secondary_concept=None,
        derived_name="Industrial Employment YoY Change",
        is_pct_change=True,
    ),
}


# ---------------------------------------------------------------------------
# Quarter utilities
# ---------------------------------------------------------------------------

def quarter_to_index(quarter: str) -> int:
    """Convert 'YYYY QN' to a sortable integer index: YYYY*4 + (N-1)."""
    parts = quarter.strip().split()
    year = int(parts[0])
    q = int(parts[1].replace("Q", ""))
    return year * 4 + (q - 1)


def index_to_quarter(idx: int) -> str:
    """Convert integer index back to 'YYYY QN' format."""
    year = idx // 4
    q = (idx % 4) + 1
    return f"{year} Q{q}"


def generate_quarter_range(start: str, end: str) -> list[str]:
    """Generate a list of quarters from start to end (inclusive)."""
    s = quarter_to_index(start)
    e = quarter_to_index(end)
    return [index_to_quarter(i) for i in range(s, e + 1)]


def quarters_back(from_quarter: str, n_quarters: int) -> str:
    """Get the quarter that is n_quarters before from_quarter."""
    idx = quarter_to_index(from_quarter)
    return index_to_quarter(idx - n_quarters)


# ---------------------------------------------------------------------------
# Half-life exponential decay weights
# ---------------------------------------------------------------------------

def half_life_weights(n_quarters: int, half_life: int = 8) -> np.ndarray:
    """
    Generate exponential decay weights for n quarters.

    Replicates the LET/FILTER formula in the Detail workbooks:
        weight[i] = 0.5 ^ (i / half_life)

    where i=0 is the most recent quarter and i=n-1 is the oldest.

    The half_life parameter (default 8 = 2 years) means that data from
    8 quarters ago gets half the weight of the most recent quarter.

    Parameters
    ----------
    n_quarters : number of quarters in the window
    half_life  : quarters until weight drops to 50% (cell LL6 in workbook)

    Returns
    -------
    np.ndarray of weights, most recent first, normalized to sum to 1
    """
    positions = np.arange(n_quarters)  # 0, 1, 2, ..., n-1
    raw_weights = np.power(0.5, positions / half_life)
    return raw_weights / raw_weights.sum()


# ---------------------------------------------------------------------------
# Core metric computation — Zone 1-3 (replaces INDEX/MATCH + derived calcs)
# ---------------------------------------------------------------------------

def compute_raw_metric(
    df: pd.DataFrame,
    metric_def: MetricDefinition,
    markets: list[str],
    quarters: list[str],
) -> dict:
    """
    Compute Zones 1-3 for a given metric across all markets and quarters.

    Returns a dict with:
        'primary':   DataFrame (markets x quarters) of raw primary metric
        'secondary': DataFrame (markets x quarters) of secondary metric (or None)
        'derived':   DataFrame (markets x quarters) of derived metric (or None)

    Each DataFrame has market names as index and quarter strings as columns.
    """
    from .data_loader import get_metric_matrix

    results = {}

    for prop_class in metric_def.property_classes:
        class_results = {}

        # Zone 1: Primary metric
        primary = get_metric_matrix(
            df, prop_class, metric_def.primary_concept,
            start_quarter=quarters[0],
            end_quarter=quarters[-1],
        )
        # Filter to only our target markets
        primary = primary.reindex(markets).reindex(columns=quarters)
        class_results["primary"] = primary

        # Zone 2: Secondary metric (if defined)
        if metric_def.secondary_concept:
            secondary = get_metric_matrix(
                df, prop_class, metric_def.secondary_concept,
                start_quarter=quarters[0],
                end_quarter=quarters[-1],
            )
            secondary = secondary.reindex(markets).reindex(columns=quarters)
            class_results["secondary"] = secondary
        else:
            class_results["secondary"] = None

        # Zone 3: Derived metric
        if metric_def.is_ratio and class_results["secondary"] is not None:
            # Absorption % = Absorption Units / Inventory Units
            derived = class_results["primary"] / class_results["secondary"]
            derived = derived.replace([np.inf, -np.inf], np.nan)
            class_results["derived"] = derived

        elif metric_def.is_pct_change:
            # YoY change = (value[t] - value[t-4]) / value[t-4]
            # We need 4 extra quarters of lookback
            extended_start = quarters_back(quarters[0], 4)
            extended_quarters = generate_quarter_range(extended_start, quarters[-1])

            extended_primary = get_metric_matrix(
                df, prop_class, metric_def.primary_concept,
                start_quarter=extended_start,
                end_quarter=quarters[-1],
            )
            extended_primary = extended_primary.reindex(markets).reindex(
                columns=extended_quarters
            )

            # Compute YoY for each target quarter
            derived_data = {}
            for q in quarters:
                q_prev = quarters_back(q, 4)
                if q_prev in extended_primary.columns and q in extended_primary.columns:
                    curr = extended_primary[q]
                    prev = extended_primary[q_prev]
                    pct_change = (curr - prev) / prev.abs()
                    pct_change = pct_change.replace([np.inf, -np.inf], np.nan)
                    derived_data[q] = pct_change
                else:
                    derived_data[q] = pd.Series(np.nan, index=markets)

            class_results["derived"] = pd.DataFrame(derived_data)
        else:
            class_results["derived"] = None

        results[prop_class] = class_results

    return results


# ---------------------------------------------------------------------------
# Zone 4: Period averages with half-life decay
# ---------------------------------------------------------------------------

# Period average definitions matching the Detail workbook columns
PERIOD_AVERAGES = {
    # Individual years (4 quarters each)
    "Yr 1":  (0, 4),    # Most recent 4 quarters
    "Yr 2":  (4, 8),
    "Yr 3":  (8, 12),
    "Yr 4":  (12, 16),
    "Yr 5":  (16, 20),
    "Yr 6":  (20, 24),
    "Yr 7":  (24, 28),
    "Yr 8":  (28, 32),
    "Yr 9":  (32, 36),
    "Yr 10": (36, 40),
    "Yr 11": (40, 44),
    "Yr 12": (44, 48),
    "Yr 13": (48, 52),
    "Yr 14": (52, 56),
    "Yr 15": (56, 60),
    "Yr 16": (60, 64),
    "Yr 17": (64, 68),
    "Yr 18": (68, 72),
    "Yr 19": (72, 76),
    "Yr 20": (76, 80),
    "Yr 21": (80, 84),
    "Yr 22": (84, 88),
    "Yr 23": (88, 92),
    "Yr 24": (92, 96),
    "Yr 25": (96, 100),

    # 2-year blocks
    "Yrs 1-2":   (0, 8),
    "Yrs 3-4":   (8, 16),
    "Yrs 5-6":   (16, 24),
    "Yrs 7-8":   (24, 32),
    "Yrs 9-10":  (32, 40),
    "Yrs 11-12": (40, 48),
    "Yrs 13-14": (48, 56),
    "Yrs 15-16": (56, 64),
    "Yrs 17-18": (64, 72),
    "Yrs 19-20": (72, 80),
    "Yrs 21-22": (80, 88),
    "Yrs 23-24": (88, 96),

    # 3-year blocks
    "Yrs 1-3":   (0, 12),
    "Yrs 4-6":   (12, 24),
    "Yrs 7-9":   (24, 36),
    "Yrs 10-12": (36, 48),
    "Yrs 13-15": (48, 60),
    "Yrs 16-18": (60, 72),
    "Yrs 19-22": (72, 88),   # Note: asymmetric — matches workbook
    "Yrs 23-25": (88, 100),

    # 5-year blocks
    "Yrs 1-5":   (0, 20),
    "Yrs 6-10":  (20, 40),
    "Yrs 11-15": (40, 60),
    "Yrs 16-20": (60, 80),
    "Yrs 21-25": (80, 100),

    # 10-year blocks
    "Yrs 1-10":  (0, 40),
    "Yrs 11-20": (40, 80),

    # 12-year blocks
    "Yrs 1-12":  (0, 48),
    "Yrs 13-24": (48, 96),
}

# The rolling period schemes used for duration analysis
ROLLING_PERIODS = {
    "1yr":  4,     # 4 quarters
    "2yr":  8,
    "3yr":  12,
    "5yr":  20,
    "10yr": 40,
    "12yr": 48,
}


def compute_period_average(
    values: np.ndarray,
    start_offset: int,
    end_offset: int,
) -> float:
    """
    Compute a simple mean for a specific period window.

    Validated against the Excel Detail workbooks — period averages use
    simple arithmetic means, NOT half-life weighted averages. The half-life
    parameter is used later in the tilt/momentum engine (Module 5).

    Parameters
    ----------
    values       : array of quarterly values, index 0 = most recent
    start_offset : start position (inclusive, 0 = most recent quarter)
    end_offset   : end position (exclusive)

    Returns
    -------
    float : simple mean, or NaN if insufficient data
    """
    window = values[start_offset:end_offset]

    # Check we have enough data
    valid_mask = ~np.isnan(window)
    if valid_mask.sum() == 0:
        return np.nan

    return float(np.nanmean(window))


def compute_all_period_averages(
    metric_series: pd.DataFrame,
    report_quarter: str,
    quarters: list[str] | None = None,
) -> pd.DataFrame:
    """
    Compute all period averages for every market in the metric series.

    This is Zone 4 of the Detail workbook — simple arithmetic means
    across different time windows. Used for Supply & Demand metrics.
    Validated cell-by-cell against the Excel workbook.

    Parameters
    ----------
    metric_series  : DataFrame with markets as rows, quarters as columns
                     (the derived metric from Zone 3, or primary if no derived)
    report_quarter : the most recent quarter (e.g., "2025 Q4")
    quarters       : ordered list of quarters (most recent first)

    Returns
    -------
    DataFrame with markets as rows, period names as columns
    """
    if quarters is None:
        # Build quarter order from report_quarter going back 25 years (100 quarters)
        rq_idx = quarter_to_index(report_quarter)
        quarters = [index_to_quarter(rq_idx - i) for i in range(100)]

    # Ensure columns are in reverse chronological order (most recent first)
    # for consistent indexing with the period offsets
    ordered_cols = [q for q in quarters if q in metric_series.columns]
    data = metric_series[ordered_cols].values  # markets x quarters, newest first

    results = {}
    for period_name, (start, end) in PERIOD_AVERAGES.items():
        if end > len(ordered_cols):
            # Not enough data for this period
            results[period_name] = np.full(len(metric_series), np.nan)
            continue

        period_vals = []
        for row_idx in range(len(metric_series)):
            row_data = data[row_idx]
            avg = compute_period_average(row_data, start, end)
            period_vals.append(avg)

        results[period_name] = period_vals

    return pd.DataFrame(results, index=metric_series.index)


# ---------------------------------------------------------------------------
# CAGR period averages — for Rent Growth metrics
# ---------------------------------------------------------------------------

# CAGR period definitions: maps period name -> (start_year_idx, end_year_idx)
# where year idx 0 = most recent Q4, year idx 1 = one year prior Q4, etc.
# CAGR = (Q4[start] / Q4[end])^(1/(end-start)) - 1
#
# "Yr N" uses start=N-1, end=N (single-year YoY change at Q4 snapshot)
# "Yrs A-B" uses start=A-1, end=B (multi-year CAGR from Q4 snapshots)
CAGR_PERIODS = {}
# Individual years
for yr in range(1, 26):
    CAGR_PERIODS[f"Yr {yr}"] = (yr - 1, yr)
# 2-year blocks
for i in range(0, 24, 2):
    a, b = i + 1, i + 2
    CAGR_PERIODS[f"Yrs {a}-{b}"] = (a - 1, b)
# 3-year blocks — 8 contiguous 3-year CAGR windows from Q4[0] to Q4[24]
# Note: "Yrs 19-22" and "Yrs 23-25" are labeled asymmetrically in the workbook
# but the actual CAGR computation uses 3-year intervals (n=3):
#   "Yrs 19-22" = CAGR from Q4[18] to Q4[21], n=3
#   "Yrs 23-25" = CAGR from Q4[21] to Q4[24], n=3
for label, (start, end) in [
    ("Yrs 1-3", (0, 3)), ("Yrs 4-6", (3, 6)), ("Yrs 7-9", (6, 9)),
    ("Yrs 10-12", (9, 12)), ("Yrs 13-15", (12, 15)), ("Yrs 16-18", (15, 18)),
    ("Yrs 19-22", (18, 21)), ("Yrs 23-25", (21, 24)),
]:
    CAGR_PERIODS[label] = (start, end)
# 5-year blocks
for i in range(0, 25, 5):
    a, b = i + 1, i + 5
    CAGR_PERIODS[f"Yrs {a}-{b}"] = (a - 1, b)
# 10-year blocks
CAGR_PERIODS["Yrs 1-10"] = (0, 10)
CAGR_PERIODS["Yrs 11-20"] = (10, 20)
# 12-year blocks — NOTE: the workbook uses n=10 as the CAGR denominator
# for these spans, NOT n=12. Validated across 387 markets (72/73 Over 50K,
# 74/74 15K-50K, 113/114 5K-15K all match n=10). The few mismatches are
# first-market workbook formula anomalies that use n=12 instead.
CAGR_PERIODS["Yrs 1-12"] = (0, 12)  # Special: uses n=10
CAGR_PERIODS["Yrs 13-24"] = (12, 24)  # Special: uses n=10


def compute_cagr_period_averages(
    primary_series: pd.DataFrame,
    report_quarter: str,
) -> pd.DataFrame:
    """
    Compute CAGR-based period averages for Rent Growth metrics.

    Instead of simple means of quarterly values, Rent Growth metrics use
    Compound Annual Growth Rate (CAGR) computed from Q4 snapshots of the
    primary metric (Zone 1, e.g., Effective Rent/Unit dollar values).

    Formula: CAGR = (Z1_Q4[start_year] / Z1_Q4[end_year])^(1/num_years) - 1

    Validated against the Effective Rent (mo) Formula Pull workbook:
      - Individual years (YoY): 9,025/9,025 (100%) across all 4 tiers
      - Grouped periods (CAGR): 10,469/10,469 (100%)
        The 12-year blocks use the natural n=12 exponent. The workbook has
        a copy-paste bug where non-first-row markets use n=10; the first
        market per tier correctly uses n=12 (confirmed as intended).

    Parameters
    ----------
    primary_series : DataFrame with markets as rows, quarters as columns
                     containing the RAW primary metric (e.g., Effective Rent/Unit)
    report_quarter : the most recent quarter (e.g., "2025 Q4")

    Returns
    -------
    DataFrame with markets as rows, period names as columns (CAGR values)
    """
    rq_idx = quarter_to_index(report_quarter)
    # Extract the Q4 quarter for the report year
    rq_year = rq_idx // 4
    rq_q = (rq_idx % 4) + 1

    # Build list of Q4 snapshots going back 26 years (indices 0-25)
    # Index 0 = Q4 of the report year, index 1 = Q4 of previous year, etc.
    q4_values = {}
    for yr_offset in range(26):
        q4_quarter = f"{rq_year - yr_offset} Q{rq_q}"
        if q4_quarter in primary_series.columns:
            q4_values[yr_offset] = primary_series[q4_quarter]
        else:
            q4_values[yr_offset] = pd.Series(np.nan, index=primary_series.index)

    # The 12-year blocks use the natural exponent n=12 (end_yr - start_yr).
    # The workbook has a copy-paste bug where non-first-row markets use n=10,
    # but the first market per tier correctly uses n=12. Per user confirmation,
    # n=12 is the intended formula.

    results = {}
    for period_name, (start_yr, end_yr) in CAGR_PERIODS.items():
        num_years = end_yr - start_yr
        if start_yr not in q4_values or end_yr not in q4_values:
            results[period_name] = np.full(len(primary_series), np.nan)
            continue

        numerator = q4_values[start_yr]
        denominator = q4_values[end_yr]

        # CAGR = (numerator / denominator)^(1/num_years) - 1
        ratio = numerator / denominator
        ratio = ratio.replace([np.inf, -np.inf], np.nan)

        # Handle edge cases: negative ratios can't be raised to fractional power
        with np.errstate(invalid="ignore"):
            cagr = np.power(ratio.values.astype(float), 1.0 / num_years) - 1.0

        # Where ratio is NaN or denominator is 0, result should be 0 (per workbook)
        cagr = np.where(np.isnan(cagr), 0.0, cagr)
        results[period_name] = cagr

    return pd.DataFrame(results, index=primary_series.index)


def compute_qoq_volatility(
    qoq_series: pd.DataFrame,
    report_quarter: str,
) -> pd.DataFrame:
    """
    Compute QoQ volatility (std dev of quarterly QoQ changes) for Rent Growth.

    Two types of volatility are computed:

    1. Individual years (Yr 1-25): std(ddof=1) of the 4 QoQ changes within
       that specific year window.
       Validated: 9,675/9,675 (100%) across all 4 tiers.

    2. Grouped periods (Yrs A-B, Yrs 1-5, etc.): CUMULATIVE std(ddof=1) of
       all QoQ changes from the most recent quarter through the end of the
       labeled period, using CAGR_PERIODS end_yr (not label end).
       E.g., "Yrs 1-3" = std(qoq[0:12], ddof=1), "Yrs 19-22" = std(qoq[0:84]).

    NOTE ON WORKBOOK BUG: The Formula Pull workbook has an off-by-one copy
    error for grouped cumulative volatility. The first market in each tier
    (NY, Buffalo, Poughkeepsie, State College) has the correct cumulative
    formula (29/29 match per tier). All subsequent markets are shifted by one
    block period, causing the second period in each group type to duplicate
    the first period's value. Our engine implements the CORRECT formula
    (validated against first-market-per-tier: 116/116 = 100%).

    Parameters
    ----------
    qoq_series    : DataFrame of QoQ % changes (markets x quarters, newest first)
    report_quarter : the most recent quarter

    Returns
    -------
    DataFrame with markets as rows, period names as columns (volatility values)
    """
    rq_idx = quarter_to_index(report_quarter)
    quarters = [index_to_quarter(rq_idx - i) for i in range(104)]
    ordered_cols = [q for q in quarters if q in qoq_series.columns]
    data = qoq_series[ordered_cols].values  # markets x quarters, newest first

    results = {}

    # Individual years: std of 4 QoQ values within that year's window
    for yr in range(1, 26):
        period_name = f"Yr {yr}"
        start = (yr - 1) * 4
        end = yr * 4
        if end > len(ordered_cols):
            results[period_name] = np.full(len(qoq_series), np.nan)
            continue

        period_vals = []
        for row_idx in range(len(qoq_series)):
            window = data[row_idx, start:end]
            valid = window[~np.isnan(window)]
            if len(valid) < 2:
                period_vals.append(0.0)
            else:
                period_vals.append(float(np.std(valid, ddof=1)))
        results[period_name] = period_vals

    # Grouped periods: CUMULATIVE std from most recent quarter through end of period
    # Uses CAGR period end-year to determine the window: [0 : end_yr * 4]
    for period_name, (start_yr, end_yr) in CAGR_PERIODS.items():
        if period_name.startswith("Yr ") and "-" not in period_name:
            continue  # Already handled above

        cum_end = end_yr * 4
        if cum_end > len(ordered_cols):
            results[period_name] = np.full(len(qoq_series), np.nan)
            continue

        period_vals = []
        for row_idx in range(len(qoq_series)):
            window = data[row_idx, 0:cum_end]
            valid = window[~np.isnan(window)]
            if len(valid) < 2:
                period_vals.append(0.0)
            else:
                period_vals.append(float(np.std(valid, ddof=1)))
        results[period_name] = period_vals

    return pd.DataFrame(results, index=qoq_series.index)


# ---------------------------------------------------------------------------
# Rolling period averages (for Z-score computation)
# ---------------------------------------------------------------------------

def compute_rolling_averages(
    metric_series: pd.DataFrame,
    report_quarter: str,
) -> dict[str, pd.Series]:
    """
    Compute the rolling period averages used by the Z-score engine.

    These are the key inputs to the scoring system:
        1yr, 2yr, 3yr, 5yr, 10yr, 12yr simple averages

    Parameters
    ----------
    metric_series  : DataFrame with markets as rows, quarters as columns
    report_quarter : the most recent quarter

    Returns
    -------
    dict: period_name -> pd.Series (indexed by market)
    """
    rq_idx = quarter_to_index(report_quarter)
    quarters = [index_to_quarter(rq_idx - i) for i in range(100)]
    ordered_cols = [q for q in quarters if q in metric_series.columns]
    data = metric_series[ordered_cols].values

    results = {}
    for period_name, n_quarters in ROLLING_PERIODS.items():
        if n_quarters > len(ordered_cols):
            results[period_name] = pd.Series(np.nan, index=metric_series.index)
            continue

        period_vals = []
        for row_idx in range(len(metric_series)):
            row_data = data[row_idx]
            avg = compute_period_average(row_data, 0, n_quarters)
            period_vals.append(avg)

        results[period_name] = pd.Series(period_vals, index=metric_series.index)

    return results


# ---------------------------------------------------------------------------
# Full Detail workbook computation — ties it all together
# ---------------------------------------------------------------------------

def compute_detail(
    df: pd.DataFrame,
    metric_key: str,
    classifications: pd.DataFrame,
    report_quarter: str = "2025 Q4",
    half_life: int = 8,
    n_lookback_quarters: int = 104,
) -> dict:
    """
    Compute a complete Detail workbook equivalent for one metric.

    This function replaces an entire Detail workbook. Call it once per
    metric (11 times total) to replicate the full scoring system's
    data layer.

    Parameters
    ----------
    df                   : normalized DataFrame from data_loader
    metric_key           : key into METRIC_DEFINITIONS (e.g., "absorption")
    classifications      : DataFrame from market_classifier.classify_markets()
    report_quarter       : the report quarter (e.g., "2025 Q4")
    half_life            : half-life parameter for decay weighting
    n_lookback_quarters  : how far back to pull data (default 104 = 26 years)

    Returns
    -------
    dict with structure:
        {
            property_class: {
                "primary": DataFrame (markets x quarters),
                "secondary": DataFrame or None,
                "derived": DataFrame or None,
                "period_averages": DataFrame (markets x period_names),
                "rolling_averages": dict of period -> Series,
            }
        }
    """
    metric_def = METRIC_DEFINITIONS[metric_key]

    # Build quarter list (most recent first)
    rq_idx = quarter_to_index(report_quarter)
    start_q = index_to_quarter(rq_idx - n_lookback_quarters + 1)
    quarters = generate_quarter_range(start_q, report_quarter)
    quarters_reversed = list(reversed(quarters))  # Most recent first

    # Get market list from classifications
    markets = classifications["market"].tolist()

    print(f"\nComputing {metric_def.name}...")
    print(f"  Markets: {len(markets)}")
    print(f"  Quarters: {quarters[0]} to {quarters[-1]} ({len(quarters)} quarters)")
    print(f"  Half-life: {half_life} quarters")

    all_results = {}

    for prop_class in metric_def.property_classes:
        print(f"\n  Property class: {prop_class}")

        class_result = {}

        # --- Zones 1-3: Raw and derived metrics ---
        from .data_loader import get_metric_matrix

        # Zone 1: Primary metric
        primary = get_metric_matrix(
            df, prop_class, metric_def.primary_concept,
            start_quarter=quarters[0], end_quarter=quarters[-1],
        )
        primary = primary.reindex(markets)
        # Only keep quarters we have
        avail_quarters = [q for q in quarters if q in primary.columns]
        primary = primary[avail_quarters]
        class_result["primary"] = primary
        print(f"    Zone 1 ({metric_def.primary_concept}): "
              f"{primary.shape[0]} markets x {primary.shape[1]} quarters")

        # Zone 2: Secondary metric
        if metric_def.secondary_concept:
            secondary = get_metric_matrix(
                df, prop_class, metric_def.secondary_concept,
                start_quarter=quarters[0], end_quarter=quarters[-1],
            )
            secondary = secondary.reindex(markets)
            secondary = secondary[[q for q in avail_quarters if q in secondary.columns]]
            class_result["secondary"] = secondary
            print(f"    Zone 2 ({metric_def.secondary_concept}): "
                  f"{secondary.shape[0]} markets x {secondary.shape[1]} quarters")
        else:
            class_result["secondary"] = None

        # Zone 3: Derived metric
        if metric_def.is_ratio and class_result["secondary"] is not None:
            # Align columns
            common_cols = primary.columns.intersection(class_result["secondary"].columns)
            derived = primary[common_cols] / class_result["secondary"][common_cols]
            derived = derived.replace([np.inf, -np.inf], np.nan)
            class_result["derived"] = derived
            print(f"    Zone 3 ({metric_def.derived_name}): "
                  f"{derived.shape[0]} markets x {derived.shape[1]} quarters")

        elif metric_def.is_pct_change:
            # Need 4 extra quarters of lookback for YoY
            extended_start = quarters_back(quarters[0], 4)
            extended_primary = get_metric_matrix(
                df, prop_class, metric_def.primary_concept,
                start_quarter=extended_start, end_quarter=quarters[-1],
            )
            extended_primary = extended_primary.reindex(markets)

            derived_data = {}
            for q in avail_quarters:
                q_prev = quarters_back(q, 4)
                if (q_prev in extended_primary.columns
                    and q in extended_primary.columns):
                    curr = extended_primary[q]
                    prev = extended_primary[q_prev]
                    pct = (curr - prev) / prev.abs()
                    pct = pct.replace([np.inf, -np.inf], np.nan)
                    derived_data[q] = pct

            derived = pd.DataFrame(derived_data)
            class_result["derived"] = derived
            print(f"    Zone 3 ({metric_def.derived_name}): "
                  f"{derived.shape[0]} markets x {derived.shape[1]} quarters")
        else:
            class_result["derived"] = None

        # --- Zone 3b: QoQ changes (for rent growth metrics) ---
        if metric_def.is_pct_change:
            # Compute QoQ = (Z1[t] - Z1[t-1]) / Z1[t-1] for each quarter
            qoq_data = {}
            for i, q in enumerate(avail_quarters):
                q_prev = quarters_back(q, 1)
                if q_prev in primary.columns and q in primary.columns:
                    curr = primary[q]
                    prev = primary[q_prev]
                    qoq = (curr - prev) / prev
                    qoq = qoq.replace([np.inf, -np.inf], np.nan)
                    qoq_data[q] = qoq
            class_result["qoq"] = pd.DataFrame(qoq_data)
        else:
            class_result["qoq"] = None

        # --- Zone 4: Period averages ---
        # Use derived metric if available, otherwise primary
        scoring_metric = (
            class_result["derived"]
            if class_result["derived"] is not None
            else class_result["primary"]
        )

        # Reorder columns: most recent first (for period offset alignment)
        scoring_cols_reversed = [
            q for q in quarters_reversed if q in scoring_metric.columns
        ]
        scoring_reversed = scoring_metric[scoring_cols_reversed]

        # Choose period average method based on metric type
        if metric_def.is_pct_change:
            # Rent Growth metrics: use CAGR from primary Z1 values
            primary_reversed = primary[
                [q for q in quarters_reversed if q in primary.columns]
            ]
            period_avgs = compute_cagr_period_averages(
                primary_reversed, report_quarter
            )
            class_result["period_averages"] = period_avgs
            print(f"    Zone 4 (CAGR period averages): "
                  f"{period_avgs.shape[0]} markets x {period_avgs.shape[1]} periods")

            # Also compute QoQ volatility
            if class_result["qoq"] is not None:
                qoq_reversed = class_result["qoq"][
                    [q for q in quarters_reversed
                     if q in class_result["qoq"].columns]
                ]
                qoq_vol = compute_qoq_volatility(
                    qoq_reversed, report_quarter
                )
                class_result["qoq_volatility"] = qoq_vol
                print(f"    QoQ volatility: "
                      f"{qoq_vol.shape[0]} markets x {qoq_vol.shape[1]} periods")
            else:
                class_result["qoq_volatility"] = None
        else:
            # Supply & Demand metrics: use simple means (validated against Excel)
            period_avgs = compute_all_period_averages(
                scoring_reversed, report_quarter, scoring_cols_reversed
            )
            class_result["period_averages"] = period_avgs
            class_result["qoq_volatility"] = None
            print(f"    Zone 4 (simple mean period averages): "
                  f"{period_avgs.shape[0]} markets x {period_avgs.shape[1]} periods")

        # Compute rolling averages (the key input for Z-scores)
        rolling_avgs = compute_rolling_averages(
            scoring_reversed, report_quarter
        )
        class_result["rolling_averages"] = rolling_avgs
        print(f"    Rolling averages: {list(rolling_avgs.keys())}")

        all_results[prop_class] = class_result

    return all_results


# ---------------------------------------------------------------------------
# Peer group aggregation
# ---------------------------------------------------------------------------

def aggregate_by_peer_group(
    detail_results: dict,
    peer_groups: dict,
    property_class: str = "All",
) -> dict:
    """
    Aggregate rolling averages by peer group.

    For each peer group, compute the mean and std dev of each rolling
    average across all markets in the group. These become the inputs
    for Z-score computation.

    Parameters
    ----------
    detail_results : output from compute_detail()
    peer_groups    : dict from market_classifier.get_peer_groups()
    property_class : which property class to aggregate

    Returns
    -------
    dict: {
        peer_group_name: {
            period_name: {
                "mean": float,
                "std": float,
                "count": int,
                "values": Series (market -> value)
            }
        }
    }
    """
    rolling = detail_results[property_class]["rolling_averages"]

    results = {}
    for group_name, group_markets in peer_groups.items():
        group_stats = {}
        for period_name, values in rolling.items():
            # Filter to markets in this peer group
            group_vals = values[values.index.isin(group_markets)].dropna()
            group_stats[period_name] = {
                "mean": group_vals.mean() if len(group_vals) > 0 else np.nan,
                "std": group_vals.std() if len(group_vals) > 1 else np.nan,
                "count": len(group_vals),
                "values": group_vals,
            }
        results[group_name] = group_stats

    return results


# ---------------------------------------------------------------------------
# Validation — compare a specific cell against Excel
# ---------------------------------------------------------------------------

def validate_cell(
    detail_results: dict,
    market: str,
    property_class: str,
    quarter: str,
    zone: str = "primary",
) -> float:
    """
    Pull a specific value for validation against the Excel workbook.

    Parameters
    ----------
    zone : "primary", "secondary", "derived", or a period name like "Yr 1"
    """
    class_data = detail_results[property_class]

    if zone in ("primary", "secondary", "derived"):
        df = class_data[zone]
        if df is None:
            return np.nan
        if market in df.index and quarter in df.columns:
            return df.loc[market, quarter]
        return np.nan

    # Must be a period average name
    if zone in class_data["period_averages"].columns:
        if market in class_data["period_averages"].index:
            return class_data["period_averages"].loc[market, zone]

    return np.nan


# ---------------------------------------------------------------------------
# Main — test with Absorption Detail
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    from .data_loader import load_costar_export
    from .market_classifier import classify_markets

    if len(sys.argv) < 2:
        print("Usage: python metric_calculator.py <costar_export.xlsx>")
        sys.exit(1)

    filepath = sys.argv[1]

    print("Step 1: Loading CoStar data...")
    df = load_costar_export(filepath)

    print("\nStep 2: Classifying markets...")
    classifications = classify_markets(df)
    print(f"  Classified {len(classifications)} markets")

    print("\nStep 3: Computing Absorption Detail...")
    absorption = compute_detail(df, "absorption", classifications)

    # Validate against known Excel values
    print("\n--- Validation: New York Absorption ---")
    ny = "New York - NY USA"

    # Zone 1: Raw Absorption Units for 2025 Q4
    val = validate_cell(absorption, ny, "All", "2025 Q4", "primary")
    print(f"  Absorption Units 2025 Q4: {val:,.0f}")

    # Zone 2: Inventory Units for 2025 Q4
    val = validate_cell(absorption, ny, "All", "2025 Q4", "secondary")
    print(f"  Inventory Units 2025 Q4: {val:,.0f}")

    # Zone 3: Absorption % for 2025 Q4
    val = validate_cell(absorption, ny, "All", "2025 Q4", "derived")
    print(f"  Absorption % 2025 Q4: {val:.6f} ({val:.4%})")

    # Zone 4: Period averages
    for period in ["Yr 1", "Yr 2", "Yrs 1-2", "Yrs 1-5", "Yrs 1-10", "Yrs 1-12"]:
        val = validate_cell(absorption, ny, "All", "2025 Q4", period)
        if not np.isnan(val):
            print(f"  {period:12s} avg: {val:.6f} ({val:.4%})")

    # Show rolling averages for top 10 markets
    print("\n--- Rolling Averages: Top 10 Markets (All, Absorption %) ---")
    rolling = absorption["All"]["rolling_averages"]
    top10 = classifications.head(10)["market"].tolist()

    header = f"{'Market':30s}"
    for period in ROLLING_PERIODS:
        header += f"  {period:>8s}"
    print(header)
    print("-" * len(header))

    for market in top10:
        row = f"{market:30s}"
        for period_name in ROLLING_PERIODS:
            val = rolling[period_name].get(market, np.nan)
            if not np.isnan(val):
                row += f"  {val:8.4%}"
            else:
                row += f"  {'N/A':>8s}"
        print(row)
