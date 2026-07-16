"""
Deal Analytics Warehouse Bridge.

Centralized write hooks that persist deal-level computation results
(proforma, distribution, debt, TIF) into the DuckDB analytical warehouse.

Each `persist_*` function takes the output of its respective engine and
stores it using WarehouseEngine methods.  Modules call these after
computation — the bridge handles connection, error recovery, and
logging.

Usage in a module:
    from warehouse.deal_analytics import persist_proforma

    snap = get_proforma_snapshot('baseline')
    persist_proforma('chamberlain', snap)   # fire-and-forget
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import WarehouseEngine

logger = logging.getLogger(__name__)

# ─── Lazy singleton ────────────────────────────────────────────────

_wh: Optional['WarehouseEngine'] = None

# DuckDB is single-writer; the warehouse engine + its connection are shared state.
# `_init_lock` guards one-time creation (double-checked) so parallel requests can't
# create two engines; `_write_lock` serializes every persist so concurrent writers
# can't race on the shared connection (which produced intermittent
# "'NoneType' object is not subscriptable" and duplicate ingestion counters).
_init_lock = threading.Lock()
_write_lock = threading.Lock()


def _get_wh() -> 'WarehouseEngine':
    global _wh
    if _wh is None:
        with _init_lock:
            if _wh is None:
                from .engine import WarehouseEngine
                wh = WarehouseEngine()
                wh.connect()
                _wh = wh
    return _wh


def _safe(fn):
    """Decorator: serialize the write and catch warehouse errors so deal modules
    never break (fire-and-forget semantics)."""
    def wrapper(*args, **kwargs):
        try:
            with _write_lock:
                return fn(*args, **kwargs)
        except Exception as e:
            logger.warning(f'Warehouse write failed ({fn.__name__}): {e}')
            return None
    return wrapper


# ─── Proforma ──────────────────────────────────────────────────────

@_safe
def persist_proforma(deal_id: str, snap) -> None:
    """Write a ProformaSnapshot to the warehouse.

    Args:
        deal_id: Deal identifier (e.g. 'chamberlain')
        snap: ProformaSnapshot from proforma_bridge
    """
    wh = _get_wh()

    # Build annual rows
    years = []
    for yr in sorted(snap.levered_cf_by_year.keys()):
        years.append({
            'year': yr,
            'calendar_year': snap.calendar_years.get(yr),
            'noi': snap.noi_by_year.get(yr, 0),
            'debt_service': snap.debt_service_by_year.get(yr, 0),
            'capex': snap.capex_by_year.get(yr, 0),
            'non_operating': snap.non_op_by_year.get(yr, 0),
            'levered_cf': snap.levered_cf_by_year.get(yr, 0),
            'dscr': snap.dscr_by_year.get(yr, 0),
        })

    wh.store_proforma_annual(deal_id, snap.tif_scenario, years)

    # Store deal summary
    wh.store_deal_summary(deal_id, snap.tif_scenario, {
        'hold_years': snap.hold_years,
        'initial_equity': snap.initial_equity,
        'acquisition_cost_basis': snap.acquisition_cost_basis,
        'levered_irr': snap.levered_irr,
        'equity_multiple': snap.equity_multiple,
        'avg_dscr': snap.avg_dscr,
        'exit_cap_rate': snap.exit_cap_rate,
        'gross_sale_price': snap.gross_sale_price,
        'net_sale_proceeds': snap.net_sale_proceeds,
        'loan_repayment_at_sale': snap.loan_repayment_at_sale,
    })

    logger.info(f'Persisted proforma to warehouse: {deal_id}/{snap.tif_scenario}')


# ─── Distribution ──────────────────────────────────────────────────

@_safe
def persist_distribution(deal_id: str, result, tif_scenario: str = 'baseline') -> None:
    """Write a DistributionResult to the warehouse.

    Args:
        deal_id: Deal identifier
        result: DistributionResult from distribution engine
        tif_scenario: TIF scenario id
    """
    wh = _get_wh()

    # Flatten per-partner per-year rows
    partner_years = []
    for yr in result.years:
        for pid in yr.distributions_by_partner:
            partner_years.append({
                'year': yr.year,
                'calendar_year': yr.calendar_year,
                'partner_id': pid,
                'distribution': yr.distributions_by_partner.get(pid, 0),
                'pref_accrued': yr.pref_accrued_by_partner.get(pid, 0),
                'pref_paid': yr.pref_paid_by_partner.get(pid, 0),
                'cash_on_cash': yr.coc_by_partner.get(pid, 0),
            })

    wh.store_distribution_annual(deal_id, tif_scenario, partner_years)

    # Store partner dimension
    if result.partner_summary:
        wh.store_partners(deal_id, result.partner_summary)

    # Update deal summary with distribution returns
    if result.returns and result.returns.get('deal'):
        deal_ret = result.returns['deal']
        wh.store_deal_summary(deal_id, tif_scenario, {
            'deal_irr': deal_ret.get('irr'),
            'deal_em': deal_ret.get('equity_multiple'),
            'total_distributed': deal_ret.get('total_distributed'),
        })

    logger.info(f'Persisted distribution to warehouse: {deal_id}/{tif_scenario}')


# ─── Debt ──────────────────────────────────────────────────────────

@_safe
def persist_debt(deal_id: str, result, tif_scenario: str = 'baseline') -> None:
    """Write a DebtAnalysisResult to the warehouse.

    Args:
        deal_id: Deal identifier
        result: DebtAnalysisResult from debt engine
        tif_scenario: TIF scenario id
    """
    wh = _get_wh()

    # Build annual rows from amortization + DSCR + LTV + MIP
    result_dict = result.to_dict()
    amort_years = {y['year']: y for y in result_dict.get('amortization_annual', [])}
    dscr_years = {y['year']: y for y in result_dict.get('dscr_annual', [])}
    ltv_years = {y['year']: y for y in result_dict.get('ltv_annual', [])}
    mip_years = {y['year']: y for y in result_dict.get('mip_annual', [])}

    all_years = sorted(set(
        list(amort_years.keys()) +
        list(dscr_years.keys())
    ))

    rows = []
    for yr in all_years:
        a = amort_years.get(yr, {})
        d = dscr_years.get(yr, {})
        l = ltv_years.get(yr, {})
        m = mip_years.get(yr, {})
        rows.append({
            'year': yr,
            'calendar_year': a.get('calendar_year') or d.get('calendar_year'),
            'beginning_balance': a.get('beginning_balance'),
            'ending_balance': a.get('ending_balance'),
            'total_payment': a.get('total_payment'),
            'total_principal': a.get('total_principal'),
            'total_interest': a.get('total_interest'),
            'noi': d.get('noi'),
            'debt_service': d.get('debt_service'),
            'dscr': d.get('dscr'),
            'dscr_with_mip': d.get('dscr_with_mip'),
            'mip_amount': m.get('mip_amount'),
            'ltv': l.get('ltv'),
            'estimated_value': l.get('estimated_value'),
        })

    wh.store_debt_annual(deal_id, tif_scenario, rows)

    logger.info(f'Persisted debt metrics to warehouse: {deal_id}/{tif_scenario}')


# ─── TIF ───────────────────────────────────────────────────────────

@_safe
def persist_tif(deal_id: str, scenario_result, tif_scenario: str) -> None:
    """Write a TIF ScenarioResult to the warehouse.

    Args:
        deal_id: Deal identifier
        scenario_result: ScenarioResult dict from TIF engine
        tif_scenario: TIF scenario id
    """
    wh = _get_wh()

    # Annual rows
    years = scenario_result.get('years', [])
    wh.store_tif_annual(deal_id, tif_scenario, years)

    logger.info(f'Persisted TIF annual to warehouse: {deal_id}/{tif_scenario}')


@_safe
def persist_tif_comparison(deal_id: str, comparisons: list) -> None:
    """Write TIF scenario comparison results to the warehouse."""
    wh = _get_wh()
    wh.store_tif_comparison(deal_id, comparisons)
    logger.info(f'Persisted TIF comparison to warehouse: {deal_id}')


# ─── Full deal persist (all modules at once) ───────────────────────

@_safe
def persist_full_deal(deal_id: str, tif_scenario: str = 'baseline') -> None:
    """Run and persist all deal analytics for one TIF scenario.

    This is a convenience function that runs proforma → distribution →
    debt → TIF and stores everything. Useful for initial warehouse
    population or after config changes.
    """
    from modules.distribution.proforma_bridge import get_proforma_snapshot
    from modules.distribution.engine import DistributionEngine
    from modules.distribution.routes import _run_with_proforma

    # 1. Proforma
    snap = get_proforma_snapshot(tif_scenario)
    persist_proforma(deal_id, snap)

    # 2. Distribution (runs its own proforma internally)
    dist_result, _ = _run_with_proforma(tif_scenario)
    persist_distribution(deal_id, dist_result, tif_scenario)

    # 3. Debt
    try:
        from modules.debt_analysis.engine import DebtAnalysisEngine
        debt_eng = DebtAnalysisEngine()
        debt_result = debt_eng.run_analysis(tif_scenario=tif_scenario)
        persist_debt(deal_id, debt_result, tif_scenario)
    except Exception as e:
        logger.warning(f'Debt analysis unavailable for warehouse: {e}')

    # 4. TIF
    try:
        from modules.tif_analysis.engine import TIFEngine
        tif_eng = TIFEngine()
        tif_result = tif_eng.run_scenario(tif_scenario)
        persist_tif(deal_id, tif_result.to_dict() if hasattr(tif_result, 'to_dict') else tif_result, tif_scenario)
    except Exception as e:
        logger.warning(f'TIF analysis unavailable for warehouse: {e}')

    logger.info(f'Full deal persist complete: {deal_id}/{tif_scenario}')
