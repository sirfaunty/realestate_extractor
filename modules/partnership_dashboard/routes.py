"""
Partnership Dashboard module routes — executive summary UI + JSON API.

Routes:
  GET  /partnership/                        — executive dashboard page
  GET  /partnership/api/dashboard           — full dashboard (all scenarios)
  GET  /partnership/api/scenario/<name>     — single scenario detail
  GET  /partnership/api/comparison          — cross-scenario comparison only
  GET  /partnership/api/export/docx         — download investor report (.docx)
"""

import json
import logging
import tempfile
from flask import Blueprint, jsonify, request, render_template, send_file

from .engine import PartnershipDashboardEngine

logger = logging.getLogger(__name__)

partnership_bp = Blueprint('partnership_dashboard', __name__,
                           url_prefix='/partnership')


from registry.deal_context import (
    deal_id_from_request as _deal_id,
    deal_label as _deal_label,
)


def _get_engine(deal_id=None):
    """Build a partnership dashboard engine bound to the selected deal. The engine
    threads the deal's config into its distribution/debt sub-engines; an unknown
    deal or missing config yields the seeded defaults."""
    return PartnershipDashboardEngine(deal_id)


def register_partnership_routes(app):
    """Register the partnership dashboard blueprint."""
    app.register_blueprint(partnership_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@partnership_bp.route('/')
def partnership_index():
    """Partnership Dashboard — executive summary."""
    return render_template('partnership_dashboard.html')


# ─── API ───────────────────────────────────────────────────────────

@partnership_bp.route('/api/dashboard')
def api_dashboard():
    """Return the full dashboard across all TIF scenarios."""
    eng = _get_engine(_deal_id())
    result = eng.build_dashboard()
    return jsonify(result.to_dict())


@partnership_bp.route('/api/scenario/<name>')
def api_scenario(name):
    """Return dashboard data for a single TIF scenario.

    Args:
        name: TIF scenario id (baseline, mid_appeal, aggressive_appeal, maa_floor)
    """
    eng = _get_engine(_deal_id())

    # Map common names to labels
    label_map = {
        'baseline': 'Baseline',
        'mid_appeal': 'Mid Appeal',
        'aggressive_appeal': 'Aggressive Appeal',
        'maa_floor': 'Maa Floor',
    }
    label = label_map.get(name, name.replace('_', ' ').title())

    try:
        result = eng.build_scenario(name, label)
        return jsonify(result.to_dict())
    except Exception as e:
        logger.error(f'Failed to build scenario {name}: {e}')
        return jsonify({'error': str(e)}), 500


@partnership_bp.route('/api/comparison')
def api_comparison():
    """Return just the cross-scenario comparison table."""
    eng = _get_engine(_deal_id())
    result = eng.build_dashboard()
    return jsonify({
        'comparison': result.comparison,
        'scenario_names': result.scenario_names,
    })


@partnership_bp.route('/api/market-context')
def api_market_context():
    """Return market cap rates and rent benchmarks for deal validation."""
    eng = _get_engine(_deal_id())
    market = request.args.get('market', 'Minneapolis')
    context = eng.get_market_context(market)
    return jsonify(context)


@partnership_bp.route('/api/export/docx')
def api_export_docx():
    """Generate and download an investor report as .docx.

    Query params:
      scenario — primary TIF scenario to feature (default: baseline)
    """
    primary = request.args.get('scenario', 'baseline')
    eng = _get_engine(_deal_id())
    result = eng.build_dashboard()
    data = result.to_dict()

    # Find the matching scenario name for the primary scenario ID
    for name, sc in data['scenarios'].items():
        if sc['tif_scenario'] == primary:
            data['_primary_scenario'] = name
            break

    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
        output_path = tmp.name

    try:
        from .report_docx import build_investor_report
        build_investor_report(data, output_path)
        logger.info(f'Report generated: {output_path}')

        from datetime import datetime
        import re
        timestamp = datetime.now().strftime('%Y%m%d')
        safe_label = re.sub(r'[^A-Za-z0-9]+', '_', _deal_label(_deal_id())).strip('_') or 'Deal'
        filename = f'{safe_label}_Investor_Report_{timestamp}.docx'

        return send_file(
            output_path,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        logger.error(f'Report export error: {e}')
        return jsonify({'error': str(e)}), 500
