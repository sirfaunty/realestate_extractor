"""Debt structure: acquisition loan, capital-funding loan, optional refinance.

Chamberlain's actual structure (from the Property Overview 'Existing Loan'
tab + LLC Agreement):

  Acquisition loan: Colliers Mortgage HUD 223(f)
    - Original $52,967,700 @ 2.33% fixed
    - 35-year amortization, 35-year term
    - First payment 12/1/2021, maturity 11/1/2056
    - Monthly P&I: $184,565.17
    - MIP 0.35% of UPB (separate from P&I)

  Capital funding loan: $1,013,857 @ 0% (per Excel "Inc Improvements" scenario)
    - Funds 5-year capex shortfall after equity draw
    - Equity-first funding: each month's capex draws from equity reserve until
      depleted, then loan funds the remainder

  Refinance: modeled as toggleable in Year 5 (not exercised in current scenarios)
    - $30M proceeds @ 6% rate, 5-yr term, 30-yr amort, 2-yr I/O
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .citation import Cited


class LoanType(str, Enum):
    HUD_223D = "hud_223d"        # construction/substantial rehab
    HUD_223F = "hud_223f"        # refinance/acquisition
    CMBS = "cmbs"
    BANK = "bank"
    LIFE_INSURANCE = "life_insurance"
    AGENCY = "agency"
    BRIDGE = "bridge"
    INTERCOMPANY = "intercompany"
    OTHER = "other"


# --------------------------------------------------------------------------
# Acquisition Loan
# --------------------------------------------------------------------------


class AcquisitionLoan(BaseModel):
    """The senior loan in place at the start of the proforma.

    For Chamberlain, this is the Colliers HUD 223(f) refi loan that was
    in place at the proforma start. All amounts in dollars; rates in
    decimal form.
    """

    model_config = ConfigDict(extra="forbid")

    lender: Cited[str]
    loan_type: LoanType
    original_principal: Cited[float]
    rate: Cited[float] = Field(description="annual rate as decimal, e.g. 0.0233")
    term_months: Cited[int] = Field(description="term in months")
    amortization_months: Cited[int] = Field(description="amort schedule in months")
    io_period_months: Cited[int] = Field(default_factory=lambda: Cited(value=0, citations=[]))

    # Dates
    first_payment_date: Cited[date]
    maturity_date: Cited[date]

    # Beginning balance for the proforma start period (if not first payment date)
    proforma_start_balance: Optional[Cited[float]] = Field(
        default=None,
        description="If proforma starts mid-loan, the UPB at proforma start month",
    )
    proforma_start_balance_date: Optional[date] = None

    # Optional fees / extras
    finance_fee: Optional[Cited[float]] = Field(
        default=None,
        description="% of loan amount paid at origination",
    )
    monthly_payment_override: Optional[Cited[float]] = Field(
        default=None,
        description="If provided, used directly; otherwise PMT() is computed",
    )

    # Allow Cited construction with empty citations for the default IO=0
    @classmethod
    def io_zero_default(cls) -> Cited[int]:
        """Default helper for io_period_months when no IO period applies."""
        # Avoid Cited validation requiring citations by using a placeholder
        return Cited(value=0, citations=[
            # callers should override this with a real citation in production
        ])


# --------------------------------------------------------------------------
# Capital Funding Loan
# --------------------------------------------------------------------------


class CapitalFundingLoan(BaseModel):
    """Loan that funds capex shortfall after equity reserve is drawn.

    Chamberlain: $1,013,857 @ 0% (so it's effectively additional debt that
    just sits on the balance sheet without interest accrual or amortization).
    """

    model_config = ConfigDict(extra="forbid")

    max_principal: Cited[float] = Field(description="cap on capex loan draw")
    rate: Cited[float] = Field(description="annual rate; 0.0 for Chamberlain")
    funding_priority: str = Field(
        default="equity_first",
        description="equity_first | loan_first | pari_passu",
    )
    fully_funded_balance: Optional[Cited[float]] = Field(
        default=None,
        description="loan balance assuming full capex plan draws on loan",
    )


# --------------------------------------------------------------------------
# Refinance
# --------------------------------------------------------------------------


class RefinanceLoan(BaseModel):
    """Modeled optional refinance event.

    For Chamberlain's current scenarios this is configured but not exercised
    (proceeds 0). Future scenarios may exercise it.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    refi_year: Optional[Cited[int]] = None  # proforma year of refi event

    proceeds: Optional[Cited[float]] = None  # cash to LLC at refi
    existing_loan_payoff: Optional[Cited[float]] = None  # what gets paid off

    new_principal: Optional[Cited[float]] = None
    new_rate: Optional[Cited[float]] = None
    new_term_months: Optional[Cited[int]] = None
    new_amortization_months: Optional[Cited[int]] = None
    new_io_period_months: Optional[Cited[int]] = None
    finance_fee: Optional[Cited[float]] = None


# --------------------------------------------------------------------------
# Debt Stack
# --------------------------------------------------------------------------


class DebtStack(BaseModel):
    """All debt instruments for the property."""

    model_config = ConfigDict(extra="forbid")

    acquisition_loan: AcquisitionLoan
    capital_funding_loan: Optional[CapitalFundingLoan] = None
    refinance: Optional[RefinanceLoan] = None


__all__ = [
    "LoanType",
    "AcquisitionLoan",
    "CapitalFundingLoan",
    "RefinanceLoan",
    "DebtStack",
]
