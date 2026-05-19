"""CapEx engine.

Schedules capital expenditures by year/month and determines funding split
between an equity reserve and the capital funding loan.

Funding policies:
  - equity_first: draw from equity reserve until depleted, then loan
  - loan_first:   draw from loan until cap, then equity
  - pari_passu:   draw pro-rata between equity and loan

For Chamberlain, the standard policy is equity_first. The equity reserve
is implicitly the difference between cap_funding_loan.fully_funded_balance
and the actual loan principal drawn — but more simply, the model fronts
the equity required until the loan kicks in.

Distribution within a year:
  Annual capex amounts spread evenly across all 12 months of the year
  unless specified otherwise. This matches the Excel proforma's monthly
  capex assumption of (annual / 12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .time_grid import TimeGrid
from ..models.assumptions import CapExSchedule
from ..models.debt import CapitalFundingLoan


@dataclass
class CapExMonthlySeries:
    """Monthly capex arrays."""

    by_line: dict[str, list[float]] = field(default_factory=dict)
    total: list[float] = field(default_factory=list)

    # Funding split per month
    equity_funded: list[float] = field(default_factory=list)
    loan_funded: list[float] = field(default_factory=list)

    # Cumulative tracking
    cumulative_equity_funded: list[float] = field(default_factory=list)
    cumulative_loan_funded: list[float] = field(default_factory=list)
    remaining_equity_reserve: list[float] = field(default_factory=list)

    def annual_total(self, year: int, grid: TimeGrid) -> float:
        return sum(self.total[m.proforma_month - 1] for m in grid.months_for_year(year))


def build_capex_series(
    capex: CapExSchedule,
    grid: TimeGrid,
    cap_funding_loan: Optional[CapitalFundingLoan] = None,
    equity_reserve_amount: float = 0.0,
) -> CapExMonthlySeries:
    """Build the monthly capex series with equity/loan funding split.

    Args:
        capex: the CapExSchedule from the scenario
        grid: monthly time grid
        cap_funding_loan: the capital funding loan; if None, all equity-funded
        equity_reserve_amount: total equity available for capex over the
            hold; once depleted, loan kicks in.

    Returns:
        CapExMonthlySeries with capex and funding-split arrays.
    """
    n = len(grid)
    series = CapExMonthlySeries()
    series.total = [0.0] * n
    series.equity_funded = [0.0] * n
    series.loan_funded = [0.0] * n
    series.cumulative_equity_funded = [0.0] * n
    series.cumulative_loan_funded = [0.0] * n
    series.remaining_equity_reserve = [equity_reserve_amount] * n

    # Build per-line monthly capex (annual / 12 spread across the year)
    for line in capex.lines:
        line_series = [0.0] * n
        for year_num, amt in line.amount_by_year.items():
            monthly_amt = amt.value / 12.0
            for m in grid.months_for_year(year_num):
                line_series[m.proforma_month - 1] += monthly_amt
                series.total[m.proforma_month - 1] += monthly_amt
        series.by_line[line.line_id] = line_series

    # Apply funding policy
    loan_cap = cap_funding_loan.max_principal.value if cap_funding_loan else 0.0
    funding_priority = cap_funding_loan.funding_priority if cap_funding_loan else "equity_first"

    eq_remaining = equity_reserve_amount
    loan_drawn_total = 0.0
    cum_eq = 0.0

    for i in range(n):
        month_capex = series.total[i]

        if month_capex <= 0:
            series.remaining_equity_reserve[i] = eq_remaining
            series.cumulative_equity_funded[i] = cum_eq
            series.cumulative_loan_funded[i] = loan_drawn_total
            continue

        if funding_priority == "equity_first":
            eq_take = min(month_capex, eq_remaining)
            loan_take = min(month_capex - eq_take, max(0.0, loan_cap - loan_drawn_total))
        elif funding_priority == "loan_first":
            loan_take = min(month_capex, max(0.0, loan_cap - loan_drawn_total))
            eq_take = min(month_capex - loan_take, eq_remaining)
        else:  # pari_passu
            avail_loan = max(0.0, loan_cap - loan_drawn_total)
            avail_eq = eq_remaining
            total_avail = avail_loan + avail_eq
            if total_avail <= 0:
                eq_take, loan_take = 0.0, 0.0
            else:
                eq_take = min(month_capex * avail_eq / total_avail, avail_eq)
                loan_take = min(month_capex - eq_take, avail_loan)

        eq_remaining -= eq_take
        loan_drawn_total += loan_take
        cum_eq += eq_take

        series.equity_funded[i] = eq_take
        series.loan_funded[i] = loan_take
        series.cumulative_equity_funded[i] = cum_eq
        series.cumulative_loan_funded[i] = loan_drawn_total
        series.remaining_equity_reserve[i] = eq_remaining

    return series


__all__ = ["CapExMonthlySeries", "build_capex_series"]
