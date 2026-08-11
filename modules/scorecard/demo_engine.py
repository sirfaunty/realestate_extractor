"""
demo_engine.py — Demographics Scorecard Module
================================================

Parallel module to the MF Fundamentals scoring pipeline, focused on
demographics data: Population/Household, Income/Rent Affordability,
and Employment Growth.

Architecture mirrors tilt_engine.py + z_score_engine.py:
  1. DemoScorecardConfig — all tuneable knobs
  2. compute_demo_metrics() — derive 26 metrics from raw CoStar fields
  3. build_demo_tilt_input() — quarterly Z → period Z (half-life weighted)
  4. score_demo_tier() — full scoring pipeline entry point

Reuses tilt_engine core math:
  asymmetric_adjust, bounded_multiplier, total_z_score_per_metric,
  period_adjustment, momentum_effective, etc.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Configuration
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DemoScorecardConfig:
    """
    All tuneable knobs for the Demographics scorecard.
    """

    # --- Category Weights (3 categories, must sum to 1.0) ---
    pop_weight: float = 0.30          # Population / Household Stats
    afford_weight: float = 0.35       # Median Income / Rent Affordability
    emp_weight: float = 0.35          # Employment Growth

    # --- Analysis Duration ---
    analysis_duration_years: int = 10
    auto_duration_weights: bool = True

    # --- Unit Tier Weights (same as MF) ---
    tier_weights: dict[str, float] = field(default_factory=lambda: {
        "All":          0.40,
        "4 & 5 Star":   0.25,
        "3 Star":       0.25,
        "1 & 2 Star":   0.10,
    })

    # --- Signal Indicator Parameters (reuse MF defaults) ---
    category_indicator:   tuple[float, float, float] = (3.0, 0.25, 0.25)
    volatility_indicator: tuple[float, float, float] = (3.0, 0.35, 0.25)
    period_indicator:     tuple[float, float, float] = (3.0, 0.20, 0.25)

    # --- Total Z-Score Clamping ---
    total_z_cap: float = 3.0
    total_z_floor: float = -3.0

    # --- Broad Dispersion Weight (mirrors MF dispersion_weight) ---
    # Same formula as vol/cat/period: mult = 1 + weight × disp_z.
    # 0 = no effect (default). Higher = more sensitivity to cross-metric agreement.
    # Applied to all 3 category scores (Pop/HH, Afford, Emp) before final blend.
    demo_dispersion_weight: float = 0.0
    demo_dispersion_cap: float = 2.0
    demo_dispersion_floor: float = 0.5

    # --- Momentum ---
    mom_knob: float = 0.35
    recent_momentum_tilt_multiplier: float = 1.0
    momentum_config: dict[str, tuple[float, float, float]] = field(default_factory=lambda: {
        "Quarterly": (8.0,  0.50, 8),
        "Annual":    (3.0,  0.35, 12),
        "2Yr":       (1.5,  0.30, 12),
        "3Yr":       (1.0,  0.30, 12),
        "5Yr":       (1.0,  0.20, 25),
        "10Yr":      (0.0,  0.00, 0),
        "12Yr":      (0.0,  0.00, 0),
    })

    # --- Period Signal Z Constants ---
    # Demographics data is smoother than MF fundamentals, so use slightly
    # lower period Z constants (less period adjustment needed).
    demo_period_signal_z: dict[str, float] = field(default_factory=lambda: {
        "Q1":     0.9000,
        "Annual": 0.8200,
        "2Yr":    0.7600,
        "3Yr":    0.7200,
        "5Yr":    0.6800,
        "10Yr":   0.6200,
        "12Yr":   0.5800,
    })

    # --- Period Weights (fallback when auto_duration_weights=False) ---
    period_weights: dict[str, float] = field(default_factory=lambda: {
        "Annual":  0.10,
        "2Yr":     0.15,
        "5Yr":     0.25,
        "10Yr":    0.50,
    })

    # --- Dispersion Weight (per-metric, used by affordability) ---
    dispersion_weight: float = 0.0
    dispersion_cap: float = 2.0
    dispersion_floor: float = 0.5

    # --- Direction Overrides ---
    # User-toggled overrides for metric direction (True = higher is better).
    # Keys are demo metric keys (e.g. "mf_inv_pop").
    # Empty dict = use defaults from DEMO_METRIC_DIRECTION.
    direction_overrides: dict[str, bool] = field(default_factory=dict)

    # =====================================================================
    # Category 1: Population / Household Stats (7 metrics)
    # =====================================================================
    # Two-layer blend:
    #   Drivers = "Demographic Growth Drivers" — structured hierarchy
    #   Context = structural ratios (supply-constraint-driven)
    # pop_drivers_weight controls Drivers vs Context blend. Higher = more emphasis
    # on demand-driver metrics.
    pop_drivers_weight: float = 0.80

    # Flat weights (legacy fallback)
    pop_metric_weights: dict[str, float] = field(default_factory=lambda: {
        "pop_growth":          1.0,
        "mf_inv_pop":          1.0,
        "mf_inv_pop_growth":   1.0,
        "hh_growth":           1.0,
        "hh_pop_ratio":        1.0,
        "hh_formation_trend":  1.0,
        "income_growth":       1.0,
    })

    # Legacy per-metric weights (kept for backward compat but no longer used
    # in scoring — replaced by DGD structure below)
    pop_driver_weights: dict[str, float] = field(default_factory=lambda: {
        "pop_growth":          1.0,
        "hh_growth":           1.0,
        "hh_formation_trend":  1.0,
        "income_growth":       1.0,
    })
    pop_context_weights: dict[str, float] = field(default_factory=lambda: {
        "mf_inv_pop":          1.0,
        "mf_inv_pop_growth":   1.0,
        "hh_pop_ratio":        1.0,
    })

    # ---- Demographic Growth Drivers (DGD) ----
    # Structured hierarchy replacing the flat pop_driver_weights average.
    # Four sub-categories, each with its own weight (normalized when used):
    dgd_hhpop_weight: float = 1.0        # HH/Population Growth
    dgd_hh_formation_weight: float = 1.0 # HH Formation Growth
    dgd_income_weight: float = 1.0       # Income Growth
    dgd_emp_weight: float = 1.0          # Employment Growth

    # Within HH/Pop Growth: blend between Household and Population
    dgd_hh_weight: float = 1.0           # Household Growth portion
    dgd_pop_weight: float = 1.0          # Population Growth portion

    # Within Employment Growth: blend between Total, Office, Industrial
    dgd_emp_total_weight: float = 1.0
    dgd_emp_office_weight: float = 1.0
    dgd_emp_industrial_weight: float = 1.0

    # =====================================================================
    # Category 2: Median Income / Rent Affordability Stats (10 metrics)
    # =====================================================================
    # Layer 1: Overall vs Unit Type weight (like MF rent_overall_weight)
    afford_overall_weight: float = 0.50

    # Unit-type dispersion tilt — how much cross-unit-type spread influences
    # the unit-type blend.  0.0 = equal-weight (current default),
    # >0.0 = dispersion-aware weighting (like MF category dispersion).
    afford_unit_dispersion_weight: float = 0.0

    # Layer 2: Snapshot (level) vs Growth (trend) weight
    afford_snapshot_weight: float = 0.60

    # Metric weights within each sub-group
    afford_snapshot_weights: dict[str, float] = field(default_factory=lambda: {
        "afford_all":     1.0,
        "afford_studio":  1.0,
        "afford_1br":     1.0,
        "afford_2br":     1.0,
        "afford_3br":     1.0,
    })
    afford_growth_weights: dict[str, float] = field(default_factory=lambda: {
        "afford_growth_all":     1.0,
        "afford_growth_studio":  1.0,
        "afford_growth_1br":     1.0,
        "afford_growth_2br":     1.0,
        "afford_growth_3br":     1.0,
    })

    # =====================================================================
    # Category 3: Employment Growth Stats (9 metrics)
    # =====================================================================
    # Two-layer blend (mirrors Affordability / Pop/HH):
    #   Drivers = employment growth + job-support growth (demand drivers)
    #   Context = static jobs-per-MF-unit ratios (structural saturation)
    emp_drivers_weight: float = 0.80

    # Flat weights (legacy fallback)
    emp_metric_weights: dict[str, float] = field(default_factory=lambda: {
        "emp_total_growth":       1.0,
        "job_support_total":      1.0,
        "job_support_total_growth": 1.0,
        "emp_office_growth":      1.0,
        "job_support_office":     1.0,
        "job_support_office_growth": 1.0,
        "emp_industrial_growth":  1.0,
        "job_support_industrial": 1.0,
        "job_support_industrial_growth": 1.0,
    })

    # Per-metric weights WITHIN each sub-group
    emp_driver_weights: dict[str, float] = field(default_factory=lambda: {
        "emp_total_growth":              1.0,
        "emp_office_growth":             1.0,
        "emp_industrial_growth":         1.0,
        "job_support_total_growth":      1.0,
        "job_support_office_growth":     1.0,
        "job_support_industrial_growth": 1.0,
    })
    emp_context_weights: dict[str, float] = field(default_factory=lambda: {
        "job_support_total":      1.0,
        "job_support_office":     1.0,
        "job_support_industrial": 1.0,
    })


DEFAULT_DEMO_CONFIG = DemoScorecardConfig()


# Metric direction: True = higher is better, False = lower is better
def get_demo_direction(metric_key: str, overrides: dict = None) -> bool:
    """Get demo metric direction, checking overrides first, then DEMO_METRIC_DIRECTION."""
    if overrides and metric_key in overrides:
        return overrides[metric_key]
    return DEMO_METRIC_DIRECTION.get(metric_key, True)

DEMO_METRIC_DIRECTION = {
    # Pop/Household (Cat 1)
    "pop_growth":          True,   # ▲ More population growth = good
    "mf_inv_pop":          False,  # ▼ Less inventory per person = less saturated
    "mf_inv_pop_growth":   False,  # ▼ Ratio growing = more saturated
    "hh_growth":           False,  # ▼ Per outline
    "hh_pop_ratio":        True,   # ▲ More households per pop = good
    "hh_formation_trend":  True,   # ▲ Accelerating formation = good
    "income_growth":       True,   # ▲ Rising incomes = good

    # Affordability (Cat 2) — all ▼ (lower ratio / lower growth = better)
    "afford_all":           False,
    "afford_studio":        False,
    "afford_1br":           False,
    "afford_2br":           False,
    "afford_3br":           False,
    "afford_growth_all":    False,
    "afford_growth_studio": False,
    "afford_growth_1br":    False,
    "afford_growth_2br":    False,
    "afford_growth_3br":    False,

    # Employment (Cat 3) — all ▲
    "emp_total_growth":           True,
    "job_support_total":          True,
    "job_support_total_growth":   True,
    "emp_office_growth":          True,
    "job_support_office":         True,
    "job_support_office_growth":  True,
    "emp_industrial_growth":      True,
    "job_support_industrial":     True,
    "job_support_industrial_growth": True,
}

# All 26 demo metric keys
ALL_DEMO_METRICS = list(DEMO_METRIC_DIRECTION.keys())

# Category membership
CAT_POP_METRICS = [
    "pop_growth", "mf_inv_pop", "mf_inv_pop_growth",
    "hh_growth", "hh_pop_ratio", "hh_formation_trend", "income_growth",
]

# Two-layer split for Pop/HH (Drivers = demand dynamics, Context = structural ratios)
CAT_POP_DRIVERS = [
    "pop_growth", "hh_growth", "hh_formation_trend", "income_growth",
]
CAT_POP_CONTEXT = [
    "mf_inv_pop", "mf_inv_pop_growth", "hh_pop_ratio",
]
CAT_AFFORD_SNAPSHOT = [
    "afford_all", "afford_studio", "afford_1br", "afford_2br", "afford_3br",
]
CAT_AFFORD_GROWTH = [
    "afford_growth_all", "afford_growth_studio", "afford_growth_1br",
    "afford_growth_2br", "afford_growth_3br",
]
CAT_AFFORD_METRICS = CAT_AFFORD_SNAPSHOT + CAT_AFFORD_GROWTH
CAT_EMP_METRICS = [
    "emp_total_growth", "job_support_total", "job_support_total_growth",
    "emp_office_growth", "job_support_office", "job_support_office_growth",
    "emp_industrial_growth", "job_support_industrial",
    "job_support_industrial_growth",
]

# Two-layer split for Employment (Drivers = growth, Context = static saturation)
CAT_EMP_DRIVERS = [
    "emp_total_growth", "emp_office_growth", "emp_industrial_growth",
    "job_support_total_growth", "job_support_office_growth",
    "job_support_industrial_growth",
]
CAT_EMP_CONTEXT = [
    "job_support_total", "job_support_office", "job_support_industrial",
]


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Compute Demographics Metrics from Raw Data
# ═══════════════════════════════════════════════════════════════════════════

def compute_demo_metrics(
    all_detail_results: dict,
    property_class: str = "All",
) -> dict[str, pd.DataFrame]:
    """
    Compute all 26 demographics metric quarterly DataFrames.

    Takes the existing all_detail_results (which now includes population,
    employment, income, households, office_employment, industrial_employment)
    and derives the 26 metrics as quarterly DataFrames (markets × quarters).

    Returns
    -------
    dict: {demo_metric_key: DataFrame (markets × quarters)}
    """

    def _get_derived_or_primary(metric_key):
        """Get the derived (YoY change) or primary DataFrame for a metric."""
        if metric_key not in all_detail_results:
            return None
        if property_class not in all_detail_results[metric_key]:
            return None
        cd = all_detail_results[metric_key][property_class]
        return cd["derived"] if cd.get("derived") is not None else cd["primary"]

    def _get_primary(metric_key):
        """Get the primary (raw level) DataFrame for a metric."""
        if metric_key not in all_detail_results:
            return None
        if property_class not in all_detail_results[metric_key]:
            return None
        return all_detail_results[metric_key][property_class]["primary"]

    def _safe_ratio(num_df, den_df):
        """Compute ratio with common index/columns, handling division by zero."""
        common_cols = num_df.columns.intersection(den_df.columns)
        common_idx = num_df.index.intersection(den_df.index)
        ratio = num_df.loc[common_idx, common_cols] / den_df.loc[common_idx, common_cols]
        ratio = ratio.replace([np.inf, -np.inf], np.nan)
        return ratio

    def _yoy_change(df):
        """Compute YoY % change (4-quarter lag) from a quarterly DataFrame."""
        if df is None or df.empty:
            return pd.DataFrame()
        from .metric_calculator import quarter_to_index, index_to_quarter
        cols = sorted(df.columns)
        result_data = {}
        col_map = {c: quarter_to_index(c) for c in cols}
        for col in cols:
            lag_idx = col_map[col] - 4
            lag_q = index_to_quarter(lag_idx)
            if lag_q in df.columns:
                current = df[col]
                previous = df[lag_q]
                change = (current - previous) / previous.abs()
                change = change.replace([np.inf, -np.inf], np.nan)
                result_data[col] = change
        if not result_data:
            return pd.DataFrame()
        return pd.DataFrame(result_data, index=df.index)

    metrics = {}

    # --- Raw level DataFrames ---
    pop_level = _get_primary("population")
    hh_level = _get_primary("households")
    income_level = _get_primary("income")
    inv_level = _get_primary("absorption")  # Inventory Units is secondary for absorption
    emp_level = _get_primary("employment")
    office_level = _get_primary("office_employment")
    industrial_level = _get_primary("industrial_employment")

    # Get Inventory Units directly from the absorption metric's secondary
    if "absorption" in all_detail_results and property_class in all_detail_results["absorption"]:
        inv_data = all_detail_results["absorption"][property_class].get("secondary")
        if inv_data is not None:
            inv_level = inv_data

    # Effective rent levels (for affordability ratios)
    rent_all_level = _get_primary("effective_rent_unit")
    rent_studio_level = _get_primary("effective_rent_studio")
    rent_1br_level = _get_primary("effective_rent_1br")
    rent_2br_level = _get_primary("effective_rent_2br")
    rent_3br_level = _get_primary("effective_rent_3br")

    # --- YoY change DataFrames (already computed by metric_calculator) ---
    pop_yoy = _get_derived_or_primary("population")
    hh_yoy = _get_derived_or_primary("households")
    income_yoy = _get_derived_or_primary("income")
    emp_yoy = _get_derived_or_primary("employment")
    office_yoy = _get_derived_or_primary("office_employment")
    industrial_yoy = _get_derived_or_primary("industrial_employment")

    # =====================================================================
    # Category 1: Population / Household Stats
    # =====================================================================

    # 1. Population Growth (YoY %)
    if pop_yoy is not None:
        metrics["pop_growth"] = pop_yoy

    # 2. MF Inventory / Population (ratio — lower = less saturated)
    if inv_level is not None and pop_level is not None:
        metrics["mf_inv_pop"] = _safe_ratio(inv_level, pop_level)

    # 3. MF Inventory / Population Growth (YoY change of ratio)
    if "mf_inv_pop" in metrics:
        metrics["mf_inv_pop_growth"] = _yoy_change(metrics["mf_inv_pop"])

    # 4. Household Growth (YoY %)
    if hh_yoy is not None:
        metrics["hh_growth"] = hh_yoy

    # 5. Households / Population (ratio — higher = more formed households)
    if hh_level is not None and pop_level is not None:
        metrics["hh_pop_ratio"] = _safe_ratio(hh_level, pop_level)

    # 6. Household Formation Trend — CAGR of HH/Population ratio
    # Per outline: (Current HH/Pop Ratio / Prior HH/Pop Ratio)^(1/N) - 1
    # Computed as YoY change of the HH/Pop ratio over time.
    if "hh_pop_ratio" in metrics:
        metrics["hh_formation_trend"] = _yoy_change(metrics["hh_pop_ratio"])

    # 7. Median Income Growth (YoY %)
    if income_yoy is not None:
        metrics["income_growth"] = income_yoy

    # =====================================================================
    # Category 2: Rent Affordability
    # =====================================================================
    # Affordability ratio = (Annual Rent) / (Median Household Income)
    # = (Monthly Rent × 12) / Median Income

    def _compute_afford(rent_level, income_lvl):
        if rent_level is None or income_lvl is None:
            return None
        annual_rent = rent_level * 12.0
        return _safe_ratio(annual_rent, income_lvl)

    afford_all = _compute_afford(rent_all_level, income_level)
    afford_studio = _compute_afford(rent_studio_level, income_level)
    afford_1br = _compute_afford(rent_1br_level, income_level)
    afford_2br = _compute_afford(rent_2br_level, income_level)
    afford_3br = _compute_afford(rent_3br_level, income_level)

    # Snapshot metrics (level)
    if afford_all is not None:
        metrics["afford_all"] = afford_all
    if afford_studio is not None:
        metrics["afford_studio"] = afford_studio
    if afford_1br is not None:
        metrics["afford_1br"] = afford_1br
    if afford_2br is not None:
        metrics["afford_2br"] = afford_2br
    if afford_3br is not None:
        metrics["afford_3br"] = afford_3br

    # Growth metrics (YoY change of affordability ratio)
    if afford_all is not None:
        metrics["afford_growth_all"] = _yoy_change(afford_all)
    if afford_studio is not None:
        metrics["afford_growth_studio"] = _yoy_change(afford_studio)
    if afford_1br is not None:
        metrics["afford_growth_1br"] = _yoy_change(afford_1br)
    if afford_2br is not None:
        metrics["afford_growth_2br"] = _yoy_change(afford_2br)
    if afford_3br is not None:
        metrics["afford_growth_3br"] = _yoy_change(afford_3br)

    # =====================================================================
    # Category 3: Employment Growth Stats
    # =====================================================================

    # 1. Total Employment Growth
    if emp_yoy is not None:
        metrics["emp_total_growth"] = emp_yoy

    # 2. Total Job Support per MF Unit (Employment / Inventory)
    if emp_level is not None and inv_level is not None:
        metrics["job_support_total"] = _safe_ratio(emp_level, inv_level)

    # 3. Total Job Support Growth (YoY change of ratio)
    if "job_support_total" in metrics:
        metrics["job_support_total_growth"] = _yoy_change(metrics["job_support_total"])

    # 4. Office Employment Growth
    if office_yoy is not None:
        metrics["emp_office_growth"] = office_yoy

    # 5. Office Job Support per MF Unit
    if office_level is not None and inv_level is not None:
        metrics["job_support_office"] = _safe_ratio(office_level, inv_level)

    # 6. Office Job Support Growth
    if "job_support_office" in metrics:
        metrics["job_support_office_growth"] = _yoy_change(metrics["job_support_office"])

    # 7. Industrial Employment Growth
    if industrial_yoy is not None:
        metrics["emp_industrial_growth"] = industrial_yoy

    # 8. Industrial Job Support per MF Unit
    if industrial_level is not None and inv_level is not None:
        metrics["job_support_industrial"] = _safe_ratio(industrial_level, inv_level)

    # 9. Industrial Job Support Growth
    if "job_support_industrial" in metrics:
        metrics["job_support_industrial_growth"] = _yoy_change(
            metrics["job_support_industrial"]
        )

    return metrics


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Build Tilt Engine Input (quarterly Z → period Z)
# ═══════════════════════════════════════════════════════════════════════════

def build_demo_tilt_input(
    all_detail_results: dict,
    property_class: str = "All",
    config: DemoScorecardConfig = None,
    peer_group_markets: set = None,
    report_quarter: str = "2025 Q4",
) -> dict[str, dict]:
    """
    Build per-market, per-period data for demographics scoring.

    Mirrors z_score_engine.build_tilt_engine_input() but for demo metrics.

    Returns
    -------
    dict: {market_id: {period_name: {signal_indicators, volatility_indicators, ...}}}
    """
    from .tilt_engine import max_momentum_tilt, get_duration_weights
    from .z_score_engine import compute_z_scores
    from .metric_calculator import quarter_to_index, index_to_quarter

    if config is None:
        config = DEFAULT_DEMO_CONFIG

    # --- Step 1: Compute all 26 demo metrics ---
    demo_metrics = compute_demo_metrics(all_detail_results, property_class)

    if not demo_metrics:
        print("  Warning: No demographics metrics computed")
        return {}

    # --- Step 2: Build ordered quarter list ---
    rq_idx = quarter_to_index(report_quarter)
    max_quarters = config.analysis_duration_years * 4
    all_quarters = [index_to_quarter(rq_idx - i) for i in range(max_quarters)]

    # Period definitions
    ALL_PERIOD_QUARTERS = {
        "Q1": 1, "Annual": 4, "2Yr": 8, "3Yr": 12,
        "5Yr": 20, "10Yr": 40, "12Yr": 48,
    }
    PERIOD_QUARTERS = {
        name: nq for name, nq in ALL_PERIOD_QUARTERS.items()
        if nq <= max_quarters
    }

    # --- Step 3: Compute quarterly Signal Z per metric ---
    quarterly_z_by_metric = {}

    for demo_key, qdata in demo_metrics.items():
        if not isinstance(qdata, pd.DataFrame) or qdata.empty:
            continue

        direction = get_demo_direction(demo_key, config.direction_overrides if config else None)
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

        if metric_qz:
            quarterly_z_by_metric[demo_key] = metric_qz

    # --- Step 4: Aggregate quarterly Z into period-level Z (equal-weight) ---
    # Simple average of quarterly Z-scores within each period.
    # Recency is handled solely by the duration weight table (period weights),
    # NOT by intra-period half-life decay.
    period_z_by_metric = {}
    window_periods = dict(PERIOD_QUARTERS)
    window_periods["Window"] = max_quarters

    for demo_key, metric_qz in quarterly_z_by_metric.items():
        period_z = {}
        for period_name, n_quarters in window_periods.items():
            period_quarters = all_quarters[:n_quarters]
            available_quarters = [q for q in period_quarters if q in metric_qz]

            if not available_quarters:
                continue

            # Equal-weight average of quarterly Z-scores
            z_sum = None
            count = 0
            for q in available_quarters:
                z_series = metric_qz[q]
                if z_sum is None:
                    z_sum = z_series.fillna(0.0).copy()
                else:
                    z_sum = z_sum.add(z_series.fillna(0.0), fill_value=0.0)
                count += 1

            if count > 0 and z_sum is not None:
                period_z[period_name] = z_sum / count

        period_z_by_metric[demo_key] = period_z

    # --- Step 5: Compute per-period Volatility Z per metric ---
    # Volatility is computed per-period using each period's own quarter window,
    # so the volatility adjustment naturally follows the duration weighting.
    # HIGHER vol Z = MORE STABLE (negated: high std → negative vol Z).

    vol_z_by_period = {}  # {period_name: {demo_key: pd.Series}}

    for period_name, n_quarters in PERIOD_QUARTERS.items():
        period_vol = {}
        period_cols = all_quarters[:n_quarters]
        if len(period_cols) < 2:
            vol_z_by_period[period_name] = {}
            continue

        for demo_key, qdata in demo_metrics.items():
            if not isinstance(qdata, pd.DataFrame) or qdata.empty:
                continue
            avail_cols = [c for c in period_cols if c in qdata.columns]
            if len(avail_cols) < 2:
                continue
            market_stds = qdata[avail_cols].std(axis=1, skipna=True)
            if peer_group_markets is not None:
                peer_stds = market_stds[market_stds.index.isin(peer_group_markets)]
            else:
                peer_stds = market_stds
            if len(peer_stds.dropna()) >= 3:
                period_vol[demo_key] = -compute_z_scores(peer_stds)

        vol_z_by_period[period_name] = period_vol

    # --- Step 6: Get all markets ---
    # Score ALL markets that have data, not just peer group markets.
    # The peer group is used only for Z-score normalization (mean/std),
    # but every market gets scored against those stats.
    all_markets = set()
    for pz in period_z_by_metric.values():
        for series in pz.values():
            all_markets.update(series.dropna().index)
    all_markets = sorted(all_markets)

    print(f"    Building demo tilt input for {len(all_markets)} markets, "
          f"{len(period_z_by_metric)} metrics")

    # --- Step 7: Determine active periods and momentum tilts ---
    if config.auto_duration_weights:
        effective_weights = get_duration_weights(config.analysis_duration_years)
        weighted_periods = set(k for k, v in effective_weights.items() if v > 0)
    else:
        weighted_periods = set(config.period_weights.keys())
    active_periods = ["Q1"] + [p for p in PERIOD_QUARTERS if p in weighted_periods and p != "Q1"]
    if "Window" not in active_periods:
        active_periods.append("Window")

    tilt_values = {}
    for tilt_period in active_periods:
        mc = config.momentum_config.get(tilt_period)
        if mc is not None and mc[0] > 0:
            hl_steps = mc[0] / config.recent_momentum_tilt_multiplier
            period_step_map = {"Annual": 0, "2Yr": 1, "5Yr": 2, "10Yr": 3}
            step = period_step_map.get(tilt_period, 0)
            tilt_values[tilt_period] = max_momentum_tilt(step, hl_steps, mc[1])
        else:
            tilt_values[tilt_period] = 1.0

    # --- Step 8: Build per-market, per-period data ---
    market_data = {}

    for market in all_markets:
        market_periods = {}

        for tilt_period in active_periods:
            signal_indicators = {}
            for demo_key in ALL_DEMO_METRICS:
                if demo_key in period_z_by_metric and tilt_period in period_z_by_metric[demo_key]:
                    series = period_z_by_metric[demo_key][tilt_period]
                    signal_indicators[demo_key] = (
                        series.get(market, 0.0)
                        if market in series.index else 0.0
                    )
                else:
                    signal_indicators[demo_key] = 0.0

            # Volatility indicators (per-period — each period uses its own window)
            period_vol = vol_z_by_period.get(tilt_period, {})
            volatility_indicators = {}
            for demo_key in ALL_DEMO_METRICS:
                if demo_key in period_vol:
                    series = period_vol[demo_key]
                    volatility_indicators[demo_key] = (
                        series.get(market, 0.0)
                        if market in series.index else 0.0
                    )
                else:
                    volatility_indicators[demo_key] = 0.0

            # Category values for dispersion Z
            pop_signal_vals = [signal_indicators.get(mk, 0.0) for mk in CAT_POP_METRICS]
            pop_category_values = {mk: pop_signal_vals.copy() for mk in CAT_POP_METRICS}

            afford_signal_vals = [signal_indicators.get(mk, 0.0) for mk in CAT_AFFORD_METRICS]
            afford_category_values = {mk: afford_signal_vals.copy() for mk in CAT_AFFORD_METRICS}

            emp_signal_vals = [signal_indicators.get(mk, 0.0) for mk in CAT_EMP_METRICS]
            emp_category_values = {mk: emp_signal_vals.copy() for mk in CAT_EMP_METRICS}

            # Period signal Z (same constant for all 3 demo categories)
            demo_period_z = config.demo_period_signal_z.get(tilt_period, 0.9)

            market_periods[tilt_period] = {
                "signal_indicators": signal_indicators,
                "volatility_indicators": volatility_indicators,
                "pop_category_values": pop_category_values,
                "afford_category_values": afford_category_values,
                "emp_category_values": emp_category_values,
                "demo_period_signal_z": demo_period_z,
                "tilt_value": tilt_values.get(tilt_period, 1.0),
            }

        if market_periods:
            market_data[market] = market_periods

    return market_data


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Scoring Pipeline
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DemoMetricZScores:
    """Z-score components for a single metric."""
    signal_z: float = 0.0
    volatility_z: float = 0.0
    category_z: float = 0.0
    total_z: float = 0.0


@dataclass
class DemoPeriodScores:
    """Scores for a single period for a single market (demographics)."""
    pop_metric_z: dict[str, DemoMetricZScores] = field(default_factory=dict)
    afford_metric_z: dict[str, DemoMetricZScores] = field(default_factory=dict)
    emp_metric_z: dict[str, DemoMetricZScores] = field(default_factory=dict)
    period_signal_z: float = 0.0
    overall_pop_raw: float = 0.0
    overall_pop_adj: float = 0.0
    overall_afford_raw: float = 0.0
    overall_afford_adj: float = 0.0
    overall_emp_raw: float = 0.0
    overall_emp_adj: float = 0.0
    tilt_value: float = 1.0
    overall_demo: float = 0.0


@dataclass
class DemoMarketScore:
    """Complete scoring result for a single market (demographics)."""
    market_id: str = ""
    tier: str = "All"
    period_scores: dict[str, DemoPeriodScores] = field(default_factory=dict)
    duration_weighted_pop: float = 0.0
    duration_weighted_afford: float = 0.0
    duration_weighted_emp: float = 0.0
    duration_weighted_demo: float = 0.0
    duration_weighted_pop_raw: float = 0.0
    duration_weighted_afford_raw: float = 0.0
    duration_weighted_emp_raw: float = 0.0
    final_score: float = 0.0


def score_demo_market_period(
    data: dict,
    config: DemoScorecardConfig = None,
) -> DemoPeriodScores:
    """
    Score one market for one period (demographics).
    """
    from .tilt_engine import (
        total_z_score_per_metric as total_z,
        category_z_score as cat_z_fn,
        period_adjustment,
        asymmetric_adjust,
        momentum_effective,
        bounded_multiplier,
        dispersion_multiplier,
    )

    if config is None:
        config = DEFAULT_DEMO_CONFIG

    sig = data["signal_indicators"]
    vol = data["volatility_indicators"]

    result = DemoPeriodScores(
        period_signal_z=data["demo_period_signal_z"],
        tilt_value=data["tilt_value"],
    )

    # Use a modified config for total_z that has our demo caps/floors
    # Create a minimal adapter for the tilt_engine functions
    class _Adapter:
        def __init__(self, cfg):
            self.category_indicator = cfg.category_indicator
            self.volatility_indicator = cfg.volatility_indicator
            self.period_indicator = cfg.period_indicator
            self.total_z_cap = cfg.total_z_cap
            self.total_z_floor = cfg.total_z_floor
    adapter = _Adapter(config)

    # --- Category 1: Population / Household ---
    pop_total_z = {}
    for mk in CAT_POP_METRICS:
        s_z = sig.get(mk, 0.0)
        v_z = vol.get(mk, 0.0)
        cat_vals = data["pop_category_values"].get(mk, [])
        c_z = cat_z_fn(s_z, cat_vals) if cat_vals else 0.0
        t_z = total_z(s_z, v_z, c_z, adapter)
        result.pop_metric_z[mk] = DemoMetricZScores(
            signal_z=s_z, volatility_z=v_z, category_z=c_z, total_z=t_z
        )
        pop_total_z[mk] = t_z

    # --- Category 2: Rent Affordability ---
    afford_total_z = {}
    for mk in CAT_AFFORD_METRICS:
        s_z = sig.get(mk, 0.0)
        v_z = vol.get(mk, 0.0)
        cat_vals = data["afford_category_values"].get(mk, [])
        c_z = cat_z_fn(s_z, cat_vals) if cat_vals else 0.0
        t_z = total_z(s_z, v_z, c_z, adapter)
        result.afford_metric_z[mk] = DemoMetricZScores(
            signal_z=s_z, volatility_z=v_z, category_z=c_z, total_z=t_z
        )
        afford_total_z[mk] = t_z

    # --- Category 3: Employment ---
    emp_total_z = {}
    for mk in CAT_EMP_METRICS:
        s_z = sig.get(mk, 0.0)
        v_z = vol.get(mk, 0.0)
        cat_vals = data["emp_category_values"].get(mk, [])
        c_z = cat_z_fn(s_z, cat_vals) if cat_vals else 0.0
        t_z = total_z(s_z, v_z, c_z, adapter)
        result.emp_metric_z[mk] = DemoMetricZScores(
            signal_z=s_z, volatility_z=v_z, category_z=c_z, total_z=t_z
        )
        emp_total_z[mk] = t_z

    # --- Category Roll-ups ---

    def _weighted_avg(metric_keys, weights_dict, totals):
        """Weighted average of total_z over the given metric keys."""
        num = 0.0
        den = 0.0
        for mk in metric_keys:
            w = weights_dict.get(mk, 1.0)
            z = totals.get(mk, 0.0)
            num += w * z
            den += abs(w)
        return (num / den) if den > 0 else 0.0

    # Cat 1: Population/Household — DGD (Demographic Growth Drivers) vs Context blend
    # DGD sub-category 1: HH/Pop Growth — weighted blend of hh_growth + pop_growth
    _dgd_hhpop_num = (
        config.dgd_hh_weight * pop_total_z.get("hh_growth", 0.0)
        + config.dgd_pop_weight * pop_total_z.get("pop_growth", 0.0)
    )
    _dgd_hhpop_den = abs(config.dgd_hh_weight) + abs(config.dgd_pop_weight)
    dgd_hhpop = _dgd_hhpop_num / _dgd_hhpop_den if _dgd_hhpop_den > 0 else 0.0

    # DGD sub-category 2: HH Formation Growth
    dgd_hh_formation = pop_total_z.get("hh_formation_trend", 0.0)

    # DGD sub-category 3: Income Growth
    dgd_income = pop_total_z.get("income_growth", 0.0)

    # DGD sub-category 4: Employment Growth — weighted blend of Total/Office/Industrial
    # (pulls from emp_total_z since these metrics are scored in Employment category)
    _dgd_emp_num = (
        config.dgd_emp_total_weight * emp_total_z.get("emp_total_growth", 0.0)
        + config.dgd_emp_office_weight * emp_total_z.get("emp_office_growth", 0.0)
        + config.dgd_emp_industrial_weight * emp_total_z.get("emp_industrial_growth", 0.0)
    )
    _dgd_emp_den = (
        abs(config.dgd_emp_total_weight)
        + abs(config.dgd_emp_office_weight)
        + abs(config.dgd_emp_industrial_weight)
    )
    dgd_emp = _dgd_emp_num / _dgd_emp_den if _dgd_emp_den > 0 else 0.0

    # DGD composite: weighted average of the 4 sub-categories
    _dgd_num = (
        config.dgd_hhpop_weight * dgd_hhpop
        + config.dgd_hh_formation_weight * dgd_hh_formation
        + config.dgd_income_weight * dgd_income
        + config.dgd_emp_weight * dgd_emp
    )
    _dgd_den = (
        abs(config.dgd_hhpop_weight)
        + abs(config.dgd_hh_formation_weight)
        + abs(config.dgd_income_weight)
        + abs(config.dgd_emp_weight)
    )
    pop_drivers_avg = _dgd_num / _dgd_den if _dgd_den > 0 else 0.0

    pop_context_avg = _weighted_avg(
        CAT_POP_CONTEXT, config.pop_context_weights, pop_total_z
    )
    pop_dw = config.pop_drivers_weight
    result.overall_pop_raw = pop_dw * pop_drivers_avg + (1.0 - pop_dw) * pop_context_avg

    # Cat 2: Affordability — 2-layer weighting
    # Layer 1: Overall vs Unit Type (applied to both snapshot and growth)
    #   With optional dispersion tilt: when afford_unit_dispersion_weight > 0,
    #   each unit type's effective weight is scaled by how far its Z deviates
    #   from the unit-type mean — mirroring MF's category_z_score mechanism.
    def _afford_subgroup_score(metric_keys, overall_key, weights_dict):
        overall_w = config.afford_overall_weight
        overall_z = afford_total_z.get(overall_key, 0.0)

        unit_keys = [k for k in metric_keys if k != overall_key]
        disp_w = config.afford_unit_dispersion_weight

        if disp_w > 0 and len(unit_keys) >= 2:
            # Dispersion-aware: compute cross-unit-type mean/std of total Z,
            # then tilt each unit type's weight by its deviation.
            unit_zs = {mk: afford_total_z.get(mk, 0.0) for mk in unit_keys}
            vals = list(unit_zs.values())
            u_mean = sum(vals) / len(vals)
            u_std = (sum((v - u_mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else 0.0

            unit_score = 0.0
            unit_denom = 0.0
            for mk in unit_keys:
                base_w = weights_dict.get(mk, 1.0)
                z = unit_zs[mk]
                # Dispersion multiplier: how many std devs from unit-type mean
                disp_z = (z - u_mean) / u_std if u_std > 0 else 0.0
                # Scale: base_weight * (1 + disp_weight * |disp_z|)
                # Higher deviation → more weight (amplifies signal from outlier unit types)
                eff_w = base_w * (1.0 + disp_w * abs(disp_z))
                unit_score += eff_w * z
                unit_denom += abs(eff_w)
            unit_avg = unit_score / unit_denom if unit_denom > 0 else 0.0
        else:
            # Equal-weight (original behavior)
            unit_score = 0.0
            unit_denom = 0.0
            for mk in unit_keys:
                w = weights_dict.get(mk, 1.0)
                z = afford_total_z.get(mk, 0.0)
                unit_score += w * z
                unit_denom += abs(w)
            unit_avg = unit_score / unit_denom if unit_denom > 0 else 0.0

        return overall_w * overall_z + (1.0 - overall_w) * unit_avg

    snapshot_score = _afford_subgroup_score(
        CAT_AFFORD_SNAPSHOT, "afford_all", config.afford_snapshot_weights
    )
    growth_score = _afford_subgroup_score(
        CAT_AFFORD_GROWTH, "afford_growth_all", config.afford_growth_weights
    )

    # Layer 2: Snapshot vs Growth blend
    result.overall_afford_raw = (
        config.afford_snapshot_weight * snapshot_score
        + (1.0 - config.afford_snapshot_weight) * growth_score
    )

    # Cat 3: Employment — Drivers (growth) vs Context (static saturation) blend
    emp_drivers_avg = _weighted_avg(
        CAT_EMP_DRIVERS, config.emp_driver_weights, emp_total_z
    )
    emp_context_avg = _weighted_avg(
        CAT_EMP_CONTEXT, config.emp_context_weights, emp_total_z
    )
    emp_dw = config.emp_drivers_weight
    result.overall_emp_raw = emp_dw * emp_drivers_avg + (1.0 - emp_dw) * emp_context_avg

    # Apply period adjustment
    per_adj = period_adjustment(result.period_signal_z, adapter)
    result.overall_pop_adj = asymmetric_adjust(result.overall_pop_raw, per_adj)
    result.overall_afford_adj = asymmetric_adjust(result.overall_afford_raw, per_adj)
    result.overall_emp_adj = asymmetric_adjust(result.overall_emp_raw, per_adj)

    # Broad dispersion weight — mirrors MF dispersion_weight
    # Compute period-level dispersion Z from spread of all metric total Z scores
    pop_for_blend = result.overall_pop_adj
    afford_for_blend = result.overall_afford_adj
    emp_for_blend = result.overall_emp_adj
    if config.demo_dispersion_weight != 0.0:
        all_total_z = list(pop_total_z.values()) + list(afford_total_z.values()) + list(emp_total_z.values())
        if len(all_total_z) >= 2:
            tz_mean = sum(all_total_z) / len(all_total_z)
            tz_std = (sum((v - tz_mean) ** 2 for v in all_total_z) / (len(all_total_z) - 1)) ** 0.5
            period_disp_z = tz_std  # higher std = more dispersion
        else:
            period_disp_z = 0.0
        disp_mult = dispersion_multiplier(
            period_disp_z, config.demo_dispersion_weight,
            config.demo_dispersion_cap, config.demo_dispersion_floor,
        )
        pop_for_blend = asymmetric_adjust(pop_for_blend, disp_mult)
        afford_for_blend = asymmetric_adjust(afford_for_blend, disp_mult)
        emp_for_blend = asymmetric_adjust(emp_for_blend, disp_mult)

    # Overall Demographics = weighted blend with momentum
    mom_eff = momentum_effective(result.tilt_value, config.mom_knob)
    pop_mom = asymmetric_adjust(pop_for_blend, mom_eff)
    afford_mom = asymmetric_adjust(afford_for_blend, mom_eff)
    emp_mom = asymmetric_adjust(emp_for_blend, mom_eff)

    total_w = config.pop_weight + config.afford_weight + config.emp_weight
    if total_w > 0:
        result.overall_demo = (
            config.pop_weight * pop_mom
            + config.afford_weight * afford_mom
            + config.emp_weight * emp_mom
        ) / total_w
    else:
        result.overall_demo = 0.0

    return result


def score_demo_market_all_periods(
    market_data: dict[str, dict],
    config: DemoScorecardConfig = None,
) -> DemoMarketScore:
    """Score a market across all periods and compute duration-weighted result."""
    from .tilt_engine import get_duration_weights

    if config is None:
        config = DEFAULT_DEMO_CONFIG

    result = DemoMarketScore()

    for period, data in market_data.items():
        ps = score_demo_market_period(data, config)
        result.period_scores[period] = ps

    # Duration-weighted aggregation (same pattern as MF)
    # Use get_duration_weights() to blend named period scores (which already
    # have momentum applied via their tilt_values). Previously this branch
    # used the "Window" period score directly, but Window has no momentum_config
    # entry so its tilt_value is always 1.0, making momentum a no-op.
    if config.auto_duration_weights:
        effective_weights = get_duration_weights(config.analysis_duration_years)
    else:
        effective_weights = config.period_weights

    total_weight = 0.0
    w_pop = w_afford = w_emp = w_demo = 0.0
    w_pop_raw = w_afford_raw = w_emp_raw = 0.0

    for period, weight in effective_weights.items():
        if period in result.period_scores:
            ps = result.period_scores[period]
            w_pop += weight * ps.overall_pop_adj
            w_afford += weight * ps.overall_afford_adj
            w_emp += weight * ps.overall_emp_adj
            w_demo += weight * ps.overall_demo
            w_pop_raw += weight * ps.overall_pop_raw
            w_afford_raw += weight * ps.overall_afford_raw
            w_emp_raw += weight * ps.overall_emp_raw
            total_weight += weight

    if total_weight > 0:
        result.duration_weighted_pop = w_pop / total_weight
        result.duration_weighted_afford = w_afford / total_weight
        result.duration_weighted_emp = w_emp / total_weight
        result.duration_weighted_demo = w_demo / total_weight
        result.duration_weighted_pop_raw = w_pop_raw / total_weight
        result.duration_weighted_afford_raw = w_afford_raw / total_weight
        result.duration_weighted_emp_raw = w_emp_raw / total_weight

    result.final_score = result.duration_weighted_demo
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 5 — Entry Point: score all markets for one tier
# ═══════════════════════════════════════════════════════════════════════════

def score_demo_tier(
    all_detail_results: dict,
    property_class: str = "All",
    config: DemoScorecardConfig = None,
    peer_group_markets: set = None,
) -> dict[str, DemoMarketScore]:
    """
    Build input and score all markets for one property class (demographics).

    Returns
    -------
    dict: {market_id: DemoMarketScore}
    """
    if config is None:
        config = DEFAULT_DEMO_CONFIG

    market_data = build_demo_tilt_input(
        all_detail_results, property_class, config,
        peer_group_markets=peer_group_markets,
    )

    results = {}
    for market_id, periods in market_data.items():
        ms = score_demo_market_all_periods(periods, config)
        ms.market_id = market_id
        ms.tier = property_class
        results[market_id] = ms

    return results
