"""
tilt_engine.py — Module 5 of the CoStar Market Scorecard Engine

Implements the full scoring pipeline reverse-engineered from:
  - Assumption Summary workbook (Sheets 1-3)  — formula structures
  - Z Score Summary Pull workbook (Sheets 1-2) — FINAL config values & formulas

Every formula is extracted from _xlfn.LET cells and verified against raw XML.

Pipeline (per market, per unit tier, per period column):
  1. Signal Indicator Z Score (absolute)
  2. Category Signal Indicator Z Score (signed, within-category)
  3. Volatility / Category bounded multipliers
  4. TOTAL Z Score per metric  (Zsig × CatAdj × VolAdj — asymmetric)
  5. Overall D&S (raw)         (weighted avg of 7 D&S TOTAL Z's)
  6. Overall D&S (adjusted)    (× asymmetric Period Adj)
  7. Overall Rent Growth (raw) (weighted avg of 5 rent TOTAL Z's)
  8. Overall Rent Growth (adj) (× asymmetric Period Adj)
  9. Overall MF Fundamental    (D&S + Rent, × asymmetric Momentum)
 10. Momentum Decay per period (half-life exponential)
 11. Duration-weighted final score

IMPORTANT — Asymmetric multiplier pattern:
  All adjustments (Vol, Period, Momentum) use the SAME asymmetric pattern:
    result = Z × IF(Z ≥ 0, Adj, 1/Adj)
  When the base score is positive, the adjustment amplifies (multiply).
  When the base score is negative, the adjustment dampens (divide).
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Configuration (Z Score Summary Pull Sheet2 = FINAL values)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ScorecardConfig:
    """
    All tuneable knobs.

    Defaults come from the Z Score Summary Pull Sheet2 (the FINAL workbook).
    Where values differ from the Assumption Summary, the Z Score Summary Pull
    values are authoritative.
    """

    # --- Unit Tier Weights (Sheet2 rows 4-7) ---
    tier_weights: dict[str, float] = field(default_factory=lambda: {
        "All":          0.40,
        "4 & 5 Star":   0.25,
        "3 Star":       0.25,
        "1 & 2 Star":   0.10,
    })

    # --- Category Weights (3-category model: D&S, Occ, Rent) ---
    ds_weight: float = 0.35
    occ_weight: float = 0.25
    rg_weight: float = 0.40

    # --- Analysis Duration (Sheet2 row 16) ---
    analysis_duration_years: int = 10

    # --- Period Weights (Sheet2 row 27) ---
    # If auto_duration_weights=True, these are overridden by
    # duration_weight_table[analysis_duration_years].
    # Otherwise, these static weights are used directly.
    period_weights: dict[str, float] = field(default_factory=lambda: {
        "Q1":      0.00,
        "Annual":  0.10,
        "2Yr":     0.15,
        "3Yr":     0.00,
        "5Yr":     0.25,
        "10Yr":    0.50,
    })
    auto_duration_weights: bool = False  # set True to auto-compute from duration

    # --- Period Mode ---
    # "cumulative" = original overlapping windows (Annual=Q1-4, 2Yr=Q1-8, etc.)
    # "standalone" = non-overlapping year windows (Yr1=Q1-4, Yr2=Q5-8, etc.)
    # Standalone mode eliminates the recency bias from cumulative overlap.
    period_mode: str = "cumulative"

    # Standalone period weights (used when period_mode="standalone").
    # Equal weighting by default — each year contributes equally.
    # Only years within analysis_duration_years are used; weights are
    # auto-normalized to sum to 1.0 at scoring time.
    standalone_period_weights: dict[str, float] = field(default_factory=lambda: {
        "Yr1":  10.0, "Yr2":  10.0, "Yr3":  10.0, "Yr4":  10.0, "Yr5":  10.0,
        "Yr6":  10.0, "Yr7":  10.0, "Yr8":  10.0, "Yr9":  10.0, "Yr10": 10.0,
    })

    # --- Half-Life Config (Sheet2 rows 49-54) ---
    # {period: (half_life_steps, max_tilt, half_life_qtrs)}
    momentum_config: dict[str, tuple[float, float, float]] = field(default_factory=lambda: {
        "Quarterly": (8.0,  0.50, 8),
        "Annual":    (3.0,  0.35, 12),
        "2Yr":       (1.5,  0.30, 12),
        "3Yr":       (1.0,  0.30, 12),
        "5Yr":       (1.0,  0.20, 25),
        "10Yr":      (0.0,  0.00, 0),   # No tilt
        "12Yr":      (0.0,  0.00, 0),   # No tilt (same as 10Yr)
    })

    # --- Recent Momentum Tilt Multiplier (Sheet2 row 44) ---
    # Scales effective half-lives: EffectiveHL = HL_steps / this value
    recent_momentum_tilt_multiplier: float = 1.0

    # --- Momentum Knob (Sheet2 row 80, col D) ---
    # MomEff = max_momentum_tilt ^ mom_knob
    mom_knob: float = 0.35

    # --- Occupancy Blending (Sheet2 rows 71-72) ---
    actual_occ_weight: float = 0.35
    effective_occ_weight: float = 0.65

    # --- Signal Indicator Parameters (Sheet2 rows 77-79) ---
    # {indicator_type: (Cap, W_Impact, W_Min/Floor)}
    category_indicator:   tuple[float, float, float] = (3.0, 0.25, 0.25)
    volatility_indicator: tuple[float, float, float] = (3.0, 0.35, 0.25)
    period_indicator:     tuple[float, float, float] = (3.0, 0.20, 0.25)

    # --- Period Signal Z Constants (Sheet3 rows 46/132) ---
    # These are per-period constants, NOT per-market.
    # Back-computed from the workbook: PerAdj = 1 + 0.20 * PerZ
    # Q1 constants validated against workbook (D&S PerAdj=1.2262, Rent PerAdj=1.1335).
    # Other periods derived via scaling: PerZ(period) = PerZ(Q1) × ratio,
    # where ratio = mean|signal_z|(period) / mean|signal_z|(Q1) across Over 50K markets.
    # This captures the expected pattern: longer averaging windows reduce noise,
    # so period Z decreases monotonically (less adjustment needed for smoother signals).
    # D&S ratios:  Annual=0.8698, 2Yr=0.8114, 5Yr=0.7943, 10Yr=0.7688
    # Rent ratios: Annual=0.9514, 2Yr=0.9176, 5Yr=0.6607, 10Yr=0.5326
    q1_ds_period_signal_z: float = 1.1311      # keep for backward compat
    q1_rent_period_signal_z: float = 0.6673     # keep for backward compat
    q1_occ_period_signal_z: float = 0.9000      # Occ-specific (between D&S and Rent)
    ds_period_signal_z: dict[str, float] = field(default_factory=lambda: {
        "Q1":     1.1311,
        "Annual": 0.9838,
        "2Yr":    0.9178,
        "3Yr":    0.9080,   # interpolated between 2Yr and 5Yr
        "5Yr":    0.8984,
        "10Yr":   0.8695,
        "12Yr":   0.8550,   # extrapolated from 10Yr trend
    })
    occ_period_signal_z: dict[str, float] = field(default_factory=lambda: {
        "Q1":     0.9000,
        "Annual": 0.8200,
        "2Yr":    0.7800,
        "3Yr":    0.7400,
        "5Yr":    0.7000,
        "10Yr":   0.6500,
        "12Yr":   0.6200,
    })
    rent_period_signal_z: dict[str, float] = field(default_factory=lambda: {
        "Q1":     0.6673,
        "Annual": 0.6349,
        "2Yr":    0.6123,
        "3Yr":    0.5266,   # interpolated between 2Yr and 5Yr
        "5Yr":    0.4409,
        "10Yr":   0.3554,
        "12Yr":   0.3200,   # extrapolated from 10Yr trend
    })

    # --- Dispersion Tilt (from outline) ---
    # Controls how aggressively the model responds to dispersion signals.
    # 1.0 = neutral (no dispersion effect, default)
    # >1.0 = amplify high-dispersion periods/categories
    # <1.0 = dampen dispersion-driven swings (stability-oriented)
    # The tilt works by converting dispersion Z-scores into multipliers
    # that adjust magnitude of signals without flipping their sign.
    dispersion_weight: float = 0.0   # 0 = no effect (default). Same formula as vol/cat/period.
    dispersion_cap: float = 2.0     # max amplification from dispersion
    dispersion_floor: float = 0.5   # max dampening from dispersion

    # --- Total Z-Score Clamping ---
    # Universal cap/floor applied to every metric's total Z after the
    # signal × CatAdj × VolAdj formula.  Prevents extreme outliers
    # (e.g., yrs-to-stabilize) from dominating the composite score.
    total_z_cap: float = 3.0    # max total Z per metric
    total_z_floor: float = -3.0  # min total Z per metric

    # --- Additional Knobs (Sheet2 rows 65, 67) ---
    disruption_tilt_multiplier: float = 1.0   # (row 65)
    volatility_tilt_multiplier: float = 1.0   # (row 67)

    # --- Direction Overrides ---
    # User-toggled overrides for metric direction (True = higher is better).
    # Keys are metric_calculator keys (e.g. "net_deliveries").
    # Empty dict = use defaults from METRIC_DIRECTION.
    direction_overrides: dict[str, bool] = field(default_factory=dict)

    # --- D&S Metric Weights (Supply & Demand only) ---
    ds_metric_weights: dict[str, float] = field(default_factory=lambda: {
        "absorption":         1.0,
        "deliveries":         1.0,
        "abs_del":            1.0,
    })

    # --- Occ Metric Weights (Occupancy & Yrs to Stabilization) ---
    occ_metric_weights: dict[str, float] = field(default_factory=lambda: {
        "blended_occ":        1.0,   # blended from Actual+Effective
        "under_construction": 1.0,
        "yrs_to_stab":        1.0,
    })

    # --- Rent Metric Weights (Sheet2 row 69, cols C-G) ---
    rent_metric_weights: dict[str, float] = field(default_factory=lambda: {
        "eff_rent_overall":  1.0,
        "eff_rent_1br":      1.0,
        "eff_rent_studio":   1.0,
        "eff_rent_2br":      1.0,
        "eff_rent_3br":      1.0,
    })

    # --- External Metric Weights (FRED + Census enrichment) ---
    # These supplement the D&S category with external economic indicators.
    # Lower default weights (0.50) so they inform but don't dominate the
    # existing CoStar-derived D&S metrics.
    #
    # sf_permits_yoy:          SF building permit YoY change — supply pressure
    #                          indicator. More SF permits = more housing options
    #                          = competition for multifamily. Direction: INVERSE
    #                          (higher permits → lower score).
    #
    # renter_weighted_pop_yoy: Renter-propensity-weighted population YoY change.
    #                          Weights age cohorts by national renter rates
    #                          (20-34: 65%, 35-54: 40%, 55+: 22%).
    #                          Direction: POSITIVE (growing renter pool = good).
    #
    # pop_20_34_share:         Share of population aged 20-34 (peak renter years).
    #                          Direction: POSITIVE (larger renter cohort = good).
    #
    # Set any weight to 0.0 to disable that metric without removing it.
    external_metric_weights: dict[str, float] = field(default_factory=lambda: {
        "sf_permits_yoy":           0.50,
        "renter_weighted_pop_yoy":  0.50,
        "pop_20_34_share":          0.25,
    })


# ═══════════════════════════════════════════════════════════════════════════
# Duration-Based Period Weight Table (from outline)
# ═══════════════════════════════════════════════════════════════════════════
#
# Maps analysis_duration_years → {period: weight}.
# Short durations emphasize recent data (Annual); long durations emphasize
# structural durability (5Yr, 10Yr). These are defaults — users can override.
#
# Periods not yet wired through the scoring pipeline (3Yr, 12Yr) are included
# for completeness and will be used once Gap #6 is resolved.

DURATION_WEIGHT_TABLE: dict[int, dict[str, float]] = {
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


def get_duration_weights(duration_years: int) -> dict[str, float]:
    """
    Return period weights for a given analysis duration.

    If the exact duration isn't in the table, interpolate between
    the nearest lower and upper entries.
    """
    if duration_years in DURATION_WEIGHT_TABLE:
        return DURATION_WEIGHT_TABLE[duration_years].copy()

    # Interpolation between nearest defined durations
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
    result = {}
    for p in all_periods:
        result[p] = lo_w.get(p, 0.0) * (1 - frac) + hi_w.get(p, 0.0) * frac
    return result


DEFAULT_CONFIG = ScorecardConfig()


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Asymmetric Multiplier (core pattern used EVERYWHERE)
# ═══════════════════════════════════════════════════════════════════════════

def asymmetric_adjust(base: float, adj: float) -> float:
    """
    Apply an adjustment multiplier asymmetrically.

    Formula (from every LET block in the workbook):
        result = base × IF(base ≥ 0, adj, 1/adj)

    When base is positive: amplify by multiplying by adj.
    When base is negative: dampen by dividing by adj (i.e., multiply by 1/adj).

    This is used for Volatility, Period, and Momentum adjustments.
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

def bounded_multiplier(
    z_score: float,
    w_impact: float,
    cap: float,
    floor: float = 0.25,
) -> float:
    """
    Compute a bounded adjustment multiplier from a z-score.

    Formula (from every adjuster in the workbook LET blocks):
        raw  = 1 + w_impact * z_score
        mult = MIN(cap, MAX(floor, raw))

    Parameters
    ----------
    z_score  : The signed z-score
    w_impact : Weight of impact
    cap      : Upper bound (3.0 for all indicators in Z Score Summary Pull)
    floor    : Lower bound (0.25 = W_Min from Sheet2)

    Returns
    -------
    float : multiplier in [floor, cap]
    """
    raw = 1.0 + w_impact * z_score
    return min(cap, max(floor, raw))


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Signal Indicator Z Score (absolute)
# ═══════════════════════════════════════════════════════════════════════════

def signal_indicator_z_score(
    value: float,
    period_mean: float,
    period_std: float,
) -> float:
    """
    Compute Signal Indicator Z Score.

    Formula:  (value - mean) / std

    SIGNED — positive means above peer mean. Direction adjustment (for
    "lower is better" metrics) is applied by the caller via sign multiplier.
    """
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
# Section 5 — Category Signal Indicator Z Score (signed)
# ═══════════════════════════════════════════════════════════════════════════

def category_z_score(
    metric_value: float,
    category_values: list[float],
) -> float:
    """
    Compute Category Signal Indicator Z Score.
    (Assumption Summary Sheet2 rows 121-270)

    Formula:  (metric_value - category_mean) / category_std

    SIGNED — positive means above category average.

    For D&S: category_values = [BlendedOcc, Abs, Del, AbsDel, UC, YTS]
    For Rent: category_values = [EffRent, 1BR, Studio, 2BR, 3BR]
    """
    clean = [v for v in category_values if not np.isnan(v)]
    if len(clean) < 2:
        return 0.0
    avg = np.mean(clean)
    std = np.std(clean, ddof=1)
    if std == 0 or np.isnan(std) or np.isnan(metric_value):
        return 0.0
    return (metric_value - avg) / std


def blended_occupancy(
    actual_occ: float,
    effective_occ: float,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute blended occupancy (Sheet2 rows 71-72).

    Formula:  0.35 * actual_occ + 0.65 * effective_occ
    """
    return (config.actual_occ_weight * actual_occ
            + config.effective_occ_weight * effective_occ)


# ═══════════════════════════════════════════════════════════════════════════
# Section 6 — TOTAL Z Score per Metric
# ═══════════════════════════════════════════════════════════════════════════

def total_z_score_per_metric(
    signal_z: float,
    volatility_z: float,
    category_z: float,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute TOTAL Z Score for a single metric.
    (Z Score Summary Pull Sheet1, rows 29/33/37/41/45/49/53/60/64/68/72/76)

    Formula (from M29 _xlfn.LET block):
        Wvol = Sheet2!$D$78  = 0.35
        Wcat = Sheet2!$D$77  = 0.25
        VolCap = Sheet2!$C$78 = 3.0
        CatCap = Sheet2!$C$77 = 3.0
        VolFloor = Sheet2!$E$78 = 0.25
        CatFloor = Sheet2!$E$77 = 0.25

        VolAdj = MIN(VolCap, MAX(VolFloor, 1 + Wvol * Zvol))
        CatAdj = MIN(CatCap, MAX(CatFloor, 1 + Wcat * Zcat))

        TOTAL = (Zsig × CatAdj) × IF(Zsig ≥ 0, VolAdj, 1/VolAdj)
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

    # Category always multiplies directly; volatility is asymmetric
    base = signal_z * cat_adj
    raw_total = asymmetric_adjust(base, vol_adj)

    # Clamp to universal cap/floor
    return min(config.total_z_cap, max(config.total_z_floor, raw_total))


# ═══════════════════════════════════════════════════════════════════════════
# Section 7 — Momentum Decay (half-life exponential)
# ═══════════════════════════════════════════════════════════════════════════

def momentum_decay(
    period_step: int,
    half_life_steps: float,
) -> float:
    """
    Compute momentum decay factor.
    (Z Score Summary Pull Sheet1 row 22)

    Formula:  POWER(2, -period_step / effective_half_life)

    Where effective_half_life = Sheet2!$G$49 (= HL_steps / RecentMomentumTiltMultiplier)
    """
    if half_life_steps <= 0:
        return 1.0 if period_step == 0 else 0.0
    return 2.0 ** (-period_step / half_life_steps)


def max_momentum_tilt(
    period_step: int,
    half_life_steps: float,
    max_tilt: float,
) -> float:
    """
    Compute maximum momentum tilt multiplier.
    (Z Score Summary Pull Sheet1 row 23)

    Formula:  1 + max_tilt × decay
    """
    decay = momentum_decay(period_step, half_life_steps)
    return 1.0 + max_tilt * decay


# ═══════════════════════════════════════════════════════════════════════════
# Section 8 — Period Signal Adjustment (asymmetric)
# ═══════════════════════════════════════════════════════════════════════════

def period_adjustment(
    period_z: float,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute the Period Adjustment multiplier.
    (Z Score Summary Pull Sheet1 rows 55, 78)

    Formula:
        PerAdj = MIN(PerCap, MAX(PerFloor, 1 + Wper * Zper))

    Where:
      PerCap   = Sheet2!$C$79 = 3.0
      Wper     = Sheet2!$D$79 = 0.20
      PerFloor = Sheet2!$E$79 = 0.25

    This produces the multiplier. It is then applied ASYMMETRICALLY:
      result = base × IF(base ≥ 0, PerAdj, 1/PerAdj)

    Returns the PerAdj multiplier (NOT the final adjusted value).
    """
    cap, w_impact, floor = config.period_indicator
    if np.isnan(period_z):
        return 1.0
    return bounded_multiplier(period_z, w_impact, cap, floor)


# ═══════════════════════════════════════════════════════════════════════════
# Section 9 — Overall Demand & Supply
# ═══════════════════════════════════════════════════════════════════════════

def overall_demand_supply_raw(
    metric_total_z: dict[str, float],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute raw Overall D&S score (NO period adjustment).
    Uses D&S metrics (absorption, deliveries, abs_del) plus any
    external metrics (SF permits, renter-weighted pop, etc.).
    Occupancy metrics are handled separately via overall_occupancy().
    """
    # Combine core D&S weights with external metric weights
    all_weights = dict(config.ds_metric_weights)
    all_weights.update(config.external_metric_weights)

    base_score = 0.0
    denominator = 0.0

    for metric_key, w in all_weights.items():
        z = metric_total_z.get(metric_key, 0.0)
        if np.isnan(z):
            z = 0.0
        base_score += w * z
        denominator += abs(w)

    if denominator == 0:
        return 0.0
    return base_score / denominator


def overall_demand_supply(
    metric_total_z: dict[str, float],
    period_signal_z: float,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute Overall D&S WITH period adjustment (asymmetric).
    """
    zds = overall_demand_supply_raw(metric_total_z, config)
    per_adj = period_adjustment(period_signal_z, config)
    return asymmetric_adjust(zds, per_adj)


# ═══════════════════════════════════════════════════════════════════════════
# Section 9b — Overall Occupancy & Years to Stabilization
# ═══════════════════════════════════════════════════════════════════════════

def overall_occupancy_raw(
    metric_total_z: dict[str, float],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute raw Overall Occupancy score (NO period adjustment).
    Uses occ_metric_weights: blended_occ, under_construction, yrs_to_stab.
    """
    weights = config.occ_metric_weights
    base_score = 0.0
    denominator = 0.0

    for metric_key, w in weights.items():
        z = metric_total_z.get(metric_key, 0.0)
        if np.isnan(z):
            z = 0.0
        base_score += w * z
        denominator += abs(w)

    if denominator == 0:
        return 0.0
    return base_score / denominator


def overall_occupancy(
    metric_total_z: dict[str, float],
    period_signal_z: float,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute Overall Occupancy WITH period adjustment (asymmetric).
    """
    zocc = overall_occupancy_raw(metric_total_z, config)
    per_adj = period_adjustment(period_signal_z, config)
    return asymmetric_adjust(zocc, per_adj)


# ═══════════════════════════════════════════════════════════════════════════
# Section 10 — Overall Rent Growth
# ═══════════════════════════════════════════════════════════════════════════

def overall_rent_growth_raw(
    metric_total_z: dict[str, float],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute raw Overall Rent Growth (NO period adjustment).
    (Z Score Summary Pull Sheet1 row 77)

    Formula:
        Num = Tru*Zru + T1br*Z1br + Tst*Zst + T2br*Z2br + T3br*Z3br
        Den = Tru + T1br + Tst + T2br + T3br
        Result = Num / Den

    With all weights = 1.0, this equals a simple average.
    """
    weights = config.rent_metric_weights
    base_score = 0.0
    denominator = 0.0

    for metric_key, w in weights.items():
        z = metric_total_z.get(metric_key, 0.0)
        if np.isnan(z):
            z = 0.0
        base_score += w * z
        denominator += abs(w)

    if denominator == 0:
        return 0.0
    return base_score / denominator


def overall_rent_growth(
    metric_total_z: dict[str, float],
    period_signal_z: float,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute Overall Rent Growth WITH period adjustment (asymmetric).
    (Z Score Summary Pull Sheet1 row 78)

    Formula:
        Zrg = overall_rent_growth_raw(...)
        PerAdj = MIN(Cap, MAX(Floor, 1 + Wper * Zper))
        Result = Zrg × IF(Zrg ≥ 0, PerAdj, 1/PerAdj)

    Note: Uses the SAME period adjustment formula as D&S (not DispKnob-weighted
    like the Assumption Summary suggested). Zper comes from Sheet3 row 132.
    """
    zrg = overall_rent_growth_raw(metric_total_z, config)
    per_adj = period_adjustment(period_signal_z, config)
    return asymmetric_adjust(zrg, per_adj)


# ═══════════════════════════════════════════════════════════════════════════
# Section 11 — Overall MF Fundamental Score
# ═══════════════════════════════════════════════════════════════════════════

def momentum_effective(
    tilt_value: float,
    mom_knob: float,
) -> float:
    """
    Compute the effective momentum multiplier.
    (Z Score Summary Pull Sheet1 row 79 variable _xlpm.MomEff)

    Formula:  MomEff = max_momentum_tilt ^ MomKnob

    Where:
      tilt_value = value from row 23 (= 1 + max_tilt × decay)
      MomKnob   = Sheet2!$D$80 = 0.35

    The power operation softens the momentum tilt:
      tilt=1.5, knob=0.35 → MomEff = 1.5^0.35 ≈ 1.155
      tilt=1.0,            → MomEff = 1.0
      tilt=1.25, knob=0.35 → MomEff = 1.25^0.35 ≈ 1.084
    """
    if np.isnan(tilt_value) or tilt_value <= 0:
        return 1.0
    if mom_knob == 0:
        return 1.0
    return tilt_value ** mom_knob


def overall_mf_fundamental(
    ds_adj: float,
    occ_adj: float,
    rg_adj: float,
    tilt_value: float,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Compute the Overall MF Fundamental Z Score (3-category blend).

    Formula:
        MomEff = tilt_value ^ MomKnob

        Zds_mom  = Zds  × IF(Zds  ≥ 0, MomEff, 1/MomEff)
        Zocc_mom = Zocc × IF(Zocc ≥ 0, MomEff, 1/MomEff)
        Zrg_mom  = Zrg  × IF(Zrg  ≥ 0, MomEff, 1/MomEff)

        Result = (Wds*Zds_mom + Wocc*Zocc_mom + Wrg*Zrg_mom) / (Wds+Wocc+Wrg)
    """
    mom_eff = momentum_effective(tilt_value, config.mom_knob)

    # Apply momentum asymmetrically to all three categories
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
# Section 11b — Dispersion Tilt Functions
# ═══════════════════════════════════════════════════════════════════════════

def dispersion_multiplier(
    disp_z: float,
    weight: float = 0.0,
    cap: float = 2.0,
    floor: float = 0.5,
) -> float:
    """
    Convert a dispersion Z-score into a multiplier.

    Same formula as volatility/category/period:
        mult = clamp(1 + weight × disp_z, floor, cap)

    weight = 0.0 → no effect (default).
    weight = 0.20 → each z-score unit adds 20% to the multiplier.
    weight = 0.35 → same sensitivity as volatility.
    """
    if weight == 0.0:
        return 1.0  # no dispersion effect

    raw_mult = 1.0 + weight * disp_z
    return min(cap, max(floor, raw_mult))


def apply_dispersion_to_score(
    score: float,
    period_disp_z: float,
    config: ScorecardConfig,
) -> float:
    """
    Apply period dispersion tilt to a score.
    Amplifies magnitude without flipping sign.
    """
    if config.dispersion_weight == 0.0:
        return score  # no dispersion effect

    mult = dispersion_multiplier(
        period_disp_z, config.dispersion_weight,
        config.dispersion_cap, config.dispersion_floor,
    )
    return asymmetric_adjust(score, mult)


# ═══════════════════════════════════════════════════════════════════════════
# Section 12 — Full Market Scoring Pipeline
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
    ds_metric_z: dict[str, MetricZScores] = field(default_factory=dict)
    occ_metric_z: dict[str, MetricZScores] = field(default_factory=dict)
    rent_metric_z: dict[str, MetricZScores] = field(default_factory=dict)
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
    period_scores: dict[str, PeriodScores] = field(default_factory=dict)
    duration_weighted_ds: float = 0.0
    duration_weighted_occ: float = 0.0
    duration_weighted_rent: float = 0.0
    duration_weighted_mf: float = 0.0
    final_score: float = 0.0


def score_market_period(
    signal_indicators: dict[str, float],
    volatility_indicators: dict[str, float],
    ds_category_values: dict[str, list[float]],
    occ_category_values: dict[str, list[float]],
    rent_category_values: dict[str, list[float]],
    ds_period_signal_z: float,
    occ_period_signal_z: float,
    rent_period_signal_z: float,
    tilt_value: float = 1.0,
    period_dispersion_z: float = 0.0,
    category_dispersion_z: dict[str, float] | None = None,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> PeriodScores:
    """
    Compute all scores for one market in one period column (3-category model).

    Parameters
    ----------
    signal_indicators     : {metric_key: signal_indicator_z_score}
    volatility_indicators : {metric_key: volatility_z_score}
    ds_category_values    : {metric_key: [all category values]} for D&S metrics
    occ_category_values   : {metric_key: [all category values]} for Occ metrics
    rent_category_values  : {metric_key: [all category values]} for Rent metrics
    ds_period_signal_z    : Period Signal Indicator Z for D&S
    occ_period_signal_z   : Period Signal Indicator Z for Occ
    rent_period_signal_z  : Period Signal Indicator Z for Rent
    tilt_value            : Max Momentum Tilt for this period
    config                : Scorecard configuration
    """
    result = PeriodScores(
        ds_period_signal_z=ds_period_signal_z,
        occ_period_signal_z=occ_period_signal_z,
        rent_period_signal_z=rent_period_signal_z,
        tilt_value=tilt_value,
    )

    # --- D&S Metrics (absorption, deliveries, abs_del only) ---
    ds_total_z = {}
    for metric_key in config.ds_metric_weights:
        sig_z = signal_indicators.get(metric_key, 0.0)
        vol_z = volatility_indicators.get(metric_key, 0.0)
        cat_vals = ds_category_values.get(metric_key, [])
        metric_val = signal_indicators.get(metric_key, np.nan)
        cat_z = category_z_score(metric_val, cat_vals) if cat_vals else 0.0

        total = total_z_score_per_metric(sig_z, vol_z, cat_z, config)

        mz = MetricZScores(signal_z=sig_z, volatility_z=vol_z,
                           category_z=cat_z, total_z=total)
        result.ds_metric_z[metric_key] = mz
        ds_total_z[metric_key] = total

    # --- External Metrics (FRED permits, Census demographics) ---
    # Scored the same way as D&S metrics but tracked separately.
    # Their total Z's feed into ds_total_z for the overall D&S computation.
    for metric_key in config.external_metric_weights:
        sig_z = signal_indicators.get(metric_key, 0.0)
        vol_z = volatility_indicators.get(metric_key, 0.0)
        cat_vals = ds_category_values.get(metric_key, [])
        metric_val = signal_indicators.get(metric_key, np.nan)
        cat_z = category_z_score(metric_val, cat_vals) if cat_vals else 0.0

        total = total_z_score_per_metric(sig_z, vol_z, cat_z, config)

        mz = MetricZScores(signal_z=sig_z, volatility_z=vol_z,
                           category_z=cat_z, total_z=total)
        result.ds_metric_z[metric_key] = mz
        ds_total_z[metric_key] = total

    # --- Occ Metrics (blended_occ, under_construction, yrs_to_stab) ---
    occ_total_z = {}
    for metric_key in config.occ_metric_weights:
        if metric_key == "blended_occ":
            # Blended Occ: compute TOTAL Z for actual_occ and effective_occ
            # separately, then blend at the TOTAL Z level (matching workbook).
            actual_sig = signal_indicators.get("actual_occ", 0.0)
            actual_vol = volatility_indicators.get("actual_occ", 0.0)
            actual_cat_vals = occ_category_values.get("blended_occ", [])
            actual_cat = category_z_score(actual_sig, actual_cat_vals) if actual_cat_vals else 0.0
            actual_total = total_z_score_per_metric(actual_sig, actual_vol, actual_cat, config)

            effective_sig = signal_indicators.get("effective_occ", 0.0)
            effective_vol = volatility_indicators.get("effective_occ", 0.0)
            effective_cat_vals = occ_category_values.get("blended_occ", [])
            effective_cat = category_z_score(effective_sig, effective_cat_vals) if effective_cat_vals else 0.0
            effective_total = total_z_score_per_metric(effective_sig, effective_vol, effective_cat, config)

            # Blend at TOTAL Z level, then clamp
            blended_total = (config.actual_occ_weight * actual_total
                             + config.effective_occ_weight * effective_total)
            blended_total = min(config.total_z_cap, max(config.total_z_floor, blended_total))

            # Store with blended signal Z for reporting
            blended_sig = (config.actual_occ_weight * actual_sig
                           + config.effective_occ_weight * effective_sig)
            mz = MetricZScores(signal_z=blended_sig, volatility_z=0.0,
                               category_z=0.0, total_z=blended_total)
            result.occ_metric_z[metric_key] = mz
            occ_total_z[metric_key] = blended_total
            continue

        sig_z = signal_indicators.get(metric_key, 0.0)
        vol_z = volatility_indicators.get(metric_key, 0.0)
        cat_vals = occ_category_values.get(metric_key, [])
        metric_val = signal_indicators.get(metric_key, np.nan)
        cat_z = category_z_score(metric_val, cat_vals) if cat_vals else 0.0

        total = total_z_score_per_metric(sig_z, vol_z, cat_z, config)

        mz = MetricZScores(signal_z=sig_z, volatility_z=vol_z,
                           category_z=cat_z, total_z=total)
        result.occ_metric_z[metric_key] = mz
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

        mz = MetricZScores(signal_z=sig_z, volatility_z=vol_z,
                           category_z=cat_z, total_z=total)
        result.rent_metric_z[metric_key] = mz
        rent_total_z[metric_key] = total

    # --- Overall Scores (3-category) ---
    result.overall_ds_raw = overall_demand_supply_raw(ds_total_z, config)
    result.overall_ds_adj = overall_demand_supply(
        ds_total_z, ds_period_signal_z, config
    )
    result.overall_occ_raw = overall_occupancy_raw(occ_total_z, config)
    result.overall_occ_adj = overall_occupancy(
        occ_total_z, occ_period_signal_z, config
    )
    result.overall_rent_raw = overall_rent_growth_raw(rent_total_z, config)
    result.overall_rent_adj = overall_rent_growth(
        rent_total_z, rent_period_signal_z, config
    )

    # Apply dispersion tilt to all three category scores before MF blend
    ds_for_mf = result.overall_ds_adj
    occ_for_mf = result.overall_occ_adj
    rent_for_mf = result.overall_rent_adj
    if config.dispersion_weight != 0.0:
        ds_for_mf = apply_dispersion_to_score(ds_for_mf, period_dispersion_z, config)
        occ_for_mf = apply_dispersion_to_score(occ_for_mf, period_dispersion_z, config)
        rent_for_mf = apply_dispersion_to_score(rent_for_mf, period_dispersion_z, config)

    result.overall_mf = overall_mf_fundamental(
        ds_for_mf, occ_for_mf, rent_for_mf, tilt_value, config
    )

    return result


def score_market_all_periods(
    market_data: dict[str, dict],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> MarketScore:
    """
    Score a market across all periods and compute duration-weighted result.

    Parameters
    ----------
    market_data : {period_name: {
        "signal_indicators": {metric: z},
        "volatility_indicators": {metric: z},
        "ds_category_values": {metric: [vals]},
        "rent_category_values": {metric: [vals]},
        "ds_period_signal_z": float,
        "rent_period_signal_z": float,
        "tilt_value": float,   # from row 23 (Max Momentum Tilt)
        "actual_occ_total_z": float,   # optional
        "effective_occ_total_z": float, # optional
    }}
    """
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
            category_dispersion_z=data.get("category_dispersion_z"),
            config=config,
        )
        result.period_scores[period] = ps

    # Duration-weighted aggregation
    # Choose weight source based on period mode
    if config.period_mode == "standalone":
        # Standalone mode: use per-year weights, limited to analysis duration
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
# Section 13 — Batch Scoring (all markets, all tiers)
# ═══════════════════════════════════════════════════════════════════════════

def score_all_markets(
    all_market_data: dict[str, dict[str, dict[str, dict]]],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> dict[str, dict[str, MarketScore]]:
    """
    Score every market across every tier.

    Parameters
    ----------
    all_market_data : {tier: {market_id: {period: {...}}}}

    Returns
    -------
    {tier: {market_id: MarketScore}}
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
    tier_scores: dict[str, dict[str, MarketScore]],
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """
    Compute tier-weighted final rankings.

    For each market: weighted-average MF Fundamental across tiers,
    then rank by final score.
    """
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
        df["rank"] = df["final_score"].rank(
            ascending=False, method="min"
        ).astype(int)
        df = df.sort_values("rank").reset_index(drop=True)

    return df


# ═══════════════════════════════════════════════════════════════════════════
# Section 14 — Utility: Momentum-weighted period averages
# ═══════════════════════════════════════════════════════════════════════════

def apply_momentum_weights(
    quarterly_values: list[float],
    period_name: str,
    config: ScorecardConfig = DEFAULT_CONFIG,
) -> float:
    """
    Apply momentum half-life decay to quarterly values.

    Weights recent quarters more heavily according to the half-life
    config for the given period.
    """
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
    return np.sum(w_arr * v_arr) / np.sum(w_arr)


# ═══════════════════════════════════════════════════════════════════════════
# Main — standalone test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Tilt Engine — Module 5 (CoStar Scoring Pipeline)")
    print("=" * 60)
    print()

    cfg = ScorecardConfig()

    # --- Test asymmetric_adjust ---
    print("Asymmetric Adjust Tests:")
    assert abs(asymmetric_adjust(2.0, 1.5) - 3.0) < 1e-10      # pos × adj
    assert abs(asymmetric_adjust(-2.0, 1.5) - (-2.0/1.5)) < 1e-10  # neg × 1/adj
    assert abs(asymmetric_adjust(0.0, 1.5) - 0.0) < 1e-10        # zero stays zero
    print("  All passed.")

    # --- Test bounded_multiplier (now with floor=0.25) ---
    print("\nBounded Multiplier Tests:")
    assert abs(bounded_multiplier(0.0, 0.25, 3.0, 0.25) - 1.0) < 1e-10
    assert abs(bounded_multiplier(4.0, 0.35, 3.0, 0.25) - 2.4) < 1e-10
    assert abs(bounded_multiplier(10.0, 0.35, 3.0, 0.25) - 3.0) < 1e-10  # capped
    assert abs(bounded_multiplier(-10.0, 0.35, 3.0, 0.25) - 0.25) < 1e-10  # floored
    print("  All passed.")

    # --- Test signal_indicator_z_score ---
    print("\nSignal Indicator Z Score Tests:")
    z = signal_indicator_z_score(10.0, 5.0, 2.0)
    assert abs(z - 2.5) < 1e-10
    z = signal_indicator_z_score(3.0, 5.0, 2.0)
    assert abs(z - 1.0) < 1e-10
    print("  All passed.")

    # --- Test total_z_score_per_metric (with new params) ---
    print("\nTOTAL Z Score per Metric Tests:")
    sig, vol, cat = 2.0, 1.0, 0.5
    va = min(3.0, max(0.25, 1 + 0.35 * vol))  # 1.35
    ca = min(3.0, max(0.25, 1 + 0.25 * cat))  # 1.125
    base = sig * ca  # 2.25
    expected = base * va  # positive → multiply
    t = total_z_score_per_metric(sig, vol, cat, cfg)
    assert abs(t - expected) < 1e-10, f"got {t}, expected {expected}"
    print(f"  total_z(sig=2.0, vol=1.0, cat=0.5) = {t:.4f}")
    print("  All passed.")

    # --- Test momentum_decay ---
    print("\nMomentum Decay Tests:")
    assert abs(momentum_decay(0, 8) - 1.0) < 1e-10
    assert abs(momentum_decay(8, 8) - 0.5) < 1e-10
    assert abs(momentum_decay(16, 8) - 0.25) < 1e-10
    print("  All passed.")

    # --- Test momentum_effective ---
    print("\nMomentum Effective Tests:")
    me = momentum_effective(1.5, 0.35)
    expected_me = 1.5 ** 0.35
    assert abs(me - expected_me) < 1e-10
    print(f"  MomEff(tilt=1.5, knob=0.35) = {me:.6f} (expected {expected_me:.6f})")
    assert abs(momentum_effective(1.0, 0.35) - 1.0) < 1e-10
    print("  All passed.")

    # --- Test period_adjustment ---
    print("\nPeriod Adjustment Tests:")
    pa = period_adjustment(0.0, cfg)
    assert abs(pa - 1.0) < 1e-10
    pa = period_adjustment(2.0, cfg)
    expected_pa = min(3.0, max(0.25, 1 + 0.2 * 2.0))  # 1.4
    assert abs(pa - expected_pa) < 1e-10
    pa = period_adjustment(-10.0, cfg)
    assert abs(pa - 0.25) < 1e-10  # floored
    print("  All passed.")

    # --- Test overall_mf_fundamental with momentum (3-category) ---
    print("\nOverall MF Fundamental Tests (3-category):")
    tilt = 1.5  # max momentum tilt for this period column
    mf = overall_mf_fundamental(1.0, 1.0, 1.0, tilt, cfg)
    me = 1.5 ** 0.35
    # All positive: ds_adj=1.0*me, occ_adj=1.0*me, rg_adj=1.0*me
    expected_mf = me  # all same, so weighted avg = me
    assert abs(mf - expected_mf) < 1e-10
    print(f"  MF(ds=1.0, occ=1.0, rg=1.0, tilt=1.5) = {mf:.6f} (expected {expected_mf:.6f})")

    # With negative D&S (asymmetric application)
    mf_neg = overall_mf_fundamental(-1.0, 0.5, 1.0, tilt, cfg)
    ds_mom = -1.0 * (1.0 / me)   # negative → divide by MomEff
    occ_mom = 0.5 * me            # positive → multiply by MomEff
    rg_mom = 1.0 * me             # positive → multiply by MomEff
    total_w = cfg.ds_weight + cfg.occ_weight + cfg.rg_weight
    expected_neg = (cfg.ds_weight * ds_mom + cfg.occ_weight * occ_mom + cfg.rg_weight * rg_mom) / total_w
    assert abs(mf_neg - expected_neg) < 1e-10
    print(f"  MF(ds=-1.0, occ=0.5, rg=1.0, tilt=1.5) = {mf_neg:.6f} (expected {expected_neg:.6f})")
    print("  All passed.")

    # --- Integration test ---
    print("\n" + "=" * 60)
    print("Integration Test: Score a synthetic market")
    print("=" * 60)

    market_data = {}
    for period in ["Annual", "2Yr", "5Yr", "10Yr"]:
        market_data[period] = {
            "signal_indicators": {
                "absorption": 1.2, "deliveries": 0.8,
                "abs_del": 1.0, "blended_occ": 0.5,
                "under_construction": 0.3, "yrs_to_stab": 0.7,
                "eff_rent_overall": 1.1, "eff_rent_1br": 0.9,
                "eff_rent_studio": 0.6, "eff_rent_2br": 1.0,
                "eff_rent_3br": 0.4,
            },
            "volatility_indicators": {
                "absorption": 0.5, "deliveries": 0.3,
                "abs_del": 0.4, "blended_occ": 0.2,
                "under_construction": 0.6, "yrs_to_stab": 0.1,
                "eff_rent_overall": 0.3, "eff_rent_1br": 0.4,
                "eff_rent_studio": 0.2, "eff_rent_2br": 0.3,
                "eff_rent_3br": 0.5,
            },
            "ds_category_values": {k: [1.2, 0.8, 1.0, 0.5, 0.3, 0.7]
                                   for k in cfg.ds_metric_weights},
            "rent_category_values": {k: [1.1, 0.9, 0.6, 1.0, 0.4]
                                     for k in cfg.rent_metric_weights},
            "ds_period_signal_z": 0.5,
            "rent_period_signal_z": 0.8,
            "tilt_value": 1.3,  # Example momentum tilt
        }

    ms = score_market_all_periods(market_data, cfg)

    for period, ps in ms.period_scores.items():
        print(f"\n  {period}:")
        print(f"    D&S raw:   {ps.overall_ds_raw:+.4f}")
        print(f"    D&S adj:   {ps.overall_ds_adj:+.4f}")
        print(f"    Rent raw:  {ps.overall_rent_raw:+.4f}")
        print(f"    Rent adj:  {ps.overall_rent_adj:+.4f}")
        print(f"    MF final:  {ps.overall_mf:+.4f}")

    print(f"\n  Duration-weighted scores:")
    print(f"    D&S:   {ms.duration_weighted_ds:+.4f}")
    print(f"    Rent:  {ms.duration_weighted_rent:+.4f}")
    print(f"    MF:    {ms.duration_weighted_mf:+.4f}")
    print(f"    Final: {ms.final_score:+.4f}")

    print("\nTilt engine ready.")
