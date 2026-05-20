"""
Office inventory module routes — office property Z-score UI + API.
"""

import logging
from flask import Blueprint, jsonify, request, render_template

from .engine import OfficeEngine, MEASURES, LENSES, MEASURE_LABELS, LENS_LABELS

logger = logging.getLogger(__name__)

office_bp = Blueprint('office', __name__, url_prefix='/office')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = OfficeEngine()
    return _engine


def register_office_routes(app):
    """Register the office blueprint with the Flask app."""
    app.register_blueprint(office_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@office_bp.route('/')
def office_index():
    """Office inventory explorer — rankings overview."""
    return render_template('office.html')


@office_bp.route('/property/<int:pid>')
def office_property_detail(pid):
    """Office property detail page."""
    return render_template('office.html', detail_pid=pid)


# ─── API ───────────────────────────────────────────────────────────

@office_bp.route('/api/properties')
def api_properties():
    """List properties with filters, sorting, pagination."""
    eng = _get_engine()

    filters = {}
    bc = request.args.get('building_class')
    if bc:
        filters['building_class'] = bc
    sub = request.args.get('submarket')
    if sub:
        filters['submarket'] = sub
    status = request.args.get('status')
    if status:
        filters['building_status'] = status

    sort = request.args.get('sort', 'z_score')
    order = request.args.get('order', 'desc')
    limit = min(int(request.args.get('limit', 100)), 500)
    offset = int(request.args.get('offset', 0))
    search = request.args.get('search', '').strip() or None
    measure = request.args.get('measure', 'rent_sf_yr_mid')
    lens = request.args.get('lens', 'status')

    # Validate measure/lens
    if measure not in MEASURES:
        measure = 'rent_sf_yr_mid'
    if lens not in LENSES:
        lens = 'status'

    result = eng.get_properties(
        filters=filters, sort=sort, order=order,
        limit=limit, offset=offset, search=search,
        measure=measure, lens=lens)
    return jsonify(result)


@office_bp.route('/api/property/<int:pid>')
def api_property(pid):
    """Single property detail with Z-scores."""
    eng = _get_engine()
    prop = eng.get_property(pid)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404
    return jsonify(prop)


@office_bp.route('/api/property/<int:pid>/tenants')
def api_property_tenants(pid):
    """Tenants for a specific property."""
    eng = _get_engine()
    tenants = eng.get_tenants(pid)
    return jsonify(tenants)


@office_bp.route('/api/stats')
def api_stats():
    """Summary statistics."""
    eng = _get_engine()
    stats = eng.get_stats()
    return jsonify(stats)


@office_bp.route('/api/peer-health')
def api_peer_health():
    """Peer cell health table."""
    eng = _get_engine()
    health = eng.get_peer_health()
    return jsonify(health)


@office_bp.route('/api/z-distribution')
def api_z_distribution():
    """Z-score distribution for a measure/lens."""
    eng = _get_engine()
    measure = request.args.get('measure', 'rent_sf_yr_mid')
    lens = request.args.get('lens', 'status')
    building_class = request.args.get('building_class')

    if measure not in MEASURES:
        measure = 'rent_sf_yr_mid'
    if lens not in LENSES:
        lens = 'status'

    result = eng.get_z_distribution(measure, lens, building_class)
    return jsonify(result)


@office_bp.route('/api/geo-panel')
def api_geo_panel():
    """Geo panel time series."""
    eng = _get_engine()
    measure = request.args.get('measure')
    source = request.args.get('source')
    limit = min(int(request.args.get('limit', 500)), 2000)
    rows = eng.get_geo_panel(measure=measure, source=source, limit=limit)
    return jsonify(rows)


@office_bp.route('/api/meta')
def api_meta():
    """Return measures, lenses, and labels for the UI."""
    return jsonify({
        'measures': MEASURES,
        'lenses': LENSES,
        'measure_labels': MEASURE_LABELS,
        'lens_labels': LENS_LABELS,
    })
