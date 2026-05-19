"""Other Income engine.

Computes the monthly other-income line for each line item in
OtherIncomeAssumptions. In detail mode, each line has:
  - year1_amount (annual $)
  - optional per-year inflation override; otherwise the global
    other-income inflation applies

Year-y amount for a line = year1 × Π(1 + inflation_y) for y > 1
Monthly = year_amount / 12.

TIF revenue is intentionally NOT in this module — TIF has its own engine
(tif.py) producing a separate cited monthly series.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .time_grid import TimeGrid
from ..models.assumptions import (
    InflationSchedule,
    OtherIncomeAssumptions,
    OtherIncomeLine,
    OtherIncomeMethod,
)
from ..models.property import PropertyInfo


@dataclass
class OtherIncomeMonthlySeries:
    """Monthly arrays per line + total."""

    by_line: dict[str, list[float]] = field(default_factory=dict)
    total: list[float] = field(default_factory=list)

    def annual_total(self, line_id: str, year: int, grid: TimeGrid) -> float:
        series = self.by_line[line_id]
        return sum(series[m.proforma_month - 1] for m in grid.months_for_year(year))

    def annual_grand_total(self, year: int, grid: TimeGrid) -> float:
        return sum(self.total[m.proforma_month - 1] for m in grid.months_for_year(year))


def _line_inflation_factor(
    line: OtherIncomeLine,
    year: int,
    default_schedule: InflationSchedule,
) -> float:
    """Cumulative inflation factor from Year 1 to `year` (1-indexed).

    If line has a per-year override for year y, use that; otherwise the
    global other-income inflation rate.
    """
    if year <= 1:
        return 1.0
    factor = 1.0
    for y in range(2, year + 1):
        if y in line.inflation_override_by_year:
            rate = line.inflation_override_by_year[y].value
        else:
            rate = default_schedule.rate(y)
        factor *= (1.0 + rate)
    return factor


def build_other_income_series(
    other_income: OtherIncomeAssumptions,
    other_income_inflation: InflationSchedule,
    property_info: PropertyInfo,
    grid: TimeGrid,
) -> OtherIncomeMonthlySeries:
    """Build the monthly other-income series."""
    series = OtherIncomeMonthlySeries()

    # Initialize totals array
    series.total = [0.0] * len(grid)

    if other_income.method == OtherIncomeMethod.PLUG:
        # Plug mode: PUPY × units × inflation factor / 12
        plug_pupy = (other_income.plug_year1_pupy.value
                     if other_income.plug_year1_pupy else 0.0)
        units = property_info.total_units.value
        for m in grid:
            y = m.proforma_year
            factor = 1.0 if y == 1 else 1.0
            # For plug mode, use Y2 inflation if specified; then global inflation
            for yi in range(2, y + 1):
                if yi == 2 and other_income.plug_y2_inflation:
                    factor *= (1.0 + other_income.plug_y2_inflation.value)
                else:
                    factor *= (1.0 + other_income_inflation.rate(yi))
            monthly = plug_pupy * units * factor / 12
            series.total[m.proforma_month - 1] = monthly
        series.by_line["plug"] = list(series.total)
        return series

    # Detail mode
    for line in other_income.lines:
        line_series = [0.0] * len(grid)
        for m in grid:
            y = m.proforma_year
            factor = _line_inflation_factor(line, y, other_income_inflation)
            annual = line.year1_amount.value * factor
            monthly = annual / 12.0
            line_series[m.proforma_month - 1] = monthly
            series.total[m.proforma_month - 1] += monthly
        series.by_line[line.line_id] = line_series

    return series


__all__ = ["OtherIncomeMonthlySeries", "build_other_income_series"]
