"""Governing Provisions and Reconciliations.

A GoverningProvision is a special class of Extracted Fact: a clause or term
from a governing document (LLC Agreement, Loan Agreement, MAA, TIF Plan, etc.)
that defines what *should* be true. Provisions are the "spine" against which
actual practice (Extracted Facts) is reconciled.

A Reconciliation is the output of comparing actual practice against a
Governing Provision: did the practice match what the document requires?

From DATA_MODEL_AND_ARCHITECTURE.md §6: "The reconciliation engine traverses
this graph. Build it deliberately; don't let it be an afterthought."

For Phase A, we encode the provisions and define the data shape of
Reconciliations, but the actual reconciliation engine (Phase B) is not
implemented. The forward engine consumes provisions where it needs them
(e.g., the waterfall provision drives the distribution engine).
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .citation import Citation


# --------------------------------------------------------------------------
# Provision types
# --------------------------------------------------------------------------


class ProvisionType(str, Enum):
    """Categories of governing provisions.

    Driven by what the Chamberlain corpus contains and what other lenses
    will need. Not exhaustive — adding new types is normal.
    """

    DEFINITION = "definition"
    CAPITAL_CONTRIBUTION = "capital_contribution"
    DISTRIBUTION_WATERFALL = "distribution_waterfall"
    PROFIT_LOSS_ALLOCATION = "profit_loss_allocation"
    FEE_SCHEDULE = "fee_schedule"
    COVENANT = "covenant"
    CONSENT_RIGHT = "consent_right"
    PREEMPTIVE_RIGHT = "preemptive_right"
    DEFAULT_REMEDY = "default_remedy"
    GUARANTEE = "guarantee"
    TRANSFER_RESTRICTION = "transfer_restriction"
    MAA_VALUE_FLOOR = "maa_value_floor"
    TIF_MECHANICS = "tif_mechanics"
    LOAN_TERM = "loan_term"
    LOAN_COVENANT = "loan_covenant"
    RESERVE_REQUIREMENT = "reserve_requirement"
    NOTE_PAYMENT_TERM = "note_payment_term"


# --------------------------------------------------------------------------
# GoverningProvision
# --------------------------------------------------------------------------


class GoverningProvision(BaseModel):
    """A clause/term from a governing document, encoded for reconciliation.

    Has two halves:
      1. Verbatim text + citation — the human-readable contract language
      2. structured_logic — the same provision encoded in a form the
         engine can evaluate against the fact base

    The structured_logic schema is provision-type-specific. For example:

      DISTRIBUTION_WATERFALL:
        {
          "tiers": [
            {
              "name": "Escrow Recapture",
              "trigger": "escrow_release_event",
              "allocation": {"KA": 1.0, "IDP": 0.0},
              "until": "escrow_funds_returned"
            },
            {
              "name": "Preferred Return",
              "trigger": "any_distribution",
              "rate": 0.065,
              "compound": "monthly",
              "applies_to": ["KA_contributed_capital", "IDP_contributed_capital"],
              "allocation": "pro_rata_to_unreturned_pref"
            },
            {
              "name": "Pari Passu",
              "trigger": "remaining_cash",
              "allocation": {"KA": 0.75, "IDP": 0.25}
            }
          ]
        }

      MAA_VALUE_FLOOR:
        {
          "floor_value": 43835000,
          "effective_from": "2021-01-02",
          "effective_through": "TIF_termination",
          "applies_to": "taxable_market_value"
        }

      LOAN_TERM:
        {
          "original_principal": 52967700,
          "rate": 0.0233,
          "term_months": 420,
          "amortization_months": 420,
          "io_months": 0,
          "monthly_payment": 184565.17,
          "first_payment_date": "2021-12-01",
          "maturity_date": "2056-11-01"
        }

    The engine inspects provision_type to know how to interpret structured_logic.
    Type safety on structured_logic is intentionally loose to support extension;
    use provision-type-specific helpers in the engine to validate at use time.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="stable id, e.g. 'llc_5_2_distributions'")
    provision_type: ProvisionType
    title: str = Field(description="short human name, e.g. 'LLC §5.2 — Distribution of Cash Flow'")
    description: Optional[str] = None

    # The source
    citations: list[Citation] = Field(min_length=1)
    verbatim_text: Optional[str] = Field(
        default=None,
        description="full verbatim clause text (separate from per-citation snippets)",
    )

    # The encoded logic
    structured_logic: dict[str, Any] = Field(
        default_factory=dict,
        description="provision-type-specific encoded form; engine validates per type",
    )

    # Defined terms this provision depends on
    defined_terms: list[str] = Field(
        default_factory=list,
        description="ids of Definition provisions referenced by this one",
    )

    # Temporal scope
    effective_date: Optional[date] = None
    termination_date: Optional[date] = None

    # Related provisions (graph edges)
    supersedes: Optional[str] = Field(
        default=None,
        description="id of a prior provision this one replaces (amendments)",
    )
    references: list[str] = Field(
        default_factory=list,
        description="ids of other provisions this depends on",
    )


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


class ReconciliationStatus(str, Enum):
    """Outcome of comparing actual practice to a governing provision."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    UNVERIFIABLE = "unverifiable"
    NEEDS_REVIEW = "needs_review"


class ImpactDirection(str, Enum):
    """If a divergence exists, who benefits and who is harmed."""

    NONE = "none"
    FAVORS_KA = "favors_ka"
    FAVORS_IDP = "favors_idp"
    FAVORS_LLC = "favors_llc"
    HARMS_LLC = "harms_llc"
    NEUTRAL = "neutral"
    INDETERMINATE = "indeterminate"


class Reconciliation(BaseModel):
    """The output of comparing actual practice to a Governing Provision.

    Not built in Phase A — defined here so downstream code can be written
    against the right shape. Phase B implements the engine that produces
    these.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    governing_provision_id: str = Field(description="provision being reconciled against")

    # The facts being compared
    actual_practice_fact_ids: list[str] = Field(
        default_factory=list,
        description="ids of Cited values representing observed practice",
    )

    # The result
    status: ReconciliationStatus
    direction_of_impact: ImpactDirection = ImpactDirection.NONE
    magnitude: Optional[float] = Field(default=None, description="$ magnitude of divergence")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # Narrative
    finding: str = Field(description="one-line human-readable finding")
    detail: Optional[str] = None

    # Provenance
    computed_at: Optional[date] = None
    computed_by: Optional[str] = Field(default=None, description="engine | analyst name")


__all__ = [
    "ProvisionType",
    "GoverningProvision",
    "ReconciliationStatus",
    "ImpactDirection",
    "Reconciliation",
]
