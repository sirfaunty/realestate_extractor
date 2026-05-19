"""Property-level information and unit roster.

The unit roster drives the entire revenue build: each unit type has a count,
square footage, in-place face rent, in-place NER, and a proforma Year 1
market rent that grows under the rent inflation schedule.

Every input field is wrapped in Cited[T] so the property facts and unit
counts trace back to source documents (the LLC Agreement and Property
Overview workbooks for property facts; the rent roll for unit-level data).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .citation import Cited


class PropertyInfo(BaseModel):
    """Static property facts.

    Every numeric or text field with material consequence is cited.
    Sourced primarily from the Property Overview Summary 11.7.25
    'Property Summary' tab.
    """

    model_config = ConfigDict(extra="forbid")

    name: Cited[str]
    address: Cited[str]
    city: Cited[str]
    state: Cited[str]
    zip_code: Cited[str]
    market: Cited[str] = Field(description="Costar market")
    submarket: Optional[Cited[str]] = None

    year_built: Cited[int]
    renovated_year: Optional[Cited[int]] = None
    delivery_date: Optional[Cited[date]] = Field(
        default=None,
        description="Date property delivered / placed in service",
    )

    total_units: Cited[int]
    total_buildings: Cited[int]
    rentable_sf: Cited[int]
    stories: Cited[int]
    land_acres: Cited[float]

    # Parking
    surface_spaces: Optional[Cited[int]] = None
    covered_spaces: Optional[Cited[int]] = None
    structured_spaces: Optional[Cited[int]] = None

    # Construction
    construction_type: Optional[Cited[str]] = None
    roof_type: Optional[Cited[str]] = None
    laundry: Optional[Cited[str]] = None
    water_metering: Optional[Cited[str]] = None

    # Legal entity context
    borrower_entity: Optional[Cited[str]] = Field(
        default=None,
        description="legal entity that owns the property",
    )

    @property
    def total_parking_spaces(self) -> int:
        sfc = self.surface_spaces.value if self.surface_spaces else 0
        cov = self.covered_spaces.value if self.covered_spaces else 0
        st = self.structured_spaces.value if self.structured_spaces else 0
        return sfc + cov + st

    @property
    def avg_unit_sf(self) -> float:
        units = self.total_units.value
        return self.rentable_sf.value / units if units else 0.0


# --------------------------------------------------------------------------
# Unit Type & Roster
# --------------------------------------------------------------------------


class UnitType(BaseModel):
    """A single floorplan / unit type within the roster."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="e.g. 'Market Rate 1 Bedroom'")
    bedroom_category: str = Field(
        description="Studio | Alcove | 1 Bedroom | 2 Bedroom | 3 Bedroom",
    )
    affordability: str = Field(
        default="market",
        description="market | affordable_lihtc | affordable_vintage | new_affordable",
    )

    unit_count: Cited[int]
    unit_sf: Cited[int]
    in_place_face_rent: Cited[float] = Field(description="$/unit/month before concessions")
    in_place_ner: Cited[float] = Field(description="$/unit/month after concessions (NER)")
    proforma_year1_rent: Cited[float] = Field(description="$/unit/month Year-1 market target")

    @property
    def in_place_face_rent_psf(self) -> float:
        sf = self.unit_sf.value
        return self.in_place_face_rent.value / sf if sf else 0.0

    @property
    def total_monthly_face_rent(self) -> float:
        return self.unit_count.value * self.in_place_face_rent.value

    @property
    def total_annual_face_rent(self) -> float:
        return self.total_monthly_face_rent * 12

    @property
    def total_monthly_ner(self) -> float:
        return self.unit_count.value * self.in_place_ner.value

    @property
    def concession_pct(self) -> float:
        face = self.in_place_face_rent.value
        if not face:
            return 0.0
        return (self.in_place_ner.value - face) / face


class UnitRoster(BaseModel):
    """Collection of unit types making up the property."""

    model_config = ConfigDict(extra="forbid")

    units: list[UnitType]
    as_of_date: Optional[date] = Field(
        default=None,
        description="snapshot date of this roster",
    )

    @model_validator(mode="after")
    def _validate(self) -> "UnitRoster":
        if not self.units:
            raise ValueError("UnitRoster must contain at least one UnitType")
        seen: set[str] = set()
        for u in self.units:
            if u.name in seen:
                raise ValueError(f"Duplicate UnitType name: {u.name}")
            seen.add(u.name)
        return self

    @property
    def total_units(self) -> int:
        return sum(u.unit_count.value for u in self.units)

    @property
    def total_sf(self) -> int:
        return sum(u.unit_count.value * u.unit_sf.value for u in self.units)

    @property
    def total_monthly_face_rent(self) -> float:
        return sum(u.total_monthly_face_rent for u in self.units)

    @property
    def total_annual_face_rent(self) -> float:
        return self.total_monthly_face_rent * 12

    @property
    def avg_face_rent_per_unit(self) -> float:
        n = self.total_units
        return self.total_monthly_face_rent / n if n else 0.0

    @property
    def avg_unit_sf(self) -> float:
        n = self.total_units
        return self.total_sf / n if n else 0.0

    def by_bedroom_category(self) -> dict[str, list[UnitType]]:
        out: dict[str, list[UnitType]] = {}
        for u in self.units:
            out.setdefault(u.bedroom_category, []).append(u)
        return out


__all__ = ["PropertyInfo", "UnitType", "UnitRoster"]
