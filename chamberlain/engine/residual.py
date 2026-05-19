"""Residual (sale at exit) engine.

Computes the Year-N sale event:

  Sale Price = (Year N+1 forward NOI) / residual_cap_rate
  Cost of Sale = Sale Price × cost_of_sale_pct
  Loan Repayment = outstanding UPB at sale (acquisition + capital funding)
  Net Sale Proceeds = Sale Price - Cost of Sale - Loan Repayment

The "forward NOI" used for the residual is the NOI the buyer underwrites —
the Excel convention is the *next* year's NOI (Year 11 if selling end of
Year 10), reflecting a forward-looking cap rate. We replicate that.

The going-in cap rate is also reported (Year-1 NOI / purchase basis).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.scenario import Scenario


@dataclass
class ResidualResult:
    """Sale-at-exit outputs."""

    sale_year: int
    forward_noi: float          # Year N+1 NOI used for valuation
    residual_cap_rate: float
    gross_sale_price: float
    cost_of_sale: float
    loan_repayment: float
    net_sale_proceeds: float
    going_in_cap_rate: float    # Year-1 NOI / acquisition basis


def compute_residual(
    scenario: Scenario,
    noi_by_year: dict[int, float],
    forward_noi_year_n_plus_1: float,
    loan_balance_at_sale: float,
) -> ResidualResult:
    """Compute the residual sale.

    Args:
        scenario: the Scenario (for residual assumptions + acquisition basis)
        noi_by_year: proforma-year -> NOI (for going-in cap rate)
        forward_noi_year_n_plus_1: the NOI the buyer underwrites (Year N+1).
            The engine extrapolates Year N+1 by growing Year N NOI one more
            period (the runner supplies this).
        loan_balance_at_sale: total outstanding debt UPB at the sale month.

    Returns:
        ResidualResult
    """
    sale_year = scenario.residual.sale_year.value
    cap_rate = scenario.residual.residual_cap_rate.value
    cost_pct = scenario.residual.cost_of_sale_pct.value

    gross = forward_noi_year_n_plus_1 / cap_rate if cap_rate else 0.0
    cost_of_sale = gross * cost_pct
    net = gross - cost_of_sale - loan_balance_at_sale

    basis = scenario.acquisition_cost_basis.value
    y1_noi = noi_by_year.get(1, 0.0)
    going_in = (y1_noi / basis) if basis else 0.0

    return ResidualResult(
        sale_year=sale_year,
        forward_noi=forward_noi_year_n_plus_1,
        residual_cap_rate=cap_rate,
        gross_sale_price=gross,
        cost_of_sale=cost_of_sale,
        loan_repayment=loan_balance_at_sale,
        net_sale_proceeds=net,
        going_in_cap_rate=going_in,
    )


__all__ = ["ResidualResult", "compute_residual"]
