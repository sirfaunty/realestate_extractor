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

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = TIFEngine()
    return _engine


def register_tif_routes(app):
    """Register the tif_analysis blueprint with the Flask app."""
    app.register_blueprint(tif_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@tif_bp.route('/')
def tif_index():
    """TIF Analysis dashboard."""
    return render_template('tif_analysis.html')


# ─── API ───────────────────────────────────────────────────────────

@tif_bp.route('/api/assumptions')
def api_assumptions():
    """Return current model assumptions."""
    eng = _get_engine()
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
    eng = _get_engine()

    # Allow overrides via query params
    scenarios = {}
    for key, default_tmv in CHAMBERLAIN_SCENARIOS.items():
        param = request.args.get(key.lower().replace(' ', '_'))
        tmv = float(param) if param else default_tmv
        scenarios[key] = eng._make_flat_schedule(tmv)

    result = eng.compare_scenarios(scenarios)
    return jsonify(result)


@tif_bp.route('/api/breakeven')
def api_breakeven():
    """Run breakeven analysis."""
    eng = _get_engine()
    result = eng.breakeven_analysis()
    return jsonify(result)


@tif_bp.route('/api/sensitivity')
def api_sensitivity():
    """Run sensitivity sweep.

    Query params:
      steps — number of sweep points (default 12)
      baseline — baseline TMV (default 54866000)
    """
    eng = _get_engine()
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
    eng = _get_engine()
    tmv = request.args.get('tmv')
    growth = request.args.get('growth')

    if not tmv:
        return jsonify({'error': 'tmv parameter required'}), 400

    tmv_val = float(tmv)
    if growth:
        result = eng.run_with_growth(name, tmv_val, float(growth))
    else:
        result = eng.run_scenario(name, eng._make_flat_schedule(tmv_val))

    return jsonify(result.to_dict())
