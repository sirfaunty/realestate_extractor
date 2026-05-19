"""Pydantic data models for the Chamberlain financial model.

Two layers:

1. Platform-foundation entities (citation.py, governing.py):
   - SourceDocument, Citation, Cited[T], Locator, AuthorityTier
   - GoverningProvision, Reconciliation
   These are domain-agnostic; they describe HOW we represent facts and rules.

2. Chamberlain-domain entities (everything else):
   - PropertyInfo, UnitRoster
   - Assumptions (inflation, income offsets, OpEx, other income, CapEx, non-op)
   - DebtStack (acquisition, capex funding, refinance)
   - PartnershipConfig + InvestorClass + WaterfallTier + CapitalEvent
   - TIFConfiguration + TMVTrajectory
   - CommercialSpace + LeaseTerm
   - HistoricalActuals + EquityLedger
   - Scenario (top-level container)
"""

from .citation import (
    AuthorityTier, Citation, Cited, Derived, DocumentType, Locator,
    SourceDocument, SourceDocumentRegistry, UncitedReason, cite,
)
from .governing import (
    GoverningProvision, ImpactDirection, ProvisionType, Reconciliation,
    ReconciliationStatus,
)
from .property import PropertyInfo, UnitRoster, UnitType
from .assumptions import (
    CapExLine, CapExSchedule, InflationCategory, InflationSchedule,
    IncomeOffsetAssumptions, NonOperatingAssumptions,
    OperatingExpenseAssumptions, OperatingExpenseLine, OpExBasis,
    OtherIncomeAssumptions, OtherIncomeBasis, OtherIncomeLine,
    OtherIncomeMethod, RentAssumptions,
)
from .debt import (
    AcquisitionLoan, CapitalFundingLoan, DebtStack, LoanType, RefinanceLoan,
)
from .partnership import (
    CapitalEvent, CapitalEventType, InvestorClass, PartnershipConfig,
    TierType, WaterfallTier,
)
from .commercial import CommercialSpace, LeaseTerm
from .tif import (
    AttorneyFeeAssumption, TIFConfiguration, TIFMechanics, TIFScenarioName,
    TMVTrajectory,
)
from .historical import (
    EquityLedger, EquityTransaction, FiscalPeriod, FiscalPeriodType,
    HistoricalActuals, HistoricalLineCategory, HistoricalLineItem,
    HistoricalPeriod,
)
from .scenario import ResidualAssumptions, Scenario, ScenarioMeta

__all__ = [
    "AuthorityTier", "Citation", "Cited", "Derived", "DocumentType", "Locator",
    "SourceDocument", "SourceDocumentRegistry", "UncitedReason", "cite",
    "GoverningProvision", "ImpactDirection", "ProvisionType", "Reconciliation",
    "ReconciliationStatus",
    "PropertyInfo", "UnitRoster", "UnitType",
    "CapExLine", "CapExSchedule", "InflationCategory", "InflationSchedule",
    "IncomeOffsetAssumptions", "NonOperatingAssumptions",
    "OperatingExpenseAssumptions", "OperatingExpenseLine", "OpExBasis",
    "OtherIncomeAssumptions", "OtherIncomeBasis", "OtherIncomeLine",
    "OtherIncomeMethod", "RentAssumptions",
    "AcquisitionLoan", "CapitalFundingLoan", "DebtStack", "LoanType", "RefinanceLoan",
    "CapitalEvent", "CapitalEventType", "InvestorClass", "PartnershipConfig",
    "TierType", "WaterfallTier",
    "CommercialSpace", "LeaseTerm",
    "AttorneyFeeAssumption", "TIFConfiguration", "TIFMechanics", "TIFScenarioName",
    "TMVTrajectory",
    "EquityLedger", "EquityTransaction", "FiscalPeriod", "FiscalPeriodType",
    "HistoricalActuals", "HistoricalLineCategory", "HistoricalLineItem",
    "HistoricalPeriod",
    "ResidualAssumptions", "Scenario", "ScenarioMeta",
]
