"""Operating Expense engine.

Detail mode: each OpEx line has a year-1 dollar amount that grows each
year by either a line-specific inflation override or the global OpEx
inflation rate.

For Year-y monthly OpEx on a single line:
  annual_y = year1_amount × cumulative_inflation(y)
  monthly_y = annual_y / 12

Special handling for basis types beyond TOTAL_DOLLARS:
  - PCT_OF_EGR: 3rd-party mgmt fees (not used in Chamberlain detail mode
    — but the engine supports it). Computed in non_operating.py since
    EGR depends on Revenue + Other Income.
  - PER_UNIT_PER_YEAR: multiplied by unit count.

Chamberlain uses TOTAL_DOLLARS basis for all 10 lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .time_grid import TimeGrid
from ..models.assumptions import (
    InflationSchedule,
    OperatingExpenseAssumptions,
    OperatingExpenseLine,
    OpExBasis,
)
from ..models.property import PropertyInfo


@dataclass
class OpExMonthlySeries:
    """Monthly arrays per line + total."""

    by_line: dict[str, list[float]] = field(default_factory=dict)
    total: list[float] = field(default_factory=list)

    def annual_total(self, line_id: str, year: int, grid: TimeGrid) -> float:
        series = self.by_line[line_id]
        return sum(series[m.proforma_month - 1] for m in grid.months_for_year(year))

    def annual_grand_total(self, year: int, grid: TimeGrid) -> float:
        return sum(self.total[m.proforma_month - 1] for m in grid.months_for_year(year))


def _line_cumulative_factor(
    line: OperatingExpenseLine,
    year: int,
    default_schedule: InflationSchedule,
) -> float:
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


def build_opex_series(
    opex: OperatingExpenseAssumptions,
    opex_inflation: InflationSchedule,
    property_info: PropertyInfo,
    grid: TimeGrid,
) -> OpExMonthlySeries:
    """Build monthly OpEx series.

    Note: lines with basis PCT_OF_EGR are skipped here (computed in
    non_operating.py because they need EGR which depends on revenue
    + other income). The convention is that property/asset mgmt fees
    live on `non_operating`, not in OpExAssumptions.
    """
    series = OpExMonthlySeries()
    series.total = [0.0] * len(grid)

    if opex.method == "PLUG":
        pupy = opex.plug_pupy.value if opex.plug_pupy else 0.0
        units = property_info.total_units.value
        for m in grid:
            y = m.proforma_year
            factor = 1.0
            for yi in range(2, y + 1):
                factor *= (1.0 + opex_inflation.rate(yi))
            annual = pupy * units * factor
            series.total[m.proforma_month - 1] = annual / 12.0
        series.by_line["plug"] = list(series.total)
        return series

    # Detail mode
    units = property_info.total_units.value
    for line in opex.lines:
        line_series = [0.0] * len(grid)
        for m in grid:
            y = m.proforma_year
            factor = _line_cumulative_factor(line, y, opex_inflation)
            if line.basis == OpExBasis.TOTAL_DOLLARS:
                annual = line.year1_amount.value * factor
            elif line.basis == OpExBasis.PER_UNIT_PER_YEAR:
                annual = line.year1_amount.value * units * factor
            elif line.basis in (OpExBasis.PCT_OF_EGR, OpExBasis.PCT_OF_UPB):
                # Computed elsewhere
                annual = 0.0
            else:
                annual = line.year1_amount.value * factor
            monthly = annual / 12.0
            line_series[m.proforma_month - 1] = monthly
            series.total[m.proforma_month - 1] += monthly
        series.by_line[line.line_id] = line_series

    return series


__all__ = ["OpExMonthlySeries", "build_opex_series"]
