"""Historical actuals data structures.

For Chamberlain we have:
  - Monthly MRI 12-month income statements for 2017-2024 (file per year)
  - 9 months of 2025 actuals from TTM Sep 2025 file (Jan-Sep)
  - 2025 Q4 reforecast + 2026 budget from Property Overview workbook

The shape carries:
  - line items (90+ in MRI exports) at the granularity of the source
  - aggregated rollups (Total Revenue, OpEx categories, NOI) for cross-asset
    comparability
  - the validated "Property NOI" (NOI before AMF, CapEx, non-op) per the
    Q2 2026 reconciliation work

Every line item, every total, every period is Cited.

EquityLedger holds the JV partner contribution / distribution timeline
sourced from the MRI capital accounts + JV-Equity Return Calc files.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .citation import Cited


class FiscalPeriodType(str, Enum):
    """Granularity of a HistoricalPeriod."""

    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    TTM = "ttm"          # trailing-12-month at point-in-time


class FiscalPeriod(BaseModel):
    """A discrete time slice (year, quarter, month, or TTM)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period_type: FiscalPeriodType
    year: int
    month: Optional[int] = Field(default=None, ge=1, le=12)
    quarter: Optional[int] = Field(default=None, ge=1, le=4)
    label: Optional[str] = Field(default=None, description="e.g. '2023A', 'TTM Sep 2025'")

    def __str__(self) -> str:
        if self.label:
            return self.label
        if self.period_type == FiscalPeriodType.YEAR:
            return f"{self.year}A"
        if self.period_type == FiscalPeriodType.QUARTER:
            return f"Q{self.quarter} {self.year}"
        if self.period_type == FiscalPeriodType.MONTH:
            return f"{self.year}-{self.month:02d}" if self.month else str(self.year)
        return f"TTM {self.year}"


# --------------------------------------------------------------------------
# Historical line items
# --------------------------------------------------------------------------


class HistoricalLineCategory(str, Enum):
    """Top-level taxonomy for line items.

    Maps to the standardized chart-of-accounts the Property Overview uses
    (Payroll, Advertising/Marketing, Administrative, Leasing Commissions,
    Repairs & Maintenance, Turnover, Management Fees, Utilities, RE Taxes,
    Insurance, etc.). The HistoricalLineItem.label is the raw MRI label;
    category is the rollup bucket.
    """

    # Revenue
    GROSS_POTENTIAL_RENT = "gross_potential_rent"
    LOSS_TO_LEASE = "loss_to_lease"
    VACANCY = "vacancy"
    CONCESSIONS = "concessions"
    NON_REVENUE_UNITS = "non_revenue_units"
    PARKING = "parking"
    REIMBURSEMENT_INCOME = "reimbursement_income"
    PET_FEES = "pet_fees"
    LATE_FEES = "late_fees"
    MISC_TENANT_REVENUE = "misc_tenant_revenue"
    TIF_REVENUE = "tif_revenue"
    INTEREST_INCOME = "interest_income"
    OTHER_INCOME = "other_income"

    # OpEx — controllable
    PAYROLL = "payroll"
    ADVERTISING_MARKETING = "advertising_marketing"
    ADMINISTRATIVE = "administrative"
    LEASING_COMMISSIONS = "leasing_commissions"
    REPAIRS_MAINTENANCE = "repairs_maintenance"
    TURNOVER_COMMON_AREA = "turnover_common_area"
    MANAGEMENT_FEES = "management_fees"

    # OpEx — non-controllable
    UTILITIES = "utilities"
    REAL_ESTATE_TAXES = "real_estate_taxes"
    INSURANCE = "insurance"

    # Non-operating / capital / debt service
    ASSET_MGMT_FEE = "asset_mgmt_fee"
    DEBT_SERVICE_INTEREST = "debt_service_interest"
    DEBT_SERVICE_PRINCIPAL = "debt_service_principal"
    MIP = "mip"
    SURPLUS_CASH_NOTE = "surplus_cash_note"
    DEPRECIATION = "depreciation"
    AMORTIZATION = "amortization"
    NON_OPERATING_OTHER = "non_operating_other"
    INTERCOMPANY_INTEREST = "intercompany_interest"

    # CapEx
    ROUTINE_CAPEX = "routine_capex"
    IMPROVEMENT_CAPEX = "improvement_capex"

    # Working capital / accounting adjustments
    WORKING_CAPITAL_ADJUSTMENT = "working_capital_adjustment"
    OTHER = "other"


class HistoricalLineItem(BaseModel):
    """A single line item for a given period.

    The 'label' is the raw source label (e.g., "Gross Potential Rent-Residential"
    from MRI). 'category' is the rollup bucket. 'amount' is signed in the
    source convention (revenue positive, expenses negative).
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="source label, e.g. 'Real Estate Tax Expense'")
    category: HistoricalLineCategory
    amount: Cited[float] = Field(description="signed amount; revenue +, expense -")
    period: FiscalPeriod


class HistoricalPeriod(BaseModel):
    """All line items for one period (typically a year).

    Carries both the raw line items AND the validated rollups (Total Revenue,
    OpEx, NOI, Property NOI). Rollups can be either Cited (e.g., from the
    Property Overview Summary directly) or Derived (computed from line items).
    """

    model_config = ConfigDict(extra="forbid")

    period: FiscalPeriod
    line_items: list[HistoricalLineItem] = Field(default_factory=list)

    # Rollups — when available from a higher-authority source than the line items
    total_revenue: Optional[Cited[float]] = None
    total_opex_as_reported: Optional[Cited[float]] = None
    noi_as_reported: Optional[Cited[float]] = None

    # The validated "Property NOI" from the Q2 2026 reconciliation
    # (NOI before AMF, CapEx, non-operating items)
    property_noi: Optional[Cited[float]] = None

    # Below-the-line
    amf_amount: Optional[Cited[float]] = None
    routine_capex: Optional[Cited[float]] = None
    improvement_capex: Optional[Cited[float]] = None
    debt_service: Optional[Cited[float]] = None

    def line_items_by_category(self) -> dict[HistoricalLineCategory, list[HistoricalLineItem]]:
        out: dict[HistoricalLineCategory, list[HistoricalLineItem]] = {}
        for li in self.line_items:
            out.setdefault(li.category, []).append(li)
        return out

    def sum_category(self, cat: HistoricalLineCategory) -> float:
        return sum(li.amount.value for li in self.line_items if li.category == cat)


class HistoricalActuals(BaseModel):
    """Full historical actuals for a property across periods."""

    model_config = ConfigDict(extra="forbid")

    periods: list[HistoricalPeriod] = Field(default_factory=list)

    def period(self, year: int, period_type: FiscalPeriodType = FiscalPeriodType.YEAR) -> Optional[HistoricalPeriod]:
        for p in self.periods:
            if p.period.year == year and p.period.period_type == period_type:
                return p
        return None


# --------------------------------------------------------------------------
# Equity Ledger
# --------------------------------------------------------------------------


class EquityTransaction(BaseModel):
    """A single equity event for a specific investor class.

    Sourced from:
      - MRI Equity Account Details (capital account ledger)
      - JV-Equity Return Calc files (8/2022, 2/2023)
      - Closing Proceeds Summary 10/26/21 (refi event)
      - Project Loan Interest Calc (intercompany loan tracking)
    """

    model_config = ConfigDict(extra="forbid")

    transaction_date: Cited[date]
    investor_class_id: str = Field(description="'KA', 'IDP', etc.")
    transaction_type: str = Field(
        description="contribution | distribution | project_loan_advance | "
        "project_loan_repayment | fee_contribution | escrow_return | refi_distribution",
    )

    # Signed amount: + = into investor / out of LLC; - = out of investor / into LLC
    # By convention: contributions are NEGATIVE (cash from investor), distributions POSITIVE
    amount: Cited[float]

    # What governs this transaction
    governing_provision_id: Optional[str] = Field(
        default=None,
        description="LLC §5.2(a) etc., for reconciliation",
    )

    # Descriptive context
    description: Optional[str] = None
    period_label: Optional[str] = Field(default=None, description="e.g., 'May 2022 SC Distribution'")

    # MRI references (when sourced from a capital account ledger)
    mri_account: Optional[str] = Field(default=None, description="e.g., 'MR29105003 - KA'")
    mri_ref_number: Optional[str] = None


class EquityLedger(BaseModel):
    """All historical equity transactions per investor class."""

    model_config = ConfigDict(extra="forbid")

    transactions: list[EquityTransaction] = Field(default_factory=list)

    def for_investor(self, class_id: str) -> list[EquityTransaction]:
        return [t for t in self.transactions if t.investor_class_id == class_id]

    def cumulative_by_type(self, class_id: str, transaction_type: str) -> float:
        return sum(
            t.amount.value
            for t in self.transactions
            if t.investor_class_id == class_id and t.transaction_type == transaction_type
        )


__all__ = [
    "FiscalPeriodType",
    "FiscalPeriod",
    "HistoricalLineCategory",
    "HistoricalLineItem",
    "HistoricalPeriod",
    "HistoricalActuals",
    "EquityTransaction",
    "EquityLedger",
]
