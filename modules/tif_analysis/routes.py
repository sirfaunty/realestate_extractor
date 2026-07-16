"""
TIF Analysis module routes — scenario modeling UI + JSON API.

Routes:
  GET  /tif-analysis/                       — interactive TIF dashboard
  GET  /tif-analysis/api/scenarios          — run scenario comparison
  GET  /tif-analysis/api/breakeven          — breakeven analysis
  GET  /tif-analysis/api/sensitivity        — sensitivity sweep
  GET  /tif-analysis/api/scenario/<name>    — single scenario detail
"""

import logging
from flask import Blueprint, jsonify, request, render_template

from .engine import TIFEngine, TIFAssumptions, CHAMBERLAIN_SCENARIOS

logger = logging.getLogger(__name__)

tif_bp = Blueprint('tif_analysis', __name__, url_prefix='/tif-analysis')


from registry.deal_context import (
    deal_id_from_request as _deal_id,
    warehouse_deal_id as _warehouse_deal_id,
    deal_config as _deal_config,
)


def _get_engine(deal_id=None):
    """Build a TIF engine for the given deal. Config-driven; None config (or an
    unknown deal) yields the Chamberlain defaults, so behavior is unchanged."""
    cfg = _deal_config(deal_id, 'tif') if deal_id else None
    return TIFEngine(TIFAssumptions.from_config(cfg))


def _scenario_tmvs(deal_id):
    """Base scenario TMVs for the deal: from config if present, else the
    Chamberlain defaults."""
    cfg = _deal_config(deal_id, 'tif')
    base = (cfg or {}).get('scenarios') if cfg else None
    return base or CHAMBERLAIN_SCENARIOS


def register_tif_routes(app):
    """Register the tif_analysis blueprint with the Flask app."""
    app.register_blueprint(tif_bp)


# ─── Pages ─────────────────────────────────────────────────────────

# Engine scenario names -> stable UI slugs the template renders against.
_NAME_TO_SLUG = {
    'Current': 'current',
    'Mid': 'mid',
    'Aggressive': 'aggressive',
    'MAA Floor': 'maa_floor',
}


def _shape_scenarios_for_ui(result):
    """Reshape compare_scenarios() output into the `scenarios` object the
    dashboard consumes: keyed by UI slug, one flat record per scenario with the
    totals, deltas-vs-current, and a note-amortization series for the chart.

    The engine returns {baseline, results, comparison}; the template reads
    data.scenarios[slug]. Without this bridge the table and chart render empty.
    """
    results = result.get('results', {})
    scenarios = {}
    for comp in result.get('comparison', []):
        name = comp.get('name')
        slug = _NAME_TO_SLUG.get(name)
        if slug is None:
            continue
        res = results.get(name, {})
        years = res.get('years', []) or []
        totals = res.get('totals', {}) or {}
        nominal_net_benefit = comp.get('nominal_net_benefit', 0.0) or 0.0
        attorney_fees = comp.get('attorney_fees', 0.0) or 0.0
        scenarios[slug] = {
            'starting_tmv': years[0].get('tmv') if years else None,
            'payoff_year': comp.get('payoff_year'),
            'total_net_tif': comp.get('total_net_tif'),
            'total_note_interest': comp.get('total_note_interest'),
            'total_property_tax': totals.get('total_property_tax'),
            'tax_savings_nominal': comp.get('nominal_tax_savings'),
            'tax_savings_npv': comp.get('npv_tax_savings'),
            'tif_reduction_nominal': comp.get('nominal_tif_reduction'),
            'net_benefit_nominal': nominal_net_benefit,
            'net_benefit_npv': comp.get('npv_net_benefit'),
            'attorney_fees': attorney_fees,
            'net_benefit_after_fees': round(nominal_net_benefit - attorney_fees, 2),
            'amortization': [
                {'year': y.get('year'), 'end_balance': y.get('note_end_bal')}
                for y in years
            ],
        }
    return scenarios


def _persist_bg(func_name, *args):
    """Best-effort warehouse write, run OFF the request thread. DuckDB is single-writer
    across processes, so a warehouse held open elsewhere (e.g. an export) would block a
    write indefinitely — a try/except can't catch a hang, so we never do it inline."""
    def _run():
        try:
            import importlib
            getattr(importlib.import_module('warehouse.deal_analytics'), func_name)(*args)
        except Exception:
            pass
    import threading
    threading.Thread(target=_run, daemon=True).start()


@tif_bp.route('/')
def tif_index():
    """TIF Analysis dashboard."""
    return render_template('tif_analysis.html')


# ─── API ───────────────────────────────────────────────────────────

@tif_bp.route('/api/assumptions')
def api_assumptions():
    """Return current model assumptions."""
    eng = _get_engine(_deal_id())
    a = eng.a
    return jsonify({
        'class_rate': a.class_rate,
        'tax_capacity_rate': a.tax_capacity_rate,
        'developer_share': a.developer_share,
        'admin_holdback_pct': a.admin_holdback_pct,
        'osa_fee_pct': a.osa_fee_pct,
        'original_ntc': a.original_ntc,
        'note_principal': a.note_principal,
        'note_interest_rate': a.note_interest_rate,
        'note_start_balance': a.note_start_balance,
        'maa_floor': a.maa_floor,
        'first_pay_year': a.first_pay_year,
        'last_tif_year': a.last_tif_year,
        'discount_rate': a.discount_rate,
        'attorney_fee_pct': a.attorney_fee_pct,
        'n_years': a.n_years,
        'net_tif_multiplier': a.net_tif_multiplier,
    })


@tif_bp.route('/api/scenarios')
def api_scenarios():
    """Run the default 4-scenario comparison.

    Query params (all optional):
      current, mid, aggressive, floor — override TMV values (flat)
      discount_rate — override NPV rate (default 0.05)
    """
    deal_id = _deal_id()
    eng = _get_engine(deal_id)

    # Base scenario TMVs come from the deal's config (Chamberlain defaults if none);
    # each is still overridable via query params.
    scenarios = {}
    for key, default_tmv in _scenario_tmvs(deal_id).items():
        param = request.args.get(key.lower().replace(' ', '_'))
        tmv = float(param) if param else default_tmv
        scenarios[key] = eng._make_flat_schedule(tmv)

    result = eng.compare_scenarios(scenarios)

    # Bridge the engine output to the shape the dashboard table + chart read.
    result['scenarios'] = _shape_scenarios_for_ui(result)

    # Persist to the analytical warehouse off-thread so a locked/slow DuckDB warehouse
    # can never hang the page (genuinely fire-and-forget).
    _persist_bg('persist_tif_comparison', _warehouse_deal_id(deal_id),
                result.get('comparison', []))

    return jsonify(result)


@tif_bp.route('/api/breakeven')
def api_breakeven():
    """Run breakeven analysis."""
    eng = _get_engine(_deal_id())
    result = eng.breakeven_analysis()
    return jsonify(result)


@tif_bp.route('/api/sensitivity')
def api_sensitivity():
    """Run sensitivity sweep.

    Query params:
      steps — number of sweep points (default 12)
      baseline — baseline TMV (default 54866000)
    """
    eng = _get_engine(_deal_id())
    steps = int(request.args.get('steps', 12))
    baseline = request.args.get('baseline')
    baseline_tmv = float(baseline) if baseline else None

    result = eng.sensitivity_sweep(steps=steps, baseline_tmv=baseline_tmv)
    return jsonify(result)


@tif_bp.route('/api/scenario/<name>')
def api_scenario_detail(name):
    """Run a single named scenario and return year-by-year detail.

    Query params:
      tmv — flat TMV value (required)
      growth — annual growth rate (default 0, overrides flat tmv)
    """
    deal_id = _deal_id()
    eng = _get_engine(deal_id)
    tmv = request.args.get('tmv')
    growth = request.args.get('growth')

    if not tmv:
        return jsonify({'error': 'tmv parameter required'}), 400

    tmv_val = float(tmv)
    if growth:
        result = eng.run_with_growth(name, tmv_val, float(growth))
    else:
        result = eng.run_scenario(name, eng._make_flat_schedule(tmv_val))

    # Persist off-thread (see api_scenarios) so a locked warehouse can't hang the page.
    _persist_bg('persist_tif', _warehouse_deal_id(deal_id), result.to_dict(), name)

    return jsonify(result.to_dict())
