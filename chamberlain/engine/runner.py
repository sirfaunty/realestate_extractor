"""Top-level proforma orchestrator.

Takes a Scenario and runs all engine modules in dependency order to produce
a single ProformaResult with annual + monthly views and deal-level returns.

Dependency order:
  1. time grid
  2. revenue (GPR → TRI)
  3. other income
  4. TIF (active scenario) → TIF revenue + property tax
  5. opex (note: RE taxes line is replaced by TIF-driven property tax)
  6. EGR = TRI + other income + TIF net increment
  7. NOI = EGR − opex
  8. debt amortization (acquisition + capital funding)
  9. capex (equity-first funding)
  10. non-operating (AMF, MIP, reserves, surplus cash note)
  11. levered/unlevered cash flow
  12. residual at sale year
  13. returns (IRR/EM/DSCR/YoC)

The Excel proforma replaces the RE-tax OpEx line with the TIF sub-engine's
property-tax output (since TMV drives both property tax and TIF revenue).
For the baseline scenario the OpEx RE-tax line and the TIF property tax
should be close; we use the TIF engine's property tax as authoritative
when TIF is configured and keep the OpEx RE-tax line out to avoid double
counting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .capex import build_capex_series
from .debt_amort import build_debt_series
from .non_operating import build_non_operating_series
from .opex import build_opex_series
from .other_income import build_other_income_series
from .residual import ResidualResult, compute_residual
from .returns import ReturnsResult, compute_returns
from .revenue import build_revenue_series
from .tif import run_tif
from .time_grid import TimeGrid
from ..models.scenario import Scenario
from ..models.tif import TIFScenarioName


@dataclass
class AnnualLine:
    """One year's worth of the proforma P&L + cash flow."""

    year: int
    calendar_year: int
    gross_potential_rent: float = 0.0
    loss_to_lease: float = 0.0
    vacancy: float = 0.0
    concessions: float = 0.0
    bad_debt: float = 0.0
    total_rental_income: float = 0.0
    other_income: float = 0.0
    tif_net_increment: float = 0.0
    effective_gross_revenue: float = 0.0
    operating_expenses: float = 0.0
    property_tax: float = 0.0
    net_operating_income: float = 0.0
    asset_mgmt_fee: float = 0.0
    replacement_reserves: float = 0.0
    non_operating_total: float = 0.0
    property_noi: float = 0.0          # NOI before AMF/capex/non-op (validated def)
    capex: float = 0.0
    debt_service: float = 0.0
    capital_funding_draw: float = 0.0
    levered_cash_flow: float = 0.0
    unlevered_cash_flow: float = 0.0
    debt_balance_eoy: float = 0.0
    dscr: float = 0.0


@dataclass
class ProformaResult:
    """Full proforma output."""

    scenario_id: str
    scenario_name: str
    tif_scenario: TIFScenarioName
    annual_lines: list[AnnualLine] = field(default_factory=list)
    residual: Optional[ResidualResult] = None
    returns: Optional[ReturnsResult] = None

    # Echo key inputs for outputs/drill-back
    acquisition_cost_basis: float = 0.0
    initial_equity: float = 0.0

    def year(self, y: int) -> Optional[AnnualLine]:
        for ln in self.annual_lines:
            if ln.year == y:
                return ln
        return None


def run_proforma(
    scenario: Scenario,
    tif_scenario: Optional[TIFScenarioName] = None,
) -> ProformaResult:
    """Run the full proforma for a scenario.

    Args:
        scenario: the loaded Scenario
        tif_scenario: which TIF scenario to run; defaults to
            scenario.active_tif_scenario or BASELINE

    Returns:
        ProformaResult
    """
    active_tif = (
        tif_scenario
        or scenario.active_tif_scenario
        or TIFScenarioName.BASELINE
    )

    grid = TimeGrid(
        start_date=scenario.meta.proforma_start_date,
        hold_years=scenario.meta.hold_years,
    )
    n_months = len(grid)
    hold = scenario.meta.hold_years

    # 1. Revenue
    rev = build_revenue_series(
        scenario.unit_roster,
        scenario.base_rent_inflation,
        scenario.income_offsets,
        grid,
    )

    # 2. Other income
    oi = build_other_income_series(
        scenario.other_income,
        scenario.other_income_inflation,
        scenario.property,
        grid,
    )

    # 3. TIF (active scenario)
    # TIF reaches the partnership ONLY via the executed TIF Note waterfall:
    # receipts amortize the Note first; the LLC receives the residual
    # increment only after the Note is satisfied (projected ~2039 baseline /
    # ~2043 appeal-adjusted per the executed TIF documentation, i.e. after
    # a 2026-2036 hold). tif_result.monthly.tif_revenue already reflects
    # this post-Note residual.
    tif_result = None
    tif_net_monthly = [0.0] * n_months
    property_tax_monthly = [0.0] * n_months
    if scenario.tif is not None:
        tif_result = run_tif(scenario.tif, active_tif, grid)
        property_tax_monthly = list(tif_result.monthly.property_tax_expense)
        property_tax_monthly = (property_tax_monthly + [0.0] * n_months)[:n_months]
        tif_net_monthly = list(tif_result.monthly.tif_revenue)
        tif_net_monthly = (tif_net_monthly + [0.0] * n_months)[:n_months]

    # 4. OpEx (exclude RE-tax line if TIF supplies property tax)
    opex = build_opex_series(
        scenario.opex,
        scenario.opex_inflation,
        scenario.property,
        grid,
    )
    tif_supplies_tax = scenario.tif is not None

    # 5. Build monthly EGR (TRI + other income + TIF net increment)
    monthly_egr = [0.0] * n_months
    for i in range(n_months):
        tri = rev.total_rental_income[i] if i < len(rev.total_rental_income) else 0.0
        other = oi.total[i] if i < len(oi.total) else 0.0
        tif_inc = tif_net_monthly[i]
        monthly_egr[i] = tri + other + tif_inc

    # 6. CapEx (equity-first; equity reserve = total capex − capex loan principal)
    cap_loan = scenario.debt.capital_funding_loan
    equity_reserve = 0.0
    if cap_loan is not None:
        total_capex_plan = sum(
            sum(amt.value for amt in line.amount_by_year.values())
            for line in scenario.capex.lines
        )
        # Equity funds everything except what the capex loan covers (equity-first)
        equity_reserve = max(
            0.0,
            total_capex_plan - cap_loan.max_principal.value,
        )
    capex = build_capex_series(scenario.capex, grid, cap_loan, equity_reserve)

    # 7. Debt amortization — capex loan draws feed the capital funding loan
    capex_loan_draws = list(capex.loan_funded)
    capex_loan_draws = (capex_loan_draws + [0.0] * n_months)[:n_months]
    debt = build_debt_series(scenario.debt, grid, capex_loan_draws)
    acq_balance_monthly = list(debt.acq_ending_balance)
    acq_balance_monthly = (acq_balance_monthly + [0.0] * n_months)[:n_months]

    # 8. Non-operating
    non_op = build_non_operating_series(
        scenario.non_operating,
        scenario.property,
        scenario.debt,
        monthly_egr,
        acq_balance_monthly,
        grid,
    )

    # 9. Aggregate to annual lines
    annual_lines: list[AnnualLine] = []
    noi_by_year: dict[int, float] = {}
    levered_cf_by_year: dict[int, float] = {}
    unlevered_cf_by_year: dict[int, float] = {}
    ds_by_year: dict[int, float] = {}

    for y in range(1, hold + 1):
        months = grid.months_for_year(y)
        cy = months[0].calendar_year

        def msum(series: list[float]) -> float:
            return sum(
                series[m.proforma_month - 1]
                for m in months
                if m.proforma_month - 1 < len(series)
            )

        gpr = msum(rev.gross_potential_rent)
        l2l = msum(rev.loss_to_lease)
        vac = msum(rev.vacancy)
        conc = msum(rev.concessions)
        bd = msum(rev.bad_debt)
        tri = msum(rev.total_rental_income)
        other = oi.annual_grand_total(y, grid)
        tif_inc = msum(tif_net_monthly)
        prop_tax = msum(property_tax_monthly)

        egr = tri + other + tif_inc

        # OpEx grand total; if TIF supplies property tax, remove RE-tax line
        opex_total = opex.annual_grand_total(y, grid)
        if tif_supplies_tax:
            re_tax_line = 0.0
            for lid in ("real_estate_taxes",):
                if lid in opex.by_line:
                    re_tax_line += opex.annual_total(lid, y, grid)
            opex_total = opex_total - re_tax_line + prop_tax

        noi = egr - opex_total

        amf = non_op.annual_total("asset_mgmt_fee", y, grid)
        reserves = non_op.annual_total("replacement_reserves", y, grid)
        non_op_total = non_op.annual_grand_total(y, grid)

        cx = capex.annual_total(y, grid)
        ds = debt.annual_total("total_debt_service", y, grid)
        cap_draw = debt.annual_total("cap_funding_draw", y, grid)
        eoy_bal = debt.end_of_year_balance(y, grid)

        # Property NOI (validated definition): NOI before AMF, capex, non-op
        property_noi = noi  # NOI already excludes AMF (it's non-op) and capex

        # Levered CF = NOI − non-op − capex − debt service + capital funding draw
        levered_cf = noi - non_op_total - cx - ds + cap_draw
        # Unlevered CF = NOI − capex (no debt, no non-op financing items;
        # keep reserves out to match Excel unlevered convention)
        unlevered_cf = noi - cx

        dscr = (noi / ds) if ds > 0 else 0.0

        annual_lines.append(AnnualLine(
            year=y,
            calendar_year=cy,
            gross_potential_rent=gpr,
            loss_to_lease=l2l,
            vacancy=vac,
            concessions=conc,
            bad_debt=bd,
            total_rental_income=tri,
            other_income=other,
            tif_net_increment=tif_inc,
            effective_gross_revenue=egr,
            operating_expenses=opex_total,
            property_tax=prop_tax,
            net_operating_income=noi,
            asset_mgmt_fee=amf,
            replacement_reserves=reserves,
            non_operating_total=non_op_total,
            property_noi=property_noi,
            capex=cx,
            debt_service=ds,
            capital_funding_draw=cap_draw,
            levered_cash_flow=levered_cf,
            unlevered_cash_flow=unlevered_cf,
            debt_balance_eoy=eoy_bal,
            dscr=dscr,
        ))
        noi_by_year[y] = noi
        levered_cf_by_year[y] = levered_cf
        unlevered_cf_by_year[y] = unlevered_cf
        ds_by_year[y] = ds

    # 10. Residual — Year N+1 forward NOI = Year N NOI grown one more period
    sale_year = scenario.residual.sale_year.value
    last_noi = noi_by_year.get(sale_year, 0.0)
    # Forward growth = base rent inflation terminal rate as proxy for NOI growth
    fwd_growth = scenario.base_rent_inflation.rate(sale_year + 1)
    forward_noi = last_noi * (1.0 + fwd_growth)
    loan_bal_at_sale = 0.0
    if annual_lines:
        loan_bal_at_sale = next(
            (ln.debt_balance_eoy for ln in annual_lines if ln.year == sale_year),
            0.0,
        )
    residual = compute_residual(
        scenario,
        noi_by_year,
        forward_noi,
        loan_bal_at_sale,
    )

    # 11. Returns
    # Initial equity = acquisition cost basis − initial loan funding
    acq_loan_amt = scenario.debt.acquisition_loan.original_principal.value
    cost_basis = scenario.acquisition_cost_basis.value
    initial_equity = max(0.0, cost_basis - acq_loan_amt)

    gross_sale_less_cost = residual.gross_sale_price - residual.cost_of_sale

    returns = compute_returns(
        initial_equity=initial_equity,
        acquisition_cost_basis=cost_basis,
        levered_cf_by_year=levered_cf_by_year,
        unlevered_cf_by_year=unlevered_cf_by_year,
        noi_by_year=noi_by_year,
        debt_service_by_year=ds_by_year,
        net_sale_proceeds=residual.net_sale_proceeds,
        gross_sale_less_cost=gross_sale_less_cost,
        sale_year=sale_year,
    )

    return ProformaResult(
        scenario_id=scenario.meta.scenario_id,
        scenario_name=scenario.meta.name,
        tif_scenario=active_tif,
        annual_lines=annual_lines,
        residual=residual,
        returns=returns,
        acquisition_cost_basis=cost_basis,
        initial_equity=initial_equity,
    )


__all__ = ["AnnualLine", "ProformaResult", "run_proforma"]
