"""Debt amortization engine.

Computes monthly amortization for:
  - acquisition_loan: the senior loan in place
  - capital_funding_loan: capex shortfall loan (typically 0% Chamberlain)
  - refinance: optional refi event in a future year

For the acquisition loan, the engine supports starting mid-loan (i.e.,
proforma_start_balance + proforma_start_balance_date) so the proforma
amortization picks up where the loan actually is at the start of Year 1.

PMT formula (matches Excel PMT):
  PMT = P × [r(1+r)^n] / [(1+r)^n - 1]
  where:
    P = principal
    r = periodic rate (annual / 12)
    n = amortization periods (months)

Each month:
  interest_month = beginning_balance × r
  principal_month = monthly_payment - interest_month
  ending_balance = beginning_balance - principal_month

If IO period, monthly_payment = interest_month during IO, then
re-amortizes for the remaining term over remaining amort.

For the capital funding loan (0% Chamberlain), the engine treats it as
a balloon with no interest or scheduled principal during the proforma —
it just sits on the balance sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .time_grid import TimeGrid
from ..models.debt import AcquisitionLoan, CapitalFundingLoan, DebtStack


@dataclass
class DebtMonthlySeries:
    """Monthly arrays for the debt stack."""

    # Acquisition loan
    acq_beginning_balance: list[float] = field(default_factory=list)
    acq_interest: list[float] = field(default_factory=list)
    acq_principal: list[float] = field(default_factory=list)
    acq_payment: list[float] = field(default_factory=list)
    acq_ending_balance: list[float] = field(default_factory=list)

    # Capital funding loan
    cap_beginning_balance: list[float] = field(default_factory=list)
    cap_interest: list[float] = field(default_factory=list)
    cap_principal: list[float] = field(default_factory=list)
    cap_funding_draw: list[float] = field(default_factory=list)
    cap_ending_balance: list[float] = field(default_factory=list)

    # Total
    total_debt_service: list[float] = field(default_factory=list)
    total_balance: list[float] = field(default_factory=list)

    def annual_total(self, attr: str, year: int, grid: TimeGrid) -> float:
        s = getattr(self, attr)
        return sum(s[m.proforma_month - 1] for m in grid.months_for_year(year))

    def end_of_year_balance(self, year: int, grid: TimeGrid) -> float:
        last_m = grid.last_month_of_year(year)
        return self.total_balance[last_m.proforma_month - 1]


def _pmt(principal: float, annual_rate: float, n_periods: int) -> float:
    """Excel PMT — monthly payment for a fully-amortizing loan."""
    if n_periods <= 0:
        return 0.0
    if annual_rate <= 0.0:
        return principal / n_periods
    r = annual_rate / 12.0
    return principal * (r * (1 + r) ** n_periods) / ((1 + r) ** n_periods - 1)


def build_debt_series(
    debt: DebtStack,
    grid: TimeGrid,
    capex_loan_draws_by_month: Optional[list[float]] = None,
) -> DebtMonthlySeries:
    """Compute the monthly debt series.

    Args:
        debt: the DebtStack from the scenario
        grid: monthly time grid
        capex_loan_draws_by_month: optional list of $ amounts drawn from
            the capital_funding_loan in each month. If None, no draws.

    Returns:
        DebtMonthlySeries with all per-month arrays populated.
    """
    n = len(grid)
    series = DebtMonthlySeries()

    # --------- Acquisition loan ---------
    acq = debt.acquisition_loan
    annual_rate = acq.rate.value

    # Starting balance: use proforma_start_balance if provided, else original_principal
    if acq.proforma_start_balance is not None:
        upb = acq.proforma_start_balance.value
    else:
        upb = acq.original_principal.value

    # Monthly payment: use override if provided, otherwise compute
    if acq.monthly_payment_override is not None:
        monthly_payment = acq.monthly_payment_override.value
    else:
        monthly_payment = _pmt(
            acq.original_principal.value,
            annual_rate,
            acq.amortization_months.value,
        )

    io_remaining = acq.io_period_months.value

    series.acq_beginning_balance = [0.0] * n
    series.acq_interest = [0.0] * n
    series.acq_principal = [0.0] * n
    series.acq_payment = [0.0] * n
    series.acq_ending_balance = [0.0] * n

    for i, m in enumerate(grid):
        series.acq_beginning_balance[i] = upb
        monthly_interest = upb * annual_rate / 12.0

        if io_remaining > 0:
            # Interest-only month
            principal = 0.0
            payment = monthly_interest
            io_remaining -= 1
        else:
            payment = monthly_payment
            principal = max(0.0, payment - monthly_interest)
            # Floor: don't pay more principal than the outstanding balance
            principal = min(principal, upb)

        series.acq_interest[i] = monthly_interest
        series.acq_principal[i] = principal
        series.acq_payment[i] = payment
        upb = max(0.0, upb - principal)
        series.acq_ending_balance[i] = upb

    # --------- Capital funding loan ---------
    cap = debt.capital_funding_loan
    series.cap_beginning_balance = [0.0] * n
    series.cap_interest = [0.0] * n
    series.cap_principal = [0.0] * n
    series.cap_funding_draw = list(capex_loan_draws_by_month or [0.0] * n)
    series.cap_ending_balance = [0.0] * n

    if cap is not None:
        cap_upb = 0.0
        cap_rate = cap.rate.value
        for i in range(n):
            series.cap_beginning_balance[i] = cap_upb
            monthly_interest = cap_upb * cap_rate / 12.0
            draw = series.cap_funding_draw[i]
            cap_upb += draw
            # No scheduled amortization in Chamberlain's capex loan model
            series.cap_interest[i] = monthly_interest
            series.cap_principal[i] = 0.0
            series.cap_ending_balance[i] = cap_upb

    # --------- Refinance event (not modeled in v1 unless enabled) ---------
    # Chamberlain's base scenario does not refinance; this hook is for
    # future scenarios. Refi logic would zero-out the acq loan balance at
    # refi_year start and start a new acq schedule.
    # For Phase A: refi.enabled=false in base; skip implementation.

    # --------- Totals ---------
    series.total_debt_service = [0.0] * n
    series.total_balance = [0.0] * n
    for i in range(n):
        series.total_debt_service[i] = (
            series.acq_payment[i]
            + series.cap_interest[i]
            + series.cap_principal[i]
        )
        series.total_balance[i] = (
            series.acq_ending_balance[i] + series.cap_ending_balance[i]
        )

    return series


__all__ = ["DebtMonthlySeries", "build_debt_series", "_pmt"]
