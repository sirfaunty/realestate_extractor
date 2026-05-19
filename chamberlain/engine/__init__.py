"""Forward proforma engine.

Monthly granularity under the hood; aggregates to annual for output.

Modules:
  - time_grid: monthly time index, year boundaries, period mappings
  - revenue: unit-level rent build, vacancy, concessions, L2L
  - other_income: parking, pet, reimbursements
  - opex: detail-mode operating expenses with separate inflation per line
  - debt_amort: multi-loan monthly amortization (PMT-based)
  - capex: capex schedule + equity-first vs loan-first funding
  - tif: TIF sub-engine — 4 scenarios with Ehlers reconciliation
  - non_operating: AMF, MIP, replacement reserves, surplus cash note
  - residual: Year-N sale proceeds
  - returns: IRR, EM, DSCR, YoC, CoC at deal level
  - waterfall: configurable tier evaluator → investor-class cash flows
  - run: top-level orchestration produces ProformaResult from a Scenario
"""

from .time_grid import TimeGrid, MonthIndex

__all__ = ["TimeGrid", "MonthIndex"]
