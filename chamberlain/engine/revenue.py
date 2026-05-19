"""Revenue engine.

Builds the monthly rent revenue line from the unit roster + assumptions:

  Gross Potential Rent (GPR)
    - Loss to Lease
  Adjusted Gross Rent (AGR)
    - Vacancy
    - Concessions
    - Bad Debt / Collection Loss
  Total Rental Income (TRI)

Each unit type's per-month face rent grows with base_rent_inflation each
proforma year. Year-1 anchor is unit.proforma_year1_rent.

Sign convention: GPR and TRI positive. Offsets (vacancy, concessions,
L2L, bad debt) stored as positive percentages, multiplied through to
produce positive deduction amounts which are subtracted from GPR.

Output: RevenueMonthlySeries with monthly arrays for each line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .time_grid import TimeGrid
from ..models.assumptions import IncomeOffsetAssumptions, InflationSchedule
from ..models.property import UnitRoster


@dataclass
class RevenueMonthlySeries:
    """Monthly revenue arrays. All in dollars, length = total months."""

    gross_potential_rent: list[float] = field(default_factory=list)
    loss_to_lease: list[float] = field(default_factory=list)
    adjusted_gross_rent: list[float] = field(default_factory=list)
    vacancy: list[float] = field(default_factory=list)
    concessions: list[float] = field(default_factory=list)
    bad_debt: list[float] = field(default_factory=list)
    total_rental_income: list[float] = field(default_factory=list)

    def annual_total(self, attr: str, year: int, grid: TimeGrid) -> float:
        series = getattr(self, attr)
        months_in_year = grid.months_for_year(year)
        return sum(series[m.proforma_month - 1] for m in months_in_year)


def build_revenue_series(
    roster: UnitRoster,
    base_rent_inflation: InflationSchedule,
    income_offsets: IncomeOffsetAssumptions,
    grid: TimeGrid,
) -> RevenueMonthlySeries:
    """Build the full monthly revenue series.

    Algorithm (matches Excel proforma logic):
      For each proforma year y:
        - Compute year-y face rent multiplier:
            mult(1) = 1
            mult(y) = mult(y-1) * (1 + inflation(y))   for y >= 2
        - Monthly GPR for the year = sum_unit(count * rent_year1 * mult) ÷ 1
          (rent_year1 is already monthly per Excel convention)
        - For each month in the year:
            GPR_month = monthly_gpr
            L2L_pct = income_offsets.loss_to_lease(y)
            L2L = L2L_pct * GPR_month
            AGR = GPR - L2L
            Vac = stabilized_vacancy * GPR        # Excel applies vacancy to GPR not AGR
            Conc = income_offsets.concessions(y) * GPR
            BadDebt = income_offsets.bad_debt(y) * GPR
            TRI = AGR - Vac - Conc - BadDebt
    """
    series = RevenueMonthlySeries()

    # Pre-compute year-by-year base rent multiplier
    year_mult: dict[int, float] = {1: 1.0}
    for y in range(2, grid.hold_years + 1):
        year_mult[y] = year_mult[y - 1] * (1.0 + base_rent_inflation.rate(y))

    # Pre-compute monthly GPR for each year (sum across all unit types)
    monthly_gpr_by_year: dict[int, float] = {}
    for y in range(1, grid.hold_years + 1):
        mult = year_mult[y]
        # Each UnitType's proforma_year1_rent is in $/unit/month
        gpr_monthly = sum(
            u.unit_count.value * u.proforma_year1_rent.value * mult
            for u in roster.units
        )
        monthly_gpr_by_year[y] = gpr_monthly

    for m in grid:
        y = m.proforma_year
        gpr = monthly_gpr_by_year[y]
        l2l_pct = income_offsets.loss_to_lease(y)
        conc_pct = income_offsets.concessions(y)
        bad_debt_pct = income_offsets.bad_debt(y)
        vac_pct = income_offsets.stabilized_vacancy.value

        l2l = gpr * l2l_pct
        agr = gpr - l2l
        vac = gpr * vac_pct
        conc = gpr * conc_pct
        bad_debt = gpr * bad_debt_pct
        tri = agr - vac - conc - bad_debt

        series.gross_potential_rent.append(gpr)
        series.loss_to_lease.append(l2l)
        series.adjusted_gross_rent.append(agr)
        series.vacancy.append(vac)
        series.concessions.append(conc)
        series.bad_debt.append(bad_debt)
        series.total_rental_income.append(tri)

    return series


__all__ = ["RevenueMonthlySeries", "build_revenue_series"]
