"""Commercial leasing.

Not used by Chamberlain (no commercial component) but built into the engine
for portfolio reuse. Mirrors the Excel ASSUMPTIONS!D394:N566 structure but
generalized.

Per-space modeling:
  - Initial lease term: start month, end month, starting rent, annual
    escalators (or per-CPI), TI/LC at lease start, abatement period
  - Renewal / 2nd term: same fields, plus downtime months between
    leases (during which rent is zero and no recovery)
  - Multiple terms supported (the Excel only had 2)
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .citation import Cited


class LeaseTerm(BaseModel):
    """A single lease term (initial or renewal) for a commercial space.

    All months are proforma-month ordinals (1 = first month of proforma).
    rent_psf is annual; the engine spreads to monthly.
    """

    model_config = ConfigDict(extra="forbid")

    term_index: int = Field(description="1 = initial, 2 = first renewal, etc.")
    start_month: int = Field(ge=1)
    end_month: int = Field(ge=1)

    rent_psf_annual: Cited[float]
    rent_increase_pct_per_year: Cited[float] = Field(description="annual % increase")
    rent_increase_first_month: Optional[int] = Field(
        default=None,
        description="month at which first escalation applies; defaults to month 13",
    )

    abatement_months: int = Field(default=0, ge=0, description="free rent months at start")
    leasing_capital_psf: Cited[float] = Field(description="TI/LC $/RSF, paid at lease start")

    # Re-leasing assumption: months between this term ending and next term starting
    downtime_months_after: int = Field(default=0, ge=0)


class CommercialSpace(BaseModel):
    """A single commercial space with one or more leases over the hold."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="e.g. 'Retail 1' or 'Anchor Tenant'")
    rentable_sf: Cited[int]

    terms: list[LeaseTerm] = Field(default_factory=list)


__all__ = ["LeaseTerm", "CommercialSpace"]
