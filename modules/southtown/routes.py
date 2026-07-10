"""
Southtown / Lease Abstraction module routes.

No-code workflow (all local):
  1. The page lists lease .docx files staged under southtown_db/source_docs/.
  2. POST /southtown/api/generate {lease, force} — a background job segments the
     lease into provisions, abstracts each provision with the local model (reusing
     existing abstracts unless force=1), and builds the Word compendium.
  3. GET /southtown/api/status/<job_id> — poll progress (per-provision).
  4. GET /southtown/api/download — download the compendium.

The engine is the standalone `southtown_db` project at <repo>/southtown_db/. We add
that directory to sys.path so its modules can be imported in-process.
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
_ST_ROOT = os.path.join(_REPO_ROOT, 'southtown_db')
_DATA_DIR = os.path.join(_ST_ROOT, 'data')
_SRC_DIR = os.path.join(_ST_ROOT, 'source_docs')
_WAREHOUSE = os.path.join(_DATA_DIR, 'lease_warehouse.db')
_GOLD = os.path.join(_DATA_DIR, 'gold_lease_warehouse.db')
if _ST_ROOT not in sys.path:
    sys.path.insert(0, _ST_ROOT)

southtown_bp = Blueprint('southtown', __name__, url_prefix='/southtown')

_JOBS = {}
_LATEST = {'docx': None, 'summary': None, 'returns': None, 'returns_summary': None}


def register_southtown_routes(app):
    app.register_blueprint(southtown_bp)


def _list_leases():
    """Lease .docx files staged for local processing (excludes exhibit bundles)."""
    out = []
    for root, _dirs, files in os.walk(_SRC_DIR):
        for f in sorted(files):
            if f.lower().endswith('.docx') and not f.startswith('~'):
                rel = os.path.relpath(os.path.join(root, f), _SRC_DIR)
                is_exhibits = 'exhibit' in f.lower()
                out.append({'path': rel, 'name': f, 'is_exhibits': is_exhibits})
    # Put likely lease bodies first, exhibit bundles last
    out.sort(key=lambda d: (d['is_exhibits'], d['name']))
    return out


def _figure_recall(model):
    """Optional quality read: figure recall vs. the gold warehouse, if present."""
    if not os.path.exists(_GOLD):
        return None
    try:
        import score_abstracts as S
        built = S._load(_WAREHOUSE, model=model)
        gold = S._load(_GOLD)
        tiers = {}
        for tier in S.TIERS:
            rs = []
            for (sn, t), g in gold.items():
                if t != tier:
                    continue
                b = built.get((sn, t))
                if not b:
                    continue
                gf = S._figures(g)
                rs.append(len(gf & S._figures(b)) / len(gf) if gf else 1.0)
            if rs:
                tiers[tier] = round(100 * sum(rs) / len(rs))
        return tiers or None
    except Exception:
        logger.exception('figure recall failed')
        return None


def _run_generate(job_id, lease_rel, force):
    job = _JOBS[job_id]

    def step(s, detail=''):
        job['step'] = s
        job['detail'] = detail

    try:
        import build_warehouse
        import abstract_lease
        import compendium_docx

        lease_path = os.path.join(_SRC_DIR, lease_rel)
        if not os.path.exists(lease_path):
            raise RuntimeError('That lease file is no longer available on this device.')

        # 1. Segment -> warehouse. Rebuild only if missing or forced (rebuild wipes abstracts).
        if force or not os.path.exists(_WAREHOUSE):
            step('segmenting', 'Segmenting the lease into provisions…')
            rep = build_warehouse.build(lease_path, _WAREHOUSE)
            n_prov = rep['provisions']
        else:
            import sqlite3
            n_prov = sqlite3.connect(_WAREHOUSE).execute(
                'SELECT COUNT(*) FROM provisions').fetchone()[0]

        # 2. Abstract every provision still missing abstracts (first run does all).
        model = abstract_lease.OLLAMA_MODEL

        def prog(i, total, sn, heading):
            step('abstracting', f'Abstracting provision {i}/{total} — §{sn} {heading[:40]}')

        step('abstracting', 'Abstracting provisions with the local model…')
        res = abstract_lease.run(_WAREHOUSE, model=model, missing=True, progress_cb=prog)

        # 3. Build the Word compendium.
        step('exporting', 'Generating the Lease Abstract Compendium…')
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = os.path.join(_DATA_DIR, f'Southtown_Lease_Abstract_Compendium_{ts}.docx')
        compendium_docx.build(_WAREHOUSE, out_path, engine=model)

        summary = {
            'provisions': n_prov,
            'abstracted_now': res['ok'],
            'covered': res['covered'],
            'failed': [sn for sn, _ in res['failed']],
            'figure_recall': _figure_recall(model),
            'model': model,
            'docx_name': os.path.basename(out_path),
            'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        }
        job.update(status='done', step='complete', detail='Compendium ready.', summary=summary)
        _LATEST['docx'] = out_path
        _LATEST['summary'] = summary
    except Exception as e:
        logger.exception('Southtown generate failed')
        job.update(status='error', error=str(e), traceback=traceback.format_exc())


# ── Page ──────────────────────────────────────────────────────────────────
@southtown_bp.route('/')
def southtown_index():
    return render_template('southtown.html', latest=_LATEST.get('summary'),
                           latest_returns=_LATEST.get('returns_summary'))


# ── API ───────────────────────────────────────────────────────────────────
@southtown_bp.route('/api/leases')
def api_leases():
    return jsonify(_list_leases())


@southtown_bp.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json(silent=True) or request.form
    lease_rel = (data.get('lease') or '').strip()
    if not lease_rel:
        return jsonify({'error': 'Select a lease first.'}), 400
    force = str(data.get('force') or '').lower() in ('1', 'true', 'yes', 'on')
    for jid, j in _JOBS.items():
        if j.get('status') == 'running':
            return jsonify({'job_id': jid, 'already_running': True}), 202
    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {
        'id': job_id, 'status': 'running', 'step': 'queued', 'detail': 'Starting…',
        'error': None, 'lease': lease_rel, 'force': force,
        'started': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    threading.Thread(target=_run_generate, args=(job_id, lease_rel, force),
                     daemon=True).start()
    return jsonify({'job_id': job_id})


@southtown_bp.route('/api/status/<job_id>')
def api_status(job_id):
    job = _JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Unknown job'}), 404
    return jsonify({k: v for k, v in job.items() if k != 'traceback'})


@southtown_bp.route('/api/download')
def api_download():
    path = _LATEST.get('docx')
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


# ── Co-Tenancy & Returns Model (fast, synchronous — no lease warehouse needed) ──
@southtown_bp.route('/api/generate_returns', methods=['POST'])
def api_generate_returns():
    try:
        import returns_model as RM
        import returns_xlsx
        os.makedirs(_DATA_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(_DATA_DIR, f'Southtown_CoTenancy_and_Returns_Model_{ts}.xlsx')
        returns_xlsx.build(out)

        ct = RM.cotenancy_table()
        base = next(s for s in ct if s['name'].startswith('Base'))
        y = RM.yield_on_cost()
        checks = RM.validate()
        summary = {
            'base_occupancy': base['occupancy'], 'base_pass': base['pass'],
            'yoc_all_in': y['all_in']['yoc'], 'yoc_cash': y['cash']['yoc'],
            'tpc': RM.total_project_cost(),
            'tie_out_pass': all(ok for *_, ok in checks), 'tie_out_n': len(checks),
            'roster_source': RM.ROSTER_SOURCE, 'uses_source': RM.USES_SOURCE,
            'scenarios': [{'name': s['name'], 'occ': s['occupancy'], 'pass': s['pass']}
                          for s in ct],
            'xlsx_name': os.path.basename(out),
            'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        }
        _LATEST['returns'] = out
        _LATEST['returns_summary'] = summary
        return jsonify(summary)
    except Exception as e:
        logger.exception('Southtown returns generate failed')
        return jsonify({'error': str(e)}), 500


@southtown_bp.route('/api/download_returns')
def api_download_returns():
    path = _LATEST.get('returns')
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))
