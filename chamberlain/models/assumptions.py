"""Forward-looking assumptions for the proforma engine.

Mirrors the Excel ASSUMPTIONS sheet structure but with citations and
explicit data shapes for each section. The forward engine reads these
models to produce the 10-year monthly projection.

Sections:
  - InflationSchedule: per-year growth rates for rent, opex, other income
  - RentAssumptions: market rent year-1 + escalators (per unit type, applied in revenue.py)
  - IncomeOffsetAssumptions: loss-to-lease, vacancy, concessions, bad debt
  - OperatingExpenseLine: detail-mode OpEx items with separate inflation overrides
  - OtherIncomeLine: detail-mode other income items (parking, pet, reimbursements, etc.)
  - CapExLine: capex items by year
  - NonOperatingAssumptions: reserves, AMF, MIP, surplus cash note, etc.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .citation import Cited


# --------------------------------------------------------------------------
# Inflation
# --------------------------------------------------------------------------


class InflationCategory(str, Enum):
    BASE_RENT = "base_rent"
    OPERATING_EXPENSE = "operating_expense"
    OTHER_INCOME = "other_income"


class InflationSchedule(BaseModel):
    """Per-year inflation rates by category.

    Year keys are proforma-year ordinals (1..N) where year 1 is the first
    forward year of the proforma. Year 1 has no inflation since it's the
    base year. Years beyond the schedule terminus use the terminal_rate.

    Excel maps to ASSUMPTIONS!D28:N30 (one row per category, Years 2-11+).
    """

    model_config = ConfigDict(extra="forbid")

    category: InflationCategory
    # Map proforma year -> Cited rate. Year 1 typically absent (no inflation).
    rates_by_year: dict[int, Cited[float]] = Field(default_factory=dict)
    terminal_rate: Cited[float] = Field(
        description="rate to apply beyond the last explicit year (Year 11+ in Excel)",
    )

    def rate(self, year: int) -> float:
        """Get the inflation rate for proforma year N (1-indexed)."""
        if year <= 1:
            return 0.0
        if year in self.rates_by_year:
            return self.rates_by_year[year].value
        return self.terminal_rate.value


# --------------------------------------------------------------------------
# Rent assumptions
# --------------------------------------------------------------------------


class RentAssumptions(BaseModel):
    """Container for rent-related assumptions beyond per-unit market rents.

    Per-unit Year-1 market rents live on UnitType.proforma_year1_rent.
    This struct holds anything that applies to rent rolls in aggregate.

    The base_rent_inflation reference is the InflationSchedule above; this
    model just carries portfolio-level rent assumptions that aren't per-unit.
    """

    model_config = ConfigDict(extra="forbid")

    # Currently empty — the per-unit Year-1 rents on UnitType are sufficient.
    # Future: rent burndown schedules, lease-up curves, retention assumptions, etc.
    note: Optional[str] = None


# --------------------------------------------------------------------------
# Income offsets
# --------------------------------------------------------------------------


class IncomeOffsetAssumptions(BaseModel):
    """Loss-to-lease, vacancy, concessions, bad debt — as % of GPR.

    Excel maps to ASSUMPTIONS!D33:I36. Each line has a schedule by year:
    Year 1 / Year 2 / Year 3 / Year 4+. We model it as a dict keyed by
    proforma year, with a terminal value beyond.

    The schedules differ for each offset — Chamberlain has them defined
    separately for: Loss to Lease, Lease Concessions, Bad Debt.

    Vacancy is handled separately as it has a single stabilized rate
    (Excel ASSUMPTIONS!D47).
    """

    model_config = ConfigDict(extra="forbid")

    # Each is a year -> Cited[rate as decimal of GPR] map plus a terminal rate.
    loss_to_lease_by_year: dict[int, Cited[float]] = Field(default_factory=dict)
    loss_to_lease_terminal: Cited[float]

    concessions_by_year: dict[int, Cited[float]] = Field(default_factory=dict)
    concessions_terminal: Cited[float]

    bad_debt_by_year: dict[int, Cited[float]] = Field(default_factory=dict)
    bad_debt_terminal: Cited[float]

    # Stabilized vacancy (single rate)
    stabilized_vacancy: Cited[float] = Field(
        description="% of GPR (decimal); applied to all years equally",
    )
    vacancy_stabilized_year: int = Field(
        default=1,
        description="proforma year at which vacancy stabilizes (used for ramp logic if needed)",
    )

    def lookup(self, table: dict[int, Cited[float]], terminal: Cited[float], year: int) -> float:
        if year in table:
            return table[year].value
        return terminal.value

    def loss_to_lease(self, year: int) -> float:
        return self.lookup(self.loss_to_lease_by_year, self.loss_to_lease_terminal, year)

    def concessions(self, year: int) -> float:
        return self.lookup(self.concessions_by_year, self.concessions_terminal, year)

    def bad_debt(self, year: int) -> float:
        return self.lookup(self.bad_debt_by_year, self.bad_debt_terminal, year)


# --------------------------------------------------------------------------
# Operating Expenses
# --------------------------------------------------------------------------


class OpExBasis(str, Enum):
    """How a line item is sized."""

    TOTAL_DOLLARS = "total_dollars"      # $/year flat
    PER_UNIT_PER_YEAR = "per_unit_per_year"
    PCT_OF_EGR = "pct_of_egr"            # % of Effective Gross Revenue
    PCT_OF_UPB = "pct_of_upb"            # % of outstanding loan balance (for MIP etc.)


class OperatingExpenseLine(BaseModel):
    """A single OpEx line item (Detail mode).

    The Excel detail mode has 10 line items for Chamberlain (RE Taxes,
    Insurance, Utilities, Turnover, R&M, Marketing, Payroll, Admin,
    3rd-Party Mgmt, plus Other). Each can have its own Year 2 inflation
    override; subsequent years use the global Operating Expense Inflation
    from the InflationSchedule.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    line_id: str = Field(description="stable id, e.g. 'real_estate_taxes'")
    basis: OpExBasis = OpExBasis.TOTAL_DOLLARS

    # Base year-1 amount
    year1_amount: Cited[float] = Field(description="Year-1 amount in the basis unit (PUPY, $/yr, etc.)")

    # Optional per-year inflation override; otherwise uses global opex inflation
    # year -> override rate
    inflation_override_by_year: dict[int, Cited[float]] = Field(default_factory=dict)


class OperatingExpenseAssumptions(BaseModel):
    """All OpEx lines + the global inflation reference."""

    model_config = ConfigDict(extra="forbid")

    method: str = Field(default="DETAIL", description="DETAIL or PLUG")

    # Detail mode: full line list
    lines: list[OperatingExpenseLine] = Field(default_factory=list)

    # Plug mode (Excel ASSUMPTIONS!D580): single PUPY with global inflation
    plug_pupy: Optional[Cited[float]] = None

    @model_validator(mode="after")
    def _validate(self) -> "OperatingExpenseAssumptions":
        if self.method == "DETAIL" and not self.lines:
            raise ValueError("DETAIL OpEx requires at least one line")
        if self.method == "PLUG" and self.plug_pupy is None:
            raise ValueError("PLUG OpEx requires plug_pupy")
        return self


# --------------------------------------------------------------------------
# Other Income
# --------------------------------------------------------------------------


class OtherIncomeMethod(str, Enum):
    PLUG = "PLUG"
    DETAIL = "DETAIL"


class OtherIncomeBasis(str, Enum):
    TOTAL_DOLLARS = "total_dollars"
    PER_UNIT_PER_YEAR = "per_unit_per_year"
    PARKING_RATE = "parking_rate"          # rate × occupied spaces × 12
    PET_RATE = "pet_rate"                  # rate × pet_count × 12
    STORAGE_RATE = "storage_rate"


class OtherIncomeLine(BaseModel):
    """A single other-income line item (Detail mode).

    Chamberlain's detail mode has lines for: Parking, Storage, Work-from-Home
    Offices, Pet Rents, Trash Reimbursement, Utility Reimbursement,
    Cable/Internet Reimbursement, Application Fees, Move-in Fees,
    Transfer Fees, Late Fees, Renter's Insurance, Rent-Up Charge,
    Misc Other Income, Damages/Bad Debt, TIF Revenue (special — see TIFSchedule).

    For parking/pet/storage, the basis is rate-driven (rate × occupied units).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    line_id: str
    basis: OtherIncomeBasis = OtherIncomeBasis.TOTAL_DOLLARS

    year1_amount: Cited[float] = Field(description="Year-1 amount in the basis unit")

    # Rate-driven lines (parking, pet, storage) have rate-specific schedules
    # by year. For simple lines this is unused.
    rate_by_year: dict[int, Cited[float]] = Field(default_factory=dict)
    occupancy_by_year: dict[int, Cited[float]] = Field(default_factory=dict)
    units_count: Optional[Cited[int]] = Field(
        default=None,
        description="for parking/pet/storage: number of spaces/units (denominator)",
    )

    # Inflation override (otherwise global other-income inflation applies)
    inflation_override_by_year: dict[int, Cited[float]] = Field(default_factory=dict)


class OtherIncomeAssumptions(BaseModel):
    """All other-income line items."""

    model_config = ConfigDict(extra="forbid")

    method: OtherIncomeMethod = OtherIncomeMethod.DETAIL

    # Detail mode
    lines: list[OtherIncomeLine] = Field(default_factory=list)

    # Plug mode (Excel ASSUMPTIONS!D308)
    plug_year1_pupy: Optional[Cited[float]] = None
    plug_y2_inflation: Optional[Cited[float]] = None

    @model_validator(mode="after")
    def _validate(self) -> "OtherIncomeAssumptions":
        if self.method == OtherIncomeMethod.DETAIL and not self.lines:
            raise ValueError("DETAIL OtherIncome requires at least one line")
        if self.method == OtherIncomeMethod.PLUG and self.plug_year1_pupy is None:
            raise ValueError("PLUG OtherIncome requires plug_year1_pupy")
        return self


# --------------------------------------------------------------------------
# CapEx
# --------------------------------------------------------------------------


class CapExLine(BaseModel):
    """A single capex line by year.

    Excel maps to ASSUMPTIONS!D643:N656. Each line has a TOTAL by year
    for years 1-5 (Chamberlain's improvement period); years 6+ are zero
    unless overridden.

    Funding mechanics (equity-first or loan-first, capex reserve, etc.)
    live on the DebtStack / CapitalFundingLoan, not here.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    line_id: str
    category: str = Field(default="improvement", description="improvement | maintenance | required")

    # year -> $ for that year. Years not present default to 0.
    amount_by_year: dict[int, Cited[float]] = Field(default_factory=dict)


class CapExSchedule(BaseModel):
    """Collection of capex lines + funding policy."""

    model_config = ConfigDict(extra="forbid")

    lines: list[CapExLine] = Field(default_factory=list)
    funding_type: str = Field(
        default="equity_first",
        description="equity_first | loan_first | pari_passu",
    )


# --------------------------------------------------------------------------
# Non-Operating
# --------------------------------------------------------------------------


class NonOperatingAssumptions(BaseModel):
    """Reserves, asset mgmt fees, MIP, etc.

    Excel maps to ASSUMPTIONS!D587:I594.
    """

    model_config = ConfigDict(extra="forbid")

    # Property management fee (typically % of EGR)
    property_mgmt_fee_pct_egr: Cited[float]
    property_mgmt_fee_includes_commercial: bool = True

    # Asset management fee (% of EGR)
    asset_mgmt_fee_pct_egr: Cited[float]
    asset_mgmt_fee_includes_commercial: bool = True

    # Replacement reserves (PUPY)
    replacement_reserves_pupy: Cited[float]

    # Professional / other non-op
    professional_expenses_annual: Cited[float] = Field(description="$/year flat")

    # MIP (% of UPB, HUD loans)
    mip_pct_upb: Optional[Cited[float]] = None

    # Surplus Cash Note (Chamberlain-specific: $22,641 × 2 = $45,282/year)
    surplus_cash_note_annual: Optional[Cited[float]] = None

    # Extra slots for future / other deals
    other_lines: list[OperatingExpenseLine] = Field(default_factory=list)


__all__ = [
    "InflationCategory",
    "InflationSchedule",
    "RentAssumptions",
    "IncomeOffsetAssumptions",
    "OpExBasis",
    "OperatingExpenseLine",
    "OperatingExpenseAssumptions",
    "OtherIncomeMethod",
    "OtherIncomeBasis",
    "OtherIncomeLine",
    "OtherIncomeAssumptions",
    "CapExLine",
    "CapExSchedule",
    "NonOperatingAssumptions",
]
