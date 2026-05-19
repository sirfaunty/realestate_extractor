"""Returns engine.

Computes deal-level return metrics from the annual cash flow stream:

  - Leveraged IRR: IRR of [−equity, levered CF Y1..N + net sale proceeds]
  - Unleveraged IRR: IRR of [−cost basis, unlevered CF Y1..N + gross sale net of cost]
  - Equity Multiple (EM): total distributions / total equity invested
  - Average DSCR: mean of (NOI / debt service) across the hold
  - Year-1 Yield on Cost (YoC): Year-1 NOI / total cost basis
  - Cash-on-Cash (CoC): per-year levered CF / cumulative equity invested

IRR uses numpy-financial when available; falls back to a bisection /
Newton hybrid otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


def _irr(cash_flows: list[float], guess: float = 0.1) -> Optional[float]:
    """Internal rate of return.

    Tries numpy_financial.irr; falls back to a robust bisection on NPV.
    Returns None if no sign change (IRR undefined).
    """
    try:
        import numpy_financial as npf

        r = npf.irr(cash_flows)
        if r is None or (isinstance(r, float) and (r != r)):  # NaN check
            raise ValueError("npf returned NaN")
        return float(r)
    except Exception:
        pass

    # Fallback: bisection on NPV. Requires a sign change.
    def npv(rate: float) -> float:
        return sum(cf / ((1.0 + rate) ** i) for i, cf in enumerate(cash_flows))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        # No sign change in range — IRR undefined / outside bracket
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


@dataclass
class ReturnsResult:
    """Deal-level return metrics."""

    levered_irr: Optional[float] = None
    unlevered_irr: Optional[float] = None
    equity_multiple: float = 0.0
    avg_dscr: float = 0.0
    min_dscr: float = 0.0
    year1_yield_on_cost: float = 0.0
    coc_by_year: dict[int, float] = field(default_factory=dict)

    # The cash-flow vectors used (for drill-back / display)
    levered_cash_flows: list[float] = field(default_factory=list)
    unlevered_cash_flows: list[float] = field(default_factory=list)


def compute_returns(
    *,
    initial_equity: float,
    acquisition_cost_basis: float,
    levered_cf_by_year: dict[int, float],
    unlevered_cf_by_year: dict[int, float],
    noi_by_year: dict[int, float],
    debt_service_by_year: dict[int, float],
    net_sale_proceeds: float,
    gross_sale_less_cost: float,
    sale_year: int,
) -> ReturnsResult:
    """Compute all return metrics.

    Args:
        initial_equity: equity invested at close (Year 0 outflow), positive number
        acquisition_cost_basis: total cost basis (unlevered Year-0 outflow)
        levered_cf_by_year: year -> levered cash flow (after debt service, before sale)
        unlevered_cf_by_year: year -> unlevered cash flow (NOI less capex, before debt)
        noi_by_year: year -> NOI (for DSCR + YoC)
        debt_service_by_year: year -> total debt service (for DSCR)
        net_sale_proceeds: levered net proceeds at sale (after loan repayment)
        gross_sale_less_cost: unlevered net sale (price − cost of sale, no loan)
        sale_year: proforma year of sale

    Returns:
        ReturnsResult
    """
    years = sorted(set(levered_cf_by_year) | {sale_year})

    # Levered IRR vector
    lev_vec = [-initial_equity]
    for y in years:
        cf = levered_cf_by_year.get(y, 0.0)
        if y == sale_year:
            cf += net_sale_proceeds
        lev_vec.append(cf)

    # Unlevered IRR vector
    unlev_vec = [-acquisition_cost_basis]
    for y in years:
        cf = unlevered_cf_by_year.get(y, 0.0)
        if y == sale_year:
            cf += gross_sale_less_cost
        unlev_vec.append(cf)

    lev_irr = _irr(lev_vec)
    unlev_irr = _irr(unlev_vec)

    # Equity multiple = total positive distributions / equity invested
    total_distributions = sum(cf for cf in lev_vec[1:] if cf > 0)
    em = total_distributions / initial_equity if initial_equity else 0.0

    # DSCR
    dscrs = []
    for y in years:
        ds = debt_service_by_year.get(y, 0.0)
        noi = noi_by_year.get(y, 0.0)
        if ds > 0:
            dscrs.append(noi / ds)
    avg_dscr = sum(dscrs) / len(dscrs) if dscrs else 0.0
    min_dscr = min(dscrs) if dscrs else 0.0

    # Year-1 yield on cost
    y1_noi = noi_by_year.get(1, 0.0)
    yoc = y1_noi / acquisition_cost_basis if acquisition_cost_basis else 0.0

    # Cash on cash by year (levered CF / initial equity)
    coc = {}
    for y in years:
        cf = levered_cf_by_year.get(y, 0.0)
        coc[y] = cf / initial_equity if initial_equity else 0.0

    return ReturnsResult(
        levered_irr=lev_irr,
        unlevered_irr=unlev_irr,
        equity_multiple=em,
        avg_dscr=avg_dscr,
        min_dscr=min_dscr,
        year1_yield_on_cost=yoc,
        coc_by_year=coc,
        levered_cash_flows=lev_vec,
        unlevered_cash_flows=unlev_vec,
    )


__all__ = ["ReturnsResult", "compute_returns"]
