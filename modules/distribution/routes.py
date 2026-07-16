"""
Distribution & Surplus Cash module routes — waterfall UI + JSON API.

Routes:
  GET  /distribution/                       — interactive dashboard
  GET  /distribution/api/assumptions        — current model assumptions
  GET  /distribution/api/waterfall          — run waterfall distribution
  GET  /distribution/api/scenarios          — TIF scenario comparison
  GET  /distribution/api/sensitivity        — CF sensitivity sweep
  GET  /distribution/api/surplus-note       — surplus cash note schedule
  GET  /distribution/api/proforma-context   — proforma data feeding distributions
"""

import logging
from flask import Blueprint, jsonify, request, render_template

from .engine import DistributionEngine, DistributionAssumptions, CHAMBERLAIN_DEFAULT_CF

logger = logging.getLogger(__name__)

distribution_bp = Blueprint('distribution', __name__, url_prefix='/distribution')


from registry.deal_context import (
    deal_id_from_request as _deal_id,
    warehouse_deal_id as _warehouse_deal_id,
    deal_config as _deal_config,
)


def _get_engine(deal_id=None):
    """Build a distribution engine for the deal. None config (or unknown deal)
    yields the Chamberlain defaults, so behavior is unchanged."""
    cfg = _deal_config(deal_id, 'distribution') if deal_id else None
    assumptions = DistributionAssumptions.from_config(cfg) if cfg else None
    return DistributionEngine(assumptions)


def _get_proforma_snapshot(tif_scenario='baseline'):
    """Try to load live proforma data; return None if unavailable."""
    try:
        from .proforma_bridge import get_proforma_snapshot
        return get_proforma_snapshot(tif_scenario)
    except Exception as e:
        logger.warning(f'Proforma bridge unavailable, using defaults: {e}')
        return None


def _run_with_proforma(tif_scenario='baseline', include_sale=True, deal_id=None):
    """Run distribution with live proforma data if available.

    Returns (DistributionResult, proforma_source_label).
    """
    eng = _get_engine(deal_id)
    snap = _get_proforma_snapshot(tif_scenario)

    if snap:
        # Live proforma data available
        cf = dict(snap.levered_cf_by_year)
        sale_proceeds = snap.net_sale_proceeds if include_sale else 0.0
        result = eng.run_distribution(
            distributable_cf=cf,
            net_sale_proceeds=sale_proceeds,
            sale_year=snap.sale_year,
        )
        result.proforma_source = 'live'
        result.tif_scenario = tif_scenario
        result.proforma_context = {
            'noi_by_year': {
                str(k): round(v, 2) for k, v in snap.noi_by_year.items()
            },
            'debt_service_by_year': {
                str(k): round(v, 2) for k, v in snap.debt_service_by_year.items()
            },
            'capex_by_year': {
                str(k): round(v, 2) for k, v in snap.capex_by_year.items()
            },
            'dscr_by_year': {
                str(k): round(v, 3) for k, v in snap.dscr_by_year.items()
            },
            'net_sale_proceeds': round(snap.net_sale_proceeds, 2),
            'gross_sale_price': round(snap.gross_sale_price, 2),
            'exit_cap_rate': snap.exit_cap_rate,
            'proforma_irr': round(snap.levered_irr, 4) if snap.levered_irr else None,
            'proforma_em': round(snap.equity_multiple, 4),
        }

        # Persist distribution to analytical warehouse (fire-and-forget)
        try:
            from warehouse.deal_analytics import persist_distribution
            persist_distribution(_warehouse_deal_id(deal_id), result, tif_scenario)
        except Exception:
            pass
        return result
    else:
        # Fallback to hardcoded defaults
        result = eng.run_distribution()
        result.proforma_source = 'defaults'
        return result


def register_distribution_routes(app):
    """Register the distribution blueprint with the Flask app."""
    app.register_blueprint(distribution_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@distribution_bp.route('/')
def distribution_index():
    """Distribution & Surplus Cash dashboard."""
    return render_template('distribution.html')


# ─── API ───────────────────────────────────────────────────────────

@distribution_bp.route('/api/assumptions')
def api_assumptions():
    """Return current model assumptions."""
    eng = _get_engine(_deal_id())
    data = eng.get_assumptions()

    # Add available TIF scenarios
    try:
        from .proforma_bridge import get_available_tif_scenarios
        data['tif_scenarios'] = get_available_tif_scenarios()
        data['proforma_available'] = True
    except Exception:
        data['tif_scenarios'] = []
        data['proforma_available'] = False

    return jsonify(data)


@distribution_bp.route('/api/waterfall')
def api_waterfall():
    """Run the waterfall distribution with live proforma data.

    Query params:
      tif_scenario — TIF scenario name (default 'baseline')
      include_sale — include Year-10 sale proceeds (default true)
    """
    tif = request.args.get('tif_scenario', 'baseline')
    include_sale = request.args.get('include_sale', 'true').lower() != 'false'

    result = _run_with_proforma(tif, include_sale, _deal_id())
    return jsonify(result.to_dict())


@distribution_bp.route('/api/scenarios')
def api_scenarios():
    """Compare distributions across all TIF scenarios.

    Runs the full waterfall for each TIF scenario (baseline, mid_appeal,
    aggressive_appeal, maa_floor) using live proforma data.
    """
    include_sale = request.args.get('include_sale', 'true').lower() != 'false'
    deal_id = _deal_id()

    try:
        from .proforma_bridge import get_available_tif_scenarios
        tif_scenarios = get_available_tif_scenarios()
    except Exception:
        # Fallback: single default scenario
        result = _run_with_proforma('baseline', include_sale, deal_id)
        return jsonify({
            'scenarios': {'Base Case': result.to_dict()},
            'scenario_names': ['Base Case'],
            'proforma_source': 'defaults',
        })

    results = {}
    for sc in tif_scenarios:
        tif_name = sc['id']
        result = _run_with_proforma(tif_name, include_sale, deal_id)
        results[sc['label']] = {
            'tif_scenario': tif_name,
            'partner_summary': result.partner_summary,
            'returns': result.returns,
            'proforma_context': result.proforma_context,
            'years': [yr.to_dict() for yr in result.years],
        }

    return jsonify({
        'scenarios': results,
        'scenario_names': [sc['label'] for sc in tif_scenarios],
        'proforma_source': 'live',
    })


@distribution_bp.route('/api/sensitivity')
def api_sensitivity():
    """Run CF sensitivity sweep using live proforma as baseline.

    Query params:
      tif_scenario — base TIF scenario (default 'baseline')
      steps — number of sweep points (default 7)
    """
    tif = request.args.get('tif_scenario', 'baseline')
    steps = int(request.args.get('steps', 7))

    snap = _get_proforma_snapshot(tif)
    eng = _get_engine(_deal_id())

    if snap:
        base_cf = dict(snap.levered_cf_by_year)
        sale_proceeds = snap.net_sale_proceeds
    else:
        base_cf = dict(CHAMBERLAIN_DEFAULT_CF)
        sale_proceeds = 0.0

    # Generate multipliers
    if steps < 3:
        steps = 3
    half = steps // 2
    multipliers = [round(1.0 + (i - half) * 0.10, 2) for i in range(steps)]

    results = []
    for mult in multipliers:
        scaled_cf = {y: v * mult for y, v in base_cf.items()}
        # Scale sale proceeds proportionally (reflects NOI-driven cap value)
        scaled_sale = sale_proceeds * mult
        result = eng.run_distribution(
            distributable_cf=scaled_cf,
            net_sale_proceeds=scaled_sale,
        )
        total_dist = sum(yr.net_distributable for yr in result.years)
        results.append({
            'multiplier': round(mult, 2),
            'label': f'{mult:.0%}',
            'total_distributable': round(total_dist, 2),
            'ka_total': round(result.final_accounts['KA'].total_distributions, 2),
            'idp_total': round(result.final_accounts['IDP'].total_distributions, 2),
            'ka_em': round(result.final_accounts['KA'].equity_multiple, 4),
            'idp_em': round(result.final_accounts['IDP'].equity_multiple, 4),
            'ka_unpaid_pref': round(result.final_accounts['KA'].unpaid_pref, 2),
            'idp_unpaid_pref': round(result.final_accounts['IDP'].unpaid_pref, 2),
        })

    return jsonify({
        'rows': results,
        'proforma_source': 'live' if snap else 'defaults',
        'tif_scenario': tif,
    })


@distribution_bp.route('/api/surplus-note')
def api_surplus_note():
    """Return surplus cash note amortization schedule."""
    eng = _get_engine(_deal_id())
    result = eng.run_distribution()
    return jsonify({
        'schedule': [s.to_dict() for s in result.surplus_note_schedule],
        'note_info': {
            'principal': eng.a.surplus_cash_note.principal,
            'rate': eng.a.surplus_cash_note.rate,
            'annual_payment': eng.a.surplus_cash_note.annual_payment,
            'source': 'Amended & Restated Surplus Cash Note',
        } if eng.a.surplus_cash_note else None,
    })


@distribution_bp.route('/api/proforma-context')
def api_proforma_context():
    """Return the proforma data feeding distributions.

    Query params:
      tif_scenario — TIF scenario name (default 'baseline')
    """
    tif = request.args.get('tif_scenario', 'baseline')
    snap = _get_proforma_snapshot(tif)

    if not snap:
        return jsonify({
            'available': False,
            'message': 'Proforma engine not available; using default CF estimates.',
        })

    return jsonify({
        'available': True,
        'snapshot': snap.to_dict(),
    })
