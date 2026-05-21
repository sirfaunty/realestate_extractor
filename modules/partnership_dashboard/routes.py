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
import subprocess
import tempfile
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template, send_file

from .engine import PartnershipDashboardEngine

logger = logging.getLogger(__name__)

partnership_bp = Blueprint('partnership_dashboard', __name__,
                           url_prefix='/partnership')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = PartnershipDashboardEngine()
    return _engine


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
    eng = _get_engine()
    result = eng.build_dashboard()
    return jsonify(result.to_dict())


@partnership_bp.route('/api/scenario/<name>')
def api_scenario(name):
    """Return dashboard data for a single TIF scenario.

    Args:
        name: TIF scenario id (baseline, mid_appeal, aggressive_appeal, maa_floor)
    """
    eng = _get_engine()

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
    eng = _get_engine()
    result = eng.build_dashboard()
    return jsonify({
        'comparison': result.comparison,
        'scenario_names': result.scenario_names,
    })


@partnership_bp.route('/api/export/docx')
def api_export_docx():
    """Generate and download an investor report as .docx.

    Query params:
      scenario — primary TIF scenario to feature (default: baseline)
    """
    primary = request.args.get('scenario', 'baseline')
    eng = _get_engine()
    result = eng.build_dashboard()
    data = result.to_dict()

    # Find the matching scenario name for the primary scenario ID
    for name, sc in data['scenarios'].items():
        if sc['tif_scenario'] == primary:
            data['_primary_scenario'] = name
            break

    # Locate the generator script
    script = Path(__file__).parent / 'generate_report.js'
    # Find node_modules — check common locations
    node_modules = None
    for candidate in [
        Path(__file__).parent / 'node_modules',
        Path(__file__).parent.parent.parent / 'node_modules',
    ]:
        if candidate.exists():
            node_modules = candidate
            break

    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
        output_path = tmp.name

    try:
        env = {}
        if node_modules:
            env['NODE_PATH'] = str(node_modules)

        proc = subprocess.run(
            ['node', str(script), output_path],
            input=json.dumps(data),
            capture_output=True,
            text=True,
            timeout=30,
            env={**__import__('os').environ, **env},
        )

        if proc.returncode != 0:
            logger.error(f'Report generation failed: {proc.stderr}')
            return jsonify({'error': 'Report generation failed', 'detail': proc.stderr}), 500

        logger.info(f'Report generated: {proc.stdout.strip()}')

        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d')
        filename = f'Chamberlain_Investor_Report_{timestamp}.docx'

        return send_file(
            output_path,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename,
        )

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Report generation timed out'}), 504
    except Exception as e:
        logger.error(f'Report export error: {e}')
        return jsonify({'error': str(e)}), 500
