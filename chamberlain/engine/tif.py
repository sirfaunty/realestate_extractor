"""TIF (Tax Increment Financing) engine.

Implements the full 4-scenario TIF mechanics from the live TIF model.
For each year, given the scenario's TMV and the TIF mechanics, compute:

  1. Net Tax Capacity (NTC):    TMV × class_rate
  2. Property tax (LLC pays):   NTC × tax_capacity_rate
  3. Captured NTC:              NTC - base_ntc  (Original NTC subtracted)
  4. Gross TIF:                 Captured_NTC × tax_capacity_rate × developer_share
  5. Net TIF to LLC:            Gross_TIF × (1 - admin_pct) × (1 - osa_pct)
  6. TIF Note interest accrued: prev_note_balance × note_rate
  7. TIF Note principal paid:   min(Net_TIF, prev_balance + interest)
  8. Ending Note balance:       max(0, prev + interest - principal)
  9. LLC TIF income recognized: Net TIF received (cash basis)

Note payoff timing:
  When the Note balance hits zero in a year, the remaining TIF still
  flows but to the LLC directly (no longer servicing the Note).

After 2045 (TIF district last increment year), no more TIF — only the
property tax savings flow.

Attorney fees:
  Applied once per appeal_year as fee_pct × year-1 tax savings.

Output:
  TIFAnnualSeries (annual; TIF is fundamentally annual not monthly)
  + monthly_revenue: 12 even monthly payments for plumbing into the
    forward engine which works monthly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .time_grid import TimeGrid
from ..models.tif import (
    AttorneyFeeAssumption,
    TIFConfiguration,
    TIFMechanics,
    TIFScenarioName,
    TMVTrajectory,
)


@dataclass
class TIFAnnualSeries:
    """Annual TIF arrays — one entry per calendar year over the proforma."""

    calendar_years: list[int] = field(default_factory=list)
    tmv: list[float] = field(default_factory=list)
    ntc: list[float] = field(default_factory=list)
    captured_ntc: list[float] = field(default_factory=list)
    property_tax: list[float] = field(default_factory=list)
    gross_tif: list[float] = field(default_factory=list)
    net_tif: list[float] = field(default_factory=list)
    note_beginning_balance: list[float] = field(default_factory=list)
    note_interest: list[float] = field(default_factory=list)
    note_principal: list[float] = field(default_factory=list)
    note_ending_balance: list[float] = field(default_factory=list)
    llc_tif_income: list[float] = field(default_factory=list)
    attorney_fees: list[float] = field(default_factory=list)


@dataclass
class TIFMonthlySeries:
    """Monthly arrays for plumbing into the main engine."""

    tif_revenue: list[float] = field(default_factory=list)
    property_tax_expense: list[float] = field(default_factory=list)
    attorney_fees: list[float] = field(default_factory=list)


@dataclass
class TIFResult:
    """Full TIF output for a scenario."""

    annual: TIFAnnualSeries
    monthly: TIFMonthlySeries
    scenario: TIFScenarioName

    @property
    def total_nominal_tif_to_llc(self) -> float:
        return sum(self.annual.llc_tif_income)

    @property
    def note_payoff_year(self) -> Optional[int]:
        """First calendar year in which the Note balance reaches zero."""
        for y, bal in zip(self.annual.calendar_years, self.annual.note_ending_balance):
            if bal <= 0.01:
                return y
        return None


def _tax_on(tmv: float, mechanics: TIFMechanics) -> tuple[float, float]:
    """Return (NTC, property_tax_dollars) for a given TMV."""
    ntc = tmv * mechanics.class_rate.value
    property_tax = ntc * mechanics.tax_capacity_rate.value
    return ntc, property_tax


def run_tif_scenario(
    mechanics: TIFMechanics,
    trajectory: TMVTrajectory,
    attorney_fees: AttorneyFeeAssumption,
    grid: TimeGrid,
    baseline_trajectory: Optional[TMVTrajectory] = None,
) -> TIFResult:
    """Run one TIF scenario over the proforma horizon.

    Args:
        mechanics: universal TIF parameters
        trajectory: TMV-by-year for the active scenario
        attorney_fees: fee assumption
        grid: monthly time grid (gives us proforma years)
        baseline_trajectory: optional baseline TMV trajectory (used for
            attorney fee calculation: fees = pct × (baseline_tax - scenario_tax))

    Returns:
        TIFResult with annual + monthly arrays.
    """
    # Calendar years to model: union of grid years and trajectory years
    grid_years = sorted({m.calendar_year for m in grid})

    annual = TIFAnnualSeries()
    monthly = TIFMonthlySeries()
    monthly.tif_revenue = [0.0] * len(grid)
    monthly.property_tax_expense = [0.0] * len(grid)
    monthly.attorney_fees = [0.0] * len(grid)

    note_balance = mechanics.note_beginning_balance.value
    note_rate = mechanics.note_interest_rate.value
    base_ntc = mechanics.base_ntc.value
    developer_share = mechanics.developer_share.value
    admin_pct = mechanics.admin_pct.value
    osa_pct = mechanics.osa_pct.value
    last_increment_year = mechanics.tif_district_last_increment_year

    for cyr in grid_years:
        annual.calendar_years.append(cyr)

        tmv = trajectory.tmv(cyr)
        if tmv is None:
            # Beyond trajectory data — use last known TMV with growth
            if annual.tmv:
                growth = (trajectory.growth_rate_assumption.value
                          if trajectory.growth_rate_assumption else 0.04)
                tmv = annual.tmv[-1] * (1.0 + growth)
            else:
                tmv = mechanics.maa_floor.value  # safety

        # Enforce MAA floor (Section 4: "shall not be less than $43,835,000")
        # — only applies during TIF district
        if cyr <= last_increment_year:
            tmv = max(tmv, mechanics.maa_floor.value)

        ntc, property_tax = _tax_on(tmv, mechanics)
        captured_ntc = max(0.0, ntc - base_ntc)

        if cyr > last_increment_year:
            gross_tif = 0.0
        else:
            gross_tif = captured_ntc * mechanics.tax_capacity_rate.value * developer_share

        # Net TIF to LLC after admin holdback and OSA fee
        net_tif = gross_tif * (1.0 - admin_pct) * (1.0 - osa_pct)

        # TIF Note: interest accrues, principal paid from net_tif up to balance
        if note_balance > 0:
            note_interest = note_balance * note_rate
            available = note_balance + note_interest
            principal_paid = min(net_tif, available)
            llc_tif_income = max(0.0, net_tif - principal_paid)
            note_ending = max(0.0, note_balance + note_interest - principal_paid)
        else:
            note_interest = 0.0
            principal_paid = 0.0
            llc_tif_income = net_tif  # 100% flows to LLC once Note is paid
            note_ending = 0.0

        # Attorney fees — applied in appeal years vs baseline
        att_fee = 0.0
        if cyr in (trajectory.appeal_years or []):
            if baseline_trajectory:
                base_tmv = baseline_trajectory.tmv(cyr) or tmv
                if cyr <= last_increment_year:
                    base_tmv = max(base_tmv, mechanics.maa_floor.value)
                _bntc, base_tax = _tax_on(base_tmv, mechanics)
                year_savings = max(0.0, base_tax - property_tax)
            else:
                year_savings = 0.0
            att_fee = year_savings * attorney_fees.fee_pct_of_year1_savings.value

        annual.tmv.append(tmv)
        annual.ntc.append(ntc)
        annual.captured_ntc.append(captured_ntc)
        annual.property_tax.append(property_tax)
        annual.gross_tif.append(gross_tif)
        annual.net_tif.append(net_tif)
        annual.note_beginning_balance.append(note_balance)
        annual.note_interest.append(note_interest)
        annual.note_principal.append(principal_paid)
        annual.note_ending_balance.append(note_ending)
        annual.llc_tif_income.append(llc_tif_income)
        annual.attorney_fees.append(att_fee)

        note_balance = note_ending

        # Plumb into monthly: spread evenly across the 12 months in this calendar year
        monthly_revenue = llc_tif_income / 12.0
        monthly_property_tax = property_tax / 12.0
        monthly_att_fee = att_fee / 12.0  # spread; the Excel applies once
        for m in grid:
            if m.calendar_year == cyr:
                monthly.tif_revenue[m.proforma_month - 1] = monthly_revenue
                monthly.property_tax_expense[m.proforma_month - 1] = monthly_property_tax
                monthly.attorney_fees[m.proforma_month - 1] = monthly_att_fee

    return TIFResult(
        annual=annual,
        monthly=monthly,
        scenario=trajectory.scenario,
    )


def run_tif(
    tif_config: TIFConfiguration,
    active_scenario: TIFScenarioName,
    grid: TimeGrid,
) -> TIFResult:
    """Run the active TIF scenario from the configuration.

    The baseline is always passed as the comparison trajectory for
    attorney fee calculation.
    """
    active = tif_config.get_scenario(active_scenario)
    baseline = tif_config.scenarios.get(TIFScenarioName.BASELINE)
    return run_tif_scenario(
        mechanics=tif_config.mechanics,
        trajectory=active,
        attorney_fees=tif_config.attorney_fees,
        grid=grid,
        baseline_trajectory=baseline,
    )


__all__ = ["TIFAnnualSeries", "TIFMonthlySeries", "TIFResult", "run_tif", "run_tif_scenario"]
