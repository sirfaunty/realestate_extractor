"""
Scorecard module routes — market scoring UI + API.
"""

import logging
from flask import Blueprint, jsonify, request, render_template

from .engine import ScorecardEngine
from .tilt_engine import ScorecardConfig

logger = logging.getLogger(__name__)

scorecard_bp = Blueprint('scorecard', __name__, url_prefix='/scorecard')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from warehouse.engine import WarehouseEngine
        wh = WarehouseEngine()
        wh.connect()
        _engine = ScorecardEngine(wh)
    return _engine


def register_scorecard_routes(app):
    """Register the scorecard blueprint with the Flask app."""
    app.register_blueprint(scorecard_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@scorecard_bp.route('/')
def scorecard_index():
    """Scorecard dashboard — market rankings overview."""
    return render_template('scorecard.html')


@scorecard_bp.route('/category_data')
def scorecard_category_data():
    """Per-market metric detail page — rolling-window data."""
    return render_template('scorecard_category_data.html')


# ─── API ───────────────────────────────────────────────────────────

@scorecard_bp.route('/api/rankings')
def api_rankings():
    """Get market rankings."""
    eng = _get_engine()
    tier = request.args.get('tier')
    limit = int(request.args.get('limit', 100))
    return jsonify(eng.get_rankings(tier=tier, limit=limit))


@scorecard_bp.route('/api/market/<market_name>')
def api_market(market_name):
    """Get market score detail."""
    eng = _get_engine()
    score = eng.get_market_score(market_name)
    if not score:
        return jsonify({'error': f'No scores for {market_name}'}), 404
    return jsonify(score)


@scorecard_bp.route('/api/market/<market_name>/explain')
def api_explain(market_name):
    """Get score explanation for a market."""
    eng = _get_engine()
    explanation = eng.explain_score(market_name)
    if not explanation:
        return jsonify({'error': f'No scores for {market_name}'}), 404
    return jsonify(explanation)


@scorecard_bp.route('/api/market/<market_name>/history')
def api_history(market_name):
    """Get score history for a market."""
    eng = _get_engine()
    return jsonify(eng.get_score_history(market_name))


@scorecard_bp.route('/api/config')
def api_config():
    """Get the scoring configuration."""
    eng = _get_engine()
    return jsonify(eng.get_config())


@scorecard_bp.route('/api/score', methods=['POST'])
def api_score():
    """Trigger a scoring run using warehouse data."""
    eng = _get_engine()
    data = request.get_json(silent=True) or {}

    config = ScorecardConfig()
    if 'ds_weight' in data:
        config.ds_weight = float(data['ds_weight'])
    if 'occ_weight' in data:
        config.occ_weight = float(data['occ_weight'])
    if 'rg_weight' in data:
        config.rg_weight = float(data['rg_weight'])
    if 'analysis_duration' in data:
        config.analysis_duration_years = int(data['analysis_duration'])

    result = eng.score_from_warehouse(config)
    return jsonify(result)


@scorecard_bp.route('/api/scenario', methods=['POST'])
def api_scenario():
    """Run scenario comparison for a market."""
    eng = _get_engine()
    data = request.get_json(silent=True) or {}
    market = data.get('market')
    scenarios = data.get('scenarios', [])

    if not market:
        return jsonify({'error': 'market required'}), 400
    if not scenarios:
        return jsonify({'error': 'scenarios required'}), 400

    results = eng.compare_scenarios(market, scenarios)
    return jsonify(results)


# ─── CoStar Data Pipeline Routes ─────────────────────────────────

COSTAR_DATA_PATH = 'data/costar_q4_export.xlsx'


@scorecard_bp.route('/api/score/costar', methods=['POST'])
def api_score_costar():
    """Score markets using the full CoStar data pipeline.

    Optional JSON body: config overrides (ds_weight, occ_weight, etc.),
    property_class, inventory_tier, costar_file (override default path).
    """
    import os
    eng = _get_engine()
    data = request.get_json(silent=True) or {}

    costar_file = data.pop('costar_file', COSTAR_DATA_PATH)
    if not os.path.exists(costar_file):
        return jsonify({
            'error': f'CoStar data file not found: {costar_file}',
            'hint': 'Place the CoStar quarterly Excel export at data/costar_q4_export.xlsx',
        }), 404

    property_class = data.pop('property_class', 'All')
    inventory_tier = data.pop('inventory_tier', 'All')

    try:
        result = eng.score_from_costar(
            costar_file=costar_file,
            config_overrides=data if data else None,
            property_class=property_class,
            inventory_tier=inventory_tier,
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"CoStar scoring failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scorecard_bp.route('/api/score/costar/composite', methods=['POST'])
def api_score_costar_composite():
    """Score markets using multi-tier composite scoring from CoStar data.

    Scores each property class tier independently (All, 4&5 Star, 3 Star,
    1&2 Star), then tier-weights them into a final composite rank.
    """
    import os
    eng = _get_engine()
    data = request.get_json(silent=True) or {}

    costar_file = data.pop('costar_file', COSTAR_DATA_PATH)
    if not os.path.exists(costar_file):
        return jsonify({
            'error': f'CoStar data file not found: {costar_file}',
            'hint': 'Place the CoStar quarterly Excel export at data/costar_q4_export.xlsx',
        }), 404

    inventory_tier = data.pop('inventory_tier', 'All')

    try:
        result = eng.score_from_costar_composite(
            costar_file=costar_file,
            config_overrides=data if data else None,
            inventory_tier=inventory_tier,
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"CoStar composite scoring failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scorecard_bp.route('/api/data-source')
def api_data_source():
    """Check which data sources are available for scoring."""
    import os
    costar_available = os.path.exists(COSTAR_DATA_PATH)
    eng = _get_engine()
    warehouse_markets = len(eng._get_scoreable_markets())

    return jsonify({
        'costar': {
            'available': costar_available,
            'path': COSTAR_DATA_PATH if costar_available else None,
        },
        'warehouse': {
            'available': warehouse_markets > 0,
            'scoreable_markets': warehouse_markets,
        },
        'recommended': 'costar' if costar_available else 'warehouse',
    })
