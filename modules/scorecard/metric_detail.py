"""
metric_detail.py — Rolling-window drill-down detail builders.

Ported from capactive-scorecard webapp/app.py (build_all_metric_detail /
build_all_demo_metric_detail and their helpers). Backs the Category Data
drill page (/scorecard/category_data), which shows per-metric rolling-window
stats (raw value, peer mean/p90/p10, signal Z, volatility Z) for one market.

Adaptation notes vs the source:
  - The source read module-level globals (_engine_cache, build_peer_group,
    build_display_filter). Here the callers pass `adr` (all_detail_results)
    and `display_markets` (a set to restrict the peer pool, or None for all)
    explicitly — the engine resolves those from its cache + classifications.
  - All computational logic is verbatim.
"""

import numpy as np
import pandas as pd

from .metric_calculator import quarter_to_index, index_to_quarter, PERIOD_AVERAGES
from .z_score_engine import (
    TILT_TO_CALC_KEY, get_direction,
    STABILIZATION_TARGET, ABSORPTION_FLOOR, ABSORPTION_PERCENTILE, YTS_CAP,
)


MF_CATEGORY_METRICS = {
    "Supply & Demand": [
        ("absorption", "Absorption"), ("deliveries", "Deliveries"), ("abs_del", "Abs - Del"),
    ],
    "Occupancy & Yrs to Stabilization": [
        ("blended_occ", "Blended Occ"), ("under_construction", "Under Construction"), ("yrs_to_stab", "Yrs to Stabilize"),
    ],
    "Rent Growth": [
        ("eff_rent_overall", "Eff Rent (Overall)"), ("eff_rent_studio", "Eff Rent (Studio)"),
        ("eff_rent_1br", "Eff Rent (1BR)"), ("eff_rent_2br", "Eff Rent (2BR)"), ("eff_rent_3br", "Eff Rent (3BR)"),
    ],
}

DEMO_CATEGORY_METRICS = {
    "Pop/HH Drivers": [
        ("pop_growth", "Population Growth"), ("hh_growth", "Household Growth"),
        ("hh_formation_trend", "HH Formation Trend"), ("income_growth", "Income Growth"),
    ],
    "Pop/HH Context": [
        ("mf_inv_pop", "MF Inv / Population"), ("mf_inv_pop_growth", "MF Inv/Pop Growth"),
        ("hh_pop_ratio", "HH / Population"),
    ],
    "Affordability — Snapshot": [
        ("afford_all", "Affordability (All)"), ("afford_studio", "Affordability (Studio)"),
        ("afford_1br", "Affordability (1BR)"), ("afford_2br", "Affordability (2BR)"),
        ("afford_3br", "Affordability (3BR)"),
    ],
    "Affordability — Growth": [
        ("afford_growth_all", "Afford Growth (All)"), ("afford_growth_studio", "Afford Growth (Studio)"),
        ("afford_growth_1br", "Afford Growth (1BR)"), ("afford_growth_2br", "Afford Growth (2BR)"),
        ("afford_growth_3br", "Afford Growth (3BR)"),
    ],
    "Employment Drivers": [
        ("emp_total_growth", "Total Emp Growth"), ("emp_office_growth", "Office Emp Growth"),
        ("emp_industrial_growth", "Industrial Emp Growth"),
        ("job_support_total_growth", "Job Support Total Growth"),
        ("job_support_office_growth", "Job Support Office Growth"),
        ("job_support_industrial_growth", "Job Support Industrial Growth"),
    ],
    "Employment Context": [
        ("job_support_total", "Job Support Total"),
        ("job_support_office", "Job Support Office"),
        ("job_support_industrial", "Job Support Industrial"),
    ],
}


def _resolve_metric_quarterly(adr, metric_key, property_class="All"):
    """Get quarterly DataFrame for a standard MF metric."""
    calc_key = TILT_TO_CALC_KEY.get(metric_key, metric_key)

    # Handle derived metrics
    if metric_key == "abs_del":
        abs_df = _resolve_metric_quarterly(adr, "absorption", property_class)
        del_df = _resolve_metric_quarterly(adr, "deliveries", property_class)
        if abs_df is not None and del_df is not None:
            cc = abs_df.columns.intersection(del_df.columns)
            ci = abs_df.index.intersection(del_df.index)
            return abs_df.loc[ci, cc] - del_df.loc[ci, cc]
        return None
    if metric_key == "blended_occ":
        vac_df = _resolve_metric_quarterly(adr, "actual_occ", property_class)
        uc_df = _resolve_metric_quarterly(adr, "under_construction", property_class)
        if vac_df is not None and uc_df is not None:
            cc = vac_df.columns.intersection(uc_df.columns)
            ci = vac_df.index.intersection(uc_df.index)
            return (1.0 - vac_df.loc[ci, cc]) - uc_df.loc[ci, cc]
        return None

    if metric_key == "yrs_to_stab":
        absorption_detail = adr["absorption"][property_class]
        vacancy_detail = adr["vacancy"][property_class]
        construction_detail = adr["under_construction"][property_class]
        abs_q = absorption_detail.get("derived") if absorption_detail.get("derived") is not None else absorption_detail["primary"]
        occ_q = vacancy_detail["primary"]
        uc_q = construction_detail.get("derived") if construction_detail.get("derived") is not None else construction_detail["primary"]
        cc = sorted(abs_q.columns.intersection(occ_q.columns).intersection(uc_q.columns))
        eff_occ = occ_q[cc] - uc_q[cc]
        market_abs_p40 = abs_q.quantile(ABSORPTION_PERCENTILE, axis=1)
        market_abs_floor = market_abs_p40.clip(lower=ABSORPTION_FLOOR)
        abs_used = abs_q[cc].copy()
        for market in abs_used.index:
            floor = market_abs_floor.get(market, ABSORPTION_FLOOR)
            abs_used.loc[market] = abs_used.loc[market].clip(lower=floor)
        gap = STABILIZATION_TARGET - eff_occ
        yts = gap / (abs_used * 4.0)
        yts = yts.replace([np.inf, -np.inf], np.nan).clip(upper=YTS_CAP)
        return yts

    if calc_key not in adr:
        return None
    if property_class not in adr[calc_key]:
        return None
    cd = adr[calc_key][property_class]
    return cd.get("derived") if cd.get("derived") is not None else cd.get("primary")


def _compute_rolling_windows(quarterly, market, peer_markets, duration_years,
                             report_quarter="2025 Q4", direction=True):
    """
    Compute individual rolling-window stats for a metric.

    For a 10-year duration: 10 annual periods (Yr 1..Yr 10), 5 two-year blocks
    (Yrs 1-2..Yrs 9-10), 2 five-year blocks (Yrs 1-5, Yrs 6-10), 1 ten-year
    block (Yrs 1-10). Driven by PERIOD_AVERAGES from metric_calculator.

    direction: True = higher is better (positive Z for above-mean).
               False = lower is better (negate Z so above-mean = negative).
    """
    rq_idx = quarter_to_index(report_quarter)
    max_q = duration_years * 4

    if market not in quarterly.index:
        return []

    # Determine which block sizes to include based on duration.
    # Only include block sizes that tile evenly into the duration.
    valid_block_years = set()
    for bs in [1, 2, 3, 5, 10, 12]:
        if bs <= duration_years and duration_years % bs == 0:
            valid_block_years.add(bs)
    valid_block_years.add(duration_years)
    valid_block_quarters = {y * 4 for y in valid_block_years}

    eligible = []
    for name, (start, end) in PERIOD_AVERAGES.items():
        if end > max_q:
            continue
        block_size = end - start
        if block_size not in valid_block_quarters:
            continue
        eligible.append((name, start, end))

    def period_sort_key(item):
        name, start, end = item
        block_size = end - start
        return (block_size, start)
    eligible.sort(key=period_sort_key)

    def group_label(name):
        if name.startswith("Yr "):
            return "Annual"
        parts = name.replace("Yrs ", "").split("-")
        if len(parts) == 2:
            try:
                span = int(parts[1]) - int(parts[0]) + 1
                return f"{span}Yr"
            except ValueError:
                pass
        return name

    # Vectorized peer slice: one row-select up front, then per-window stats
    # via axis-1 reductions instead of per-market Python loops. Numerically
    # identical to the source's loops (mean over valid quarters per market,
    # np.std ddof=1, non-NaN filtering), ~100x faster for large peer pools.
    peer_in_index = [m for m in peer_markets if m in quarterly.index]
    peer_df = quarterly.loc[peer_in_index] if peer_in_index else quarterly.iloc[0:0]

    windows = []
    for pname, start, end in eligible:
        quarters = [index_to_quarter(rq_idx - i) for i in range(start, end)]
        valid_q = [q for q in quarters if q in quarterly.columns]
        if not valid_q:
            continue

        raw_value = float(quarterly.loc[market, valid_q].mean())

        window_df = peer_df[valid_q]
        peer_vals = window_df.mean(axis=1).dropna().values.astype(float)

        if len(peer_vals) >= 3:
            peer_mean = float(np.mean(peer_vals))
            peer_std = float(np.std(peer_vals, ddof=1)) if len(peer_vals) > 1 else 1.0
            peer_p90 = float(np.percentile(peer_vals, 90))
            peer_p10 = float(np.percentile(peer_vals, 10))
            sign = 1.0 if direction else -1.0
            signal_z = sign * ((raw_value - peer_mean) / peer_std) if peer_std > 1e-12 else 0.0

            market_qvals = quarterly.loc[market, valid_q].dropna().values.astype(float)
            volatility = float(np.std(market_qvals, ddof=1)) if len(market_qvals) > 1 else 0.0

            # std(axis=1, ddof=1) is NaN for rows with <2 valid values — the
            # dropna reproduces the source's len>1 filter.
            peer_vols = window_df.std(axis=1, ddof=1).dropna().values.astype(float)
            vol_mean = float(np.mean(peer_vols)) if len(peer_vols) else 0.0
            vol_std = float(np.std(peer_vols, ddof=1)) if len(peer_vols) > 1 else 1.0
            vol_z = (volatility - vol_mean) / vol_std if vol_std > 1e-12 else 0.0
            dispersion = peer_std
        else:
            peer_mean = peer_p90 = peer_p10 = signal_z = 0.0
            volatility = vol_z = dispersion = 0.0

        windows.append({
            "group": group_label(pname),
            "label": pname,
            "raw_value": round(raw_value, 6),
            "peer_mean": round(peer_mean, 6),
            "peer_p90": round(peer_p90, 6),
            "peer_p10": round(peer_p10, 6),
            "signal_z": round(signal_z, 6),
            "volatility": round(volatility, 6),
            "vol_z": round(vol_z, 6),
            "dispersion": round(dispersion, 6),
        })

    return windows


def build_metric_detail(adr, display_markets, metric_key, market, duration_years=10,
                        property_class="All", direction_overrides=None):
    """Build rolling-window detail for one MF metric in one market.

    display_markets: set of markets to use as the peer pool, or None for all.
    """
    quarterly = _resolve_metric_quarterly(adr, metric_key, property_class)
    if quarterly is None:
        return {"error": f"No data for {metric_key}"}

    source = display_markets if display_markets is not None else set(quarterly.index)
    peer_markets = [m for m in source if m in quarterly.index]

    # Map tilt key → calc key for direction lookup (e.g. "deliveries" → "net_deliveries")
    direction_key = TILT_TO_CALC_KEY.get(metric_key, metric_key)
    direction = get_direction(direction_key, direction_overrides)
    windows = _compute_rolling_windows(quarterly, market, peer_markets, duration_years,
                                       direction=direction)
    return {"windows": windows}


def build_all_metric_detail(adr, display_markets, market, duration_years=10,
                            property_class="All", direction_overrides=None):
    """Build detail for ALL MF metrics, grouped by category."""
    categories = []
    for cat_name, metrics in MF_CATEGORY_METRICS.items():
        cat_metrics = []
        for key, label in metrics:
            detail = build_metric_detail(adr, display_markets, key, market,
                                         duration_years, property_class,
                                         direction_overrides=direction_overrides)
            cat_metrics.append({"key": key, "label": label, "detail": detail})
        categories.append({"category": cat_name, "metrics": cat_metrics})
    return {"market": market, "categories": categories}


def build_demo_metric_detail(adr, display_markets, metric_key, market, duration_years=10,
                             direction_overrides=None, property_class="All",
                             _demo_metrics=None):
    """Build rolling-window detail for one demographics metric.

    _demo_metrics: precomputed compute_demo_metrics() dict — pass it when
    calling for many metrics so the 26 DataFrames aren't rebuilt per metric.
    """
    from .demo_engine import compute_demo_metrics, get_demo_direction

    demo_metrics = _demo_metrics if _demo_metrics is not None else compute_demo_metrics(
        adr, property_class=property_class)
    if metric_key not in demo_metrics:
        return {"error": f"No data for {metric_key}"}

    quarterly = demo_metrics[metric_key]
    source = display_markets if display_markets is not None else set(quarterly.index)
    peer_markets = [m for m in source if m in quarterly.index]

    direction = get_demo_direction(metric_key, direction_overrides)
    windows = _compute_rolling_windows(quarterly, market, peer_markets, duration_years,
                                       direction=direction)
    return {"windows": windows}


def build_all_demo_metric_detail(adr, display_markets, market, duration_years=10,
                                 direction_overrides=None, property_class="All"):
    """Build detail for ALL demo metrics, grouped by category."""
    from .demo_engine import compute_demo_metrics

    # Compute the 26 demo metric DataFrames once and share across metrics.
    demo_metrics = compute_demo_metrics(adr, property_class=property_class)

    categories = []
    for cat_name, metrics in DEMO_CATEGORY_METRICS.items():
        cat_metrics = []
        for key, label in metrics:
            detail = build_demo_metric_detail(adr, display_markets, key, market,
                                              duration_years,
                                              direction_overrides=direction_overrides,
                                              property_class=property_class,
                                              _demo_metrics=demo_metrics)
            cat_metrics.append({"key": key, "label": label, "detail": detail})
        categories.append({"category": cat_name, "metrics": cat_metrics})
    return {"market": market, "categories": categories}
