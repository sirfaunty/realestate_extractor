"""Scenario — the top-level container.

A Scenario is the input bundle to one run of the proforma engine. It holds:

  - SourceDocumentRegistry: the corpus of cited documents
  - PropertyInfo + UnitRoster: the asset
  - All forward assumptions
  - DebtStack
  - PartnershipConfig
  - TIFConfiguration
  - Commercial spaces (if any)
  - Historical actuals + EquityLedger
  - GoverningProvisions (waterfall, MAA, etc.)

The engine consumes a Scenario and produces a ProformaResult.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .assumptions import (
    InflationSchedule,
    IncomeOffsetAssumptions,
    OperatingExpenseAssumptions,
    OtherIncomeAssumptions,
    CapExSchedule,
    NonOperatingAssumptions,
)
from .citation import (
    Cited,
    SourceDocumentRegistry,
)
from .commercial import CommercialSpace
from .debt import DebtStack
from .governing import GoverningProvision
from .historical import (
    HistoricalActuals,
    EquityLedger,
)
from .partnership import PartnershipConfig
from .property import PropertyInfo, UnitRoster
from .tif import TIFConfiguration, TIFScenarioName


class ScenarioMeta(BaseModel):
    """Metadata identifying a scenario run."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(description="stable id, e.g. 'chamberlain_inc_improvements_v1'")
    name: str = Field(description="human-readable name")
    description: Optional[str] = None
    proforma_start_date: date = Field(description="month-1 of the forward proforma (Year 1)")
    hold_years: int = Field(default=10, ge=1, description="proforma horizon")
    sale_year: int = Field(default=10, description="year-N residual sale")
    # TIF reaches partnership cash flow via the executed TIF Note waterfall
    # ONLY: TIF receipts amortize the TIF Note first; the LLC receives the
    # residual increment only after the Note is satisfied. Per the executed
    # TIF documentation the Note is not projected to pay off until ~2039
    # (baseline) / ~2043 (appeal-adjusted), so $0 reaches the LLC during a
    # 2026-2036 ten-year hold. This is not configurable; an earlier
    # "gross passthrough" reconciliation toggle (which reproduced the
    # superseded 2025 Excel's ~20.9% IRR by treating committed Note cash
    # as distributable) has been removed because it contradicts the
    # executed TIF Note.


class ResidualAssumptions(BaseModel):
    """Sale-at-exit assumptions."""

    model_config = ConfigDict(extra="forbid")

    sale_year: Cited[int]
    residual_cap_rate: Cited[float]
    cost_of_sale_pct: Cited[float]


class Scenario(BaseModel):
    """Full scenario bundle.

    One Scenario instance fully describes one run. The engine accepts
    a Scenario and produces ProformaResult.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    # Identification
    meta: ScenarioMeta

    # The corpus of cited source documents
    source_registry: SourceDocumentRegistry

    # The asset
    property: PropertyInfo
    unit_roster: UnitRoster
    commercial_spaces: list[CommercialSpace] = Field(default_factory=list)

    # Forward assumptions
    base_rent_inflation: InflationSchedule
    opex_inflation: InflationSchedule
    other_income_inflation: InflationSchedule
    income_offsets: IncomeOffsetAssumptions
    opex: OperatingExpenseAssumptions
    other_income: OtherIncomeAssumptions
    non_operating: NonOperatingAssumptions
    capex: CapExSchedule

    # Capital structure
    debt: DebtStack
    partnership: PartnershipConfig

    # TIF (optional — non-TIF deals omit)
    tif: Optional[TIFConfiguration] = None
    active_tif_scenario: Optional[TIFScenarioName] = TIFScenarioName.BASELINE

    # Residual / sale
    residual: ResidualAssumptions

    # Historicals
    historical_actuals: Optional[HistoricalActuals] = None
    equity_ledger: Optional[EquityLedger] = None

    # Provisions registry (LLC §5.2, MAA, etc.)
    governing_provisions: list[GoverningProvision] = Field(default_factory=list)

    # Acquisition cost basis (used as "Year 0" reference and IRR denominator)
    acquisition_cost_basis: Cited[float] = Field(
        description="proforma cost basis at Year 0; for Chamberlain = $61,805,592 ex-Contributed Dev Fee",
    )

    def get_provision(self, provision_id: str) -> Optional[GoverningProvision]:
        for p in self.governing_provisions:
            if p.id == provision_id:
                return p
        return None


__all__ = ["ScenarioMeta", "ResidualAssumptions", "Scenario"]
