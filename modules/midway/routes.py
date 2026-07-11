"""
Midway / Disposition Diligence module routes.

No-code workflow (all local):
  1. The page shows the diligence summary from the warehouse (data/midway.db).
  2. POST /midway/api/generate {force} — a background job optionally re-runs the local
     extraction pipeline (OCR ingest -> abstracts -> missing docs -> PSA -> REA), then
     builds the Word Disposition Diligence Report.
  3. GET /midway/api/status/<job_id> — poll progress.
  4. GET /midway/api/download — download the report.

The engine is the standalone `midway_db` project at <repo>/midway_db/; we add it to
sys.path so its modules can be imported in-process.
"""
import os
import sys
import uuid
import logging
import datetime
import threading
import traceback

from flask import Blueprint, render_template, request, jsonify, send_file, abort

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MIDWAY_ROOT = os.path.join(_REPO_ROOT, 'midway_db')
_DATA_DIR = os.path.join(_MIDWAY_ROOT, 'data')
_WAREHOUSE = os.path.join(_DATA_DIR, 'midway.db')
if _MIDWAY_ROOT not in sys.path:
    sys.path.insert(0, _MIDWAY_ROOT)

midway_bp = Blueprint('midway', __name__, url_prefix='/midway')

_JOBS = {}
_LATEST = {'docx': None, 'summary': None}


def register_midway_routes(app):
    app.register_blueprint(midway_bp)


def _summary():
    """Read the warehouse into a compact diligence summary (or None if not built)."""
    if not os.path.exists(_WAREHOUSE):
        return None
    import sqlite3
    c = sqlite3.connect(_WAREHOUSE)
    def one(q, *a):
        try:
            return c.execute(q, a).fetchone()[0]
        except Exception:
            return 0
    tenants_abstracted = one("SELECT COUNT(DISTINCT tenant_id) FROM lease_abstract "
                             "WHERE source_page LIKE '[%'")
    facts = one("SELECT COUNT(*) FROM lease_abstract WHERE source_page LIKE '[%'")
    deals = [{'label': r[0], 'price': r[1]} for r in c.execute(
        "SELECT a.label, ft.value FROM agreement a "
        "LEFT JOIN financial_term ft ON ft.agreement_id=a.agreement_id "
        "AND ft.item='Purchase Price' ORDER BY a.agreement_id")]
    missing = [{'item': r[0], 'priority': r[1]} for r in c.execute(
        "SELECT item, priority FROM missing_document WHERE source_or_where LIKE 'auto:%' "
        "ORDER BY priority")]
    rea_total = one("SELECT COUNT(*) FROM rea_prohibited_use")
    rea_grocery = one("SELECT COUNT(*) FROM rea_prohibited_use WHERE "
                      "LOWER(prohibited_use) LIKE '%grocery%' OR "
                      "LOWER(prohibited_use) LIKE '%supermarket%'")
    c.close()
    return {
        'tenants_abstracted': tenants_abstracted, 'facts': facts,
        'deals': deals, 'missing': missing,
        'rea_prohibited': rea_total, 'rea_grocery_flag': rea_grocery,
    }


def _run_generate(job_id, force):
    job = _JOBS[job_id]

    def step(s, detail=''):
        job['step'] = s
        job['detail'] = detail

    try:
        import diligence_report
        if force or not os.path.exists(_WAREHOUSE):
            import ingest, abstract_facts, missing_docs, extract_psa, extract_rea
            step('ingesting', 'OCR + registering tenant documents (this can take minutes)…')
            ingest.run()
            step('abstracting', 'Extracting lease-abstract facts with the local model…')
            abstract_facts.run()
            step('diligence', 'Detecting missing documents…')
            missing_docs.run()
            step('psa', 'Extracting PSA deal economics…')
            extract_psa.run()
            step('rea', 'Extracting REA prohibited uses…')
            extract_rea.run()

        if not os.path.exists(_WAREHOUSE):
            raise RuntimeError('No warehouse found. Run the extraction pipeline first '
                               '(tick “re-run extraction”).')

        step('reporting', 'Generating the Disposition Diligence Report…')
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(_DATA_DIR, f'Midway_Disposition_Diligence_{ts}.docx')
        diligence_report.build(_WAREHOUSE, out)

        summary = _summary() or {}
        summary['generated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        summary['docx_name'] = os.path.basename(out)
        job.update(status='done', step='complete', detail='Report ready.', summary=summary)
        _LATEST['docx'] = out
        _LATEST['summary'] = summary
    except Exception as e:
        logger.exception('Midway generate failed')
        job.update(status='error', error=str(e), traceback=traceback.format_exc())


# ── Page ──────────────────────────────────────────────────────────────────
@midway_bp.route('/')
def midway_index():
    return render_template('midway.html', summary=_LATEST.get('summary') or _summary())


# ── API ───────────────────────────────────────────────────────────────────
@midway_bp.route('/api/summary')
def api_summary():
    return jsonify(_summary() or {})


@midway_bp.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json(silent=True) or request.form
    force = str(data.get('force') or '').lower() in ('1', 'true', 'yes', 'on')
    for jid, j in _JOBS.items():
        if j.get('status') == 'running':
            return jsonify({'job_id': jid, 'already_running': True}), 202
    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {
        'id': job_id, 'status': 'running', 'step': 'queued', 'detail': 'Starting…',
        'error': None, 'force': force,
        'started': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    threading.Thread(target=_run_generate, args=(job_id, force), daemon=True).start()
    return jsonify({'job_id': job_id})


@midway_bp.route('/api/status/<job_id>')
def api_status(job_id):
    job = _JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Unknown job'}), 404
    return jsonify({k: v for k, v in job.items() if k != 'traceback'})


@midway_bp.route('/api/download')
def api_download():
    path = _LATEST.get('docx')
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))
