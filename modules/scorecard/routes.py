"""
Scorecard module routes — market scoring UI + API.
"""

import logging
import math
from flask import Blueprint, jsonify, request, render_template

from .engine import ScorecardEngine
from .tilt_engine import ScorecardConfig

logger = logging.getLogger(__name__)


def _json_safe(obj):
    """Recursively replace non-finite floats (NaN, Infinity) with None.

    Python's json (and Flask's jsonify) emit bare ``NaN``/``Infinity``
    tokens, which are not valid JSON — browsers' JSON.parse rejects the
    whole payload. Scores can legitimately be NaN (e.g. volatility z-score
    for a market with insufficient history), so map them to null.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj

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


def _prewarm_costar_cache():
    """Populate the scoring caches in the background so the first user
    request doesn't pay the cold-compute (or the sidecar load)."""
    import os
    try:
        if not os.path.exists(COSTAR_DATA_PATH):
            return
        eng = _get_engine()
        logger.info("Pre-warming scorecard CoStar cache in background…")
        eng._get_costar_cache(COSTAR_DATA_PATH)
        logger.info("Scorecard CoStar cache warm.")

        # Also pre-warm the unfiltered ("All Markets") peer-group summary —
        # the common national view. ~60 slices, several minutes of background
        # compute, but only when the disk cache is missing/stale for the
        # current data version; otherwise this returns instantly.
        logger.info("Pre-warming peer-group summary (All Markets)…")
        eng.peer_group_summary(COSTAR_DATA_PATH, {"analysis_duration_years": 10})
        logger.info("Peer-group summary (All Markets) warm.")
    except Exception as e:
        logger.warning(f"Scorecard cache pre-warm skipped: {e}")


def register_scorecard_routes(app):
    """Register the scorecard blueprint with the Flask app."""
    app.register_blueprint(scorecard_bp)

    import threading
    threading.Thread(target=_prewarm_costar_cache, name="scorecard-prewarm",
                     daemon=True).start()


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
    return jsonify(_json_safe(eng.get_rankings(tier=tier, limit=limit)))


@scorecard_bp.route('/api/market/<market_name>')
def api_market(market_name):
    """Get market score detail."""
    eng = _get_engine()
    score = eng.get_market_score(market_name)
    if not score:
        return jsonify({'error': f'No scores for {market_name}'}), 404
    return jsonify(_json_safe(score))


@scorecard_bp.route('/api/market/<market_name>/explain')
def api_explain(market_name):
    """Get score explanation for a market."""
    eng = _get_engine()
    explanation = eng.explain_score(market_name)
    if not explanation:
        return jsonify({'error': f'No scores for {market_name}'}), 404
    return jsonify(_json_safe(explanation))


@scorecard_bp.route('/api/market/<market_name>/history')
def api_history(market_name):
    """Get score history for a market."""
    eng = _get_engine()
    return jsonify(_json_safe(eng.get_score_history(market_name)))


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
    return jsonify(_json_safe(result))


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
    return jsonify(_json_safe(results))


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
        return jsonify(_json_safe(result))
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
        return jsonify(_json_safe(result))
    except Exception as e:
        logger.error(f"CoStar composite scoring failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scorecard_bp.route('/api/demo_scores', methods=['POST'])
def api_demo_scores():
    """Score markets on the Demographics model (population, affordability,
    employment). Ported from the capactive-scorecard app."""
    import os
    eng = _get_engine()
    data = request.get_json(silent=True) or {}

    costar_file = data.pop('costar_file', COSTAR_DATA_PATH)
    if not os.path.exists(costar_file):
        return jsonify({
            'error': f'CoStar data file not found: {costar_file}',
            'hint': 'Place the CoStar quarterly Excel export at data/costar_q4_export.xlsx',
        }), 404

    try:
        result = eng.score_demographics(
            costar_file, config_overrides=data if data else None,
        )
        return jsonify(_json_safe(result))
    except Exception as e:
        logger.error(f"Demographics scoring failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scorecard_bp.route('/api/peer_group_summary', methods=['POST'])
def api_peer_group_summary():
    """Peer-group comparison across tier/unit/region slices (MF + demographics)."""
    import os
    eng = _get_engine()
    data = request.get_json(silent=True) or {}

    costar_file = data.pop('costar_file', COSTAR_DATA_PATH)
    if not os.path.exists(costar_file):
        return jsonify({
            'error': f'CoStar data file not found: {costar_file}',
            'hint': 'Place the CoStar quarterly Excel export at data/costar_q4_export.xlsx',
        }), 404

    try:
        result = eng.peer_group_summary(
            costar_file, config_overrides=data if data else None,
        )
        return jsonify(_json_safe(result))
    except Exception as e:
        logger.error(f"Peer group summary failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scorecard_bp.route('/api/all_metric_detail', methods=['POST'])
def api_all_metric_detail():
    """Rolling-window drill-down for all MF metrics in one market
    (backs the Category Data page)."""
    import os
    eng = _get_engine()
    params = request.get_json(silent=True) or {}

    costar_file = params.pop('costar_file', COSTAR_DATA_PATH)
    if not os.path.exists(costar_file):
        return jsonify({'error': f'CoStar data file not found: {costar_file}'}), 404

    try:
        from .metric_detail import build_all_metric_detail
        adr, classifications = eng._get_costar_cache(costar_file)
        inventory_tier = params.get('inventory_tier', 'All')
        region = params.get('region', 'All')
        region_type = params.get('region_type', 'general')
        _, display = eng._demo_peer_display(
            classifications, inventory_tier, region, region_type)

        dir_ov = {'net_deliveries': True} if params.get('flip_deliveries') else None
        result = build_all_metric_detail(
            adr, display,
            market=params.get('market', ''),
            duration_years=int(params.get('duration_years', 10)),
            property_class=params.get('property_class', 'All'),
            direction_overrides=dir_ov,
        )
        return jsonify(_json_safe(result))
    except Exception as e:
        logger.error(f"Metric detail failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@scorecard_bp.route('/api/all_demo_metric_detail', methods=['POST'])
def api_all_demo_metric_detail():
    """Rolling-window drill-down for all demographics metrics in one market
    (backs the Category Data page in demo mode)."""
    import os
    eng = _get_engine()
    params = request.get_json(silent=True) or {}

    costar_file = params.pop('costar_file', COSTAR_DATA_PATH)
    if not os.path.exists(costar_file):
        return jsonify({'error': f'CoStar data file not found: {costar_file}'}), 404

    try:
        from .metric_detail import build_all_demo_metric_detail
        adr, classifications = eng._get_costar_cache(costar_file)
        region = params.get('region', 'All')
        region_type = params.get('region_type', 'general')
        # Demo details use only the region as a display filter (tier fixed to All),
        # mirroring the source app.
        _, display = eng._demo_peer_display(
            classifications, 'All', region, region_type)

        dir_ov = ({'mf_inv_pop': True, 'mf_inv_pop_growth': True}
                  if params.get('flip_inv_pop') else None)
        result = build_all_demo_metric_detail(
            adr, display,
            market=params.get('market', ''),
            duration_years=int(params.get('duration_years', 10)),
            direction_overrides=dir_ov,
            property_class=params.get('property_class', 'All'),
        )
        return jsonify(_json_safe(result))
    except Exception as e:
        logger.error(f"Demo metric detail failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


def _scores_to_csv(scores_data: dict) -> str:
    """Convert scores to CSV string (ported from capactive-scorecard)."""
    import io
    import csv
    output = io.StringIO()
    writer = csv.writer(output)

    header = [
        "Rank", "Market", "MF Score", "MF Pctl",
        "S&D Raw", "S&D Adj", "S&D Rank", "S&D Pctl",
        "Rent Raw", "Rent Adj", "Rent Rank", "Rent Pctl",
    ]
    metric_keys = []
    if scores_data["markets"]:
        metric_keys = list(scores_data["markets"][0].get("metrics", {}).keys())
        for mk in metric_keys:
            header.extend([f"{mk}_signal", f"{mk}_vol", f"{mk}_cat",
                           f"{mk}_total", f"{mk}_pctl"])

    writer.writerow(header)
    for r in scores_data["markets"]:
        row = [
            r["mf_rank"], r["market"], r["mf_score"], r.get("mf_percentile", ""),
            r["ds_raw"], r["ds_adj"], r.get("ds_rank", ""), r.get("ds_percentile", ""),
            r["rent_raw"], r["rent_adj"], r.get("rent_rank", ""), r.get("rent_percentile", ""),
        ]
        for mk in metric_keys:
            m = r.get("metrics", {}).get(mk, {})
            row.extend([
                m.get("signal_z", ""), m.get("vol_z", ""),
                m.get("cat_z", ""), m.get("total_z", ""),
                m.get("percentile", ""),
            ])
        writer.writerow(row)

    return output.getvalue()


@scorecard_bp.route('/api/export/csv')
def api_export_csv():
    """Download the current MF scorecard as CSV (query params = config)."""
    import os
    from flask import Response
    eng = _get_engine()

    if not os.path.exists(COSTAR_DATA_PATH):
        return jsonify({'error': f'CoStar data file not found: {COSTAR_DATA_PATH}'}), 404

    try:
        overrides = {k: v for k, v in request.args.items()}
        property_class = overrides.pop('property_class', 'All')
        inventory_tier = overrides.pop('inventory_tier', 'All')
        result = eng.score_from_costar(
            costar_file=COSTAR_DATA_PATH,
            config_overrides=overrides if overrides else None,
            property_class=property_class,
            inventory_tier=inventory_tier,
        )
        csv_str = _scores_to_csv(result)
        return Response(
            csv_str, mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=scorecard_export.csv'},
        )
    except Exception as e:
        logger.error(f"CSV export failed: {e}", exc_info=True)
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
