"""
tilt_engine.py — Market Scorecard Scoring Pipeline

Ported from the partner's tilt_engine.py (Module 5).

Implements the full 11-step scoring pipeline:
  1. Signal Indicator Z Score (absolute)
  2. Category Signal Indicator Z Score (signed, within-category)
  3. Volatility / Category bounded multipliers
  4. TOTAL Z Score per metric (Zsig × CatAdj × VolAdj — asymmetric)
  5. Overall D&S (raw) — weighted avg of D&S TOTAL Z's
  6. Overall D&S (adjusted) — × asymmetric Period Adj
  7. Overall Occupancy (raw/adjusted) — separate from D&S
  8. Overall Rent Growth (raw/adjusted)
  9. Overall MF Fundamental — D&S + Occ + Rent, × asymmetric Momentum
 10. Momentum Decay per period (half-life exponential)
 11. Duration-weighted final score

Asymmetric multiplier pattern (used everywhere):
  result = Z × IF(Z ≥ 0, Adj, 1/Adj)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Configuration
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ScorecardConfig:
    """
    All tuneable knobs for the scoring pipeline.

    Defaults from the partner's Z Score Summary Pull Sheet2 (authoritative).
    """

    # --- Unit Tier Weights ---
    tier_weights: Dict[str, float] = field(default_factory=lambda: {
        "All":          0.40,
        "4 & 5 Star":   0.25,
        "3 Star":       0.25,
        "1 & 2 Star":   0.10,
    })

    # --- Category Weights (3-category model: D&S, Occ, Rent) ---
    ds_weight: float = 0.35
    occ_weight: float = 0.25
    rg_weight: float = 0.40

    # --- Analysis Duration ---
    analysis_duration_years: int = 10

    # --- Period Weights ---
    period_weights: Dict[str, float] = field(default_factory=lambda: {
        "Q1":      0.00,
        "Annual":  0.10,
        "2Yr":     0.15,
        "3Yr":     0.00,
        "5Yr":     0.25,
        "10Yr":    0.50,
    })
    auto_duration_weights: bool = False

    # --- Period Mode ---
    period_mode: str = "cumulative"

    standalone_period_weights: Dict[str, float] = field(default_factory=lambda: {
        "Yr1":  10.0, "Yr2":  10.0, "Yr3":  10.0, "Yr4":  10.0, "Yr5":  10.0,
        "Yr6":  10.0, "Yr7":  10.0, "Yr8":  10.0, "Yr9":  10.0, "Yr10": 10.0,
    })

    # --- Momentum Config: {period: (half_life_steps, max_tilt, half_life_qtrs)} ---
    momentum_config: Dict[str, Tuple[float, float, float]] = field(default_factory=lambda: {
        "Quarterly": (8.0,  0.50, 8),
        "Annual":    (3.0,  0.35, 12),
        "2Yr":       (1.5,  0.30, 12),
        "3Yr":       (1.0,  0.30, 12),
        "5Yr":       (1.0,  0.20, 25),
        "10Yr":      (0.0,  0.00, 0),
        "12Yr":      (0.0,  0.00, 0),
    })

    recent_momentum_tilt_multiplier: float = 1.0
    mom_knob: float = 0.35

    # --- Occupancy Blending ---
    actual_occ_weight: float = 0.35
    effective_occ_weight: float = 0.65

    # --- Signal Indicator Parameters: (Cap, W_Impact, W_Min/Floor) ---
    category_indicator:   Tuple[float, float, float] = (3.0, 0.25, 0.25)
    volatility_indicator: Tuple[float, float, float] = (3.0, 0.35, 0.25)
    period_indicator:     Tuple[float, float, float] = (3.0, 0.20, 0.25)

    # --- Period Signal Z Constants ---
    ds_period_signal_z: Dict[str, float] = field(default_factory=lambda: {
        "Q1": 1.1311, "Annual": 0.9838, "2Yr": 0.9178, "3Yr": 0.9080,
        "5Yr": 0.8984, "10Yr": 0.8695, "12Yr": 0.8550,
    })
    occ_period_signal_z: Dict[str, float] = field(default_factory=lambda: {
        "Q1": 0.9000, "Annual": 0.8200, "2Yr": 0.7800, "3Yr": 0.7400,
        "5Yr": 0.7000, "10Yr": 0.6500, "12Yr": 0.6200,
    })
    rent_period_signal_z: Dict[str, float] = field(default_factory=lambda: {
        "Q1": 0.6673, "Annual": 0.6349, "2Yr": 0.6123, "3Yr": 0.5266,
        "5Yr": 0.4409, "10Yr": 0.3554, "12Yr": 0.3200,
    })

    # --- Dispersion Tilt ---
    dispersion_weight: float = 0.0
    dispersion_cap: float = 2.0
    dispersion_floor: float = 0.5

    # --- Total Z-Score Clamping ---
    total_z_cap: float = 3.0
    total_z_floor: float = -3.0

    # --- Additional Knobs ---
    disruption_tilt_multiplier: float = 1.0
    volatility_tilt_multiplier: float = 1.0
    direction_overrides: Dict[str, bool] = field(default_factory=dict)

    # --- D&S Metric Weights ---
    ds_metric_weights: Dict[str, float] = field(default_factory=lambda: {
        "absorption": 1.0, "deliveries": 1.0, "abs_del": 1.0,
    })

    # --- Occ Metric Weights ---
    occ_metric_weights: Dict[str, float] = field(default_factory=lambda: {
        "blended_occ": 1.0, "under_construction": 1.0, "yrs_to_stab": 1.0,
    })

    # --- Rent Metric Weights ---
    rent_metric_weights: Dict[str, float] = field(default_factory=lambda: {
        "eff_rent_overall": 1.0, "eff_rent_1br": 1.0,
        "eff_rent_studio": 1.0, "eff_rent_2br": 1.0, "eff_rent_3br": 1.0,
    })

    # --- External Metric Weights (FRED + Census) ---
    external_metric_weights: Dict[str, float] = field(default_factory=lambda: {
        "sf_permits_yoy": 0.50, "renter_weighted_pop_yoy": 0.50,
        "pop_20_34_share": 0.25,
    })


# ═══════════════════════════════════════════════════════════════════════════
# Duration-Based Period Weight Table
# ═══════════════════════════════════════════════════════════════════════════

DURATION_WEIGHT_TABLE = {
    1:  {"Q1": 0.75, "Annual": 0.25, "2Yr": 0.00, "3Yr": 0.00, "5Yr": 0.00, "10Yr": 0.00},
    2:  {"Q1": 0.50, "Annual": 0.30, "2Yr": 0.20, "3Yr": 0.00, "5Yr": 0.00, "10Yr": 0.00},
    3:  {"Q1": 0.25, "Annual": 0.25, "2Yr": 0.25, "3Yr": 0.25, "5Yr": 0.00, "10Yr": 0.00},
    4:  {"Q1": 0.25, "Annual": 0.40, "2Yr": 0.35, "3Yr": 0.00, "5Yr": 0.00, "10Yr": 0.00},
    5:  {"Q1": 0.25, "Annual": 0.50, "2Yr": 0.00, "3Yr": 0.00, "5Yr": 0.25, "10Yr": 0.00},
    6:  {"Q1": 0.10, "Annual": 0.30, "2Yr": 0.30, "3Yr": 0.30, "5Yr": 0.00, "10Yr": 0.00},
    9:  {"Q1": 0.00, "Annual": 0.30, "2Yr": 0.00, "3Yr": 0.70, "5Yr": 0.00, "10Yr": 0.00},
    10: {"Q1": 0.00, "Annual": 0.10, "2Yr": 0.15, "3Yr": 0.00, "5Yr": 0.25, "10Yr": 0.50},
    12: {"Q1": 0.00, "Annual": 0.25, "2Yr": 0.40, "3Yr": 0.35, "5Yr": 0.00, "10Yr": 0.00},
    15: {"Q1": 0.00, "Annual": 0.10, "2Yr": 0.00, "3Yr": 0.30, "5Yr": 0.60, "10Yr": 0.00},
    20: {"Q1": 0.00, "Annual": 0.00, "2Yr": 0.20, "3Yr": 0.00, "5Yr": 0.30, "10Yr": 0.50},
}


def get_duration_weights(duration_years: int) -> Dict[str, float]:
    """Return period weights for a given analysis duration, with interpolation."""
    if duration_years in DURATION_WEIGHT_TABLE:
        return DURATION_WEIGHT_TABLE[duration_years].copy()

    all_durs = sorted(DURATION_WEIGHT_TABLE.keys())
    if duration_years <= all_durs[0]:
        return DURATION_WEIGHT_TABLE[all_durs[0]].copy()
    if duration_years >= all_durs[-1]:
        return DURATION_WEIGHT_TABLE[all_durs[-1]].copy()

    lower = max(d for d in all_durs if d <= duration_years)
    upper = min(d for d in all_durs if d >= duration_years)
    if lower == upper:
        return DURATION_WEIGHT_TABLE[lower].copy()

    frac = (duration_years - lower) / (upper - lower)
    lo_w = DURATION_WEIGHT_TABLE[lower]
    hi_w = DURATION_WEIGHT_TABLE[upper]
    all_periods = set(lo_w.keys()) | set(hi_w.keys())
    return {p: lo_w.get(p, 0.0) * (1 - frac) + hi_w.get(p, 0.0) * frac
            for p in all_periods}


DEFAULT_CONFIG = ScorecardConfig()


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Asymmetric Multiplier (core pattern used everywhere)
# ═══════════════════════════════════════════════════════════════════════════

def asymmetric_adjust(base: float, adj: float) -> float:
    """
    result = base × IF(base ≥ 0, adj, 1/adj)

    Positive base: amplify. Negative base: dampen.
    """
    if np.isnan(base) or np.isnan(adj) or adj == 0:
        return base
    if base >= 0:
        return base * adj
    else:
        return base * (1.0 / adj)


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Bounded Multiplier
# ═══════════════════════════════════════════════════════════════════════════

def bounded_multiplier(z_score: float, w_impact: float,
                       cap: float, floor: float = 0.25) -> float:
    """raw = 1 + w_impact * z_score, clamped to [floor, cap]."""
    raw = 1.0 + w_impact * z_score
    return min(cap, max(floor, raw))


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Signal Indicator Z Score
# ═══════════════════════════════════════════════════════════════════════════

def signal_indicator_z_score(value: float, period_mean: float,
                              period_std: float) -> float:
    """(value - mean) / std — signed."""
    if period_std == 0 or np.isnan(period_std) or np.isnan(value):
        return 0.0
    return (value - period_mean) / period_std


def signal_indicator_z_scores_batch(values: pd.Series) -> pd.Series:
    """Batch compute Signal Indicator Z Scores for all markets in a period."""
    clean = values.dropna()
    if len(clean) < 3:
        return pd.Series(0.0, index=values.index)
    mean = clean.mean()
    std = clean.std(ddof=1)
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=values.index)
    return (values - mean) / std


# ═══════════════════════════════════════════════════════════════════════════
# Section 5 — Category Z Score
# ═══════════════════════════════════════════════════════════════════════════

def category_z_score(metric_value: float, category_values: list) -> float:
    """(metric_value - category_mean) / category_std — signed."""
    clean = [v for v in category_values if not np.isnan(v)]
    if len(clean) < 2:
        return 0.0
    avg = np.mean(clean)
    std = np.std(clean, ddof=1)
    if std == 0 or np.isnan(std) or np.isnan(metric_value):
        return 0.0
    return (metric_value - avg) / std


def blended_occupancy(actual_occ: float, effective_occ: float,
                       config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """0.35 * actual_occ + 0.65 * effective_occ."""
    return config.actual_occ_weight * actual_occ + config.effective_occ_weight * effective_occ


# ═══════════════════════════════════════════════════════════════════════════
# Section 6 — TOTAL Z Score per Metric
# ═══════════════════════════════════════════════════════════════════════════

def total_z_score_per_metric(signal_z: float, volatility_z: float,
                              category_z: float,
                              config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """
    TOTAL = (Zsig × CatAdj) × IF(base ≥ 0, VolAdj, 1/VolAdj)
    Clamped to [total_z_floor, total_z_cap].
    """
    vol_cap, vol_w, vol_floor = config.volatility_indicator
    cat_cap, cat_w, cat_floor = config.category_indicator

    if np.isnan(signal_z):
        signal_z = 0.0
    if np.isnan(volatility_z):
        volatility_z = 0.0
    if np.isnan(category_z):
        category_z = 0.0

    vol_adj = bounded_multiplier(volatility_z, vol_w, vol_cap, vol_floor)
    cat_adj = bounded_multiplier(category_z, cat_w, cat_cap, cat_floor)

    base = signal_z * cat_adj
    raw_total = asymmetric_adjust(base, vol_adj)
    return min(config.total_z_cap, max(config.total_z_floor, raw_total))


# ═══════════════════════════════════════════════════════════════════════════
# Section 7 — Momentum Decay
# ═══════════════════════════════════════════════════════════════════════════

def momentum_decay(period_step: int, half_life_steps: float) -> float:
    """POWER(2, -period_step / effective_half_life)."""
    if half_life_steps <= 0:
        return 1.0 if period_step == 0 else 0.0
    return 2.0 ** (-period_step / half_life_steps)


def max_momentum_tilt(period_step: int, half_life_steps: float,
                       max_tilt: float) -> float:
    """1 + max_tilt × decay."""
    decay = momentum_decay(period_step, half_life_steps)
    return 1.0 + max_tilt * decay


# ═══════════════════════════════════════════════════════════════════════════
# Section 8 — Period Signal Adjustment
# ═══════════════════════════════════════════════════════════════════════════

def period_adjustment(period_z: float,
                       config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """MIN(Cap, MAX(Floor, 1 + Wper * Zper))."""
    cap, w_impact, floor = config.period_indicator
    if np.isnan(period_z):
        return 1.0
    return bounded_multiplier(period_z, w_impact, cap, floor)


# ═══════════════════════════════════════════════════════════════════════════
# Section 9 — Overall Demand & Supply
# ═══════════════════════════════════════════════════════════════════════════

def overall_demand_supply_raw(metric_total_z: Dict[str, float],
                               config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """Weighted average of D&S + external metric total Z scores."""
    all_weights = dict(config.ds_metric_weights)
    all_weights.update(config.external_metric_weights)

    score = 0.0
    denom = 0.0
    for key, w in all_weights.items():
        z = metric_total_z.get(key, 0.0)
        if np.isnan(z):
            z = 0.0
        score += w * z
        denom += abs(w)
    return score / denom if denom else 0.0


def overall_demand_supply(metric_total_z: Dict[str, float],
                           period_signal_z: float,
                           config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """D&S with asymmetric period adjustment."""
    zds = overall_demand_supply_raw(metric_total_z, config)
    per_adj = period_adjustment(period_signal_z, config)
    return asymmetric_adjust(zds, per_adj)


# ═══════════════════════════════════════════════════════════════════════════
# Section 9b — Overall Occupancy
# ═══════════════════════════════════════════════════════════════════════════

def overall_occupancy_raw(metric_total_z: Dict[str, float],
                           config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """Weighted average of Occ metric total Z scores."""
    weights = config.occ_metric_weights
    score = 0.0
    denom = 0.0
    for key, w in weights.items():
        z = metric_total_z.get(key, 0.0)
        if np.isnan(z):
            z = 0.0
        score += w * z
        denom += abs(w)
    return score / denom if denom else 0.0


def overall_occupancy(metric_total_z: Dict[str, float],
                       period_signal_z: float,
                       config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """Occupancy with asymmetric period adjustment."""
    zocc = overall_occupancy_raw(metric_total_z, config)
    per_adj = period_adjustment(period_signal_z, config)
    return asymmetric_adjust(zocc, per_adj)


# ═══════════════════════════════════════════════════════════════════════════
# Section 10 — Overall Rent Growth
# ═══════════════════════════════════════════════════════════════════════════

def overall_rent_growth_raw(metric_total_z: Dict[str, float],
                              config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """Weighted average of Rent metric total Z scores."""
    weights = config.rent_metric_weights
    score = 0.0
    denom = 0.0
    for key, w in weights.items():
        z = metric_total_z.get(key, 0.0)
        if np.isnan(z):
            z = 0.0
        score += w * z
        denom += abs(w)
    return score / denom if denom else 0.0


def overall_rent_growth(metric_total_z: Dict[str, float],
                          period_signal_z: float,
                          config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """Rent growth with asymmetric period adjustment."""
    zrg = overall_rent_growth_raw(metric_total_z, config)
    per_adj = period_adjustment(period_signal_z, config)
    return asymmetric_adjust(zrg, per_adj)


# ═══════════════════════════════════════════════════════════════════════════
# Section 11 — Overall MF Fundamental Score
# ═══════════════════════════════════════════════════════════════════════════

def momentum_effective(tilt_value: float, mom_knob: float) -> float:
    """MomEff = tilt_value ^ MomKnob (softens the momentum tilt)."""
    if np.isnan(tilt_value) or tilt_value <= 0:
        return 1.0
    if mom_knob == 0:
        return 1.0
    return tilt_value ** mom_knob


def overall_mf_fundamental(ds_adj: float, occ_adj: float, rg_adj: float,
                            tilt_value: float,
                            config: ScorecardConfig = DEFAULT_CONFIG) -> float:
    """
    3-category blend with asymmetric momentum:
    MomEff = tilt ^ knob
    Each category adjusted asymmetrically by MomEff.
    Result = weighted average.
    """
    mom_eff = momentum_effective(tilt_value, config.mom_knob)

    zds_mom = asymmetric_adjust(ds_adj, mom_eff)
    zocc_mom = asymmetric_adjust(occ_adj, mom_eff)
    zrg_mom = asymmetric_adjust(rg_adj, mom_eff)

    total_w = config.ds_weight + config.occ_weight + config.rg_weight
    if total_w == 0:
        return 0.0

    return (config.ds_weight * zds_mom
            + config.occ_weight * zocc_mom
            + config.rg_weight * zrg_mom) / total_w


# ═══════════════════════════════════════════════════════════════════════════
# Section 11b — Dispersion Tilt
# ═══════════════════════════════════════════════════════════════════════════

def dispersion_multiplier(disp_z: float, weight: float = 0.0,
                           cap: float = 2.0, floor: float = 0.5) -> float:
    """Convert a dispersion Z-score into a multiplier."""
    if weight == 0.0:
        return 1.0
    raw_mult = 1.0 + weight * disp_z
    return min(cap, max(floor, raw_mult))


def apply_dispersion_to_score(score: float, period_disp_z: float,
                                config: ScorecardConfig) -> float:
    """Apply period dispersion tilt — amplifies magnitude without flipping sign."""
    if config.dispersion_weight == 0.0:
        return score
    mult = dispersion_multiplier(
        period_disp_z, config.dispersion_weight,
        config.dispersion_cap, config.dispersion_floor,
    )
    return asymmetric_adjust(score, mult)


# ═══════════════════════════════════════════════════════════════════════════
# Section 12 — Data Structures
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MetricZScores:
    """Z-score components for a single metric, single period, single market."""
    signal_z: float = 0.0
    volatility_z: float = 0.0
    category_z: float = 0.0
    total_z: float = 0.0


@dataclass
class PeriodScores:
    """Scores for a single period for a single market."""
    ds_metric_z: Dict[str, MetricZScores] = field(default_factory=dict)
    occ_metric_z: Dict[str, MetricZScores] = field(default_factory=dict)
    rent_metric_z: Dict[str, MetricZScores] = field(default_factory=dict)
    ds_period_signal_z: float = 0.0
    occ_period_signal_z: float = 0.0
    rent_period_signal_z: float = 0.0
    volatility_signal_z: float = 0.0
    overall_ds_raw: float = 0.0
    overall_ds_adj: float = 0.0
    overall_occ_raw: float = 0.0
    overall_occ_adj: float = 0.0
    overall_rent_raw: float = 0.0
    overall_rent_adj: float = 0.0
    tilt_value: float = 1.0
    overall_mf: float = 0.0


@dataclass
class MarketScore:
    """Complete scoring result for a single market, single tier."""
    market_id: str = ""
    tier: str = "All"
    period_scores: Dict[str, PeriodScores] = field(default_factory=dict)
    duration_weighted_ds: float = 0.0
    duration_weighted_occ: float = 0.0
    duration_weighted_rent: float = 0.0
    duration_weighted_mf: float = 0.0
    final_score: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Section 13 — Full Market Scoring Pipeline
# ═══════════════════════════════════════════════════════════════════════════

def score_market_period(
    signal_indicators: Dict[str, float],
    volatility_indicators: Dict[str, float],
    ds_category_values: Dict[str, list],
    occ_category_values: Dict[str, list],
    rent_category_values: Dict[str, list],
    ds_period_signal_z: float,
    occ_period_signal_z: float,
    rent_period_signal_z: float,
    tilt_value: float = 1.0,
    period_dispersion_z: float = 0.0,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> PeriodScores:
    """Compute all scores for one market in one period column (3-category model)."""
    result = PeriodScores(
        ds_period_signal_z=ds_period_signal_z,
        occ_period_signal_z=occ_period_signal_z,
        rent_period_signal_z=rent_period_signal_z,
        tilt_value=tilt_value,
    )

    # --- D&S Metrics ---
    ds_total_z = {}
    for metric_key in config.ds_metric_weights:
        sig_z = signal_indicators.get(metric_key, 0.0)
        vol_z = volatility_indicators.get(metric_key, 0.0)
        cat_vals = ds_category_values.get(metric_key, [])
        metric_val = signal_indicators.get(metric_key, np.nan)
        cat_z = category_z_score(metric_val, cat_vals) if cat_vals else 0.0
        total = total_z_score_per_metric(sig_z, vol_z, cat_z, config)
        result.ds_metric_z[metric_key] = MetricZScores(
            signal_z=sig_z, volatility_z=vol_z, category_z=cat_z, total_z=total)
        ds_total_z[metric_key] = total

    # --- External Metrics (flow into D&S) ---
    for metric_key in config.external_metric_weights:
        sig_z = signal_indicators.get(metric_key, 0.0)
        vol_z = volatility_indicators.get(metric_key, 0.0)
        cat_vals = ds_category_values.get(metric_key, [])
        metric_val = signal_indicators.get(metric_key, np.nan)
        cat_z = category_z_score(metric_val, cat_vals) if cat_vals else 0.0
        total = total_z_score_per_metric(sig_z, vol_z, cat_z, config)
        result.ds_metric_z[metric_key] = MetricZScores(
            signal_z=sig_z, volatility_z=vol_z, category_z=cat_z, total_z=total)
        ds_total_z[metric_key] = total

    # --- Occ Metrics ---
    occ_total_z = {}
    for metric_key in config.occ_metric_weights:
        if metric_key == "blended_occ":
            # Blend at TOTAL Z level (actual + effective)
            actual_sig = signal_indicators.get("actual_occ", 0.0)
            actual_vol = volatility_indicators.get("actual_occ", 0.0)
            actual_cat_vals = occ_category_values.get("blended_occ", [])
            actual_cat = category_z_score(actual_sig, actual_cat_vals) if actual_cat_vals else 0.0
            actual_total = total_z_score_per_metric(actual_sig, actual_vol, actual_cat, config)

            eff_sig = signal_indicators.get("effective_occ", 0.0)
            eff_vol = volatility_indicators.get("effective_occ", 0.0)
            eff_cat_vals = occ_category_values.get("blended_occ", [])
            eff_cat = category_z_score(eff_sig, eff_cat_vals) if eff_cat_vals else 0.0
            eff_total = total_z_score_per_metric(eff_sig, eff_vol, eff_cat, config)

            blended_total = (config.actual_occ_weight * actual_total
                             + config.effective_occ_weight * eff_total)
            blended_total = min(config.total_z_cap, max(config.total_z_floor, blended_total))
            blended_sig = (config.actual_occ_weight * actual_sig
                           + config.effective_occ_weight * eff_sig)

            result.occ_metric_z[metric_key] = MetricZScores(
                signal_z=blended_sig, total_z=blended_total)
            occ_total_z[metric_key] = blended_total
            continue

        sig_z = signal_indicators.get(metric_key, 0.0)
        vol_z = volatility_indicators.get(metric_key, 0.0)
        cat_vals = occ_category_values.get(metric_key, [])
        metric_val = signal_indicators.get(metric_key, np.nan)
        cat_z = category_z_score(metric_val, cat_vals) if cat_vals else 0.0
        total = total_z_score_per_metric(sig_z, vol_z, cat_z, config)
        result.occ_metric_z[metric_key] = MetricZScores(
            signal_z=sig_z, volatility_z=vol_z, category_z=cat_z, total_z=total)
        occ_total_z[metric_key] = total

    # --- Rent Metrics ---
    rent_total_z = {}
    for metric_key in config.rent_metric_weights:
        sig_z = signal_indicators.get(metric_key, 0.0)
        vol_z = volatility_indicators.get(metric_key, 0.0)
        cat_vals = rent_category_values.get(metric_key, [])
        metric_val = signal_indicators.get(metric_key, np.nan)
        cat_z = category_z_score(metric_val, cat_vals) if cat_vals else 0.0
        total = total_z_score_per_metric(sig_z, vol_z, cat_z, config)
        result.rent_metric_z[metric_key] = MetricZScores(
            signal_z=sig_z, volatility_z=vol_z, category_z=cat_z, total_z=total)
        rent_total_z[metric_key] = total

    # --- Overall Scores ---
    result.overall_ds_raw = overall_demand_supply_raw(ds_total_z, config)
    result.overall_ds_adj = overall_demand_supply(ds_total_z, ds_period_signal_z, config)
    result.overall_occ_raw = overall_occupancy_raw(occ_total_z, config)
    result.overall_occ_adj = overall_occupancy(occ_total_z, occ_period_signal_z, config)
    result.overall_rent_raw = overall_rent_growth_raw(rent_total_z, config)
    result.overall_rent_adj = overall_rent_growth(rent_total_z, rent_period_signal_z, config)

    # Apply dispersion tilt before MF blend
    ds_for_mf = result.overall_ds_adj
    occ_for_mf = result.overall_occ_adj
    rent_for_mf = result.overall_rent_adj
    if config.dispersion_weight != 0.0:
        ds_for_mf = apply_dispersion_to_score(ds_for_mf, period_dispersion_z, config)
        occ_for_mf = apply_dispersion_to_score(occ_for_mf, period_dispersion_z, config)
        rent_for_mf = apply_dispersion_to_score(rent_for_mf, period_dispersion_z, config)

    result.overall_mf = overall_mf_fundamental(
        ds_for_mf, occ_for_mf, rent_for_mf, tilt_value, config)

    return result


def score_market_all_periods(
    market_data: Dict[str, dict],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> MarketScore:
    """Score a market across all periods and compute duration-weighted result."""
    result = MarketScore()

    for period, data in market_data.items():
        ps = score_market_period(
            signal_indicators=data["signal_indicators"],
            volatility_indicators=data["volatility_indicators"],
            ds_category_values=data["ds_category_values"],
            occ_category_values=data.get("occ_category_values", {}),
            rent_category_values=data["rent_category_values"],
            ds_period_signal_z=data["ds_period_signal_z"],
            occ_period_signal_z=data.get("occ_period_signal_z", 0.9),
            rent_period_signal_z=data["rent_period_signal_z"],
            tilt_value=data.get("tilt_value", 1.0),
            period_dispersion_z=data.get("period_dispersion_z", 0.0),
            config=config,
        )
        result.period_scores[period] = ps

    # Duration-weighted aggregation
    if config.period_mode == "standalone":
        n_years = config.analysis_duration_years
        effective_weights = {}
        for yr in range(1, n_years + 1):
            key = f"Yr{yr}"
            if key in config.standalone_period_weights:
                effective_weights[key] = config.standalone_period_weights[key]
    elif config.auto_duration_weights:
        effective_weights = get_duration_weights(config.analysis_duration_years)
    else:
        effective_weights = config.period_weights

    total_weight = 0.0
    weighted_ds = 0.0
    weighted_occ = 0.0
    weighted_rent = 0.0
    weighted_mf = 0.0

    for period, weight in effective_weights.items():
        if period in result.period_scores:
            ps = result.period_scores[period]
            weighted_ds += weight * ps.overall_ds_adj
            weighted_occ += weight * ps.overall_occ_adj
            weighted_rent += weight * ps.overall_rent_adj
            weighted_mf += weight * ps.overall_mf
            total_weight += weight

    if total_weight > 0:
        result.duration_weighted_ds = weighted_ds / total_weight
        result.duration_weighted_occ = weighted_occ / total_weight
        result.duration_weighted_rent = weighted_rent / total_weight
        result.duration_weighted_mf = weighted_mf / total_weight

    result.final_score = result.duration_weighted_mf
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 14 — Batch Scoring
# ═══════════════════════════════════════════════════════════════════════════

def score_all_markets(
    all_market_data: Dict[str, Dict[str, Dict[str, dict]]],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> Dict[str, Dict[str, MarketScore]]:
    """Score every market across every tier.
    Input: {tier: {market_id: {period: {...}}}}
    Output: {tier: {market_id: MarketScore}}
    """
    results = {}
    for tier, markets in all_market_data.items():
        tier_results = {}
        for market_id, market_data in markets.items():
            ms = score_market_all_periods(market_data, config)
            ms.market_id = market_id
            ms.tier = tier
            tier_results[market_id] = ms
        results[tier] = tier_results
    return results


def compute_final_rankings(
    tier_scores: Dict[str, Dict[str, MarketScore]],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Compute tier-weighted final rankings across all markets."""
    all_markets = set()
    for tier_data in tier_scores.values():
        all_markets.update(tier_data.keys())

    rows = []
    for market_id in sorted(all_markets):
        total_weight = 0.0
        weighted_mf = 0.0
        weighted_ds = 0.0
        weighted_occ = 0.0
        weighted_rent = 0.0
        tier_details = {}

        for tier, weight in config.tier_weights.items():
            if tier in tier_scores and market_id in tier_scores[tier]:
                ms = tier_scores[tier][market_id]
                weighted_mf += weight * ms.final_score
                weighted_ds += weight * ms.duration_weighted_ds
                weighted_occ += weight * ms.duration_weighted_occ
                weighted_rent += weight * ms.duration_weighted_rent
                total_weight += weight
                tier_details[f"tier_{tier}"] = ms.final_score

        if total_weight > 0:
            final = weighted_mf / total_weight
            ds = weighted_ds / total_weight
            occ = weighted_occ / total_weight
            rent = weighted_rent / total_weight
        else:
            final = ds = occ = rent = 0.0

        row = {"market_id": market_id, "final_score": final,
               "ds_score": ds, "occ_score": occ, "rent_score": rent}
        row.update(tier_details)
        rows.append(row)

    df = pd.DataFrame(rows)
    if len(df) > 0:
        df["rank"] = df["final_score"].rank(ascending=False, method="min").astype(int)
        df = df.sort_values("rank").reset_index(drop=True)

    return df


def apply_momentum_weights(
    quarterly_values: list, period_name: str,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """Apply momentum half-life decay to quarterly values."""
    mc = config.momentum_config.get(period_name)
    if mc is None or mc[0] == 0:
        clean = [v for v in quarterly_values if not np.isnan(v)]
        return np.mean(clean) if clean else 0.0

    hl_steps = mc[0] / config.recent_momentum_tilt_multiplier
    max_t = mc[1]

    weights = []
    values = []
    for step, val in enumerate(quarterly_values):
        if not np.isnan(val):
            w = max_momentum_tilt(step, hl_steps, max_t)
            weights.append(w)
            values.append(val)

    if not weights:
        return 0.0
    w_arr = np.array(weights)
    v_arr = np.array(values)
    return float(np.sum(w_arr * v_arr) / np.sum(w_arr))
