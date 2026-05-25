"""
Debt & Loan Analysis module routes — dashboard + JSON API.

Routes:
  GET  /debt/                         — interactive dashboard
  GET  /debt/api/summary              — debt position summary
  GET  /debt/api/amortization         — amortization schedule (annual)
  GET  /debt/api/amortization/monthly — amortization schedule (monthly)
  GET  /debt/api/dscr                 — DSCR analysis by year
  GET  /debt/api/mip                  — MIP schedule
  GET  /debt/api/ltv                  — LTV tracking
  GET  /debt/api/payoff               — payoff / prepayment analysis
  GET  /debt/api/full                 — full analysis (all data)
  GET  /debt/api/refi-scenario        — refinance scenario comparison
"""

import logging
from flask import Blueprint, jsonify, request, render_template

from .engine import DebtAnalysisEngine

logger = logging.getLogger(__name__)

debt_bp = Blueprint('debt_analysis', __name__, url_prefix='/debt')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = DebtAnalysisEngine()
    return _engine


def _get_proforma_data(tif_scenario='baseline'):
    """Try to load live proforma data for debt analysis."""
    try:
        from modules.distribution.proforma_bridge import get_proforma_snapshot
        snap = get_proforma_snapshot(tif_scenario)
        return {
            'noi_by_year': dict(snap.noi_by_year),
            'debt_service_by_year': dict(snap.debt_service_by_year),
            'calendar_years': dict(snap.calendar_years),
            'exit_cap_rate': snap.exit_cap_rate or 0.055,
            'hold_years': snap.hold_years,
            'source': 'live',
        }
    except Exception as e:
        logger.warning(f'Proforma bridge unavailable for debt analysis: {e}')
        return None


def _run_full_analysis(tif_scenario='baseline'):
    """Run debt analysis with live proforma data if available."""
    eng = _get_engine()
    pf = _get_proforma_data(tif_scenario)

    if pf:
        result = eng.run_analysis(
            noi_by_year=pf['noi_by_year'],
            debt_service_by_year=pf['debt_service_by_year'],
            calendar_years=pf['calendar_years'],
            exit_cap_rate=pf['exit_cap_rate'],
            hold_years=pf['hold_years'],
        )
        result.proforma_source = 'live'
        result.tif_scenario = tif_scenario

        # Persist to analytical warehouse (fire-and-forget)
        try:
            from warehouse.deal_analytics import persist_debt
            persist_debt('chamberlain', result, tif_scenario)
        except Exception:
            pass
    else:
        result = eng.run_analysis()
        result.proforma_source = 'defaults'

    return result


def register_debt_routes(app):
    """Register the debt analysis blueprint."""
    app.register_blueprint(debt_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@debt_bp.route('/')
def debt_index():
    """Debt & Loan Analysis dashboard."""
    return render_template('debt_analysis.html')


# ─── API ───────────────────────────────────────────────────────────

@debt_bp.route('/api/summary')
def api_summary():
    """Return debt position summary."""
    tif = request.args.get('tif_scenario', 'baseline')
    result = _run_full_analysis(tif)
    return jsonify({
        'summary': result.summary.to_dict(),
        'proforma_source': result.proforma_source,
    })


@debt_bp.route('/api/amortization')
def api_amortization():
    """Return annual amortization schedule."""
    tif = request.args.get('tif_scenario', 'baseline')
    result = _run_full_analysis(tif)
    return jsonify({
        'schedule': [a.to_dict() for a in result.amortization_annual],
        'proforma_source': result.proforma_source,
    })


@debt_bp.route('/api/amortization/monthly')
def api_amortization_monthly():
    """Return monthly amortization schedule.

    Query params:
      year — filter to a specific proforma year (1-10)
    """
    tif = request.args.get('tif_scenario', 'baseline')
    year_filter = request.args.get('year', type=int)
    result = _run_full_analysis(tif)

    monthly = result.amortization_monthly
    if year_filter:
        start = (year_filter - 1) * 12
        end = year_filter * 12
        monthly = monthly[start:end]

    return jsonify({
        'schedule': [m.to_dict() for m in monthly],
        'proforma_source': result.proforma_source,
    })


@debt_bp.route('/api/dscr')
def api_dscr():
    """Return DSCR analysis by year."""
    tif = request.args.get('tif_scenario', 'baseline')
    result = _run_full_analysis(tif)

    # Summary stats
    dscrs = [d.dscr for d in result.dscr_by_year]
    min_dscr = min(dscrs) if dscrs else 0
    avg_dscr = sum(dscrs) / len(dscrs) if dscrs else 0
    breaches = sum(1 for d in result.dscr_by_year if d.covenant_status == 'breach')

    return jsonify({
        'years': [d.to_dict() for d in result.dscr_by_year],
        'min_dscr': round(min_dscr, 3),
        'avg_dscr': round(avg_dscr, 3),
        'breach_count': breaches,
        'proforma_source': result.proforma_source,
    })


@debt_bp.route('/api/mip')
def api_mip():
    """Return MIP schedule."""
    tif = request.args.get('tif_scenario', 'baseline')
    result = _run_full_analysis(tif)

    total_mip = sum(m.mip_amount for m in result.mip_by_year)
    return jsonify({
        'schedule': [m.to_dict() for m in result.mip_by_year],
        'total_mip': round(total_mip, 2),
        'mip_rate': result.summary.mip_rate if result.summary else 0.0035,
        'proforma_source': result.proforma_source,
    })


@debt_bp.route('/api/ltv')
def api_ltv():
    """Return LTV tracking over hold period."""
    tif = request.args.get('tif_scenario', 'baseline')
    result = _run_full_analysis(tif)
    return jsonify({
        'years': [l.to_dict() for l in result.ltv_by_year],
        'proforma_source': result.proforma_source,
    })


@debt_bp.route('/api/payoff')
def api_payoff():
    """Return payoff / prepayment analysis."""
    tif = request.args.get('tif_scenario', 'baseline')
    result = _run_full_analysis(tif)
    return jsonify({
        'scenarios': [p.to_dict() for p in result.payoff_scenarios],
        'proforma_source': result.proforma_source,
    })


@debt_bp.route('/api/full')
def api_full():
    """Return complete debt analysis."""
    tif = request.args.get('tif_scenario', 'baseline')
    result = _run_full_analysis(tif)
    return jsonify(result.to_dict())


@debt_bp.route('/api/refi-scenario')
def api_refi_scenario():
    """Run a refinance scenario comparison.

    Query params:
      refi_year — year of refinance event (default 5)
      new_principal — new loan amount (default 30000000)
      new_rate — new rate (default 0.06)
      new_term — new term in months (default 60)
      new_amort — new amort in months (default 360)
      new_io — IO period in months (default 24)
      tif_scenario — TIF scenario for NOI data
    """
    tif = request.args.get('tif_scenario', 'baseline')
    refi_year = request.args.get('refi_year', 5, type=int)
    new_principal = request.args.get('new_principal', 30_000_000, type=float)
    new_rate = request.args.get('new_rate', 0.06, type=float)
    new_term = request.args.get('new_term', 60, type=int)
    new_amort = request.args.get('new_amort', 360, type=int)
    new_io = request.args.get('new_io', 24, type=int)

    eng = _get_engine()
    pf = _get_proforma_data(tif)

    noi = pf['noi_by_year'] if pf else None
    hold = pf['hold_years'] if pf else 10

    scenario = eng.run_refi_scenario(
        refi_year=refi_year,
        new_principal=new_principal,
        new_rate=new_rate,
        new_term_months=new_term,
        new_amort_months=new_amort,
        new_io_months=new_io,
        noi_by_year=noi,
        hold_years=hold,
    )
    scenario['proforma_source'] = 'live' if pf else 'defaults'
    return jsonify(scenario)


@debt_bp.route('/api/scenarios')
def api_debt_scenarios():
    """Compare debt metrics across all TIF scenarios."""
    try:
        from modules.distribution.proforma_bridge import get_available_tif_scenarios
        tif_scenarios = get_available_tif_scenarios()
    except Exception:
        result = _run_full_analysis('baseline')
        return jsonify({
            'scenarios': {'Base Case': {
                'summary': result.summary.to_dict(),
                'dscr': [d.to_dict() for d in result.dscr_by_year],
            }},
            'proforma_source': 'defaults',
        })

    results = {}
    for sc in tif_scenarios:
        result = _run_full_analysis(sc['id'])
        dscrs = [d.dscr for d in result.dscr_by_year]
        results[sc['label']] = {
            'tif_scenario': sc['id'],
            'avg_dscr': round(sum(dscrs) / len(dscrs), 3) if dscrs else 0,
            'min_dscr': round(min(dscrs), 3) if dscrs else 0,
            'dscr_by_year': [d.to_dict() for d in result.dscr_by_year],
            'ltv_by_year': [l.to_dict() for l in result.ltv_by_year],
        }

    return jsonify({
        'scenarios': results,
        'scenario_names': [sc['label'] for sc in tif_scenarios],
        'proforma_source': 'live',
    })
