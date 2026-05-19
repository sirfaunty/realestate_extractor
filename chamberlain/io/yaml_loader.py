"""YAML scenario loader.

Reads a chamberlain_base.yaml (and optional overlay) and produces a fully
constructed Scenario object with all citations resolved against the
SourceDocumentRegistry.

The YAML uses a consistent `{value, source, locator, verbatim, note}` shape
for any Cited[T] field. The loader unwraps this into Citation + Cited[T]
instances and verifies every source_document_id exists in the registry.

Scenario overlays:
  An overlay YAML can selectively override values in the base config.
  Overlays use dotted path notation (e.g., "tif.active_scenario: maa_floor")
  to target specific fields. Useful for "Inc Improvements" vs "No
  Improvements" without duplicating 600 lines of base config.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from ..models import (
    AcquisitionLoan,
    AttorneyFeeAssumption,
    CapExLine,
    CapExSchedule,
    CapitalFundingLoan,
    Citation,
    Cited,
    DebtStack,
    GoverningProvision,
    IncomeOffsetAssumptions,
    InflationCategory,
    InflationSchedule,
    InvestorClass,
    LoanType,
    Locator,
    NonOperatingAssumptions,
    OpExBasis,
    OperatingExpenseAssumptions,
    OperatingExpenseLine,
    OtherIncomeAssumptions,
    OtherIncomeBasis,
    OtherIncomeLine,
    OtherIncomeMethod,
    PartnershipConfig,
    PropertyInfo,
    ProvisionType,
    RefinanceLoan,
    ResidualAssumptions,
    Scenario,
    ScenarioMeta,
    SourceDocumentRegistry,
    TIFConfiguration,
    TIFMechanics,
    TIFScenarioName,
    TierType,
    TMVTrajectory,
    UnitRoster,
    UnitType,
    WaterfallTier,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Helpers: cited value unwrappers
# --------------------------------------------------------------------------


def _is_cited_dict(d: Any) -> bool:
    """Check whether a dict is a Cited[T] shape (`{value, source, ...}`)."""
    return isinstance(d, dict) and "value" in d and "source" in d


def _make_locator(loc_data: Optional[dict[str, Any]]) -> Locator:
    if not loc_data:
        return Locator()
    return Locator(**{k: v for k, v in loc_data.items() if v is not None})


def _make_cited(
    data: dict[str, Any],
    registry: SourceDocumentRegistry,
    context: str = "",
) -> Cited:
    """Construct a Cited[T] from a `{value, source, locator, verbatim, note}` dict."""
    if not _is_cited_dict(data):
        raise ValueError(f"Expected cited dict at {context}, got: {data!r}")

    source_id = data["source"]
    registry.require(source_id)  # raises KeyError if not registered

    locator = _make_locator(data.get("locator"))
    citation = Citation(
        source_document_id=source_id,
        locator=locator,
        verbatim_text=data.get("verbatim"),
        confidence=data.get("confidence", 1.0),
    )

    value = data["value"]
    # YAML loads dates as datetime.date already
    return Cited(
        value=value,
        citations=[citation],
        note=data.get("note"),
    )


def _maybe_cited(
    data: Any,
    registry: SourceDocumentRegistry,
    context: str = "",
) -> Optional[Cited]:
    """Return a Cited[T] if the data is a cited-shape dict, else None."""
    if data is None:
        return None
    if _is_cited_dict(data):
        return _make_cited(data, registry, context)
    return None


def _year_indexed_cited_map(
    data: Optional[dict[int, dict[str, Any]]],
    registry: SourceDocumentRegistry,
    context: str = "",
) -> dict[int, Cited[float]]:
    """Convert a {year: cited_dict} mapping into {year: Cited[float]}."""
    out: dict[int, Cited[float]] = {}
    if not data:
        return out
    for k, v in data.items():
        out[int(k)] = _make_cited(v, registry, f"{context}[{k}]")
    return out


# --------------------------------------------------------------------------
# Section loaders
# --------------------------------------------------------------------------


def _load_meta(meta: dict[str, Any]) -> ScenarioMeta:
    return ScenarioMeta(
        scenario_id=meta["scenario_id"],
        name=meta["name"],
        description=meta.get("description"),
        proforma_start_date=meta["proforma_start_date"]
            if isinstance(meta["proforma_start_date"], date)
            else date.fromisoformat(meta["proforma_start_date"]),
        hold_years=meta.get("hold_years", 10),
        sale_year=meta.get("sale_year", 10),
    )


def _load_property(data: dict[str, Any], reg: SourceDocumentRegistry) -> PropertyInfo:
    """Build PropertyInfo from the YAML 'property' block."""

    # All fields in PropertyInfo accept Cited[T]; some are optional.
    optional_keys = {
        "renovated_year", "delivery_date", "submarket",
        "surface_spaces", "covered_spaces", "structured_spaces",
        "construction_type", "roof_type", "laundry", "water_metering",
        "borrower_entity",
    }
    required_keys = {
        "name", "address", "city", "state", "zip_code", "market",
        "year_built", "total_units", "total_buildings", "rentable_sf",
        "stories", "land_acres",
    }

    kwargs: dict[str, Any] = {}
    for key in required_keys:
        if key not in data:
            raise ValueError(f"Property field missing in YAML: {key}")
        kwargs[key] = _make_cited(data[key], reg, f"property.{key}")
    for key in optional_keys:
        if key in data and data[key] is not None:
            kwargs[key] = _make_cited(data[key], reg, f"property.{key}")

    return PropertyInfo(**kwargs)


def _load_unit_roster(data: dict[str, Any], reg: SourceDocumentRegistry) -> UnitRoster:
    """Build UnitRoster from the YAML 'unit_roster' block."""
    units: list[UnitType] = []
    for i, u in enumerate(data.get("units", [])):
        units.append(UnitType(
            name=u["name"],
            bedroom_category=u["bedroom_category"],
            affordability=u.get("affordability", "market"),
            unit_count=_make_cited(u["unit_count"], reg, f"unit_roster[{i}].unit_count"),
            unit_sf=_make_cited(u["unit_sf"], reg, f"unit_roster[{i}].unit_sf"),
            in_place_face_rent=_make_cited(u["in_place_face_rent"], reg,
                                           f"unit_roster[{i}].in_place_face_rent"),
            in_place_ner=_make_cited(u["in_place_ner"], reg, f"unit_roster[{i}].in_place_ner"),
            proforma_year1_rent=_make_cited(u["proforma_year1_rent"], reg,
                                            f"unit_roster[{i}].proforma_year1_rent"),
        ))

    as_of = data.get("as_of_date")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    return UnitRoster(units=units, as_of_date=as_of)


def _load_inflation(
    data: dict[str, Any],
    category: InflationCategory,
    reg: SourceDocumentRegistry,
) -> InflationSchedule:
    return InflationSchedule(
        category=category,
        rates_by_year=_year_indexed_cited_map(data.get("rates_by_year"), reg, f"{category}.rates_by_year"),
        terminal_rate=_make_cited(data["terminal_rate"], reg, f"{category}.terminal_rate"),
    )


def _load_income_offsets(data: dict[str, Any], reg: SourceDocumentRegistry) -> IncomeOffsetAssumptions:
    return IncomeOffsetAssumptions(
        loss_to_lease_by_year=_year_indexed_cited_map(
            data.get("loss_to_lease_by_year"), reg, "income_offsets.loss_to_lease_by_year",
        ),
        loss_to_lease_terminal=_make_cited(data["loss_to_lease_terminal"], reg,
                                           "income_offsets.loss_to_lease_terminal"),
        concessions_by_year=_year_indexed_cited_map(
            data.get("concessions_by_year"), reg, "income_offsets.concessions_by_year",
        ),
        concessions_terminal=_make_cited(data["concessions_terminal"], reg,
                                          "income_offsets.concessions_terminal"),
        bad_debt_by_year=_year_indexed_cited_map(
            data.get("bad_debt_by_year"), reg, "income_offsets.bad_debt_by_year",
        ),
        bad_debt_terminal=_make_cited(data["bad_debt_terminal"], reg,
                                       "income_offsets.bad_debt_terminal"),
        stabilized_vacancy=_make_cited(data["stabilized_vacancy"], reg,
                                        "income_offsets.stabilized_vacancy"),
        vacancy_stabilized_year=data.get("vacancy_stabilized_year", 1),
    )


def _load_opex(data: dict[str, Any], reg: SourceDocumentRegistry) -> OperatingExpenseAssumptions:
    method = data.get("method", "DETAIL")
    lines: list[OperatingExpenseLine] = []
    for i, ln in enumerate(data.get("lines", [])):
        lines.append(OperatingExpenseLine(
            name=ln["name"],
            line_id=ln["line_id"],
            basis=OpExBasis(ln.get("basis", "total_dollars")),
            year1_amount=_make_cited(ln["year1_amount"], reg, f"opex.lines[{i}].year1_amount"),
            inflation_override_by_year=_year_indexed_cited_map(
                ln.get("inflation_override_by_year"), reg, f"opex.lines[{i}].inflation",
            ),
        ))
    plug_pupy = _maybe_cited(data.get("plug_pupy"), reg, "opex.plug_pupy")
    return OperatingExpenseAssumptions(method=method, lines=lines, plug_pupy=plug_pupy)


def _load_other_income(data: dict[str, Any], reg: SourceDocumentRegistry) -> OtherIncomeAssumptions:
    method = OtherIncomeMethod(data.get("method", "DETAIL"))
    lines: list[OtherIncomeLine] = []
    for i, ln in enumerate(data.get("lines", [])):
        lines.append(OtherIncomeLine(
            name=ln["name"],
            line_id=ln["line_id"],
            basis=OtherIncomeBasis(ln.get("basis", "total_dollars")),
            year1_amount=_make_cited(ln["year1_amount"], reg, f"other_income.lines[{i}].year1_amount"),
            rate_by_year=_year_indexed_cited_map(
                ln.get("rate_by_year"), reg, f"other_income.lines[{i}].rate_by_year",
            ),
            occupancy_by_year=_year_indexed_cited_map(
                ln.get("occupancy_by_year"), reg, f"other_income.lines[{i}].occupancy_by_year",
            ),
            units_count=_maybe_cited(ln.get("units_count"), reg,
                                      f"other_income.lines[{i}].units_count"),
            inflation_override_by_year=_year_indexed_cited_map(
                ln.get("inflation_override_by_year"), reg,
                f"other_income.lines[{i}].inflation",
            ),
        ))
    plug_pupy = _maybe_cited(data.get("plug_year1_pupy"), reg, "other_income.plug_year1_pupy")
    plug_y2 = _maybe_cited(data.get("plug_y2_inflation"), reg, "other_income.plug_y2_inflation")
    return OtherIncomeAssumptions(
        method=method, lines=lines,
        plug_year1_pupy=plug_pupy, plug_y2_inflation=plug_y2,
    )


def _load_capex(data: dict[str, Any], reg: SourceDocumentRegistry) -> CapExSchedule:
    lines: list[CapExLine] = []
    for i, ln in enumerate(data.get("lines", [])):
        lines.append(CapExLine(
            name=ln["name"],
            line_id=ln["line_id"],
            category=ln.get("category", "improvement"),
            amount_by_year=_year_indexed_cited_map(
                ln.get("amount_by_year"), reg, f"capex.lines[{i}].amount_by_year",
            ),
        ))
    return CapExSchedule(lines=lines, funding_type=data.get("funding_type", "equity_first"))


def _load_non_operating(data: dict[str, Any], reg: SourceDocumentRegistry) -> NonOperatingAssumptions:
    return NonOperatingAssumptions(
        property_mgmt_fee_pct_egr=_make_cited(data["property_mgmt_fee_pct_egr"], reg,
                                               "non_operating.property_mgmt_fee_pct_egr"),
        property_mgmt_fee_includes_commercial=data.get("property_mgmt_fee_includes_commercial", True),
        asset_mgmt_fee_pct_egr=_make_cited(data["asset_mgmt_fee_pct_egr"], reg,
                                            "non_operating.asset_mgmt_fee_pct_egr"),
        asset_mgmt_fee_includes_commercial=data.get("asset_mgmt_fee_includes_commercial", True),
        replacement_reserves_pupy=_make_cited(data["replacement_reserves_pupy"], reg,
                                               "non_operating.replacement_reserves_pupy"),
        professional_expenses_annual=_make_cited(data["professional_expenses_annual"], reg,
                                                  "non_operating.professional_expenses_annual"),
        mip_pct_upb=_maybe_cited(data.get("mip_pct_upb"), reg, "non_operating.mip_pct_upb"),
        surplus_cash_note_annual=_maybe_cited(data.get("surplus_cash_note_annual"), reg,
                                               "non_operating.surplus_cash_note_annual"),
    )


def _load_debt(data: dict[str, Any], reg: SourceDocumentRegistry) -> DebtStack:
    acq_data = data["acquisition_loan"]

    def _maybe_iso_date(v: Any) -> Optional[date]:
        if v is None:
            return None
        if isinstance(v, date):
            return v
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    # For Cited date fields where the YAML value is already a date object
    acq = AcquisitionLoan(
        lender=_make_cited(acq_data["lender"], reg, "debt.acquisition.lender"),
        loan_type=LoanType(acq_data["loan_type"]),
        original_principal=_make_cited(acq_data["original_principal"], reg,
                                        "debt.acquisition.original_principal"),
        rate=_make_cited(acq_data["rate"], reg, "debt.acquisition.rate"),
        term_months=_make_cited(acq_data["term_months"], reg, "debt.acquisition.term_months"),
        amortization_months=_make_cited(acq_data["amortization_months"], reg,
                                         "debt.acquisition.amortization_months"),
        io_period_months=_make_cited(acq_data["io_period_months"], reg,
                                      "debt.acquisition.io_period_months"),
        first_payment_date=_make_cited(acq_data["first_payment_date"], reg,
                                        "debt.acquisition.first_payment_date"),
        maturity_date=_make_cited(acq_data["maturity_date"], reg, "debt.acquisition.maturity_date"),
        proforma_start_balance=_maybe_cited(acq_data.get("proforma_start_balance"), reg,
                                             "debt.acquisition.proforma_start_balance"),
        proforma_start_balance_date=_maybe_iso_date(acq_data.get("proforma_start_balance_date")),
        finance_fee=_maybe_cited(acq_data.get("finance_fee"), reg, "debt.acquisition.finance_fee"),
        monthly_payment_override=_maybe_cited(acq_data.get("monthly_payment_override"), reg,
                                               "debt.acquisition.monthly_payment_override"),
    )

    cap_data = data.get("capital_funding_loan")
    cap = None
    if cap_data:
        cap = CapitalFundingLoan(
            max_principal=_make_cited(cap_data["max_principal"], reg,
                                       "debt.capital_funding.max_principal"),
            rate=_make_cited(cap_data["rate"], reg, "debt.capital_funding.rate"),
            funding_priority=cap_data.get("funding_priority", "equity_first"),
            fully_funded_balance=_maybe_cited(cap_data.get("fully_funded_balance"), reg,
                                               "debt.capital_funding.fully_funded_balance"),
        )

    refi_data = data.get("refinance", {})
    refi = RefinanceLoan(
        enabled=refi_data.get("enabled", False),
        refi_year=_maybe_cited(refi_data.get("refi_year"), reg, "debt.refinance.refi_year"),
        proceeds=_maybe_cited(refi_data.get("proceeds"), reg, "debt.refinance.proceeds"),
        existing_loan_payoff=_maybe_cited(refi_data.get("existing_loan_payoff"), reg,
                                           "debt.refinance.existing_loan_payoff"),
        new_principal=_maybe_cited(refi_data.get("new_principal"), reg, "debt.refinance.new_principal"),
        new_rate=_maybe_cited(refi_data.get("new_rate"), reg, "debt.refinance.new_rate"),
        new_term_months=_maybe_cited(refi_data.get("new_term_months"), reg,
                                      "debt.refinance.new_term_months"),
        new_amortization_months=_maybe_cited(refi_data.get("new_amortization_months"), reg,
                                              "debt.refinance.new_amortization_months"),
        new_io_period_months=_maybe_cited(refi_data.get("new_io_period_months"), reg,
                                           "debt.refinance.new_io_period_months"),
        finance_fee=_maybe_cited(refi_data.get("finance_fee"), reg, "debt.refinance.finance_fee"),
    )

    return DebtStack(acquisition_loan=acq, capital_funding_loan=cap, refinance=refi)


def _load_partnership(data: dict[str, Any], reg: SourceDocumentRegistry) -> PartnershipConfig:
    classes: list[InvestorClass] = []
    for i, c in enumerate(data["investor_classes"]):
        classes.append(InvestorClass(
            id=c["id"],
            name=_make_cited(c["name"], reg, f"partnership.investor_classes[{i}].name"),
            role=c["role"],
            membership_interest_pct=_make_cited(c["membership_interest_pct"], reg,
                                                 f"partnership.investor_classes[{i}].membership_interest_pct"),
            membership_adjusted_distribution_pct=_make_cited(c["membership_adjusted_distribution_pct"], reg,
                                                              f"partnership.investor_classes[{i}].membership_adjusted_distribution_pct"),
            capital_contribution_obligation=_maybe_cited(c.get("capital_contribution_obligation"),
                                                          reg, f"partnership.investor_classes[{i}].capital_contribution_obligation"),
            pref_return_rate=_maybe_cited(c.get("pref_return_rate"), reg,
                                           f"partnership.investor_classes[{i}].pref_return_rate"),
            pref_compounding=c.get("pref_compounding", "monthly"),
        ))

    tiers: list[WaterfallTier] = []
    for i, t in enumerate(data["waterfall"]):
        allocation: dict[str, Cited[float]] = {}
        for cls_id, alloc_data in t.get("allocation", {}).items():
            allocation[cls_id] = _make_cited(alloc_data, reg,
                                              f"partnership.waterfall[{i}].allocation.{cls_id}")
        tiers.append(WaterfallTier(
            tier_order=t["tier_order"],
            tier_type=TierType(t["tier_type"]),
            name=t["name"],
            governing_provision_id=t.get("governing_provision_id"),
            allocation=allocation,
            cap_amount=_maybe_cited(t.get("cap_amount"), reg,
                                     f"partnership.waterfall[{i}].cap_amount"),
            hurdle_rate=_maybe_cited(t.get("hurdle_rate"), reg,
                                      f"partnership.waterfall[{i}].hurdle_rate"),
            pref_classes=t.get("pref_classes", []),
            catch_up_target_pct=_maybe_cited(t.get("catch_up_target_pct"), reg,
                                              f"partnership.waterfall[{i}].catch_up_target_pct"),
            note=t.get("note"),
        ))

    return PartnershipConfig(
        entity_name=_make_cited(data["entity_name"], reg, "partnership.entity_name"),
        formation_date=_make_cited(data["formation_date"], reg, "partnership.formation_date"),
        governing_state=_make_cited(data["governing_state"], reg, "partnership.governing_state"),
        investor_classes=classes,
        waterfall=tiers,
        waterfall_basis=data.get("waterfall_basis", "american"),
    )


def _load_tif(data: dict[str, Any], reg: SourceDocumentRegistry) -> TIFConfiguration:
    m = data["mechanics"]
    mechanics = TIFMechanics(
        class_rate=_make_cited(m["class_rate"], reg, "tif.mechanics.class_rate"),
        tax_capacity_rate=_make_cited(m["tax_capacity_rate"], reg, "tif.mechanics.tax_capacity_rate"),
        developer_share=_make_cited(m["developer_share"], reg, "tif.mechanics.developer_share"),
        admin_pct=_make_cited(m["admin_pct"], reg, "tif.mechanics.admin_pct"),
        osa_pct=_make_cited(m["osa_pct"], reg, "tif.mechanics.osa_pct"),
        base_ntc=_make_cited(m["base_ntc"], reg, "tif.mechanics.base_ntc"),
        note_original_principal=_make_cited(m["note_original_principal"], reg,
                                             "tif.mechanics.note_original_principal"),
        note_interest_rate=_make_cited(m["note_interest_rate"], reg,
                                        "tif.mechanics.note_interest_rate"),
        note_maturity_date=_make_cited(m["note_maturity_date"], reg,
                                        "tif.mechanics.note_maturity_date"),
        note_beginning_balance=_make_cited(m["note_beginning_balance"], reg,
                                            "tif.mechanics.note_beginning_balance"),
        maa_floor=_make_cited(m["maa_floor"], reg, "tif.mechanics.maa_floor"),
        maa_effective_from=_make_cited(m["maa_effective_from"], reg,
                                        "tif.mechanics.maa_effective_from"),
        maa_effective_through=m.get("maa_effective_through", "TIF_TERMINATION"),
        tif_district_last_increment_year=m.get("tif_district_last_increment_year", 2045),
    )

    scenarios: dict[TIFScenarioName, TMVTrajectory] = {}
    for name, s_data in data["scenarios"].items():
        scenario_name = TIFScenarioName(name)
        scenarios[scenario_name] = TMVTrajectory(
            scenario=scenario_name,
            description=s_data.get("description", ""),
            tmv_by_year=_year_indexed_cited_map(s_data.get("tmv_by_year"), reg,
                                                 f"tif.scenarios.{name}.tmv_by_year"),
            appeal_years=s_data.get("appeal_years", []),
            growth_rate_assumption=_maybe_cited(s_data.get("growth_rate_assumption"), reg,
                                                  f"tif.scenarios.{name}.growth_rate_assumption"),
        )

    af = data["attorney_fees"]
    attorney_fees = AttorneyFeeAssumption(
        fee_pct_of_year1_savings=_make_cited(af["fee_pct_of_year1_savings"], reg,
                                              "tif.attorney_fees.fee_pct_of_year1_savings"),
        recurring=af.get("recurring", False),
    )

    return TIFConfiguration(
        mechanics=mechanics,
        scenarios=scenarios,
        attorney_fees=attorney_fees,
        discount_rate=_make_cited(data["discount_rate"], reg, "tif.discount_rate"),
    )


def _load_governing_provisions(data: list[dict[str, Any]], reg: SourceDocumentRegistry) -> list[GoverningProvision]:
    provisions: list[GoverningProvision] = []
    for i, p in enumerate(data or []):
        citations: list[Citation] = []
        for ci, c in enumerate(p.get("citations", [])):
            reg.require(c["source"])
            citations.append(Citation(
                source_document_id=c["source"],
                locator=_make_locator(c.get("locator")),
                verbatim_text=c.get("verbatim"),
                confidence=c.get("confidence", 1.0),
            ))
        if not citations:
            raise ValueError(f"governing_provisions[{i}] requires at least one citation")
        provisions.append(GoverningProvision(
            id=p["id"],
            provision_type=ProvisionType(p["provision_type"]),
            title=p["title"],
            description=p.get("description"),
            citations=citations,
            verbatim_text=p.get("verbatim_text"),
            structured_logic=p.get("structured_logic", {}),
            defined_terms=p.get("defined_terms", []),
            supersedes=p.get("supersedes"),
            references=p.get("references", []),
        ))
    return provisions


# --------------------------------------------------------------------------
# Overlay (selective override of base config)
# --------------------------------------------------------------------------


def _apply_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge overlay into base; overlay values win."""
    if not isinstance(overlay, dict):
        return overlay
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _apply_overlay(out[k], v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def load_scenario_from_yaml(
    base_yaml_path: Path,
    overlay_yaml_path: Optional[Path] = None,
    registry: Optional[SourceDocumentRegistry] = None,
) -> Scenario:
    """Load a Scenario from YAML, optionally applying an overlay.

    Args:
        base_yaml_path: path to chamberlain_base.yaml (or equivalent)
        overlay_yaml_path: optional overlay file to apply on top
        registry: optional pre-built SourceDocumentRegistry; if None, the
            default Chamberlain registry is built via registry_builder

    Returns:
        Fully constructed Scenario with all citations verified.
    """
    if registry is None:
        from ..historical.registry_builder import build_registry
        registry = build_registry()

    with open(base_yaml_path) as f:
        base = yaml.safe_load(f)

    if overlay_yaml_path:
        with open(overlay_yaml_path) as f:
            overlay = yaml.safe_load(f)
        merged = _apply_overlay(base, overlay)
    else:
        merged = base

    # Active TIF scenario
    active_tif_str = merged.get("tif", {}).get("active_scenario", "baseline")

    return Scenario(
        meta=_load_meta(merged["meta"]),
        source_registry=registry,
        property=_load_property(merged["property"], registry),
        unit_roster=_load_unit_roster(merged["unit_roster"], registry),
        commercial_spaces=[],  # not used by Chamberlain
        base_rent_inflation=_load_inflation(merged["base_rent_inflation"],
                                             InflationCategory.BASE_RENT, registry),
        opex_inflation=_load_inflation(merged["opex_inflation"],
                                        InflationCategory.OPERATING_EXPENSE, registry),
        other_income_inflation=_load_inflation(merged["other_income_inflation"],
                                                 InflationCategory.OTHER_INCOME, registry),
        income_offsets=_load_income_offsets(merged["income_offsets"], registry),
        opex=_load_opex(merged["opex"], registry),
        other_income=_load_other_income(merged["other_income"], registry),
        non_operating=_load_non_operating(merged["non_operating"], registry),
        capex=_load_capex(merged["capex"], registry),
        debt=_load_debt(merged["debt"], registry),
        partnership=_load_partnership(merged["partnership"], registry),
        tif=_load_tif(merged["tif"], registry),
        active_tif_scenario=TIFScenarioName(active_tif_str),
        residual=ResidualAssumptions(
            sale_year=_make_cited(merged["residual"]["sale_year"], registry, "residual.sale_year"),
            residual_cap_rate=_make_cited(merged["residual"]["residual_cap_rate"], registry,
                                            "residual.residual_cap_rate"),
            cost_of_sale_pct=_make_cited(merged["residual"]["cost_of_sale_pct"], registry,
                                           "residual.cost_of_sale_pct"),
        ),
        acquisition_cost_basis=_make_cited(merged["acquisition_cost_basis"], registry,
                                             "acquisition_cost_basis"),
        governing_provisions=_load_governing_provisions(merged.get("governing_provisions", []), registry),
    )


__all__ = ["load_scenario_from_yaml"]
