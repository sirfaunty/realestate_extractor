"""
Barrington / Portfolio Cash Flow module routes.

No-code workflow (operates on documents ALREADY stored in Capactive):
  1. The page lists portfolios; the user picks one (e.g., Barrington).
  2. POST /barrington/api/generate {portfolio_id} — a background job stages that
     portfolio's stored documents, runs the cash-flow engine, validates the NOI
     tie-out, and exports the Excel deliverable.
  3. GET /barrington/api/status/<job_id> — poll progress.
  4. GET /barrington/api/download — download the workbook.

The cash-flow engine is the standalone `barrington_db` package at
<repo>/barrington_db/. We add that project root to sys.path so it (and the
sibling export_excel.py) can be imported in-process. All work runs locally.
"""

import os
import sys
import uuid
import shutil
import logging
import threading
import datetime
import traceback

from flask import Blueprint, render_template, request, jsonify, send_file, abort, session

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BARR_ROOT = os.path.join(_REPO_ROOT, 'barrington_db')
_DATA_DIR = os.path.join(_BARR_ROOT, 'data')
_STAGE_ROOT = os.path.join(_BARR_ROOT, 'source_docs')
_CF_DIR = os.path.join(_STAGE_ROOT, 'staged_cash_flows')
_RR_DIR = os.path.join(_STAGE_ROOT, 'staged_rent_rolls')
if _BARR_ROOT not in sys.path:
    sys.path.insert(0, _BARR_ROOT)

barrington_bp = Blueprint('barrington', __name__, url_prefix='/barrington')

_JOBS = {}
_LATEST = {'xlsx': None, 'summary': None}


def register_barrington_routes(app):
    app.register_blueprint(barrington_bp)


def _org_db():
    """Return the Capactive org database the webapp is using (reuses its resolver)."""
    org_id = session.get('org_id', 'dev')
    wa = sys.modules.get('realestate_extractor.webapp') or sys.modules.get('webapp')
    if wa is None:                      # last resort (should already be loaded)
        import webapp as wa  # noqa
    return wa.get_org_db(org_id)


def _stage_documents(rows):
    """Copy each stored document into the cash-flow / rent-roll staging dirs,
    preserving original filenames (the engine matches assets by filename)."""
    for d in (_CF_DIR, _RR_DIR):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    ncf = nrr = 0
    for r in rows:
        fp = r['filepath']
        if not fp or not os.path.exists(fp):
            continue
        dest = _RR_DIR if r['document_type'] == 'rent_roll' else _CF_DIR
        shutil.copy2(fp, os.path.join(dest, os.path.basename(fp)))
        if r['document_type'] == 'rent_roll':
            nrr += 1
        else:
            ncf += 1
    return ncf, nrr


def _prune_old_workbooks(keep=5):
    """Keep only the most recent generated workbooks on disk."""
    import glob
    files = sorted(glob.glob(os.path.join(_BARR_ROOT, 'Barrington_Portfolio_*.xlsx')),
                   key=os.path.getmtime, reverse=True)
    for old in files[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def _run_generate(job_id, org_id, portfolio_id):
    job = _JOBS[job_id]

    def step(s, detail=''):
        job['step'] = s
        job['detail'] = detail

    try:
        step('staging', 'Gathering the portfolio’s stored documents…')
        wa = sys.modules.get('realestate_extractor.webapp') or sys.modules.get('webapp')
        db = wa.get_org_db(org_id)
        rows = db.conn.execute(
            """SELECT d.filename, d.filepath, d.document_type
                 FROM documents d JOIN properties p ON d.property_id = p.id
                WHERE p.portfolio_id = ?""", (portfolio_id,)).fetchall()
        try:
            db.close()
        except Exception:
            pass
        ncf, nrr = _stage_documents(rows)
        if not ncf or not nrr:
            raise RuntimeError('That portfolio has no cash-flow and rent-roll documents '
                               'to build from.')

        from barrington_db.build import build as barr_build
        from barrington_db.portfolio import Portfolio
        from barrington_db.validate import validate_noi
        import export_excel

        step('building', f'Building the cash-flow model from {ncf} cash flows + {nrr} rent rolls…')
        bdb = os.path.join(_DATA_DIR, 'barrington.db')
        conn, report = barr_build(_CF_DIR, _RR_DIR, db_path=bdb)

        step('validating', 'Validating NOI tie-out against source…')
        tie = [{'code': c, 'got': got, 'exp': exp, 'ok': bool(ok)}
               for c, got, exp, ok in validate_noi(conn)]

        step('summarizing', 'Rolling up the portfolio…')
        pf = Portfolio(conn)
        portfolio = []
        for s in pf.summary_table():
            cap = s.get('capital_2026', {}) or {}
            portfolio.append({
                'code': s['code'], 'name': s['name'], 'market': s['market'],
                'noi_2026': s['noi_2026'], 'occupied_sf': s['occupied_sf'],
                'tenants': s['tenants'], 'expiring_thru_2028': s['expiring_thru_2028'],
                'capital_2026': sum((cap.get(k) or 0)
                                    for k in ('CAP_BUILDING', 'CAP_TI', 'CAP_LC')),
            })
        total_noi = pf.total_noi(2026)

        step('exporting', 'Generating the Excel workbook…')
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        xlsx_path = os.path.join(_BARR_ROOT, f'Barrington_Portfolio_{ts}.xlsx')
        export_excel.build(conn, xlsx_path, 2026)
        try:
            conn.close()
        except Exception:
            pass
        _prune_old_workbooks(keep=5)

        summary = {
            'tie_out': tie, 'portfolio': portfolio, 'total_noi': total_noi,
            'counts': {
                'cashflow_facts': sum(report['cashflow'].values()) if isinstance(report.get('cashflow'), dict) else report.get('cashflow'),
                'rent_roll_rows': sum(report['rentroll'].values()) if isinstance(report.get('rentroll'), dict) else report.get('rentroll'),
                'leasing_assumptions': report.get('leasing_assumptions'),
            },
            'xlsx_name': os.path.basename(xlsx_path),
            'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        }
        job.update(status='done', step='complete', detail='Deliverable ready.', summary=summary)
        _LATEST['xlsx'] = xlsx_path
        _LATEST['summary'] = summary
    except Exception as e:
        logger.exception('Barrington generate failed')
        job.update(status='error', error=str(e), traceback=traceback.format_exc())


# ── Page ────────────────────────────────────────────────────────────────--
@barrington_bp.route('/')
def barrington_index():
    return render_template('barrington.html', latest=_LATEST.get('summary'))


# ── API ─────────────────────────────────────────────────────────────────--
@barrington_bp.route('/api/portfolios')
def api_portfolios():
    db = _org_db()
    try:
        rows = db.conn.execute("""
            SELECT pf.id, pf.name,
                   (SELECT COUNT(*) FROM documents d JOIN properties p ON d.property_id = p.id
                     WHERE p.portfolio_id = pf.id) AS n_docs,
                   (SELECT COUNT(*) FROM properties p WHERE p.portfolio_id = pf.id) AS n_props
            FROM portfolios pf ORDER BY pf.name
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        try:
            db.close()
        except Exception:
            pass


@barrington_bp.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json(silent=True) or request.form
    try:
        portfolio_id = int(data.get('portfolio_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Select a portfolio first.'}), 400
    org_id = session.get('org_id', 'dev')
    # Don't start a second build while one is already running.
    for jid, j in _JOBS.items():
        if j.get('status') == 'running':
            return jsonify({'job_id': jid, 'already_running': True}), 202
    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {
        'id': job_id, 'status': 'running', 'step': 'queued', 'detail': 'Starting...',
        'error': None, 'portfolio_id': portfolio_id,
        'started': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    