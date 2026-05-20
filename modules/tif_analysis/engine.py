"""
TIF / Tax Model Engine -- translates Riley's 14-tab Chamberlain TIF Excel
model into a reusable Python scoring engine.

Pure Python + dataclasses + math.  No web dependencies.

Key concepts
------------
- **TMV** (Total Market Value): assessor value for the property.
- **NTC** (Net Tax Capacity): TMV * class_rate.
- **Captured NTC**: NTC minus the frozen original_ntc base.
- **Tax Increment**: Captured NTC * composite tax capacity rate.
- **Net TIF**: Increment after OSA fee, admin holdback, and developer share.
- **TIF Note**: Developer-held note repaid from Net TIF; interest accrues on
  unpaid balances.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------

@dataclass
class TIFAssumptions:
    """All tunable parameters for the TIF model.

    Defaults match the Chamberlain project as of Pay-2026.
    """

    # Classification & tax rates
    class_rate: float = 0.0125            # MN 273.13 apartments
    tax_capacity_rate: float = 1.27866    # composite local rate

    # Developer / admin splits
    developer_share: float = 1.0          # TIF Plan section IV
    admin_holdback_pct: float = 0.10      # 10 %
    osa_fee_pct: float = 0.0036           # State Auditor TIF fee

    # Frozen base
    original_ntc: float = 37_179.0

    # TIF Note terms
    note_principal: float = 7_142_377.0
    note_interest_rate: float = 0.046     # 4.60 %
    note_start_balance: float = 7_269_118.57  # as of Pay-2026

    # Contract floor
    maa_floor: float = 43_835_000.0       # contractual minimum TMV

    # Timeline
    first_pay_year: int = 2026
    last_tif_year: int = 2044             # 19 years of increment

    # Valuation
    discount_rate: float = 0.05           # NPV at 5 %

    # Attorney / appeal costs
    attorney_fee_pct: float = 0.25        # 25 % of first-year tax savings
    attorney_fee_years: int = 1           # per appeal event

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def n_years(self) -> int:
        """Number of TIF pay years inclusive."""
        return self.last_tif_year - self.first_pay_year + 1

    @property
    def net_tif_multiplier(self) -> float:
        """Combined multiplier applied to gross tax increment to arrive at
        net TIF: (1 - osa) * (1 - admin) * developer_share."""
        return (
            (1.0 - self.osa_fee_pct)
            * (1.0 - self.admin_holdback_pct)
            * self.developer_share
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def chamberlain_defaults(cls) -> TIFAssumptions:
        """Factory for Chamberlain-specific assumptions (the defaults)."""
        return cls()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    """Year-by-year results for a single TMV scenario."""

    name: str
    years: list[dict[str, Any]]
    totals: dict[str, float]
    payoff_year: int | None
    npv_net_tif: float
    npv_property_tax: float

    # Convenience ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dictionary representation."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

# Default Chamberlain scenarios (flat TMV across all years)
CHAMBERLAIN_SCENARIOS: dict[str, float] = {
    'Current':    54_866_000.0,
    'Mid':        52_150_000.0,
    'Aggressive': 50_000_000.0,
    'MAA Floor':  43_835_000.0,
}


class TIFEngine:
    """Core TIF / Tax model engine.

    Usage
    -----
    >>> engine = TIFEngine()
    >>> result = engine.run_scenario('Current', [54_866_000] * 19)
    >>> comparison = engine.compare_scenarios()
    >>> breakeven = engine.breakeven_analysis()
    >>> sweep = engine.sensitivity_sweep()
    """

    def __init__(self, assumptions: TIFAssumptions | None = None):
        self.a = assumptions or TIFAssumptions.chamberlain_defaults()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _npv(discount_rate: float, cashflows: list[float]) -> float:
        """Present value of *cashflows* where index = year offset (t=0, 1, ...)."""
        return sum(cf / (1.0 + discount_rate) ** t for t, cf in enumerate(cashflows))

    @staticmethod
    def _pmt(rate: float, nper: int, pv: float) -> float:
        """Standard amortisation payment (positive when pv is negative).

        Equivalent to Excel PMT(rate, nper, -pv).
        """
        if rate == 0:
            return pv / nper
        factor = (1.0 + rate) ** nper
        return pv * rate * factor / (factor - 1.0)

    def _make_flat_schedule(self, tmv: float) -> list[float]:
        """Return a flat TMV schedule of length n_years."""
        return [tmv] * self.a.n_years

    # ------------------------------------------------------------------
    # Public: schedule builders
    # ------------------------------------------------------------------

    def run_with_growth(
        self,
        name: str,
        starting_tmv: float,
        annual_growth_rate: float = 0.0,
    ) -> ScenarioResult:
        """Convenience: build a TMV schedule from a start value + annual
        compound growth rate, then run the scenario."""
        schedule = [
            starting_tmv * (1.0 + annual_growth_rate) ** t
            for t in range(self.a.n_years)
        ]
        return self.run_scenario(name, schedule)

    # ------------------------------------------------------------------
    # Step 1 & 2: Single Scenario
    # ------------------------------------------------------------------

    def run_scenario(
        self,
        name: str,
        tmv_schedule: list[float],
    ) -> ScenarioResult:
        """Run a single scenario given yearly TMV values.

        Parameters
        ----------
        name : str
            Label for this scenario (e.g. ``'Current'``).
        tmv_schedule : list[float]
            One TMV per pay year (length must equal ``n_years``).

        Returns
        -------
        ScenarioResult
        """
        a = self.a
        n = a.n_years
        if len(tmv_schedule) != n:
            raise ValueError(
                f"tmv_schedule length {len(tmv_schedule)} != expected {n} years"
            )

        rows: list[dict[str, Any]] = []
        note_balance = a.note_start_balance

        net_tif_flows: list[float] = []
        property_tax_flows: list[float] = []

        # Running totals
        total_net_tif = 0.0
        total_note_interest = 0.0
        total_note_principal = 0.0
        total_property_tax = 0.0
        total_tax_increment = 0.0

        payoff_year: int | None = None

        for i in range(n):
            year = a.first_pay_year + i
            tmv = tmv_schedule[i]

            # --- Step 1: TIF calculation ---------------------------------
            ntc = tmv * a.class_rate
            captured_ntc = max(ntc - a.original_ntc, 0.0)
            tax_increment = captured_ntc * a.tax_capacity_rate
            osa = tax_increment * a.osa_fee_pct
            admin = (tax_increment - osa) * a.admin_holdback_pct
            net_tif = tax_increment * a.net_tif_multiplier
            property_tax = ntc * a.tax_capacity_rate

            # --- Step 2: Note amortisation --------------------------------
            note_beg_bal = note_balance
            note_interest = note_beg_bal * a.note_interest_rate

            if note_beg_bal <= 0:
                # Note already paid off
                note_interest = 0.0
                note_principal_pmt = 0.0
                note_end_bal = 0.0
            elif net_tif >= note_interest:
                # Enough to cover interest; rest goes to principal
                note_principal_pmt = min(net_tif - note_interest, note_beg_bal)
                note_end_bal = max(note_beg_bal - note_principal_pmt, 0.0)
            else:
                # Shortfall: unpaid interest capitalises
                note_principal_pmt = 0.0
                note_end_bal = note_beg_bal + note_interest - net_tif

            note_balance = note_end_bal

            if payoff_year is None and note_end_bal == 0.0:
                payoff_year = year

            # --- Accumulators --------------------------------------------
            total_net_tif += net_tif
            total_note_interest += note_interest
            total_note_principal += note_principal_pmt
            total_property_tax += property_tax
            total_tax_increment += tax_increment

            net_tif_flows.append(net_tif)
            property_tax_flows.append(property_tax)

            rows.append({
                'year': year,
                'tmv': tmv,
                'ntc': round(ntc, 2),
                'captured_ntc': round(captured_ntc, 2),
                'tax_increment': round(tax_increment, 2),
                'osa': round(osa, 2),
                'admin': round(admin, 2),
                'net_tif': round(net_tif, 2),
                'note_beg_bal': round(note_beg_bal, 2),
                'note_interest': round(note_interest, 2),
                'note_principal': round(note_principal_pmt, 2),
                'note_end_bal': round(note_end_bal, 2),
                'property_tax': round(property_tax, 2),
            })

        totals = {
            'total_net_tif': round(total_net_tif, 2),
            'total_note_interest': round(total_note_interest, 2),
            'total_note_principal': round(total_note_principal, 2),
            'total_property_tax': round(total_property_tax, 2),
            'total_tax_increment': round(total_tax_increment, 2),
        }

        npv_net_tif = round(self._npv(a.discount_rate, net_tif_flows), 2)
        npv_property_tax = round(self._npv(a.discount_rate, property_tax_flows), 2)

        return ScenarioResult(
            name=name,
            years=rows,
            totals=totals,
            payoff_year=payoff_year,
            npv_net_tif=npv_net_tif,
            npv_property_tax=npv_property_tax,
        )

    # ------------------------------------------------------------------
    # Step 4: Scenario Comparison
    # ------------------------------------------------------------------

    def compare_scenarios(
        self,
        scenarios: dict[str, list[float]] | None = None,
    ) -> dict[str, Any]:
        """Run multiple scenarios and compute deltas vs the first (baseline).

        Parameters
        ----------
        scenarios : dict mapping name -> TMV schedule, optional
            Defaults to the four Chamberlain flat scenarios.

        Returns
        -------
        dict with keys ``results``, ``comparison``.
        """
        a = self.a

        if scenarios is None:
            scenarios = {
                name: self._make_flat_schedule(tmv)
                for name, tmv in CHAMBERLAIN_SCENARIOS.items()
            }

        results: dict[str, ScenarioResult] = {}
        for name, schedule in scenarios.items():
            results[name] = self.run_scenario(name, schedule)

        # Baseline is the first scenario
        baseline_name = next(iter(scenarios))
        baseline = results[baseline_name]

        comparison: list[dict[str, Any]] = []
        for name, res in results.items():
            # Nominal deltas vs baseline
            nominal_tax_savings = baseline.totals['total_property_tax'] - res.totals['total_property_tax']
            nominal_tif_reduction = baseline.totals['total_net_tif'] - res.totals['total_net_tif']
            nominal_net_benefit = nominal_tax_savings - nominal_tif_reduction

            # NPV deltas vs baseline
            npv_tax_savings = baseline.npv_property_tax - res.npv_property_tax
            npv_tif_reduction = baseline.npv_net_tif - res.npv_net_tif
            npv_net_benefit = npv_tax_savings - npv_tif_reduction

            # Attorney fees: 25% of first-year tax savings
            first_year_baseline_tax = baseline.years[0]['property_tax']
            first_year_scenario_tax = res.years[0]['property_tax']
            first_year_tax_savings = first_year_baseline_tax - first_year_scenario_tax
            attorney_fees = max(first_year_tax_savings * a.attorney_fee_pct, 0.0) * a.attorney_fee_years

            comparison.append({
                'name': name,
                'payoff_year': res.payoff_year,
                'total_net_tif': res.totals['total_net_tif'],
                'total_note_interest': res.totals['total_note_interest'],
                'total_note_principal': res.totals['total_note_principal'],
                'npv_net_tif': res.npv_net_tif,
                'npv_property_tax': res.npv_property_tax,
                'nominal_tax_savings': round(nominal_tax_savings, 2),
                'nominal_tif_reduction': round(nominal_tif_reduction, 2),
                'nominal_net_benefit': round(nominal_net_benefit, 2),
                'npv_tax_savings': round(npv_tax_savings, 2),
                'npv_tif_reduction': round(npv_tif_reduction, 2),
                'npv_net_benefit': round(npv_net_benefit, 2),
                'attorney_fees': round(attorney_fees, 2),
            })

        return {
            'baseline': baseline_name,
            'results': {name: res.to_dict() for name, res in results.items()},
            'comparison': comparison,
        }

    # ------------------------------------------------------------------
    # Step 5: Breakeven Analysis
    # ------------------------------------------------------------------

    def breakeven_analysis(self) -> dict[str, Any]:
        """Find the lowest flat TMV at which the TIF Note fully pays off
        by ``last_tif_year + 1``.

        Uses the standard amortisation (PMT) formula to find the required
        annual net TIF payment, then back-solves for the corresponding TMV.

        Returns
        -------
        dict with breakeven TMV, required annual payment, and detail.
        """
        a = self.a

        # Required annual payment to fully amortise the note.
        # Use n_years + 1 (= 20): the note must pay off by last_tif_year + 1
        # (i.e. through 2045), matching the Excel's breakeven calculation.
        amort_periods = a.n_years + 1
        required_annual_pmt = self._pmt(a.note_interest_rate, amort_periods, a.note_start_balance)

        # Back-solve: Net_TIF = Captured_NTC * tax_cap_rate * net_tif_multiplier
        #   => Captured_NTC = required_pmt / (tax_cap_rate * net_tif_multiplier)
        required_captured_ntc = required_annual_pmt / (
            a.tax_capacity_rate * a.net_tif_multiplier
        )

        required_ntc = required_captured_ntc + a.original_ntc
        breakeven_tmv = required_ntc / a.class_rate

        # Verify by running the scenario
        verification = self.run_scenario(
            'Breakeven', self._make_flat_schedule(breakeven_tmv)
        )

        return {
            'breakeven_tmv': round(breakeven_tmv, 2),
            'required_annual_pmt': round(required_annual_pmt, 2),
            'required_captured_ntc': round(required_captured_ntc, 2),
            'required_ntc': round(required_ntc, 2),
            'verification_payoff_year': verification.payoff_year,
            'verification_final_balance': verification.years[-1]['note_end_bal'],
        }

    # ------------------------------------------------------------------
    # Step 6: Sensitivity Sweep
    # ------------------------------------------------------------------

    def sensitivity_sweep(
        self,
        tmv_values: list[float] | None = None,
        baseline_tmv: float | None = None,
        steps: int = 12,
    ) -> list[dict[str, Any]]:
        """Sweep appeal-target TMVs and compute the benefit at each level.

        Parameters
        ----------
        tmv_values : list[float], optional
            Explicit list of TMV values to evaluate.  If ``None``, generates
            *steps* evenly spaced values from baseline down to MAA floor.
        baseline_tmv : float, optional
            The "current" TMV to compare against.  Defaults to
            ``CHAMBERLAIN_SCENARIOS['Current']``.
        steps : int
            Number of steps when auto-generating the sweep range.

        Returns
        -------
        list[dict] -- one entry per TMV value, sorted descending.
        """
        a = self.a

        if baseline_tmv is None:
            baseline_tmv = CHAMBERLAIN_SCENARIOS['Current']

        if tmv_values is None:
            step_size = (baseline_tmv - a.maa_floor) / max(steps - 1, 1)
            tmv_values = [
                baseline_tmv - step_size * i for i in range(steps)
            ]

        # Run baseline once
        baseline = self.run_scenario('Baseline', self._make_flat_schedule(baseline_tmv))
        baseline_annual_property_tax = baseline.years[0]['property_tax']
        baseline_annual_net_tif = baseline.years[0]['net_tif']

        results: list[dict[str, Any]] = []
        for tmv in tmv_values:
            scenario = self.run_scenario(f'TMV_{tmv:,.0f}', self._make_flat_schedule(tmv))

            annual_tax_savings = baseline_annual_property_tax - scenario.years[0]['property_tax']
            annual_tif_reduction = baseline_annual_net_tif - scenario.years[0]['net_tif']
            cumulative_tax_savings = baseline.totals['total_property_tax'] - scenario.totals['total_property_tax']
            cumulative_tif_reduction = baseline.totals['total_net_tif'] - scenario.totals['total_net_tif']
            net_nominal_benefit = cumulative_tax_savings - cumulative_tif_reduction

            npv_tax_savings = baseline.npv_property_tax - scenario.npv_property_tax
            npv_tif_reduction = baseline.npv_net_tif - scenario.npv_net_tif
            npv_net_benefit = npv_tax_savings - npv_tif_reduction

            results.append({
                'tmv': tmv,
                'annual_tax_savings': round(annual_tax_savings, 2),
                'annual_tif_reduction': round(annual_tif_reduction, 2),
                'cumulative_tax_savings': round(cumulative_tax_savings, 2),
                'cumulative_tif_reduction': round(cumulative_tif_reduction, 2),
                'net_nominal_benefit': round(net_nominal_benefit, 2),
                'npv_tax_savings': round(npv_tax_savings, 2),
                'npv_tif_reduction': round(npv_tif_reduction, 2),
                'npv_net_benefit': round(npv_net_benefit, 2),
                'payoff_year': scenario.payoff_year,
            })

        return results


# ---------------------------------------------------------------------------
# Quick smoke-test when run directly
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    engine = TIFEngine()

    # Verify single year math against Excel row 6 (Pay-2026, TMV = 54,866,000)
    r = engine.run_scenario('Current', engine._make_flat_schedule(54_866_000))
    y0 = r.years[0]
    print(f"Year {y0['year']}:")
    print(f"  NTC            = {y0['ntc']:>14,.2f}   (expect 685,825.00)")
    print(f"  Captured NTC   = {y0['captured_ntc']:>14,.2f}   (expect 648,646.00)")
    print(f"  Tax Increment  = {y0['tax_increment']:>14,.2f}   (expect 829,397.69)")
    print(f"  OSA            = {y0['osa']:>14,.2f}   (expect   2,985.83)")
    print(f"  Admin          = {y0['admin']:>14,.2f}   (expect  82,641.19)")
    print(f"  Net TIF        = {y0['net_tif']:>14,.2f}   (expect 743,770.67)")
    print(f"  Payoff year    = {r.payoff_year}")
    print()

    # Breakeven
    be = engine.breakeven_analysis()
    print(f"Breakeven TMV    = {be['breakeven_tmv']:>14,.2f}   (expect ~42,159,406)")
    print(f"Required annual  = {be['required_annual_pmt']:>14,.2f}   (expect ~563,674.52)")
    print()

    # Comparison
    comp = engine.compare_scenarios()
    for row in comp['comparison']:
        print(f"  {row['name']:12s}  payoff={row['payoff_year']}  "
              f"net_benefit_nom={row['nominal_net_benefit']:>14,.2f}")
