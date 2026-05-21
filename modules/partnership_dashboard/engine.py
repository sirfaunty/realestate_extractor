"""Partnership Dashboard aggregation engine.

Pulls data from three module engines to build a single executive snapshot:

  1. Proforma (via proforma_bridge) — NOI, levered CF, returns, exit value
  2. Distribution engine — waterfall distributions, capital accounts, IRR/EM
  3. Debt engine — DSCR, LTV, amortization, MIP

All results are per-TIF-scenario so the dashboard can show side-by-side
comparisons and highlight key decision metrics.

This engine does NOT duplicate calculations — it calls the existing engines
and reshapes their output for the dashboard's consumption.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Result Data Classes ──────────────────────────────────────────

@dataclass
class PartnerSnapshot:
    """Per-partner summary for the dashboard."""
    id: str
    name: str
    role: str
    ownership_pct: float
    initial_equity: float
    total_distributions: float
    equity_multiple: float
    irr: Optional[float]
    avg_cash_on_cash: float
    accrued_pref: float
    paid_pref: float
    unpaid_pref: float

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'role': self.role,
            'ownership_pct': self.ownership_pct,
            'initial_equity': round(self.initial_equity, 2),
            'total_distributions': round(self.total_distributions, 2),
            'equity_multiple': round(self.equity_multiple, 4),
            'irr': round(self.irr, 6) if self.irr is not None else None,
            'avg_cash_on_cash': round(self.avg_cash_on_cash, 4),
            'accrued_pref': round(self.accrued_pref, 2),
            'paid_pref': round(self.paid_pref, 2),
            'unpaid_pref': round(self.unpaid_pref, 2),
        }


@dataclass
class DebtSnapshot:
    """Debt position summary for the dashboard."""
    current_balance: float
    original_principal: float
    rate: float
    annual_debt_service: float
    monthly_payment: float
    remaining_term_months: int
    mip_rate: float
    year1_mip: float
    total_mip_over_hold: float
    min_dscr: float
    avg_dscr: float
    breach_count: int
    initial_ltv: float
    terminal_ltv: float
    dscr_by_year: list[dict] = field(default_factory=list)
    ltv_by_year: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'current_balance': round(self.current_balance, 2),
            'original_principal': round(self.original_principal, 2),
            'rate': self.rate,
            'annual_debt_service': round(self.annual_debt_service, 2),
            'monthly_payment': round(self.monthly_payment, 2),
            'remaining_term_months': self.remaining_term_months,
            'mip_rate': self.mip_rate,
            'year1_mip': round(self.year1_mip, 2),
            'total_mip_over_hold': round(self.total_mip_over_hold, 2),
            'min_dscr': round(self.min_dscr, 3),
            'avg_dscr': round(self.avg_dscr, 3),
            'breach_count': self.breach_count,
            'initial_ltv': round(self.initial_ltv, 4),
            'terminal_ltv': round(self.terminal_ltv, 4),
            'dscr_by_year': self.dscr_by_year,
            'ltv_by_year': self.ltv_by_year,
        }


@dataclass
class ProformaSnapshot:
    """Proforma summary for the dashboard."""
    hold_years: int
    initial_equity: float
    acquisition_cost_basis: float
    exit_cap_rate: float
    net_sale_proceeds: float
    gross_sale_price: float
    levered_irr: Optional[float]
    equity_multiple: float
    avg_dscr: float
    noi_by_year: dict[str, float] = field(default_factory=dict)
    levered_cf_by_year: dict[str, float] = field(default_factory=dict)
    debt_service_by_year: dict[str, float] = field(default_factory=dict)
    calendar_years: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'hold_years': self.hold_years,
            'initial_equity': round(self.initial_equity, 2),
            'acquisition_cost_basis': round(self.acquisition_cost_basis, 2),
            'exit_cap_rate': self.exit_cap_rate,
            'net_sale_proceeds': round(self.net_sale_proceeds, 2),
            'gross_sale_price': round(self.gross_sale_price, 2),
            'levered_irr': round(self.levered_irr, 6) if self.levered_irr else None,
            'equity_multiple': round(self.equity_multiple, 4),
            'avg_dscr': round(self.avg_dscr, 3),
            'noi_by_year': self.noi_by_year,
            'levered_cf_by_year': self.levered_cf_by_year,
            'debt_service_by_year': self.debt_service_by_year,
            'calendar_years': self.calendar_years,
        }


@dataclass
class YearSummary:
    """One year of combined data for the annual overview table."""
    year: int
    calendar_year: int
    noi: float
    debt_service: float
    levered_cf: float
    dscr: float
    distributions_ka: float
    distributions_idp: float
    distributions_total: float
    surplus_note_payment: float
    coc_ka: float
    coc_idp: float
    ltv: Optional[float] = None
    mip: float = 0.0

    def to_dict(self) -> dict:
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'noi': round(self.noi, 2),
            'debt_service': round(self.debt_service, 2),
            'levered_cf': round(self.levered_cf, 2),
            'dscr': round(self.dscr, 3),
            'distributions_ka': round(self.distributions_ka, 2),
            'distributions_idp': round(self.distributions_idp, 2),
            'distributions_total': round(self.distributions_total, 2),
            'surplus_note_payment': round(self.surplus_note_payment, 2),
            'coc_ka': round(self.coc_ka, 4),
            'coc_idp': round(self.coc_idp, 4),
            'ltv': round(self.ltv, 4) if self.ltv is not None else None,
            'mip': round(self.mip, 2),
        }


@dataclass
class DecisionMetrics:
    """Key metrics a partner needs to evaluate the deal."""
    # Returns
    deal_irr: Optional[float]
    deal_em: float
    ka_irr: Optional[float]
    ka_em: float
    idp_irr: Optional[float]
    idp_em: float
    # Risk
    min_dscr: float
    avg_dscr: float
    dscr_breach_count: int
    initial_ltv: float
    terminal_ltv: float
    # Cash flow
    total_noi: float
    total_distributions: float
    total_surplus_note: float
    total_mip: float
    net_sale_proceeds: float
    # Pref status
    ka_unpaid_pref: float
    idp_unpaid_pref: float

    def to_dict(self) -> dict:
        return {
            'deal_irr': round(self.deal_irr, 6) if self.deal_irr else None,
            'deal_em': round(self.deal_em, 4),
            'ka_irr': round(self.ka_irr, 6) if self.ka_irr else None,
            'ka_em': round(self.ka_em, 4),
            'idp_irr': round(self.idp_irr, 6) if self.idp_irr else None,
            'idp_em': round(self.idp_em, 4),
            'min_dscr': round(self.min_dscr, 3),
            'avg_dscr': round(self.avg_dscr, 3),
            'dscr_breach_count': self.dscr_breach_count,
            'initial_ltv': round(self.initial_ltv, 4),
            'terminal_ltv': round(self.terminal_ltv, 4),
            'total_noi': round(self.total_noi, 2),
            'total_distributions': round(self.total_distributions, 2),
            'total_surplus_note': round(self.total_surplus_note, 2),
            'total_mip': round(self.total_mip, 2),
            'net_sale_proceeds': round(self.net_sale_proceeds, 2),
            'ka_unpaid_pref': round(self.ka_unpaid_pref, 2),
            'idp_unpaid_pref': round(self.idp_unpaid_pref, 2),
        }


@dataclass
class ScenarioResult:
    """Complete dashboard data for one TIF scenario."""
    tif_scenario: str
    tif_label: str
    proforma: ProformaSnapshot
    partners: list[PartnerSnapshot]
    debt: DebtSnapshot
    annual_summary: list[YearSummary]
    decision_metrics: DecisionMetrics
    proforma_source: str = 'live'

    def to_dict(self) -> dict:
        return {
            'tif_scenario': self.tif_scenario,
            'tif_label': self.tif_label,
            'proforma': self.proforma.to_dict(),
            'partners': [p.to_dict() for p in self.partners],
            'debt': self.debt.to_dict(),
            'annual_summary': [y.to_dict() for y in self.annual_summary],
            'decision_metrics': self.decision_metrics.to_dict(),
            'proforma_source': self.proforma_source,
        }


@dataclass
class DashboardResult:
    """Full dashboard response — all scenarios + comparison."""
    entity_name: str
    scenarios: dict[str, ScenarioResult]
    scenario_names: list[str]
    comparison: dict  # cross-scenario delta analysis
    proforma_source: str = 'live'

    def to_dict(self) -> dict:
        return {
            'entity_name': self.entity_name,
            'scenarios': {k: v.to_dict() for k, v in self.scenarios.items()},
            'scenario_names': self.scenario_names,
            'comparison': self.comparison,
            'proforma_source': self.proforma_source,
        }


# ─── Engine ────────────────────────────────────────────────────────

class PartnershipDashboardEngine:
    """Aggregation engine that combines proforma, distribution, and debt data."""

    def __init__(self):
        self._dist_engine = None
        self._debt_engine = None

    def _get_dist_engine(self):
        if self._dist_engine is None:
            from modules.distribution.engine import DistributionEngine
            self._dist_engine = DistributionEngine()
        return self._dist_engine

    def _get_debt_engine(self):
        if self._debt_engine is None:
            from modules.debt_analysis.engine import DebtAnalysisEngine
            self._debt_engine = DebtAnalysisEngine()
        return self._debt_engine

    def _get_proforma_snapshot(self, tif_scenario: str = 'baseline'):
        """Load proforma data via the bridge. Returns None if unavailable."""
        try:
            from modules.distribution.proforma_bridge import get_proforma_snapshot
            return get_proforma_snapshot(tif_scenario)
        except Exception as e:
            logger.warning(f'Proforma bridge unavailable: {e}')
            return None

    def _get_tif_scenarios(self) -> list[dict]:
        """Get available TIF scenarios."""
        try:
            from modules.distribution.proforma_bridge import get_available_tif_scenarios
            return get_available_tif_scenarios()
        except Exception:
            return [{'id': 'baseline', 'label': 'Baseline'}]

    def build_scenario(self, tif_scenario: str = 'baseline',
                       tif_label: str = 'Baseline') -> ScenarioResult:
        """Build complete dashboard data for one TIF scenario.

        Calls proforma_bridge, distribution engine, and debt engine,
        then merges their outputs into a single ScenarioResult.
        """
        snap = self._get_proforma_snapshot(tif_scenario)
        source = 'live' if snap else 'defaults'

        # ── 1. Distribution ────────────────────────────────────────
        dist_eng = self._get_dist_engine()
        if snap:
            cf = dict(snap.levered_cf_by_year)
            dist_result = dist_eng.run_distribution(
                distributable_cf=cf,
                net_sale_proceeds=snap.net_sale_proceeds,
                sale_year=snap.sale_year,
            )
        else:
            dist_result = dist_eng.run_distribution()

        # Build partner snapshots
        partners = []
        for ps in dist_result.partner_summary:
            pr = dist_result.returns.get('by_partner', {}).get(ps['id'], {})
            partners.append(PartnerSnapshot(
                id=ps['id'],
                name=ps['name'],
                role=ps['role'],
                ownership_pct=ps['ownership_pct'],
                initial_equity=ps['initial_equity'],
                total_distributions=ps['total_distributions'],
                equity_multiple=ps['equity_multiple'],
                irr=pr.get('irr'),
                avg_cash_on_cash=pr.get('avg_cash_on_cash', 0.0),
                accrued_pref=ps['accrued_pref'],
                paid_pref=ps['paid_pref'],
                unpaid_pref=ps['unpaid_pref'],
            ))

        # ── 2. Debt ────────────────────────────────────────────────
        debt_eng = self._get_debt_engine()
        if snap:
            debt_result = debt_eng.run_analysis(
                noi_by_year=dict(snap.noi_by_year),
                debt_service_by_year=dict(snap.debt_service_by_year),
                calendar_years=dict(snap.calendar_years),
                exit_cap_rate=snap.exit_cap_rate or 0.055,
                hold_years=snap.hold_years,
            )
        else:
            debt_result = debt_eng.run_analysis()

        # Extract debt snapshot
        dscrs = [d.dscr for d in debt_result.dscr_by_year]
        min_dscr = min(dscrs) if dscrs else 0.0
        avg_dscr = sum(dscrs) / len(dscrs) if dscrs else 0.0
        breaches = sum(1 for d in debt_result.dscr_by_year
                       if d.covenant_status == 'breach')

        mip_amounts = [m.mip_amount for m in debt_result.mip_by_year]
        total_mip = sum(mip_amounts)
        year1_mip = mip_amounts[0] if mip_amounts else 0.0

        ltvs = debt_result.ltv_by_year
        initial_ltv = ltvs[0].ltv if ltvs else 0.0
        terminal_ltv = ltvs[-1].ltv if ltvs else 0.0

        debt_snap = DebtSnapshot(
            current_balance=debt_result.summary.current_balance,
            original_principal=debt_result.summary.original_principal,
            rate=debt_result.summary.rate,
            annual_debt_service=debt_result.summary.annual_debt_service,
            monthly_payment=debt_result.summary.monthly_payment,
            remaining_term_months=debt_result.summary.remaining_term_months,
            mip_rate=debt_result.summary.mip_rate,
            year1_mip=year1_mip,
            total_mip_over_hold=total_mip,
            min_dscr=min_dscr,
            avg_dscr=avg_dscr,
            breach_count=breaches,
            initial_ltv=initial_ltv,
            terminal_ltv=terminal_ltv,
            dscr_by_year=[d.to_dict() for d in debt_result.dscr_by_year],
            ltv_by_year=[l.to_dict() for l in debt_result.ltv_by_year],
        )

        # ── 3. Proforma snapshot ───────────────────────────────────
        if snap:
            pf_snap = ProformaSnapshot(
                hold_years=snap.hold_years,
                initial_equity=snap.initial_equity,
                acquisition_cost_basis=snap.acquisition_cost_basis,
                exit_cap_rate=snap.exit_cap_rate,
                net_sale_proceeds=snap.net_sale_proceeds,
                gross_sale_price=snap.gross_sale_price,
                levered_irr=snap.levered_irr,
                equity_multiple=snap.equity_multiple,
                avg_dscr=snap.avg_dscr,
                noi_by_year={str(k): round(v, 2) for k, v in snap.noi_by_year.items()},
                levered_cf_by_year={str(k): round(v, 2)
                                    for k, v in snap.levered_cf_by_year.items()},
                debt_service_by_year={str(k): round(v, 2)
                                      for k, v in snap.debt_service_by_year.items()},
                calendar_years={str(k): v for k, v in snap.calendar_years.items()},
            )
        else:
            # Minimal defaults
            pf_snap = ProformaSnapshot(
                hold_years=10, initial_equity=8_837_892.0,
                acquisition_cost_basis=61_805_592.0, exit_cap_rate=0.055,
                net_sale_proceeds=0.0, gross_sale_price=0.0,
                levered_irr=None, equity_multiple=0.0, avg_dscr=0.0,
            )

        # ── 4. Annual summary (merged timeline) ───────────────────
        hold = snap.hold_years if snap else 10
        first_cal = snap.first_calendar_year if snap else 2026
        annual = []

        # Index distribution years and MIP by proforma year
        dist_by_year = {yr.year: yr for yr in dist_result.years}
        mip_by_year = {m.year: m.mip_amount for m in debt_result.mip_by_year}
        dscr_by_year_map = {d.year: d.dscr for d in debt_result.dscr_by_year}
        ltv_by_year_map = {l.year: l.ltv for l in debt_result.ltv_by_year}

        for y in range(1, hold + 1):
            cal = first_cal + y - 1
            noi = snap.noi_by_year.get(y, 0.0) if snap else 0.0
            ds = snap.debt_service_by_year.get(y, 0.0) if snap else 0.0
            lcf = snap.levered_cf_by_year.get(y, 0.0) if snap else 0.0

            dyr = dist_by_year.get(y)
            dist_ka = dyr.distributions_by_partner.get('KA', 0.0) if dyr else 0.0
            dist_idp = dyr.distributions_by_partner.get('IDP', 0.0) if dyr else 0.0
            note_pmt = dyr.surplus_cash_note_payment if dyr else 0.0
            coc_ka = dyr.coc_by_partner.get('KA', 0.0) if dyr else 0.0
            coc_idp = dyr.coc_by_partner.get('IDP', 0.0) if dyr else 0.0

            annual.append(YearSummary(
                year=y,
                calendar_year=cal,
                noi=noi,
                debt_service=ds,
                levered_cf=lcf,
                dscr=dscr_by_year_map.get(y, 0.0),
                distributions_ka=dist_ka,
                distributions_idp=dist_idp,
                distributions_total=dist_ka + dist_idp,
                surplus_note_payment=note_pmt,
                coc_ka=coc_ka,
                coc_idp=coc_idp,
                ltv=ltv_by_year_map.get(y),
                mip=mip_by_year.get(y, 0.0),
            ))

        # ── 5. Decision metrics ────────────────────────────────────
        deal_ret = dist_result.returns.get('deal', {})
        ka_ret = dist_result.returns.get('by_partner', {}).get('KA', {})
        idp_ret = dist_result.returns.get('by_partner', {}).get('IDP', {})

        total_noi = sum(a.noi for a in annual)
        total_dist = sum(a.distributions_total for a in annual)
        total_note = sum(a.surplus_note_payment for a in annual)

        decision = DecisionMetrics(
            deal_irr=deal_ret.get('irr'),
            deal_em=deal_ret.get('equity_multiple', 0.0),
            ka_irr=ka_ret.get('irr'),
            ka_em=ka_ret.get('equity_multiple', 0.0),
            idp_irr=idp_ret.get('irr'),
            idp_em=idp_ret.get('equity_multiple', 0.0),
            min_dscr=min_dscr,
            avg_dscr=avg_dscr,
            dscr_breach_count=breaches,
            initial_ltv=initial_ltv,
            terminal_ltv=terminal_ltv,
            total_noi=total_noi,
            total_distributions=total_dist,
            total_surplus_note=total_note,
            total_mip=total_mip,
            net_sale_proceeds=snap.net_sale_proceeds if snap else 0.0,
            ka_unpaid_pref=dist_result.final_accounts.get('KA').unpaid_pref
                if 'KA' in dist_result.final_accounts else 0.0,
            idp_unpaid_pref=dist_result.final_accounts.get('IDP').unpaid_pref
                if 'IDP' in dist_result.final_accounts else 0.0,
        )

        return ScenarioResult(
            tif_scenario=tif_scenario,
            tif_label=tif_label,
            proforma=pf_snap,
            partners=partners,
            debt=debt_snap,
            annual_summary=annual,
            decision_metrics=decision,
            proforma_source=source,
        )

    def build_dashboard(self) -> DashboardResult:
        """Build the full dashboard across all TIF scenarios.

        Returns a DashboardResult with per-scenario data and a cross-scenario
        comparison table highlighting deltas from baseline.
        """
        tif_scenarios = self._get_tif_scenarios()
        scenarios = {}
        scenario_names = []

        for sc in tif_scenarios:
            sid = sc['id']
            label = sc['label']
            scenario_names.append(label)
            try:
                scenarios[label] = self.build_scenario(sid, label)
            except Exception as e:
                logger.error(f'Failed to build scenario {sid}: {e}')

        # Build comparison table
        comparison = self._build_comparison(scenarios, scenario_names)

        return DashboardResult(
            entity_name='Chamberlain Apartments LLC',
            scenarios=scenarios,
            scenario_names=scenario_names,
            comparison=comparison,
            proforma_source='live' if scenarios else 'defaults',
        )

    def _build_comparison(self, scenarios: dict[str, ScenarioResult],
                          names: list[str]) -> dict:
        """Build cross-scenario comparison with deltas from baseline."""
        if not names or not scenarios:
            return {'rows': [], 'baseline': None}

        baseline_name = names[0]
        baseline = scenarios.get(baseline_name)
        if not baseline:
            return {'rows': [], 'baseline': None}

        rows = []
        for name in names:
            sc = scenarios.get(name)
            if not sc:
                continue
            dm = sc.decision_metrics
            row = {
                'scenario': name,
                'tif_scenario': sc.tif_scenario,
                'deal_irr': dm.deal_irr,
                'deal_em': dm.deal_em,
                'ka_irr': dm.ka_irr,
                'ka_em': dm.ka_em,
                'idp_irr': dm.idp_irr,
                'idp_em': dm.idp_em,
                'min_dscr': dm.min_dscr,
                'avg_dscr': dm.avg_dscr,
                'total_distributions': dm.total_distributions,
                'net_sale_proceeds': dm.net_sale_proceeds,
            }

            # Add deltas vs baseline
            if name != baseline_name and baseline:
                bdm = baseline.decision_metrics
                row['delta'] = {
                    'deal_irr': _delta(dm.deal_irr, bdm.deal_irr),
                    'deal_em': round(dm.deal_em - bdm.deal_em, 4),
                    'ka_em': round(dm.ka_em - bdm.ka_em, 4),
                    'idp_em': round(dm.idp_em - bdm.idp_em, 4),
                    'min_dscr': round(dm.min_dscr - bdm.min_dscr, 3),
                    'total_distributions': round(
                        dm.total_distributions - bdm.total_distributions, 2),
                    'net_sale_proceeds': round(
                        dm.net_sale_proceeds - bdm.net_sale_proceeds, 2),
                }

            rows.append(row)

        return {
            'rows': rows,
            'baseline': baseline_name,
        }


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Compute delta, handling None values."""
    if a is None or b is None:
        return None
    return round(a - b, 6)


__all__ = [
    'PartnershipDashboardEngine',
    'DashboardResult',
    'ScenarioResult',
]
