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
    distributions_p1: float
    distributions_p2: float
    distributions_total: float
    surplus_note_payment: float
    coc_p1: float
    coc_p2: float
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
            'distributions_p1': round(self.distributions_p1, 2),
            'distributions_p2': round(self.distributions_p2, 2),
            'distributions_total': round(self.distributions_total, 2),
            'surplus_note_payment': round(self.surplus_note_payment, 2),
            'coc_p1': round(self.coc_p1, 4),
            'coc_p2': round(self.coc_p2, 4),
            'ltv': round(self.ltv, 4) if self.ltv is not None else None,
            'mip': round(self.mip, 2),
        }


@dataclass
class DecisionMetrics:
    """Key metrics a partner needs to evaluate the deal."""
    # Returns
    deal_irr: Optional[float]
    deal_em: float
    p1_irr: Optional[float]
    p1_em: float
    p2_irr: Optional[float]
    p2_em: float
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
    p1_unpaid_pref: float
    p2_unpaid_pref: float

    def to_dict(self) -> dict:
        return {
            'deal_irr': round(self.deal_irr, 6) if self.deal_irr else None,
            'deal_em': round(self.deal_em, 4),
            'p1_irr': round(self.p1_irr, 6) if self.p1_irr else None,
            'p1_em': round(self.p1_em, 4),
            'p2_irr': round(self.p2_irr, 6) if self.p2_irr else None,
            'p2_em': round(self.p2_em, 4),
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
            'p1_unpaid_pref': round(self.p1_unpaid_pref, 2),
            'p2_unpaid_pref': round(self.p2_unpaid_pref, 2),
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
    partner_ids: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'entity_name': self.entity_name,
            'partner_ids': self.partner_ids,
            'scenarios': {k: v.to_dict() for k, v in self.scenarios.items()},
            'scenario_names': self.scenario_names,
            'comparison': self.comparison,
            'proforma_source': self.proforma_source,
        }


# ─── Engine ────────────────────────────────────────────────────────

class PartnershipDashboardEngine:
    """Aggregation engine that combines proforma, distribution, and debt data."""

    def __init__(self, deal_id=None):
        self._deal_id = deal_id
        self._dist_engine = None
        self._debt_engine = None

    def _deal_cfg(self, module):
        """Editable per-deal config for a sub-module, or None (engine defaults)."""
        if not self._deal_id:
            return None
        try:
            from registry import get_registry, resolve_deal
            wh_id = resolve_deal(self._deal_id)['warehouse_deal_id']
            return get_registry().get_deal_config(wh_id, module)
        except Exception:
            return None

    def _get_dist_engine(self):
        if self._dist_engine is None:
            from modules.distribution.engine import (
                DistributionEngine, DistributionAssumptions)
            cfg = self._deal_cfg('distribution')
            self._dist_engine = DistributionEngine(
                DistributionAssumptions.from_config(cfg) if cfg else None)
        return self._dist_engine

    def _get_debt_engine(self):
        if self._debt_engine is None:
            from modules.debt_analysis.engine import DebtAnalysisEngine
            self._debt_engine = DebtAnalysisEngine(self._deal_cfg('debt'))
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
            # Minimal neutral defaults (no proforma engine / deal seed loaded)
            pf_snap = ProformaSnapshot(
                hold_years=10, initial_equity=0.0,
                acquisition_cost_basis=0.0, exit_cap_rate=0.055,
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
            p1, p2 = dist_eng.partner_ids()
            dist_p1 = dyr.distributions_by_partner.get(p1, 0.0) if dyr else 0.0
            dist_p2 = dyr.distributions_by_partner.get(p2, 0.0) if dyr else 0.0
            note_pmt = dyr.surplus_cash_note_payment if dyr else 0.0
            coc_p1 = dyr.coc_by_partner.get(p1, 0.0) if dyr else 0.0
            coc_p2 = dyr.coc_by_partner.get(p2, 0.0) if dyr else 0.0

            annual.append(YearSummary(
                year=y,
                calendar_year=cal,
                noi=noi,
                debt_service=ds,
                levered_cf=lcf,
                dscr=dscr_by_year_map.get(y, 0.0),
                distributions_p1=dist_p1,
                distributions_p2=dist_p2,
                distributions_total=dist_p1 + dist_p2,
                surplus_note_payment=note_pmt,
                coc_p1=coc_p1,
                coc_p2=coc_p2,
                ltv=ltv_by_year_map.get(y),
                mip=mip_by_year.get(y, 0.0),
            ))

        # ── 5. Decision metrics ────────────────────────────────────
        # p1_*/p2_* fields = managing class / investor class (first/second
        # partner of the deal); labels come from partner_ids in the payload.
        p1, p2 = dist_eng.partner_ids()
        deal_ret = dist_result.returns.get('deal', {})
        p1_ret = dist_result.returns.get('by_partner', {}).get(p1, {})
        p2_ret = dist_result.returns.get('by_partner', {}).get(p2, {})

        total_noi = sum(a.noi for a in annual)
        total_dist = sum(a.distributions_total for a in annual)
        total_note = sum(a.surplus_note_payment for a in annual)

        decision = DecisionMetrics(
            deal_irr=deal_ret.get('irr'),
            deal_em=deal_ret.get('equity_multiple', 0.0),
            p1_irr=p1_ret.get('irr'),
            p1_em=p1_ret.get('equity_multiple', 0.0),
            p2_irr=p2_ret.get('irr'),
            p2_em=p2_ret.get('equity_multiple', 0.0),
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
            p1_unpaid_pref=dist_result.final_accounts.get(p1).unpaid_pref
                if p1 in dist_result.final_accounts else 0.0,
            p2_unpaid_pref=dist_result.final_accounts.get(p2).unpaid_pref
                if p2 in dist_result.final_accounts else 0.0,
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
            entity_name=self._get_dist_engine().a.entity_name,
            partner_ids=list(self._get_dist_engine().partner_ids()),
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
                'p1_irr': dm.p1_irr,
                'p1_em': dm.p1_em,
                'p2_irr': dm.p2_irr,
                'p2_em': dm.p2_em,
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
                    'p1_em': round(dm.p1_em - bdm.p1_em, 4),
                    'p2_em': round(dm.p2_em - bdm.p2_em, 4),
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


    def get_market_context(self, market: str = 'Minneapolis') -> dict:
        """Get market cap rates and rent benchmarks from the warehouse.

        Provides market context for validating deal assumptions (exit cap
        rate, rent growth, etc.). Returns empty dict if warehouse is
        unavailable.
        """
        try:
            from warehouse.engine import WarehouseEngine
            wh = WarehouseEngine()
            wh.connect()
            cap_rates = wh.get_market_cap_rates_for_exit(market)
            rent_benchmarks = wh.get_market_rent_benchmarks(market)
            wh.close()
            return {
                'market': market,
                'cap_rates': cap_rates,
                'rent_benchmarks': rent_benchmarks,
            }
        except Exception as e:
            logger.warning(f'Warehouse market context unavailable: {e}')
            return {}


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
