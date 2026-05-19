"""TIF (Tax Increment Financing) model.

Chamberlain's TIF is far more complex than a fixed annual schedule. The
LLC is simultaneously the property-tax payer AND the TIF Note holder, so
property tax changes propagate through TIF mechanics in a non-obvious way.

This model integrates the live TIF engine work (Claude_Context_3) so the
4 scenarios (Baseline / Mid Appeal / Aggressive / MAA Floor) can be run
through the full proforma:

  S1 Baseline:    2026 TMV $54.866M, 4% annual growth, no appeal
  S2 Mid Appeal:  2026 TMV $50.0M,  ~2.81% effective CAGR (appeals every 5 yrs)
  S3 Aggressive:  2026 TMV $47.5M,  ~2.5% effective CAGR (appeals every 5 yrs)
  S4 MAA Floor:   2026 TMV $43.835M, re-appealed to floor every 5 yrs

TIF mechanics (from Ehlers TIF Update + MN 273.13):
  - Property tax = NTC × tax_capacity_rate
    where NTC = (TMV × class_rate)
  - Tax capacity rate (composite local) = 1.27866
  - Apartment class rate = 1.25% (above first $100K)
  - TIF Note balance entering 2026: $7,269,118.57 @ 4.60%
  - Developer Share = 100% (corrected from IDP's double-count)
  - Admin holdback = 10% / OSA fee = 0.36% (both retained by City)

When TMV is reduced via appeal:
  - Property tax drops by ~$0.01598 per $1 TMV reduction
  - TIF receipt drops by ~89.64% of property tax drop
  - Net LLC impact during TIF Note pay window: ~10.36% of property tax saved
  - After TIF Note paid off (or after 2045 decert): 100% of property tax saved flows through

Reconciliation anchor: Ehlers TIF Update April 2026 — our model matches
all unit-level figures within $86.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .citation import Cited


class TIFScenarioName(str, Enum):
    BASELINE = "baseline"
    MID_APPEAL = "mid_appeal"
    AGGRESSIVE_APPEAL = "aggressive_appeal"
    MAA_FLOOR = "maa_floor"


class TIFMechanics(BaseModel):
    """Universal TIF parameters that don't change across scenarios.

    Sourced from MN statute, the TIF Plan, the executed TIF Note, and
    Ehlers' April 2026 TIF Update.
    """

    model_config = ConfigDict(extra="forbid")

    # Class rate (apartments above first $100K)
    class_rate: Cited[float]
    # Composite local tax capacity rate
    tax_capacity_rate: Cited[float]
    # Developer share (100% per corrected interpretation)
    developer_share: Cited[float]
    # Admin holdback %
    admin_pct: Cited[float]
    # OSA TIF fee %
    osa_pct: Cited[float]
    # Original frozen Net Tax Capacity (Base)
    base_ntc: Cited[float]

    # TIF Note terms
    note_original_principal: Cited[float]
    note_interest_rate: Cited[float]
    note_maturity_date: Cited[date]
    note_beginning_balance: Cited[float] = Field(
        description="Note UPB at the start of pay-2026 (Jan 1, 2026)",
    )

    # MAA
    maa_floor: Cited[float] = Field(description="Minimum Assessment Agreement value floor")
    maa_effective_from: Cited[date]
    maa_effective_through: str = Field(
        default="TIF_TERMINATION",
        description="effective until TIF terminates (district last increment year 2045)",
    )

    # TIF district terminus
    tif_district_last_increment_year: int = 2045


class TMVTrajectory(BaseModel):
    """Year-by-year Taxable Market Value path for a scenario.

    For Year keys, we use calendar year. Chamberlain's proforma starts in 2026.
    """

    model_config = ConfigDict(extra="forbid")

    scenario: TIFScenarioName
    description: str

    # year -> TMV ($)
    tmv_by_year: dict[int, Cited[float]] = Field(default_factory=dict)

    # Optional explicit appeal event years (for attorney fee accounting)
    appeal_years: list[int] = Field(
        default_factory=list,
        description="years in which a fresh appeal is filed; drives attorney fee timing",
    )
    growth_rate_assumption: Optional[Cited[float]] = Field(
        default=None,
        description="background growth rate assumed between appeal years",
    )

    def tmv(self, year: int) -> Optional[float]:
        if year in self.tmv_by_year:
            return self.tmv_by_year[year].value
        return None


class AttorneyFeeAssumption(BaseModel):
    """Attorney fee modeling for appeal scenarios.

    Live TIF model: 25% of first-year tax savings, applied once per appeal
    (not annually). Chamberlain totals:
      S2 ~$234K, S3 ~$283K, S4 ~$505K over the modeled window.
    """

    model_config = ConfigDict(extra="forbid")

    fee_pct_of_year1_savings: Cited[float] = Field(
        default_factory=lambda: Cited[float](
            value=0.25,
            citations=[],  # callers must override
        ),
    )
    recurring: bool = False


class TIFConfiguration(BaseModel):
    """Full TIF model configuration for a proforma run."""

    model_config = ConfigDict(extra="forbid")

    mechanics: TIFMechanics

    # All 4 scenarios available; the active one is selected via scenario.active_tif_scenario
    scenarios: dict[TIFScenarioName, TMVTrajectory]

    # Attorney fee assumption (only applied to appeal scenarios)
    attorney_fees: AttorneyFeeAssumption

    # Discount rate for NPV comparisons
    discount_rate: Cited[float]

    def get_scenario(self, name: TIFScenarioName) -> TMVTrajectory:
        if name not in self.scenarios:
            raise KeyError(f"TIF scenario not configured: {name}")
        return self.scenarios[name]


__all__ = [
    "TIFScenarioName",
    "TIFMechanics",
    "TMVTrajectory",
    "AttorneyFeeAssumption",
    "TIFConfiguration",
]
