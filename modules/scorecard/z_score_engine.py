"""
z_score_engine.py — Module 4 of the CoStar Market Scorecard Engine

Computes Z-scores (Mean Deviation scores) for each market across all markets
in a given property class / tier.

The workbook computes Z-scores across ALL markets (not peer-group-segmented),
then applies category, volatility, and period adjustments via tilt_engine.

This module produces:
  1. Signal Indicator Z-scores   — ABS((value - mean) / std)
  2. Volatility Z-scores         — Z-score of each market's time-series std dev
  3. Category Z-scores           — (value - category_mean) / category_std  (signed)
  4. Period-level data bundled per-market for consumption by tilt_engine

Z-scores are computed for:
  - Each metric (D&S: absorption, deliveries, abs-del, actual_occ, effective_occ,
    under_construction, yrs_to_stab; Rent: eff_rent_overall, 1br, studio, 2br, 3br)
  - Each of the 4 property classes / tiers (All, 4&5 Star, 3 Star, 1&2 Star)
  - Each rolling period (Annual/1yr, 2yr, 5yr, 10yr)
"""

import pandas as pd
import numpy as np
from typing import Optional


# ---------------------------------------------------------------------------
# Core Z-score computation
# ---------------------------------------------------------------------------

def compute_z_scores(
    values: pd.Series,
    min_count: int = 3,
) -> pd.Series:
    """
    Compute Z-scores for a series of market values.

    Z-score = (value - mean) / std_dev

    Parameters
    ----------
    values    : Series indexed by market name
    min_count : minimum non-NaN values required (default 3)

    Returns
    -------
    pd.Series of Z-scores, same index as input
    """
    clean = values.dropna()

    if len(clean) < min_count:
        return pd.Series(np.nan, index=values.index)

    mean = clean.mean()
    std = clean.std(ddof=1)

    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=values.index)

    z_scores = (values - mean) / std
    return z_scores


def compute_weighted_z_scores(
    values: pd.Series,
    weights: pd.Series,
    min_count: int = 3,
) -> tuple[pd.Series, float, float]:
    """
    Compute Z-scores using inventory-weighted peer group mean and std dev.

    Z-score = (value - weighted_mean) / weighted_std

    Returns
    -------
    tuple of (z_scores Series, weighted_mean, weighted_std)
    """
    common = values.dropna().index.intersection(weights.dropna().index)
    clean_vals = values.loc[common]
    clean_wts = weights.loc[common]

    if len(clean_vals) < min_count:
        return pd.Series(np.nan, index=values.index), np.nan, np.nan

    total_weight = clean_wts.sum()
    if total_weight == 0:
        return pd.Series(np.nan, index=values.index), np.nan, np.nan

    w_mean = (clean_vals * clean_wts).sum() / total_weight

    v1 = total_weight
    v2 = (clean_wts ** 2).sum()
    denominator = v1 - v2 / v1

    if denominator <= 0:
        return pd.Series(0.0, index=values.index), w_mean, 0.0

    w_var = (clean_wts * (clean_vals - w_mean) ** 2).sum() / denominator
    w_std = np.sqrt(w_var)

    if w_std == 0 or np.isnan(w_std):
        return pd.Series(0.0, index=values.index), w_mean, 0.0

    z_scores = (values - w_mean) / w_std
    return z_scores, w_mean, w_std


# ---------------------------------------------------------------------------
# Metric direction and configuration
# ---------------------------------------------------------------------------

# Whether HIGHER value is BETTER for each metric.
# This determines Z-score sign convention.
METRIC_DIRECTION = {
    # metric_calculator keys
    "absorption":          True,
    "net_deliveries":      False,  # More supply pressure = bad
    "vacancy":             False,  # Lower vacancy = good (Actual Occupancy proxy)
    "under_construction":  False,  # More future supply = bad
    "years_to_stabilization": False,  # Lower years = better
    "effective_rent_unit": True,
    "effective_rent_sf":   True,
    "effective_rent_studio": True,
    "effective_rent_1br":  True,
    "effective_rent_2br":  True,
    "effective_rent_3br":  True,
    "asking_rent_unit":    True,
    "asking_rent_sf":      True,
    "population":          True,
    "employment":          True,
    "income":              True,
    # tilt_engine keys (for derived metrics)
    "abs_del":             True,   # Net absorption - deliveries, higher = good
    "effective_occ":       True,   # Higher effective occupancy = good
    "blended_occ":         True,   # Higher blended occupancy = good
    "yrs_to_stab":         False,  # Lower years = better
    "eff_rent_overall":    True,
    "eff_rent_1br":        True,
    "eff_rent_studio":     True,
    "eff_rent_2br":        True,
    "eff_rent_3br":        True,
    # External data metrics (FRED + Census)
    "sf_permits_yoy":           False,  # More SF permits = more supply competition = bad for MF
    "renter_weighted_pop_yoy":  True,   # Growing renter-age population = more demand = good
    "pop_20_34_share":          True,   # Higher share of peak-renter cohort = good
}

# Mapping from tilt_engine metric keys to metric_calculator metric keys.
# The workbook uses a different set of metrics than what metric_calculator defines.
# D&S metrics in tilt_engine:
#   absorption, deliveries, abs_del, blended_occ (actual+effective), under_construction, yrs_to_stab
# Rent metrics in tilt_engine:
#   eff_rent_overall, eff_rent_1br, eff_rent_studio, eff_rent_2br, eff_rent_3br
#
# metric_calculator keys:
#   absorption, net_deliveries, vacancy, under_construction,
#   effective_rent_unit, effective_rent_studio, effective_rent_1br, effective_rent_2br, effective_rent_3br
#   asking_rent_unit, asking_rent_sf, effective_rent_sf, population, employment, income

TILT_TO_CALC_KEY = {
    # D&S
    "absorption":         "absorption",
    "deliveries":         "net_deliveries",
    "abs_del":            None,           # Derived: absorption - deliveries (computed below)
    "actual_occ":         "vacancy",      # Vacancy rate → we invert for occupancy
    "effective_occ":      None,           # Derived: Occ - UC% (computed below)
    "blended_occ":        None,           # Derived from actual_occ + effective_occ
    "under_construction": "under_construction",
    "yrs_to_stab":        None,           # Derived (YTS computation)
    # Rent
    "eff_rent_overall":   "effective_rent_unit",
    "eff_rent_1br":       "effective_rent_1br",
    "eff_rent_studio":    "effective_rent_studio",
    "eff_rent_2br":       "effective_rent_2br",
    "eff_rent_3br":       "effective_rent_3br",
}

def get_direction(metric_key: str, overrides: dict = None) -> bool:
    """Get metric direction, checking overrides first, then METRIC_DIRECTION."""
    if overrides and metric_key in overrides:
        return overrides[metric_key]
    return METRIC_DIRECTION.get(metric_key, True)

STABILIZATION_TARGET = 0.95
ABSORPTION_FLOOR = 0.0015
ABSORPTION_PERCENTILE = 0.40
YTS_CAP = 15.0


# ---------------------------------------------------------------------------
# Period mapping
# ---------------------------------------------------------------------------

# The tilt_engine expects periods named: Annual, 2Yr, 5Yr, 10Yr
# metric_calculator produces: 1yr, 2yr, 3yr, 5yr, 10yr, 12yr
PERIOD_MAP = {
    "Q1":     "1yr",   # Single most recent quarter (matches workbook Column M)
    "Annual": "1yr",
    "2Yr":    "2yr",
    "5Yr":    "5yr",
    "10Yr":   "10yr",
}


# ---------------------------------------------------------------------------
# Years to Stabilization — derived composite metric
# ---------------------------------------------------------------------------

def compute_years_to_stabilization(
    all_detail_results: dict,
    property_class: str = "All",
) -> dict[str, pd.Series]:
    """
    Compute Years to Stabilization for each rolling period.

    Validated against the Excel workbook (387/387 markets match).

    For each market, each quarter:
        Effective Occupancy = Occupancy Rate - (UC Units / Inventory)
        Absorption Floor = max(market's 40th pctile of all historical abs, 0.0015)
        Absorption Used = max(current quarter absorption, Absorption Floor)
        YTS = (0.95 - Effective Occupancy) / (Absorption Used * 4)
        YTS = min(YTS, 15.0)

    Returns
    -------
    dict: period_name -> pd.Series (indexed by market, values = years)
    """
    from .metric_calculator import ROLLING_PERIODS, quarter_to_index, index_to_quarter

    absorption_detail = all_detail_results["absorption"][property_class]
    vacancy_detail = all_detail_results["vacancy"][property_class]
    construction_detail = all_detail_results["under_construction"][property_class]

    abs_quarterly = (
        absorption_detail["derived"]
        if absorption_detail.get("derived") is not None
        else absorption_detail["primary"]
    )
    occ_quarterly = vacancy_detail["primary"]
    uc_quarterly = (
        construction_detail["derived"]
        if construction_detail.get("derived") is not None
        else construction_detail["primary"]
    )

    common_cols = abs_quarterly.columns.intersection(
        occ_quarterly.columns
    ).intersection(uc_quarterly.columns)
    common_cols = sorted(common_cols)

    eff_occ_quarterly = occ_quarterly[common_cols] - uc_quarterly[common_cols]

    market_abs_p40 = abs_quarterly.quantile(ABSORPTION_PERCENTILE, axis=1)
    market_abs_floor = market_abs_p40.clip(lower=ABSORPTION_FLOOR)

    abs_used = abs_quarterly[common_cols].copy()
    for market in abs_used.index:
        floor = market_abs_floor.get(market, ABSORPTION_FLOOR)
        abs_used.loc[market] = abs_used.loc[market].clip(lower=floor)

    gap = STABILIZATION_TARGET - eff_occ_quarterly
    annual_abs = abs_used * 4.0

    yts_quarterly = gap / annual_abs
    yts_quarterly = yts_quarterly.replace([np.inf, -np.inf], np.nan)
    yts_quarterly = yts_quarterly.clip(upper=YTS_CAP)

    report_quarter = common_cols[-1]
    rq_idx = quarter_to_index(report_quarter)
    quarters_reversed = [index_to_quarter(rq_idx - i) for i in range(len(common_cols))]
    ordered_cols = [q for q in quarters_reversed if q in yts_quarterly.columns]

    yts_ordered = yts_quarterly[ordered_cols]

    yts_rolling = {}
    for period_name, n_quarters in ROLLING_PERIODS.items():
        if n_quarters > len(ordered_cols):
            yts_rolling[period_name] = pd.Series(np.nan, index=yts_quarterly.index)
            continue
        window = yts_ordered.iloc[:, :n_quarters]
        yts_rolling[period_name] = window.mean(axis=1, skipna=True)

    return yts_rolling


# ---------------------------------------------------------------------------
# Abs-Del (Net Absorption minus Deliveries) — derived metric
# ---------------------------------------------------------------------------

def compute_abs_del_rolling(
    all_detail_results: dict,
    property_class: str = "All",
) -> dict[str, pd.Series]:
    """
    Compute Absorption - Deliveries for each rolling period.

    Both absorption and net_deliveries are stored as % of inventory (derived).
    abs_del = absorption_derived - deliveries_derived
    """
    abs_detail = all_detail_results["absorption"][property_class]
    del_detail = all_detail_results["net_deliveries"][property_class]

    abs_rolling = abs_detail["rolling_averages"]
    del_rolling = del_detail["rolling_averages"]

    result = {}
    for period in abs_rolling:
        if period in del_rolling:
            abs_vals = abs_rolling[period]
            del_vals = del_rolling[period]
            common = abs_vals.index.intersection(del_vals.index)
            result[period] = abs_vals.loc[common] - del_vals.loc[common]

    return result


# ---------------------------------------------------------------------------
# Effective Occupancy — derived metric
# ---------------------------------------------------------------------------

def compute_effective_occ_rolling(
    all_detail_results: dict,
    property_class: str = "All",
) -> dict[str, pd.Series]:
    """
    Compute Effective Occupancy = Vacancy Rate - UC% for each rolling period.

    Note: In the CoStar data, "Vacancy Rate" is actually the occupancy rate
    in the primary field. Effective Occ = Occ Rate - (UC / Inventory).
    """
    vac_detail = all_detail_results["vacancy"][property_class]
    uc_detail = all_detail_results["under_construction"][property_class]

    occ_rolling = vac_detail["rolling_averages"]
    uc_rolling = uc_detail["rolling_averages"]

    result = {}
    for period in occ_rolling:
        if period in uc_rolling:
            occ_vals = occ_rolling[period]
            uc_vals = uc_rolling[period]
            common = occ_vals.index.intersection(uc_vals.index)
            result[period] = occ_vals.loc[common] - uc_vals.loc[common]

    return result


# ---------------------------------------------------------------------------
# Volatility Z-scores (time-series std dev, z-scored across markets)
# ---------------------------------------------------------------------------

def compute_volatility_z_scores(
    all_detail_results: dict,
    property_class: str = "All",
) -> dict[str, pd.Series]:
    """
    Compute Z-scores of each market's time-series standard deviation
    across ALL markets (not segmented by peer group).

    For each metric:
      1. Take quarterly derived values across all quarters
      2. Compute per-market standard deviation
      3. Z-score those std devs across all markets

    Higher std dev = more volatile → positive volatility Z.
    The tilt engine uses this to penalize volatile markets.

    Returns
    -------
    dict: {metric_key: pd.Series(market -> volatility_z_score)}
    """
    vol_z = {}

    for metric_key, detail_result in all_detail_results.items():
        if property_class not in detail_result:
            continue

        class_data = detail_result[property_class]
        scoring_metric = (
            class_data["derived"]
            if class_data.get("derived") is not None
            else class_data["primary"]
        )

        # Per-market standard deviation across all quarters
        market_stds = scoring_metric.std(axis=1, skipna=True)
        z = compute_z_scores(market_stds)
        vol_z[metric_key] = z

    return vol_z


# ---------------------------------------------------------------------------
# Signal Indicator Z-scores (per period, across all markets)
# ---------------------------------------------------------------------------

def compute_signal_z_scores(
    all_detail_results: dict,
    property_class: str = "All",
    peer_group_markets: set = None,
    direction_overrides: dict = None,
) -> dict[str, dict[str, pd.Series]]:
    """
    Compute Signal Indicator Z-scores for each metric, each period.

    The workbook computes SIGNED Z-scores with direction flip:
      For "higher is better": Z = (value - mean) / std
      For "lower is better":  Z = -(value - mean) / std
    Positive Z = good for the market.

    Returns
    -------
    dict: {metric_key: {period_name: pd.Series(market -> signed_z_score)}}
    """
    signal_z = {}

    for metric_key, detail_result in all_detail_results.items():
        if property_class not in detail_result:
            continue

        class_data = detail_result[property_class]
        rolling = class_data["rolling_averages"]

        direction = get_direction(metric_key, direction_overrides)
        sign = 1.0 if direction else -1.0

        metric_z = {}
        for period_name, values in rolling.items():
            if peer_group_markets is not None:
                peer_values = values[values.index.isin(peer_group_markets)].dropna()
            else:
                peer_values = values.dropna()

            if len(peer_values) < 3:
                metric_z[period_name] = pd.Series(0.0, index=values.index)
                continue

            mean = peer_values.mean()
            std = peer_values.std(ddof=1)
            if std == 0 or np.isnan(std):
                metric_z[period_name] = pd.Series(0.0, index=values.index)
                continue

            # Signed Z with direction flip: positive = good for market
            raw_z = sign * (values - mean) / std
            metric_z[period_name] = raw_z

        signal_z[metric_key] = metric_z

    return signal_z


# ---------------------------------------------------------------------------
# Build the all_market_data structure for tilt_engine
# ---------------------------------------------------------------------------

def build_tilt_engine_input(
    all_detail_results: dict,
    property_class: str = "All",
    config=None,
    peer_group_markets: set = None,
    report_quarter: str = "2025 Q4",
    external_quarterly_data: dict = None,
) -> dict[str, dict]:
    """
    Build the per-market, per-period data structure that tilt_engine expects.

    Architecture (matching workbook):
      1. Get quarterly metric values for each market
      2. For each quarter: compute Z-score across peer group (signed, direction-flipped)
      3. For each period (Annual=4Q, 2Yr=8Q, 5Yr=20Q, 10Yr=40Q):
         Half-life-weighted average of quarterly Z-scores
      4. Package per-period data for tilt_engine

    Parameters
    ----------
    all_detail_results      : dict of metric_key -> compute_detail() output
    property_class          : which tier to score ("All", "4 & 5 Star", etc.)
    config                  : ScorecardConfig (or None for defaults)
    peer_group_markets      : set of market names for Z-score peer group
    report_quarter          : the report quarter (e.g., "2025 Q4")
    external_quarterly_data : dict of {metric_key: DataFrame(market × quarter)}
                              from external_data_integrator.load_external_data().
                              These are market-level metrics (no property class
                              breakdown) that get injected alongside CoStar metrics.
    """
    from .tilt_engine import (
        ScorecardConfig, DEFAULT_CONFIG,
        max_momentum_tilt, momentum_decay,
    )
    from .metric_calculator import quarter_to_index, index_to_quarter

    if config is None:
        config = DEFAULT_CONFIG

    # --- Step 1: Get quarterly metric values for each tilt metric ---
    # For standard metrics: use 'derived' (or 'primary') quarterly data
    # For derived metrics: compute quarterly values

    quarterly_by_metric = {}  # {tilt_key: DataFrame (market × quarter)}

    for tilt_key, calc_key in TILT_TO_CALC_KEY.items():
        if calc_key is not None and calc_key in all_detail_results:
            if property_class in all_detail_results[calc_key]:
                class_data = all_detail_results[calc_key][property_class]
                scoring = (
                    class_data["derived"]
                    if class_data.get("derived") is not None
                    else class_data["primary"]
                )
                quarterly_by_metric[tilt_key] = scoring

    # Derived: abs_del = absorption - deliveries (quarterly)
    if "absorption" in quarterly_by_metric and "deliveries" in quarterly_by_metric:
        abs_q = quarterly_by_metric["absorption"]
        del_q = quarterly_by_metric["deliveries"]
        common_cols = abs_q.columns.intersection(del_q.columns)
        common_idx = abs_q.index.intersection(del_q.index)
        quarterly_by_metric["abs_del"] = abs_q.loc[common_idx, common_cols] - del_q.loc[common_idx, common_cols]

    # Derived: effective_occ = occupancy_rate - uc_pct (quarterly)
    if "actual_occ" in quarterly_by_metric and "under_construction" in quarterly_by_metric:
        occ_q = quarterly_by_metric["actual_occ"]
        uc_q = quarterly_by_metric["under_construction"]
        common_cols = occ_q.columns.intersection(uc_q.columns)
        common_idx = occ_q.index.intersection(uc_q.index)
        # IMPORTANT: actual_occ uses vacancy_rate (not occupancy).
        # Convert vacancy → occupancy before computing effective occupancy:
        #   effective_occ = (1 - vacancy_rate) - UC_as_%_of_inventory
        # Higher effective_occ = higher occupancy with less future supply = better
        quarterly_by_metric["effective_occ"] = (1.0 - occ_q.loc[common_idx, common_cols]) - uc_q.loc[common_idx, common_cols]

    # --- Inject external data (FRED permits, Census demographics) ---
    # External data is market-level (no property class breakdown), so we
    # inject the same data regardless of which tier is being scored.
    if external_quarterly_data:
        for ext_key, ext_matrix in external_quarterly_data.items():
            if ext_key not in quarterly_by_metric and isinstance(ext_matrix, pd.DataFrame):
                quarterly_by_metric[ext_key] = ext_matrix
                # Note: external metrics with no direction entry default to True (higher=better)

    # Derived: yrs_to_stab — use the existing function's rolling output as approximation
    # (YTS quarterly computation is complex, keep using rolling averages for now)
    required_yts = {"absorption", "vacancy", "under_construction"}
    if required_yts.issubset(all_detail_results.keys()):
        try:
            yts_rolling = compute_years_to_stabilization(
                all_detail_results, property_class
            )
            # Store rolling averages for YTS (we'll use them directly per period)
            quarterly_by_metric["_yts_rolling"] = yts_rolling
        except Exception as e:
            print(f"  Warning: Could not compute yrs_to_stab: {e}")

    # --- Step 2: Build ordered quarter list (most recent first) ---
    rq_idx = quarter_to_index(report_quarter)
    max_quarters = 40  # 10 years
    all_quarters = [index_to_quarter(rq_idx - i) for i in range(max_quarters)]

    # Period definitions: how many quarters in each period window
    # "Q1" = single most recent quarter (matches workbook Column M)
    PERIOD_QUARTERS = {
        "Q1": 1,
        "Annual": 4,
        "2Yr": 8,
        "3Yr": 12,
        "5Yr": 20,
        "10Yr": 40,
    }

    # --- Step 3: Compute quarterly Signal Z per metric, per quarter ---
    # For each quarter, compute Z = sign * (value - peer_mean) / peer_std

    quarterly_z_by_metric = {}  # {tilt_key: {quarter: Series(market -> z)}}

    for tilt_key, qdata in quarterly_by_metric.items():
        if tilt_key == "_yts_rolling":
            continue  # handled separately

        calc_key = TILT_TO_CALC_KEY.get(tilt_key)
        direction_key = calc_key if calc_key is not None else tilt_key
        direction = get_direction(direction_key, config.direction_overrides if config else None)
        sign = 1.0 if direction else -1.0

        metric_qz = {}
        for quarter in all_quarters:
            if quarter not in qdata.columns:
                continue

            values = qdata[quarter]
            if peer_group_markets is not None:
                peer_values = values[values.index.isin(peer_group_markets)].dropna()
            else:
                peer_values = values.dropna()

            if len(peer_values) < 3:
                continue
            mean = peer_values.mean()
            std = peer_values.std(ddof=1)
            if std == 0 or np.isnan(std):
                continue

            raw_z = sign * (values - mean) / std
            metric_qz[quarter] = raw_z

        quarterly_z_by_metric[tilt_key] = metric_qz

    # --- Step 4: Aggregate quarterly Z into period-level Z (equal-weight) ---
    # For each period, simple average of quarterly Z-scores.
    # Recency is handled solely by the duration weight table (period weights),
    # NOT by intra-period half-life decay.  This ensures that a 10Yr period
    # gives proportional weight to all 10 years, and avoids compounding
    # recency bias across multiple layers.

    # Standalone year definitions: non-overlapping 4-quarter windows
    STANDALONE_PERIODS = {}
    for yr in range(1, 11):  # Yr1 through Yr10
        start = (yr - 1) * 4
        end = yr * 4
        STANDALONE_PERIODS[f"Yr{yr}"] = (start, end)

    use_standalone = config is not None and getattr(config, 'period_mode', 'cumulative') == 'standalone'

    period_z_by_metric = {}  # {tilt_key: {period: Series(market -> avg_z)}}

    def _avg_quarters(metric_qz, quarter_list):
        """Average quarterly Z-scores for a list of quarter names."""
        available = [q for q in quarter_list if q in metric_qz]
        if not available:
            return None
        z_sum = None
        count = 0
        for q in available:
            z_series = metric_qz[q]
            if z_sum is None:
                z_sum = z_series.fillna(0.0).copy()
            else:
                z_sum = z_sum.add(z_series.fillna(0.0), fill_value=0.0)
            count += 1
        if count > 0 and z_sum is not None:
            return z_sum / count
        return None

    for tilt_key, metric_qz in quarterly_z_by_metric.items():
        period_z = {}

        if use_standalone:
            # Non-overlapping year windows
            for period_name, (start, end) in STANDALONE_PERIODS.items():
                period_quarters = all_quarters[start:end]
                avg = _avg_quarters(metric_qz, period_quarters)
                if avg is not None:
                    period_z[period_name] = avg
        else:
            # Original cumulative windows
            for period_name, n_quarters in PERIOD_QUARTERS.items():
                period_quarters = all_quarters[:n_quarters]
                avg = _avg_quarters(metric_qz, period_quarters)
                if avg is not None:
                    period_z[period_name] = avg

        period_z_by_metric[tilt_key] = period_z

    # Handle YTS separately (uses rolling averages, not quarterly Z)
    if "_yts_rolling" in quarterly_by_metric:
        yts_rolling = quarterly_by_metric["_yts_rolling"]
        direction = get_direction("yrs_to_stab", config.direction_overrides if config else None)
        sign = 1.0 if direction else -1.0

        yts_period_z = {}
        for period_name, calc_period in PERIOD_MAP.items():
            if calc_period in yts_rolling:
                values = yts_rolling[calc_period]
                if peer_group_markets is not None:
                    peer_values = values[values.index.isin(peer_group_markets)].dropna()
                else:
                    peer_values = values.dropna()

                if len(peer_values) < 3:
                    continue
                mean = peer_values.mean()
                std = peer_values.std(ddof=1)
                if std > 0 and not np.isnan(std):
                    yts_period_z[period_name] = sign * (values - mean) / std

        period_z_by_metric["yrs_to_stab"] = yts_period_z

    # --- Step 5: Compute per-period Volatility Z per metric ---
    # IMPORTANT: Workbook convention is that HIGHER vol Z = MORE STABLE (less volatile).
    # We compute z-score of std_dev (higher = more volatile), then NEGATE so that
    # the asymmetric formula works correctly:
    #   Stable market → positive vol Z → VolAdj > 1 → amplifies positive signals
    #   Volatile market → negative vol Z → VolAdj < 1 → dampens positive signals
    #
    # Volatility is computed per-period using each period's own quarter window,
    # so the volatility adjustment naturally follows the duration weighting.
    # When duration = 3yr, short-period volatility dominates; at 10yr, long-horizon
    # stability matters more — matching how category scores are aggregated.

    vol_z_by_period = {}  # {period_name: {metric_key: pd.Series}}

    # Build the list of (period_name, quarter_columns) for volatility
    if use_standalone:
        vol_period_defs = []
        for period_name, (start, end) in STANDALONE_PERIODS.items():
            vol_period_defs.append((period_name, all_quarters[start:end]))
    else:
        vol_period_defs = [(pn, all_quarters[:nq]) for pn, nq in PERIOD_QUARTERS.items()]

    for period_name, period_cols in vol_period_defs:
        period_vol = {}
        if len(period_cols) < 2:
            vol_z_by_period[period_name] = {}
            continue

        # Primary metrics via TILT_TO_CALC_KEY
        for tilt_key, calc_key_raw in TILT_TO_CALC_KEY.items():
            calc_key = calc_key_raw
            if calc_key is not None and calc_key in all_detail_results:
                if property_class in all_detail_results[calc_key]:
                    class_data = all_detail_results[calc_key][property_class]
                    scoring = (
                        class_data["derived"]
                        if class_data.get("derived") is not None
                        else class_data["primary"]
                    )
                    avail_cols = [c for c in period_cols if c in scoring.columns]
                    if len(avail_cols) >= 2:
                        market_stds = scoring[avail_cols].std(axis=1, skipna=True)
                        if peer_group_markets is not None:
                            peer_stds = market_stds[market_stds.index.isin(peer_group_markets)]
                        else:
                            peer_stds = market_stds
                        if len(peer_stds.dropna()) >= 3:
                            period_vol[tilt_key] = -compute_z_scores(peer_stds)

        # Derived metric volatility
        for derived_key in ["abs_del", "effective_occ", "yrs_to_stab"]:
            if derived_key in quarterly_by_metric and derived_key not in period_vol:
                qdata = quarterly_by_metric[derived_key]
                if isinstance(qdata, pd.DataFrame):
                    avail_cols = [c for c in period_cols if c in qdata.columns]
                    if len(avail_cols) >= 2:
                        market_stds = qdata[avail_cols].std(axis=1, skipna=True)
                    else:
                        continue
                elif isinstance(qdata, dict):
                    combined = pd.DataFrame(qdata)
                    avail_cols = [c for c in period_cols if c in combined.columns]
                    if len(avail_cols) >= 2:
                        market_stds = combined[avail_cols].std(axis=1, skipna=True)
                    else:
                        continue
                else:
                    continue
                if peer_group_markets is not None:
                    peer_stds = market_stds[market_stds.index.isin(peer_group_markets)]
                else:
                    peer_stds = market_stds
                if len(peer_stds.dropna()) >= 3:
                    period_vol[derived_key] = -compute_z_scores(peer_stds)

        vol_z_by_period[period_name] = period_vol

    # --- Step 6: Get all markets ---
    # Score ALL markets that have data, not just peer group markets.
    # The peer group is used only for Z-score normalization (mean/std),
    # but every market gets scored against those stats.
    all_markets = set()
    for tilt_key, pz in period_z_by_metric.items():
        for period_name, series in pz.items():
            all_markets.update(series.dropna().index)
    all_markets = sorted(all_markets)

    print(f"    Building tilt input for {len(all_markets)} markets, "
          f"{len(period_z_by_metric)} metrics (quarterly Z architecture)")

    # --- Step 7: Determine active periods and momentum tilts ---
    # Always include Q1 (single most recent quarter) for workbook comparison,
    # plus all periods that have weights in config.
    if use_standalone:
        # Standalone mode: use Yr1..YrN based on analysis duration
        n_years = config.analysis_duration_years if config else 10
        active_periods = [f"Yr{yr}" for yr in range(1, n_years + 1)
                          if f"Yr{yr}" in config.standalone_period_weights]
    else:
        active_periods = ["Q1"] + [p for p in PERIOD_QUARTERS if p in config.period_weights and p != "Q1"]

    tilt_values = {}
    for tilt_period in active_periods:
        if use_standalone:
            # In standalone mode, momentum is disabled (each year is independent)
            tilt_values[tilt_period] = 1.0
        else:
            mc = config.momentum_config.get(tilt_period)
            if mc is not None and mc[0] > 0:
                hl_steps = mc[0] / config.recent_momentum_tilt_multiplier
                period_step_map = {"Annual": 0, "2Yr": 1, "3Yr": 2, "5Yr": 3, "10Yr": 4}
                step = period_step_map.get(tilt_period, 0)
                tilt_values[tilt_period] = max_momentum_tilt(step, hl_steps, mc[1])
            else:
                tilt_values[tilt_period] = 1.0

    ds_metrics = list(config.ds_metric_weights.keys())
    occ_metrics = list(config.occ_metric_weights.keys())
    rent_metrics = list(config.rent_metric_weights.keys())

    # --- Step 8: Build per-market, per-period data ---
    market_data = {}

    for market in all_markets:
        market_periods = {}

        for tilt_period in active_periods:
            # Signal indicators: half-life-weighted quarterly Z averages for this period
            signal_indicators = {}
            ext_metric_keys = list(getattr(config, 'external_metric_weights', {}).keys())
            all_metric_keys = (list(config.ds_metric_weights.keys())
                               + list(config.occ_metric_weights.keys())
                               + list(config.rent_metric_weights.keys())
                               + ext_metric_keys
                               + ["actual_occ", "effective_occ"])
            for tilt_key in all_metric_keys:
                if tilt_key in period_z_by_metric and tilt_period in period_z_by_metric[tilt_key]:
                    series = period_z_by_metric[tilt_key][tilt_period]
                    signal_indicators[tilt_key] = (
                        series.get(market, 0.0)
                        if market in series.index else 0.0
                    )
                else:
                    signal_indicators[tilt_key] = 0.0

            # Volatility indicators (per-period — each period uses its own window)
            period_vol = vol_z_by_period.get(tilt_period, {})
            volatility_indicators = {}
            for tilt_key in all_metric_keys:
                if tilt_key in period_vol:
                    series = period_vol[tilt_key]
                    volatility_indicators[tilt_key] = (
                        series.get(market, 0.0)
                        if market in series.index else 0.0
                    )
                else:
                    volatility_indicators[tilt_key] = 0.0

            # Category values: list of all signal Z's within each group
            # D&S category: absorption, deliveries, abs_del + active external metrics
            ds_signal_vals = []
            for mk in ds_metrics:
                ds_signal_vals.append(signal_indicators.get(mk, 0.0))
            # Include external metric signals in D&S category cross-referencing
            # (only those with non-zero weight, to avoid polluting category Z)
            active_ext = [mk for mk, w in getattr(config, 'external_metric_weights', {}).items()
                          if w != 0.0]
            for mk in active_ext:
                ds_signal_vals.append(signal_indicators.get(mk, 0.0))
            all_ds_keys = ds_metrics + active_ext
            ds_category_values = {mk: ds_signal_vals.copy() for mk in all_ds_keys}

            # Occ category: blended_occ, under_construction, yrs_to_stab
            blended_occ_signal_z = (
                config.actual_occ_weight * signal_indicators.get("actual_occ", 0.0)
                + config.effective_occ_weight * signal_indicators.get("effective_occ", 0.0)
            )
            occ_signal_vals = []
            for mk in occ_metrics:
                if mk == "blended_occ":
                    occ_signal_vals.append(blended_occ_signal_z)
                else:
                    occ_signal_vals.append(signal_indicators.get(mk, 0.0))
            occ_category_values = {mk: occ_signal_vals.copy() for mk in occ_metrics}

            # Rent category
            rent_signal_vals = []
            for mk in rent_metrics:
                rent_signal_vals.append(signal_indicators.get(mk, 0.0))
            rent_category_values = {mk: rent_signal_vals.copy() for mk in rent_metrics}

            # Period signal Z: per-period constants from config for each category
            # In standalone mode, set to 0.0 (per_adj=1.0) to remove the
            # always-positive period adjustment bias. The constants were
            # calibrated for cumulative periods and don't apply to standalone years.
            if use_standalone:
                ds_period_z = 0.0
                occ_period_z = 0.0
                rent_period_z = 0.0
            else:
                ds_period_z = config.ds_period_signal_z.get(tilt_period, config.q1_ds_period_signal_z)
                occ_period_z = config.occ_period_signal_z.get(tilt_period, config.q1_occ_period_signal_z)
                rent_period_z = config.rent_period_signal_z.get(tilt_period, config.q1_rent_period_signal_z)

            market_periods[tilt_period] = {
                "signal_indicators": signal_indicators,
                "volatility_indicators": volatility_indicators,
                "ds_category_values": ds_category_values,
                "occ_category_values": occ_category_values,
                "rent_category_values": rent_category_values,
                "ds_period_signal_z": ds_period_z,
                "occ_period_signal_z": occ_period_z,
                "rent_period_signal_z": rent_period_z,
                "tilt_value": tilt_values.get(tilt_period, 1.0),
            }

        if market_periods:
            market_data[market] = market_periods

    # --- Step 9: Compute period_dispersion_z per market per period ---
    # Dispersion = how spread-out a market's metric z-scores are within each period.
    # For each market/period, compute std of all signal z's, then z-score across peers.
    for tilt_period in active_periods:
        # Collect raw dispersion (std of signal z) per market
        market_disp = {}
        for market, periods in market_data.items():
            if tilt_period not in periods:
                continue
            sig = periods[tilt_period]["signal_indicators"]
            vals = [v for v in sig.values() if v != 0.0]
            if len(vals) >= 2:
                market_disp[market] = float(np.std(vals, ddof=1))
            else:
                market_disp[market] = 0.0

        if not market_disp:
            continue

        disp_series = pd.Series(market_disp)
        disp_mean = disp_series.mean()
        disp_std = disp_series.std(ddof=1)
        if disp_std > 0 and not np.isnan(disp_std):
            disp_z = (disp_series - disp_mean) / disp_std
        else:
            disp_z = pd.Series(0.0, index=disp_series.index)

        for market in market_data:
            if tilt_period in market_data[market]:
                market_data[market][tilt_period]["period_dispersion_z"] = float(
                    disp_z.get(market, 0.0)
                )

    return market_data


# ---------------------------------------------------------------------------
# Convenience: score all markets for one tier
# ---------------------------------------------------------------------------

def score_tier(
    all_detail_results: dict,
    property_class: str = "All",
    config=None,
    peer_group_markets: set = None,
) -> dict:
    """
    Build tilt_engine input and score all markets for one property class tier.

    Returns
    -------
    dict: {market_id: MarketScore}  (from tilt_engine)
    """
    from .tilt_engine import score_all_markets, DEFAULT_CONFIG

    if config is None:
        config = DEFAULT_CONFIG

    market_data = build_tilt_engine_input(
        all_detail_results, property_class, config,
        peer_group_markets=peer_group_markets,
    )

    # score_all_markets expects {tier: {market: {period: {...}}}}
    tier_data = {property_class: market_data}
    results = score_all_markets(tier_data, config)

    return results.get(property_class, {})


# ---------------------------------------------------------------------------
# Main — standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Z-Score Engine — Module 4 (Refactored for tilt_engine integration)")
    print("=" * 60)

    # Simple tests
    np.random.seed(42)
    markets = [f"Market_{i}" for i in range(20)]

    sample_values = pd.Series(np.random.normal(0.005, 0.002, 20), index=markets)
    z = compute_z_scores(sample_values)

    print(f"\nSample Z-scores (20 markets):")
    print(f"  Mean: {z.mean():.4f} (should be ~0)")
    print(f"  Std:  {z.std():.4f} (should be ~1)")

    print(f"\nPeriod mapping: {PERIOD_MAP}")
    print(f"Tilt metric keys: {list(TILT_TO_CALC_KEY.keys())}")

    print("\nZ-score engine ready for tilt_engine integration.")
