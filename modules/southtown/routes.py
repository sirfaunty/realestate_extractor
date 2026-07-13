"""
Southtown / Lease Abstraction module routes (deal-aware).

Properties come from the shared registry (<repo>/properties.json, module == 'southtown').
Each property's data lives in per-deal folders: southtown_db/data/<slug>/ and
southtown_db/source_docs/<slug>/ (lease_and_exhibits/ + returns/). The page shows a
property selector; both deliverables (Lease Abstract Compendium, Co-Tenancy & Returns
Model) run against the selected property.
"""
import os
import sys
import json
import uuid
import logging
import datetime
import threading
import traceback

from flask import Blueprint, render_template, request, jsonify, send_file, abort

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ST_ROOT = os.path.join(_REPO_ROOT, 'southtown_db')
_REGISTRY = os.path.join(_REPO_ROOT, 'properties.json')
if _ST_ROOT not in sys.path:
    sys.path.insert(0, _ST_ROOT)

southtown_bp = Blueprint('southtown', __name__, url_prefix='/southtown')

_JOBS = {}
_LATEST = {}   # slug -> {'docx','summary','returns','returns_summary'}


def register_southtown_routes(app):
    app.register_blueprint(southtown_bp)


def _properties():
    try:
        data = json.load(open(_REGISTRY, encoding='utf-8'))
        return [{'slug': p['slug'], 'label': p.get('label', p['slug']), 'note': p.get('note', '')}
                for p in data.get('properties', []) if p.get('module') == 'southtown']
    except Exception:
        logger.exception('failed to read property registry')
        return []


def _valid_slug(slug):
    return slug in {p['slug'] for p in _properties()}


def _deal_paths(slug):
    base = os.path.join(_ST_ROOT, 'data', slug)
    src = os.path.join(_ST_ROOT, 'source_docs', slug)
    return {
        'warehouse': os.path.join(base, 'lease_warehouse.db'),
        'gold': os.path.join(base, 'gold_lease_warehouse.db'),
        'lease_src': os.path.join(src, 'lease_and_exhibits'),
        'returns_src': os.path.join(src, 'returns'),
        'out_dir': base,
    }


def _list_leases(slug):
    """Lease .docx files staged for the selected property (exhibit bundles last)."""
    src = _deal_paths(slug)['lease_src']
    out = []
    if os.path.isdir(src):
        for root, _dirs, files in os.walk(src):
            for f in sorted(files):
                if f.lower().endswith('.docx') and not f.startswith('~'):
                    rel = os.path.relpath(os.path.join(root, f), src)
                    out.append({'path': rel, 'name': f, 'is_exhibits': 'exhibit' in f.lower()})
    out.sort(key=lambda d: (d['is_exhibits'], d['name']))
    return out


def _figure_recall(paths, model):
    if not os.path.exists(paths['gold']):
        return None
    try:
        import score_abstracts as S
        built = S._load(paths['warehouse'], model=model)
        gold = S._load(paths['gold'])
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


def _run_generate(job_id, slug, lease_rel, force):
    job = _JOBS[job_id]
    paths = _deal_paths(slug)

    def step(s, detail=''):
        job['step'] = s
        job['detail'] = detail

    try:
        import build_warehouse
        import abstract_lease
        import compendium_docx

        lease_path = os.path.join(paths['lease_src'], lease_rel)
        if not os.path.exists(lease_path):
            raise RuntimeError('That lease file is no longer available on this device.')
        os.makedirs(paths['out_dir'], exist_ok=True)
        wh = paths['warehouse']

        if force or not os.path.exists(wh):
            step('segmenting', 'Segmenting the lease into provisions…')
            n_prov = build_warehouse.build(lease_path, wh)['provisions']
        else:
            import sqlite3
            n_prov = sqlite3.connect(wh).execute('SELECT COUNT(*) FROM provisions').fetchone()[0]

        model = abstract_lease.OLLAMA_MODEL

        def prog(i, total, sn, heading):
            step('abstracting', f'Abstracting provision {i}/{total} — §{sn} {heading[:40]}')

        step('abstracting', 'Abstracting provisions with the local model…')
        res = abstract_lease.run(wh, model=model, missing=True, progress_cb=prog)

        step('exporting', 'Generating the Lease Abstract Compendium…')
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = os.path.join(paths['out_dir'],
                                f'Southtown_Lease_Abstract_Compendium_{slug}_{ts}.docx')
        compendium_docx.build(wh, out_path, engine=model)

        summary = {
            'slug': slug, 'provisions': n_prov, 'abstracted_now': res['ok'],
            'covered': res['covered'], 'failed': [sn for sn, _ in res['failed']],
            'figure_recall': _figure_recall(paths, model), 'model': model,
            'docx_name': os.path.basename(out_path),
            'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        }
        job.update(status='done', step='complete', detail='Compendium ready.', summary=summary)
        _LATEST.setdefault(slug, {}).update(docx=out_path, summary=summary)
    except Exception as e:
        logger.exception('Southtown generate failed')
        job.update(status='error', error=str(e), traceback=traceback.format_exc())


# ── Page ──────────────────────────────────────────────────────────────────
@southtown_bp.route('/')
def southtown_index():
    return render_template('southtown.html', properties=_properties())


# ── API ───────────────────────────────────────────────────────────────────
@southtown_bp.route('/api/properties')
def api_properties():
    return jsonify(_properties())


@southtown_bp.route('/api/leases')
def api_leases():
    slug = request.args.get('slug', '')
    if not _valid_slug(slug):
        return jsonify([])
    return jsonify(_list_leases(slug))


@southtown_bp.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json(silent=True) or request.form
    slug = (data.get('slug') or '').strip()
    lease_rel = (data.get('lease') or '').strip()
    if not _valid_slug(slug):
        return jsonify({'error': 'Select a property first.'}), 400
    if not lease_rel:
        return jsonify({'error': 'Select a lease first.'}), 400
    force = str(data.get('force') or '').lower() in ('1', 'true', 'yes', 'on')
    for jid, j in _JOBS.items():
        if j.get('status') == 'running':
            return jsonify({'job_id': jid, 'already_running': True}), 202
    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {
        'id': job_id, 'status': 'running', 'step': 'queued', 'detail': 'Starting…',
        'error': None, 'slug': slug, 'lease': lease_rel, 'force': force,
        'started': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    threading.Thread(target=_run_generate, args=(job_id, slug, lease_rel, force),
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
    slug = request.args.get('slug', '')
    path = (_LATEST.get(slug) or {}).get('docx')
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


# ── Co-Tenancy & Returns Model (fast, synchronous) ─────────────────────────
@southtown_bp.route('/api/generate_returns', methods=['POST'])
def api_generate_returns():
    data = request.get_json(silent=True) or request.form
    slug = (data.get('slug') or '').strip()
    if not _valid_slug(slug):
        return jsonify({'error': 'Select a property first.'}), 400
    paths = _deal_paths(slug)
    try:
        import returns_model as RM
        import returns_xlsx
        RM.configure(paths['returns_src'])   # point at this deal's rent-roll + proforma
        os.makedirs(paths['out_dir'], exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(paths['out_dir'],
                           f'Southtown_CoTenancy_and_Returns_Model_{slug}_{ts}.xlsx')
        returns_xlsx.build(out)

        ct = RM.cotenancy_table()
        base = next(s for s in ct if s['name'].startswith('Base'))
        y = RM.yield_on_cost()
        checks = RM.validate()
        summary = {
            'slug': slug,
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
        _LATEST.setdefault(slug, {}).update(returns=out, returns_summary=summary)
        return jsonify(summary)
    except Exception as e:
        logger.exception('Southtown returns generate failed')
        return jsonify({'error': str(e)}), 500


@southtown_bp.route('/api/download_returns')
def api_download_returns():
    slug = request.args.get('slug', '')
    path = (_LATEST.get(slug) or {}).get('returns')
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))
