"""
models.py — All dataclasses, types, and utility functions for the lease_analysis module.

This file reconstructs the 8 partner modules that were not delivered:
  data_loader, assumptions, breakeven, availability, forward_exposure,
  velocity, asking_achieved, seasonality, downtime

All types imported by intrinsic.py and pricing.py live here, with the
module names preserved as sub-namespaces so the delivered files can import
from them without modification when run standalone, and so the platform can
import from models.py directly.
"""

from __future__ import annotations

import datetime
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# =============================================================================
# data_loader — LeaseRecord
# =============================================================================

@dataclass
class LeaseRecord:
    """A single historical or active lease row."""
    unit: str
    unit_type: str
    general_type: str                     # e.g. "1BR", "2BR", "Studio"
    lease_start: Optional[datetime.date]
    lease_exp: Optional[datetime.date]
    exec_date: Optional[datetime.date]    # lease execution / signing date
    move_out: Optional[datetime.date]     # scheduled move-out
    actual_move_out: Optional[datetime.date]
    face_rent: Optional[float]            # asking / scheduled rent
    effective_rent: Optional[float]       # concession-adjusted rent
    term_months: Optional[float]
    is_renewal: bool = False


def load_unit_index(path: str) -> dict:
    """Stub — returns empty dict in platform context (data comes from DB)."""
    return {}


def load_historical_leases(path: str, unit_index=None) -> list[LeaseRecord]:
    """Stub — returns empty list in platform context."""
    return []


def load_rent_roll(path: str, unit_index=None) -> list:
    """Stub — returns empty list in platform context."""
    return []


def load_boxscore(path: str, unit_index=None) -> list:
    """Stub — returns empty list in platform context."""
    return []


# =============================================================================
# assumptions — Assumption dataclass + factory helpers
# =============================================================================

class _SourceRank(IntEnum):
    """Source quality ranking — lower is better (matches delivered code check)."""
    PROPERTY_ACTUAL   = 1
    PROPERTY_DERIVED  = 2
    MARKET_BENCHMARK  = 3
    MODEL_DEFAULT     = 4
    PLACEHOLDER       = 5


@dataclass
class Assumption:
    """A single modelling assumption with full provenance."""
    value: float
    source: _SourceRank
    detail: str
    confidence: str = "high"          # high | medium | low
    sample_size: Optional[int] = None

    # Convenience — lets delivered code do float(assumption)
    def __float__(self):
        return float(self.value)


def model_default(value: float, detail: str) -> Assumption:
    """Factory: assumption derived from model logic with no empirical backing."""
    return Assumption(value=value, source=_SourceRank.MODEL_DEFAULT,
                      detail=detail, confidence="low")


def property_actual(value: float, detail: str,
                    sample_size: Optional[int] = None) -> Assumption:
    """Factory: assumption measured directly from this property's data."""
    return Assumption(value=value, source=_SourceRank.PROPERTY_ACTUAL,
                      detail=detail, confidence="high",
                      sample_size=sample_size)


def market_benchmark(value: float, detail: str) -> Assumption:
    """Factory: assumption from published market research / comps."""
    return Assumption(value=value, source=_SourceRank.MARKET_BENCHMARK,
                      detail=detail, confidence="medium")


# =============================================================================
# downtime — vacancy downtime between lease turns
# =============================================================================

@dataclass
class DowntimeStat:
    """Downtime statistics for one cut (unit type, floorplan, or portfolio)."""
    median_days: float
    mean_days: float
    p75_days: float
    p90_days: float
    n_turns: int
    trimmed_mean: Optional[float] = None


# Default make-ready cost by general type — Class A urban benchmarks
DEFAULT_MAKE_READY_BY_GENERAL_TYPE: dict[str, float] = {
    "Micro":   800.0,
    "Studio":  900.0,
    "Alcove": 1000.0,
    "1BR":    1400.0,
    "2BR":    1800.0,
    "3BR":    2200.0,
    "PH":     2800.0,
}

# Fallback when general_type is unknown
DEFAULT_MAKE_READY_FALLBACK = 1500.0


def _pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_d = sorted(data)
    idx = p / 100.0 * (len(sorted_d) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_d) - 1)
    return sorted_d[lo] + (idx - lo) * (sorted_d[hi] - sorted_d[lo])


def _compute_downtime_stat(days_list: list[float],
                           trim_pct: float = 0.10) -> Optional[DowntimeStat]:
    if not days_list:
        return None
    clean = sorted(days_list)
    if trim_pct > 0 and len(clean) >= 4:
        n_trim = max(1, int(len(clean) * trim_pct))
        trimmed = clean[n_trim: -n_trim]
    else:
        trimmed = clean
    return DowntimeStat(
        median_days=statistics.median(clean),
        mean_days=statistics.mean(clean),
        p75_days=_pct(clean, 75),
        p90_days=_pct(clean, 90),
        n_turns=len(clean),
        trimmed_mean=statistics.mean(trimmed) if trimmed else None,
    )


def downtime_table(
    leases: list[LeaseRecord],
    trim_pct: float = 0.10,
) -> dict[str, dict[str, DowntimeStat]]:
    """Compute downtime stats by unit_type, floorplan, and portfolio.

    Returns nested dict:  level -> key -> DowntimeStat
      level "unit_type": key = unit_type string
      level "portfolio": key = "PORTFOLIO"
    """
    by_unit: dict[str, list[LeaseRecord]] = defaultdict(list)
    for l in leases:
        if l.unit and l.lease_start:
            by_unit[l.unit].append(l)

    ut_days: dict[str, list[float]] = defaultdict(list)
    all_days: list[float] = []

    for unit, ls in by_unit.items():
        ls.sort(key=lambda x: x.lease_start)
        for prev, nxt in zip(ls, ls[1:]):
            if nxt.is_renewal:
                continue
            end = prev.actual_move_out or prev.move_out or prev.lease_exp
            start = nxt.exec_date or nxt.lease_start
            if end and start:
                gap = (start - end).days
                if -30 <= gap <= 540:
                    ut_days[nxt.unit_type].append(float(gap))
                    all_days.append(float(gap))

    result: dict[str, dict[str, DowntimeStat]] = {
        "unit_type": {},
        "portfolio": {},
    }
    for ut, days in ut_days.items():
        stat = _compute_downtime_stat(days, trim_pct)
        if stat:
            result["unit_type"][ut] = stat

    port_stat = _compute_downtime_stat(all_days, trim_pct)
    if port_stat:
        result["portfolio"]["PORTFOLIO"] = port_stat
    else:
        # Guarantee at least a default so callers don't crash on empty data
        result["portfolio"]["PORTFOLIO"] = DowntimeStat(
            median_days=30.0, mean_days=30.0, p75_days=45.0,
            p90_days=60.0, n_turns=0,
        )

    return result


# =============================================================================
# breakeven — floor computation
# =============================================================================

@dataclass
class BreakevenAssumptions:
    """Inputs to the break-even floor model."""
    # Downtime
    downtime_statistic: str = "median"          # "median" | "mean" | "p75" | "trimmed_mean"
    downtime_outlier_cap_multiple: float = 2.0

    # Make-ready / turnover cost
    turnover_cost_flat: Optional[float] = None   # if None, scale by general_type
    turnover_cost_by_unit_type: Optional[dict] = None  # per-UT override

    # Commission
    new_commission_flat: float = 150.0           # flat fee per new lease
    renewal_commission_flat: float = 75.0
    new_commission_months: float = 0.0           # % of monthly rent
    renewal_commission_months: float = 0.0

    # Concessions (rolled into floor)
    concession_months_free: float = 0.0
    concession_flat_dollars: float = 0.0

    # Marketing
    marketing_cost_flat: float = 300.0


@dataclass
class BreakevenResult:
    """Break-even floor output for one unit type."""
    unit_type: str
    feasible: bool
    breakeven_rent: Optional[float]      # monthly effective rent floor
    components: dict = field(default_factory=dict)


def breakeven_for_unit_type(
    unit_type: str,
    inplace_rent: float,
    term_months: float,
    downtime_stat: Optional[DowntimeStat],
    general_type: str = "",
    assumptions: Optional[BreakevenAssumptions] = None,
    scenario: str = "new",
) -> BreakevenResult:
    """Compute a break-even effective rent floor for one unit type.

    Break-even floor logic:
      Monthly vacancy cost = (daily_rent * downtime_days)
      Monthly cost burden  = (vacancy_cost + make_ready + marketing + commission) / term_months
      Floor = inplace_rent + monthly_cost_burden

    The floor is expressed in effective rent terms (post-concession equivalent).
    """
    a = assumptions or BreakevenAssumptions()

    if not inplace_rent or inplace_rent <= 0 or not term_months or term_months <= 0:
        return BreakevenResult(unit_type=unit_type, feasible=False,
                               breakeven_rent=None,
                               components={"error": "missing inplace_rent or term_months"})

    # --- downtime ---
    if downtime_stat is None:
        downtime_days = 30.0
    else:
        stat_map = {
            "median": downtime_stat.median_days,
            "mean": downtime_stat.mean_days,
            "p75": downtime_stat.p75_days,
            "p90": downtime_stat.p90_days,
            "trimmed_mean": downtime_stat.trimmed_mean or downtime_stat.median_days,
        }
        downtime_days = stat_map.get(a.downtime_statistic, downtime_stat.median_days)
        # cap outliers
        cap = downtime_stat.median_days * a.downtime_outlier_cap_multiple
        downtime_days = min(downtime_days, cap)

    daily_rent = inplace_rent / 30.4
    vacancy_cost = daily_rent * downtime_days

    # --- make-ready ---
    if a.turnover_cost_by_unit_type and unit_type in a.turnover_cost_by_unit_type:
        make_ready = a.turnover_cost_by_unit_type[unit_type]
    elif a.turnover_cost_flat is not None:
        make_ready = a.turnover_cost_flat
    else:
        make_ready = DEFAULT_MAKE_READY_BY_GENERAL_TYPE.get(
            general_type, DEFAULT_MAKE_READY_FALLBACK)

    # --- commission ---
    if scenario == "renewal":
        commission = (a.renewal_commission_flat
                      + a.renewal_commission_months * inplace_rent)
    else:
        commission = (a.new_commission_flat
                      + a.new_commission_months * inplace_rent)

    # --- concession cost in $ terms ---
    concession_cost = (a.concession_months_free * inplace_rent
                       + a.concession_flat_dollars)

    # --- marketing ---
    marketing = a.marketing_cost_flat

    # --- total one-time turnover cost ---
    total_cost = vacancy_cost + make_ready + marketing + commission + concession_cost

    # --- monthly burden spread over the lease term ---
    monthly_burden = total_cost / term_months

    # Floor: existing in-place rent must cover its own monthly burden
    floor = inplace_rent + monthly_burden

    components = {
        "inplace_rent": round(inplace_rent, 2),
        "downtime_days": round(downtime_days, 1),
        "vacancy_cost": round(vacancy_cost, 2),
        "make_ready": round(make_ready, 2),
        "marketing": round(marketing, 2),
        "commission": round(commission, 2),
        "concession_cost": round(concession_cost, 2),
        "total_cost": round(total_cost, 2),
        "term_months": term_months,
        "monthly_burden": round(monthly_burden, 2),
    }

    return BreakevenResult(unit_type=unit_type, feasible=True,
                          breakeven_rent=round(floor, 2),
                          components=components)


def breakeven_for_all_unit_types(
    unit_types: list[str],
    inplace_rents: dict[str, float],
    term_months: dict[str, float],
    downtime: dict,           # result of downtime_table()
    ut_to_fp: dict[str, str],
    ut_to_gen: dict[str, str],
    assumptions: Optional[BreakevenAssumptions] = None,
    scenario: str = "new",
) -> dict[str, BreakevenResult]:
    """Compute break-even floors for all unit types."""
    port_dt = downtime.get("portfolio", {}).get("PORTFOLIO")
    ut_dt = downtime.get("unit_type", {})

    out: dict[str, BreakevenResult] = {}
    for ut in unit_types:
        rent = inplace_rents.get(ut)
        term = term_months.get(ut)
        dt_stat = ut_dt.get(ut) or port_dt
        gen = ut_to_gen.get(ut, "")
        out[ut] = breakeven_for_unit_type(
            unit_type=ut,
            inplace_rent=rent or 0.0,
            term_months=term or 12.0,
            downtime_stat=dt_stat,
            general_type=gen,
            assumptions=assumptions,
            scenario=scenario,
        )
    return out


# =============================================================================
# availability — current snapshot of vacant / exposed units
# =============================================================================

@dataclass
class AvailabilitySnapshot:
    """Current occupancy / availability at three levels."""
    available_units: int
    total_units: int
    fp_available_units: int
    fp_total_units: int
    property_available_units: int
    property_total_units: int

    @property
    def ut_availability_pct(self) -> float:
        return self.available_units / max(self.total_units, 1)

    @property
    def fp_availability_pct(self) -> float:
        return self.fp_available_units / max(self.fp_total_units, 1)

    @property
    def property_availability_pct(self) -> float:
        return self.property_available_units / max(self.property_total_units, 1)


def snapshot_from_counts(
    rows: list,
    *,
    available_statuses: tuple = ("vacant", "notice", "available", "model"),
) -> dict[str, AvailabilitySnapshot]:
    """Build AvailabilitySnapshot per unit_type from a list of row dicts/objects.

    Rows are expected to have: unit_type, floorplan, status, (optional) is_available.
    Tolerates dicts or objects with attribute access.
    """
    def _get(row, key, default=None):
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)

    if not rows:
        return {}

    # Property-level totals
    prop_total = len(rows)
    prop_avail = sum(
        1 for r in rows
        if str(_get(r, "status", "")).lower() in available_statuses
        or bool(_get(r, "is_available", False))
    )

    # Group by unit_type and floorplan
    by_ut: dict[str, list] = defaultdict(list)
    ut_to_fp: dict[str, str] = {}
    for r in rows:
        ut = str(_get(r, "unit_type", "unknown"))
        fp = str(_get(r, "floorplan", _get(r, "unit_type", "unknown")))
        by_ut[ut].append(r)
        ut_to_fp.setdefault(ut, fp)

    fp_to_uts: dict[str, list[str]] = defaultdict(list)
    for ut, fp in ut_to_fp.items():
        fp_to_uts[fp].append(ut)

    out: dict[str, AvailabilitySnapshot] = {}
    for ut, ut_rows in by_ut.items():
        fp = ut_to_fp[ut]
        fp_rows = [r for ut2 in fp_to_uts[fp] for r in by_ut[ut2]]

        def _is_avail(r):
            return (str(_get(r, "status", "")).lower() in available_statuses
                    or bool(_get(r, "is_available", False)))

        ut_avail = sum(1 for r in ut_rows if _is_avail(r))
        fp_avail = sum(1 for r in fp_rows if _is_avail(r))

        out[ut] = AvailabilitySnapshot(
            available_units=ut_avail,
            total_units=len(ut_rows),
            fp_available_units=fp_avail,
            fp_total_units=len(fp_rows),
            property_available_units=prop_avail,
            property_total_units=prop_total,
        )
    return out


def snapshot_from_db_units(
    units: list[dict],
    available_statuses: tuple = ("vacant", "notice", "available", "model"),
) -> dict[str, AvailabilitySnapshot]:
    """Build snapshots from the platform's units DB rows (dicts with unit_type, status)."""
    return snapshot_from_counts(units, available_statuses=available_statuses)


# =============================================================================
# forward_exposure — upcoming lease expirations rolling the snapshot forward
# =============================================================================

# Weighted-average horizon weights (days: weight)
DEFAULT_WEIGHTS: dict[int, float] = {
    0:   0.40,
    30:  0.25,
    60:  0.20,
    90:  0.10,
    180: 0.05,
}


@dataclass
class ForwardExposureSnapshot:
    """Forward-looking exposure for one unit type."""
    unit_type: str
    total_units: int
    current_exposed: int       # units vacant / on-notice right now
    exp_30: int                # cumulative exposed at +30d
    exp_60: int
    exp_90: int
    exp_180: int

    @property
    def current_pct(self) -> float:
        return self.current_exposed / max(self.total_units, 1)

    def pct_at(self, days: int) -> float:
        mapping = {30: self.exp_30, 60: self.exp_60,
                   90: self.exp_90, 180: self.exp_180}
        exposed = mapping.get(days, self.current_exposed)
        return exposed / max(self.total_units, 1)


def effective_exposure_pct(
    snapshot: ForwardExposureSnapshot,
    weights: dict[int, float] = DEFAULT_WEIGHTS,
) -> float:
    """Weighted-average exposure across the forward horizon."""
    horizon_map = {
        0:   snapshot.current_pct,
        30:  snapshot.pct_at(30),
        60:  snapshot.pct_at(60),
        90:  snapshot.pct_at(90),
        180: snapshot.pct_at(180),
    }
    total_w = sum(weights.values())
    return sum(horizon_map.get(d, 0.0) * w for d, w in weights.items()) / max(total_w, 1e-9)


def build_forward_exposure(
    rent_roll: list,
    current_exposed_by_ut: dict[str, int],
    total_units_by_ut: dict[str, int],
    as_of: datetime.date,
) -> dict[str, ForwardExposureSnapshot]:
    """Build forward exposure snapshots from rent-roll expiration data.

    rent_roll rows expected to have: unit_type, lease_end (date or str).
    """
    def _get(row, key, default=None):
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)

    def _parse_date(v) -> Optional[datetime.date]:
        if isinstance(v, datetime.date):
            return v
        if isinstance(v, str):
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
                try:
                    return datetime.datetime.strptime(v, fmt).date()
                except ValueError:
                    pass
        return None

    # Count expirations per unit_type per horizon
    expirations: dict[str, list[int]] = defaultdict(list)  # ut -> list of days-to-expire
    for row in rent_roll:
        ut = str(_get(row, "unit_type", ""))
        le = _parse_date(_get(row, "lease_end"))
        if not ut or le is None:
            continue
        days = (le - as_of).days
        expirations[ut].append(days)

    out: dict[str, ForwardExposureSnapshot] = {}
    all_uts = set(current_exposed_by_ut) | set(total_units_by_ut)
    for ut in all_uts:
        total = total_units_by_ut.get(ut, 0)
        current = current_exposed_by_ut.get(ut, 0)
        exp_list = expirations.get(ut, [])

        def _count_at(horizon: int) -> int:
            return current + sum(1 for d in exp_list if 0 < d <= horizon)

        out[ut] = ForwardExposureSnapshot(
            unit_type=ut,
            total_units=total,
            current_exposed=current,
            exp_30=min(_count_at(30), total),
            exp_60=min(_count_at(60), total),
            exp_90=min(_count_at(90), total),
            exp_180=min(_count_at(180), total),
        )
    return out


# =============================================================================
# velocity — leasing absorption rate
# =============================================================================

# Velocity tier thresholds (monthly absorption % of total units)
VELOCITY_TIERS = [
    ("stalled",      0.05),
    ("slow",         0.10),
    ("normal",       0.20),
    ("strong",       0.30),
    ("very_strong",  1.00),
]

VELOCITY_MULTIPLIERS = {
    "stalled":     0.85,
    "slow":        0.92,
    "normal":      1.00,
    "strong":      1.05,
    "very_strong": 1.08,
}


def _velocity_tier_name(monthly_pct: float) -> str:
    for name, cap in VELOCITY_TIERS:
        if monthly_pct <= cap:
            return name
    return "very_strong"


@dataclass
class VelocitySnapshot:
    """Leasing velocity for one unit type (or portfolio)."""
    tier: str
    absorption_rate: float          # monthly absorption as % of total units
    leases_per_period: int          # raw count in the window
    # Additional fields used in run_analysis walk-through
    leases_signed: int = 0
    monthly_absorption_pct: float = 0.0


@dataclass
class PortfolioVelocity:
    """Portfolio-level velocity (all unit types combined)."""
    tier: str
    absorption_rate: float
    leases_signed: int
    monthly_absorption_pct: float


def velocity_multiplier(snapshot: VelocitySnapshot) -> float:
    """Return the premium multiplier for the given velocity tier."""
    return VELOCITY_MULTIPLIERS.get(snapshot.tier, 1.0)


def compute_velocity(
    leases: list[LeaseRecord],
    total_units_by_ut: dict[str, int],
    as_of: datetime.date,
    window_days: int = 90,
) -> dict[str, VelocitySnapshot]:
    """Compute per-unit-type velocity over the trailing window."""
    cutoff = as_of - datetime.timedelta(days=window_days)
    window_months = window_days / 30.4

    by_ut: dict[str, int] = defaultdict(int)
    for l in leases:
        if l.is_renewal:
            continue
        ref_date = l.exec_date or l.lease_start
        if ref_date and cutoff <= ref_date <= as_of:
            by_ut[l.unit_type] += 1

    out: dict[str, VelocitySnapshot] = {}
    for ut, total in total_units_by_ut.items():
        signed = by_ut.get(ut, 0)
        monthly_pct = (signed / window_months) / max(total, 1)
        tier = _velocity_tier_name(monthly_pct)
        out[ut] = VelocitySnapshot(
            tier=tier,
            absorption_rate=monthly_pct,
            leases_per_period=signed,
            leases_signed=signed,
            monthly_absorption_pct=monthly_pct,
        )
    return out


def portfolio_velocity(
    leases: list[LeaseRecord],
    total_units: int,
    as_of: datetime.date,
    window_days: int = 90,
) -> PortfolioVelocity:
    """Compute portfolio-level velocity."""
    cutoff = as_of - datetime.timedelta(days=window_days)
    window_months = window_days / 30.4

    signed = sum(
        1 for l in leases
        if not l.is_renewal
        and (l.exec_date or l.lease_start)
        and cutoff <= (l.exec_date or l.lease_start) <= as_of
    )
    monthly_pct = (signed / window_months) / max(total_units, 1)
    tier = _velocity_tier_name(monthly_pct)
    return PortfolioVelocity(
        tier=tier,
        absorption_rate=monthly_pct,
        leases_signed=signed,
        monthly_absorption_pct=monthly_pct,
    )


# =============================================================================
# asking_achieved — asking vs. achieved (face vs. effective) gap
# =============================================================================

# Level tiers: how large is the current gap?
GAP_LEVEL_TIERS = [
    ("tight",        0.01),   # <1% concession rate
    ("light",        0.03),
    ("moderate",     0.06),
    ("elevated",     0.10),
    ("deep",         1.00),
]

GAP_MULTIPLIERS = {
    "tight":     1.03,
    "light":     1.01,
    "moderate":  1.00,
    "elevated":  0.98,
    "deep":      0.95,
}

# Trend tiers: is concession depth widening or tightening?
GAP_TREND_TIERS = [
    ("tightening",   -0.01),  # gap narrowed by >1pp
    ("stable",        0.01),
    ("widening",      1.00),
]


def _gap_level_tier(gap_pct: float) -> str:
    for name, cap in GAP_LEVEL_TIERS:
        if gap_pct <= cap:
            return name
    return "deep"


def _gap_trend_tier(trend_pp: Optional[float]) -> str:
    if trend_pp is None:
        return "stable"
    for name, cap in GAP_TREND_TIERS:
        if trend_pp <= cap:
            return name
    return "widening"


@dataclass
class AskingAchievedGap:
    """Asking-vs-achieved gap signal for one cut (unit type or portfolio)."""
    level_tier: str
    trend_tier: str
    gap_pct: float            # current (face - effective) / face
    trend_pct: float          # change in gap_pct vs. prior period (signed)
    current_gap_pct: float = 0.0
    prior_gap_pct: float = 0.0
    trend_pp: Optional[float] = None
    n: int = 0


def gap_multiplier(gap: AskingAchievedGap) -> float:
    """Return the premium multiplier for the given gap level."""
    return GAP_MULTIPLIERS.get(gap.level_tier, 1.0)


def _compute_gap_for_leases(
    leases: list[LeaseRecord],
    as_of: datetime.date,
    window_days: int,
) -> AskingAchievedGap:
    """Compute gap signal for an arbitrary list of leases."""
    cutoff = as_of - datetime.timedelta(days=window_days)
    prior_cutoff = cutoff - datetime.timedelta(days=window_days)

    current_gaps = []
    prior_gaps = []
    for l in leases:
        if l.is_renewal or not l.face_rent or not l.effective_rent:
            continue
        if l.face_rent <= 0:
            continue
        g = max(0.0, (l.face_rent - l.effective_rent) / l.face_rent)
        ref = l.exec_date or l.lease_start
        if ref is None:
            continue
        if cutoff <= ref <= as_of:
            current_gaps.append(g)
        elif prior_cutoff <= ref < cutoff:
            prior_gaps.append(g)

    cur_gap = statistics.mean(current_gaps) if current_gaps else 0.0
    pri_gap = statistics.mean(prior_gaps) if prior_gaps else cur_gap
    trend = cur_gap - pri_gap

    return AskingAchievedGap(
        level_tier=_gap_level_tier(cur_gap),
        trend_tier=_gap_trend_tier(trend),
        gap_pct=cur_gap,
        trend_pct=trend,
        current_gap_pct=cur_gap,
        prior_gap_pct=pri_gap,
        trend_pp=trend,
        n=len(current_gaps),
    )


def compute_gap(
    leases: list[LeaseRecord],
    as_of: datetime.date,
    window_days: int = 90,
    by: str = "portfolio",     # "portfolio" | "unit_type"
) -> dict[str, AskingAchievedGap]:
    """Compute asking-vs-achieved gap, optionally broken out by unit_type."""
    if by == "unit_type":
        by_ut: dict[str, list[LeaseRecord]] = defaultdict(list)
        for l in leases:
            by_ut[l.unit_type].append(l)
        return {ut: _compute_gap_for_leases(ls, as_of, window_days)
                for ut, ls in by_ut.items()}
    else:
        return {"PORTFOLIO": _compute_gap_for_leases(leases, as_of, window_days)}


# =============================================================================
# seasonality — seasonal index by calendar month
# =============================================================================

# Hard-coded seasonal index defaults when data is thin
_SEASONAL_DEFAULTS = {
    1:  0.98,   # Jan
    2:  0.98,   # Feb
    3:  1.00,
    4:  1.01,
    5:  1.02,   # summer starts
    6:  1.02,
    7:  1.02,
    8:  1.02,   # summer ends
    9:  1.00,
    10: 0.99,
    11: 0.98,   # winter
    12: 0.98,
}

_SEASONAL_DEFAULTS_RENEWAL = {m: 1.0 for m in range(1, 13)}   # renewals: flat


@dataclass
class SeasonalIndex:
    """Monthly seasonal indices derived from historical data."""
    by_month: dict[int, Assumption]    # month 1-12 -> Assumption (value = multiplier)
    window_start: Optional[datetime.date] = None
    window_end: Optional[datetime.date] = None
    total_leases_used: int = 0


def seasonal_multiplier(
    index: SeasonalIndex,
    month: int,
    scenario: str = "new",
) -> float:
    """Return the seasonal multiplier for a given month and scenario."""
    if scenario == "renewal":
        return 1.0
    a = index.by_month.get(month)
    return float(a) if a is not None else _SEASONAL_DEFAULTS.get(month, 1.0)


def build_seasonality_table(
    leases: list[LeaseRecord],
    as_of: Optional[datetime.date] = None,
    window_months: int = 24,
    ref_field: str = "exec_date",       # "exec_date" | "lease_start"
    min_n_per_month: int = 10,
    smoothing_alpha: float = 0.30,      # blend toward global mean when thin
) -> dict[str, SeasonalIndex]:
    """Build seasonality indices by exec_date and lease_start reference.

    Returns dict with keys "exec_date" and "lease_start", each a SeasonalIndex.
    """
    as_of = as_of or datetime.date.today()
    cutoff = as_of - datetime.timedelta(days=window_months * 30)

    def _ref(l: LeaseRecord, field: str) -> Optional[datetime.date]:
        return l.exec_date if field == "exec_date" else l.lease_start

    def _build_index(field: str) -> SeasonalIndex:
        by_month: dict[int, list[LeaseRecord]] = defaultdict(list)
        for l in leases:
            if l.is_renewal:
                continue
            r = _ref(l, field)
            if r and cutoff <= r <= as_of:
                by_month[r.month].append(l)

        total_used = sum(len(v) for v in by_month.values())

        # Compute raw monthly rate (leases per month, normalized to global mean)
        monthly_counts = {m: len(by_month.get(m, [])) for m in range(1, 13)}
        global_mean = statistics.mean(monthly_counts.values()) or 1.0

        month_assumptions: dict[int, Assumption] = {}
        for m in range(1, 13):
            n = monthly_counts[m]
            raw_index = (n / global_mean) if global_mean > 0 else 1.0

            if n >= min_n_per_month:
                # blend with global mean (1.0) for robustness
                blended = raw_index * (1 - smoothing_alpha) + 1.0 * smoothing_alpha
                month_assumptions[m] = property_actual(
                    blended,
                    detail=f"month {m}: {n} leases, smoothed",
                    sample_size=n,
                )
            else:
                # fall back to hard-coded defaults
                default_val = _SEASONAL_DEFAULTS.get(m, 1.0)
                month_assumptions[m] = market_benchmark(
                    default_val,
                    detail=f"month {m}: n={n} < {min_n_per_month}, using market default",
                )

        return SeasonalIndex(
            by_month=month_assumptions,
            window_start=cutoff,
            window_end=as_of,
            total_leases_used=total_used,
        )

    return {
        "exec_date":   _build_index("exec_date"),
        "lease_start": _build_index("lease_start"),
    }


# =============================================================================
# pricing — 7-layer pricing model combining all signals
# =============================================================================

@dataclass
class PricingResult:
    """Final pricing recommendation for one unit type."""
    unit_type: str
    floor: float                          # break-even floor from breakeven module
    recommended: float                    # final recommended rent
    scarcity_premium: float               # raw (uncapped) premium
    capped_premium: float                 # premium after cap
    premium_cap_pct: float                # configured cap %
    velocity_tier: Optional[str] = None
    velocity_mult: float = 1.0
    gap_level_tier: Optional[str] = None
    gap_trend_tier: Optional[str] = None
    gap_mult: float = 1.0
    seasonal_multiplier: float = 1.0
    forward_exposure_pct: float = 0.0
    intrinsic_adjustment: float = 0.0
    posture_unit_type: str = "hold"       # push / hold / concede
    ut_avail: int = 0
    ut_total: int = 0
    fp_avail: int = 0
    fp_total: int = 0
    property_avail: int = 0
    property_total: int = 0
    feasible: bool = True


@dataclass
class PricingAssumptions:
    """Tuneable knobs for the pricing pipeline."""
    premium_cap_pct: float = 0.06         # max ±6% premium
    scarcity_weight: float = 0.40         # weight of forward exposure in premium
    velocity_weight: float = 0.25
    gap_weight: float = 0.20
    seasonal_weight: float = 0.15
    push_threshold: float = 0.02          # posture thresholds
    concede_threshold: float = -0.02


def _posture(premium: float, assumptions: PricingAssumptions) -> str:
    if premium >= assumptions.push_threshold:
        return "push"
    elif premium <= assumptions.concede_threshold:
        return "concede"
    return "hold"


def price_unit_type(
    unit_type: str,
    breakeven: BreakevenResult,
    avail: Optional['AvailabilitySnapshot'] = None,
    fwd_exposure: Optional[ForwardExposureSnapshot] = None,
    velocity: Optional[VelocitySnapshot] = None,
    gap: Optional[AskingAchievedGap] = None,
    season_index: Optional[SeasonalIndex] = None,
    pricing_month: int = 1,
    scenario: str = "new",
    assumptions: Optional[PricingAssumptions] = None,
) -> PricingResult:
    """Run the 7-layer pricing model for a single unit type.

    Layers:
      1. Break-even floor (from breakeven module)
      2. Forward exposure → scarcity signal
      3. Velocity → absorption multiplier
      4. Asking-vs-achieved gap → concession pressure
      5. Seasonality → calendar multiplier
      6. Combine into premium
      7. Cap and apply
    """
    a = assumptions or PricingAssumptions()
    floor = breakeven.breakeven_rent or 0.0
    if not breakeven.feasible or floor <= 0:
        return PricingResult(
            unit_type=unit_type, floor=0.0, recommended=0.0,
            scarcity_premium=0.0, capped_premium=0.0,
            premium_cap_pct=a.premium_cap_pct, feasible=False,
        )

    # --- Layer 2: forward exposure / scarcity ---
    fwd_pct = 0.0
    if fwd_exposure:
        fwd_pct = effective_exposure_pct(fwd_exposure)
    # Low exposure → positive scarcity signal; high → negative
    # Neutral at 10% exposure; below → premium, above → discount
    scarcity_signal = (0.10 - fwd_pct)  # positive = scarce

    # --- Layer 3: velocity ---
    vel_mult = 1.0
    vel_tier = None
    if velocity:
        vel_mult = velocity_multiplier(velocity)
        vel_tier = velocity.tier

    # --- Layer 4: gap ---
    gap_mult = 1.0
    gap_lvl = None
    gap_trend = None
    if gap:
        gap_mult = GAP_MULTIPLIERS.get(gap.level_tier, 1.0)
        gap_lvl = gap.level_tier
        gap_trend = gap.trend_tier

    # --- Layer 5: seasonality ---
    seas_mult = 1.0
    if season_index:
        seas_mult = seasonal_multiplier(season_index, pricing_month, scenario)

    # --- Layer 6: combine signals into raw premium ---
    # Weighted combination of normalised signal components
    raw_premium = (
        a.scarcity_weight  * scarcity_signal
        + a.velocity_weight  * (vel_mult - 1.0)
        + a.gap_weight       * (gap_mult - 1.0)
        + a.seasonal_weight  * (seas_mult - 1.0)
    )

    # --- Layer 7: cap ---
    capped = max(-a.premium_cap_pct, min(a.premium_cap_pct, raw_premium))
    recommended = round(floor * (1.0 + capped), 2)

    # Availability info
    ut_avail = avail.available_units if avail else 0
    ut_total = avail.total_units if avail else 0
    fp_avail = avail.fp_available_units if avail else 0
    fp_total = avail.fp_total_units if avail else 0
    prop_avail = avail.property_available_units if avail else 0
    prop_total = avail.property_total_units if avail else 0

    return PricingResult(
        unit_type=unit_type,
        floor=round(floor, 2),
        recommended=recommended,
        scarcity_premium=round(raw_premium, 4),
        capped_premium=round(capped, 4),
        premium_cap_pct=a.premium_cap_pct,
        velocity_tier=vel_tier,
        velocity_mult=round(vel_mult, 4),
        gap_level_tier=gap_lvl,
        gap_trend_tier=gap_trend,
        gap_mult=round(gap_mult, 4),
        seasonal_multiplier=round(seas_mult, 4),
        forward_exposure_pct=round(fwd_pct, 4),
        intrinsic_adjustment=0.0,     # reserved for hedonic model
        posture_unit_type=_posture(capped, a),
        ut_avail=ut_avail,
        ut_total=ut_total,
        fp_avail=fp_avail,
        fp_total=fp_total,
        property_avail=prop_avail,
        property_total=prop_total,
        feasible=True,
    )


def price_all(
    breakevens: dict[str, BreakevenResult],
    availability: dict[str, 'AvailabilitySnapshot'],
    forward: dict[str, ForwardExposureSnapshot],
    velocity: dict[str, VelocitySnapshot],
    gap_by_ut: dict[str, AskingAchievedGap],
    portfolio_gap: AskingAchievedGap,
    season_index: Optional[SeasonalIndex],
    pricing_month: int,
    scenario: str = "new",
    assumptions: Optional[PricingAssumptions] = None,
) -> dict[str, PricingResult]:
    """Price all unit types using the full 7-layer model."""
    results: dict[str, PricingResult] = {}
    for ut, be in breakevens.items():
        results[ut] = price_unit_type(
            unit_type=ut,
            breakeven=be,
            avail=availability.get(ut),
            fwd_exposure=forward.get(ut),
            velocity=velocity.get(ut),
            gap=gap_by_ut.get(ut, portfolio_gap),
            season_index=season_index,
            pricing_month=pricing_month,
            scenario=scenario,
            assumptions=assumptions,
        )
    return results
