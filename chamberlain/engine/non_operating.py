"""Non-operating expense engine.

Computes the below-NOI items that reduce levered cash flow:

  - Asset Management Fee (AMF): % of EGR
  - Replacement Reserves: PUPY × units / 12 monthly
  - Professional Expenses: flat $/year
  - MIP (Mortgage Insurance Premium): % of acquisition-loan UPB (HUD)
  - Surplus Cash Note: Chamberlain-specific $22,641 every 2/1 and 8/1

Property Management Fee is treated as an operating expense and lives in
the OpEx detail lines for Chamberlain (3rd-Party Mgmt). The AMF is the
non-operating fee that the validated Property NOI strips out.

Sign convention: all amounts returned positive; the runner subtracts them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .time_grid import TimeGrid
from ..models.assumptions import NonOperatingAssumptions
from ..models.debt import DebtStack
from ..models.property import PropertyInfo


@dataclass
class NonOperatingMonthlySeries:
    """Monthly non-operating arrays. All positive dollars."""

    asset_mgmt_fee: list[float] = field(default_factory=list)
    replacement_reserves: list[float] = field(default_factory=list)
    professional_expenses: list[float] = field(default_factory=list)
    mip: list[float] = field(default_factory=list)
    surplus_cash_note: list[float] = field(default_factory=list)
    total: list[float] = field(default_factory=list)

    def annual_total(self, attr: str, year: int, grid: TimeGrid) -> float:
        s = getattr(self, attr)
        return sum(s[m.proforma_month - 1] for m in grid.months_for_year(year))

    def annual_grand_total(self, year: int, grid: TimeGrid) -> float:
        return sum(self.total[m.proforma_month - 1] for m in grid.months_for_year(year))


def build_non_operating_series(
    non_op: NonOperatingAssumptions,
    property_info: PropertyInfo,
    debt: DebtStack,
    monthly_egr: list[float],
    acq_loan_balance_by_month: list[float],
    grid: TimeGrid,
) -> NonOperatingMonthlySeries:
    """Build the monthly non-operating series.

    Args:
        non_op: NonOperatingAssumptions from the scenario
        property_info: for unit count (reserves PUPY)
        debt: debt stack (MIP is on acquisition loan)
        monthly_egr: monthly Effective Gross Revenue (drives AMF)
        acq_loan_balance_by_month: acquisition loan UPB each month (drives MIP)
        grid: monthly time grid

    Returns:
        NonOperatingMonthlySeries
    """
    n = len(grid)
    s = NonOperatingMonthlySeries()
    s.asset_mgmt_fee = [0.0] * n
    s.replacement_reserves = [0.0] * n
    s.professional_expenses = [0.0] * n
    s.mip = [0.0] * n
    s.surplus_cash_note = [0.0] * n
    s.total = [0.0] * n

    units = property_info.total_units.value
    amf_pct = non_op.asset_mgmt_fee_pct_egr.value
    reserves_pupy = non_op.replacement_reserves_pupy.value
    prof_annual = non_op.professional_expenses_annual.value
    mip_pct = non_op.mip_pct_upb.value if non_op.mip_pct_upb else 0.0
    surplus_note_annual = (
        non_op.surplus_cash_note_annual.value if non_op.surplus_cash_note_annual else 0.0
    )
    # Surplus cash note is paid in two installments: Feb (month 2) and Aug (month 8)
    surplus_installment = surplus_note_annual / 2.0

    monthly_reserves = (reserves_pupy * units) / 12.0
    monthly_prof = prof_annual / 12.0

    for i, m in enumerate(grid.months):
        egr_m = monthly_egr[i] if i < len(monthly_egr) else 0.0
        s.asset_mgmt_fee[i] = amf_pct * egr_m
        s.replacement_reserves[i] = monthly_reserves
        s.professional_expenses[i] = monthly_prof

        upb_m = (
            acq_loan_balance_by_month[i]
            if i < len(acq_loan_balance_by_month)
            else 0.0
        )
        # HUD MIP is an annual rate applied to UPB, paid monthly = rate/12 * UPB
        s.mip[i] = (mip_pct * upb_m) / 12.0

        # Surplus cash note: paid on 2/1 and 8/1 → calendar months 2 and 8
        if m.calendar_month in (2, 8):
            s.surplus_cash_note[i] = surplus_installment

        s.total[i] = (
            s.asset_mgmt_fee[i]
            + s.replacement_reserves[i]
            + s.professional_expenses[i]
            + s.mip[i]
            + s.surplus_cash_note[i]
        )

    return s


__all__ = ["NonOperatingMonthlySeries", "build_non_operating_series"]
