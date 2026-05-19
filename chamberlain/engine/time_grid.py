"""Monthly time grid for the forward engine.

The engine works at monthly granularity. This module provides:

  - MonthIndex: a single month (1-indexed proforma month + calendar date)
  - TimeGrid: the full sequence of MonthIndex covering Year 1 month 1
    through the residual sale month

Concepts:
  - proforma_month: 1-indexed from the proforma start date (so month 1 is
    Y1 M1, month 13 is Y2 M1, etc.)
  - proforma_year: derived as (proforma_month - 1) // 12 + 1
  - calendar_year/calendar_month: the actual calendar date

The grid spans `hold_years * 12 + 1` months: the +1 covers a residual month
when the sale event is modeled at the end of the last month.

For Chamberlain: start 2026-01-01, hold 10 years → 120 monthly periods
covering 2026-01 through 2035-12. Sale happens at end-of-month for the
sale_year (default 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterator

from dateutil.relativedelta import relativedelta


@dataclass(frozen=True)
class MonthIndex:
    """A single month in the proforma."""

    proforma_month: int        # 1-indexed (1 = Year 1 Month 1)
    proforma_year: int         # 1-indexed (1 = Year 1)
    month_of_year: int         # 1-12 within the proforma year
    calendar_year: int
    calendar_month: int        # 1-12

    @property
    def calendar_date(self) -> date:
        return date(self.calendar_year, self.calendar_month, 1)

    @property
    def is_first_month_of_year(self) -> bool:
        return self.month_of_year == 1

    @property
    def is_last_month_of_year(self) -> bool:
        return self.month_of_year == 12

    def __str__(self) -> str:
        return f"M{self.proforma_month:03d} (Y{self.proforma_year} M{self.month_of_year:02d}, " \
               f"{self.calendar_year}-{self.calendar_month:02d})"


class TimeGrid:
    """Monthly time grid for the proforma."""

    def __init__(self, start_date: date, hold_years: int):
        if start_date.day != 1:
            # Normalize to first of month
            start_date = date(start_date.year, start_date.month, 1)
        self.start_date = start_date
        self.hold_years = hold_years
        self.total_months = hold_years * 12

        self._months: list[MonthIndex] = []
        for m in range(1, self.total_months + 1):
            d = start_date + relativedelta(months=m - 1)
            proforma_year = (m - 1) // 12 + 1
            month_of_year = (m - 1) % 12 + 1
            self._months.append(MonthIndex(
                proforma_month=m,
                proforma_year=proforma_year,
                month_of_year=month_of_year,
                calendar_year=d.year,
                calendar_month=d.month,
            ))

    def __len__(self) -> int:
        return len(self._months)

    def __getitem__(self, idx: int) -> MonthIndex:
        return self._months[idx]

    def __iter__(self) -> Iterator[MonthIndex]:
        return iter(self._months)

    @property
    def months(self) -> list[MonthIndex]:
        return list(self._months)

    def months_for_year(self, year: int) -> list[MonthIndex]:
        """Return all months in a given proforma year (1-indexed)."""
        return [m for m in self._months if m.proforma_year == year]

    def first_month_of_year(self, year: int) -> MonthIndex:
        return self.months_for_year(year)[0]

    def last_month_of_year(self, year: int) -> MonthIndex:
        return self.months_for_year(year)[-1]

    def fiscal_year_label(self, year: int) -> str:
        """Label a proforma year by its fiscal span.

        For a 4/1/2026 start: Year 1 -> 'FY2027 (Apr'26-Mar'27)'.
        For a 1/1 start the fiscal year equals the calendar year.
        """
        first = self.first_month_of_year(year)
        last = self.last_month_of_year(year)
        if self.start_date.month == 1:
            return f"FY{first.calendar_year}"
        # Fiscal year named for the year in which it ends
        fy = last.calendar_year
        return (f"FY{fy} ({first.calendar_year % 100:02d}-"
                f"{last.calendar_year % 100:02d})")

    def fiscal_year_end(self, year: int) -> date:
        last = self.last_month_of_year(year)
        return date(last.calendar_year, last.calendar_month, 1) + \
            relativedelta(months=1) - relativedelta(days=1)


__all__ = ["MonthIndex", "TimeGrid"]
