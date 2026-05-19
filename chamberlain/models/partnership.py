"""Partnership and waterfall configuration.

The waterfall engine is configurable: any deal can be expressed as a list
of WaterfallTiers evaluated in order. Chamberlain's actual terms are
encoded as the first instance:

  Tier 1: KA 100% escrow recapture (until escrow returned)
  Tier 2: Both KA and IDP earn 6.5% pref compounding monthly on
          contributed capital (pro-rata to unreturned pref)
  Tier 3: Pari-passu 75% KA / 25% IDP on remaining

Tier 4+ would handle promote (catch-up + GP carry above hurdles) for
deals with promote — Chamberlain has none.

Other deals can have:
  - Multiple investor classes (LP1, LP2, GP, etc.)
  - Different pref rates per class
  - GP catch-up provisions (e.g., 50/50 catch-up to 20% IRR, then 80/20)
  - American vs. European waterfall (deal-by-deal vs. fund-level)
  - Lookback / true-up provisions

The engine consumes a list of TierSpec; each spec carries enough info
for the evaluator to compute distributions for that tier.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .citation import Cited


# --------------------------------------------------------------------------
# Investor Class
# --------------------------------------------------------------------------


class InvestorClass(BaseModel):
    """A class of investor in the partnership.

    Chamberlain has two: KA Member and IDP Member.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="stable id, e.g. 'KA' or 'IDP'")
    name: Cited[str]
    role: str = Field(description="MANAGING_MEMBER | LP | GP | CO_INVESTOR")

    # Ownership percentages
    membership_interest_pct: Cited[float] = Field(
        description="ownership %; sum across classes should = 1.0",
    )
    membership_adjusted_distribution_pct: Cited[float] = Field(
        description="% used in Tier-2+ distribution allocation; can differ from ownership",
    )

    # Capital posture
    capital_contribution_obligation: Optional[Cited[float]] = Field(
        default=None,
        description="committed capital amount (some classes have caps)",
    )

    # Pref return
    pref_return_rate: Optional[Cited[float]] = Field(
        default=None,
        description="annual pref rate, decimal, applied to contributed capital",
    )
    pref_compounding: str = Field(
        default="monthly",
        description="monthly | quarterly | annually | none",
    )


# --------------------------------------------------------------------------
# Waterfall Tiers
# --------------------------------------------------------------------------


class TierType(str, Enum):
    """What kind of distribution rule a tier represents."""

    ESCROW_RECAPTURE = "escrow_recapture"       # 100% to a designated class until amount returned
    RETURN_OF_CAPITAL = "return_of_capital"     # pro-rata return of contributed capital
    PREFERRED_RETURN = "preferred_return"       # accrued pref to specified classes
    CATCH_UP = "catch_up"                        # GP catch-up (e.g., 50/50 until parity)
    PARI_PASSU = "pari_passu"                   # split per fixed allocation
    PROMOTE = "promote"                          # GP carry above hurdle (e.g., 20/80 above 8%)


class WaterfallTier(BaseModel):
    """A single tier in the distribution waterfall.

    Evaluated in order (tier_order ascending). Each tier consumes cash
    from the distribution pool until its trigger is satisfied, then the
    next tier takes the remainder.
    """

    model_config = ConfigDict(extra="forbid")

    tier_order: int
    tier_type: TierType
    name: str = Field(description="human-readable label, e.g. 'KA Escrow Recapture'")

    # Citation to the contractual provision this tier encodes
    governing_provision_id: Optional[str] = Field(
        default=None,
        description="id of the GoverningProvision this tier encodes (LLC §5.2(a), etc.)",
    )

    # Which classes participate, and in what proportion
    allocation: dict[str, Cited[float]] = Field(
        default_factory=dict,
        description="investor_class_id -> share of this tier's cash (must sum to 1.0)",
    )

    # Tier-specific parameters
    cap_amount: Optional[Cited[float]] = Field(
        default=None,
        description="$ cap on tier (e.g. escrow recapture amount); None = uncapped",
    )
    hurdle_rate: Optional[Cited[float]] = Field(
        default=None,
        description="for PROMOTE/CATCH_UP tiers: the IRR threshold",
    )
    pref_classes: list[str] = Field(
        default_factory=list,
        description="for PREFERRED_RETURN tier: which class IDs accrue pref here",
    )
    catch_up_target_pct: Optional[Cited[float]] = Field(
        default=None,
        description="for CATCH_UP tier: target % distribution that catch-up restores",
    )

    note: Optional[str] = None


# --------------------------------------------------------------------------
# Partnership Configuration
# --------------------------------------------------------------------------


class PartnershipConfig(BaseModel):
    """Full partnership configuration: classes + waterfall tiers."""

    model_config = ConfigDict(extra="forbid")

    entity_name: Cited[str]
    formation_date: Cited[date]
    governing_state: Cited[str]
    investor_classes: list[InvestorClass]
    waterfall: list[WaterfallTier]

    # American (deal-by-deal) vs European (fund-level)
    waterfall_basis: str = Field(
        default="american",
        description="american | european",
    )

    def class_by_id(self, class_id: str) -> Optional[InvestorClass]:
        for c in self.investor_classes:
            if c.id == class_id:
                return c
        return None


# --------------------------------------------------------------------------
# Capital Events (historical and projected)
# --------------------------------------------------------------------------


class CapitalEventType(str, Enum):
    CONTRIBUTION = "contribution"
    DISTRIBUTION = "distribution"
    PROJECT_LOAN_ADVANCE = "project_loan_advance"
    PROJECT_LOAN_REPAYMENT = "project_loan_repayment"
    ESCROW_RETURN = "escrow_return"
    FEE_CONTRIBUTION = "fee_contribution"   # e.g. Developer Fee contributed back
    REFI_DISTRIBUTION = "refi_distribution"
    OPERATING_DISTRIBUTION = "operating_distribution"
    TIF_DISTRIBUTION = "tif_distribution"


class CapitalEvent(BaseModel):
    """A single capital event for one investor class.

    Used for historical equity ledger reconstruction and for engine output
    of projected events.
    """

    model_config = ConfigDict(extra="forbid")

    event_date: Cited[date]
    investor_class_id: str
    event_type: CapitalEventType
    amount: Cited[float] = Field(description="positive in/out; sign convention: + = inflow to investor")

    description: Optional[str] = None
    governing_provision_id: Optional[str] = Field(
        default=None,
        description="which waterfall tier or contractual provision triggered this",
    )


__all__ = [
    "InvestorClass",
    "TierType",
    "WaterfallTier",
    "PartnershipConfig",
    "CapitalEventType",
    "CapitalEvent",
]
