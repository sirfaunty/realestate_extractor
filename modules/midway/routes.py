"""
Midway / Disposition Diligence module routes (deal-aware).

Properties come from the shared registry (<repo>/properties.json, module == 'midway').
Each property's data lives in a per-deal folder: midway_db/data/<slug>/midway.db and
midway_db/source_docs/<slug>/. The page shows a property selector; reports run against
the selected deal.

Report generation builds from the (already-extracted) warehouse and is fast. The heavy
OCR + extraction pipeline is a local CLI job (see midway_db/README.md), not a web
request — but the background job supports it via force= for completeness.
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
_MIDWAY_ROOT = os.path.join(_REPO_ROOT, 'midway_db')
_REGISTRY = os.path.join(_REPO_ROOT, 'properties.json')
if _MIDWAY_ROOT not in sys.path:
    sys.path.insert(0, _MIDWAY_ROOT)

midway_bp = Blueprint('midway', __name__, url_prefix='/midway')

_JOBS = {}
_LATEST = {}   # slug -> {'docx': path, 'summary': {...}}


def register_midway_routes(app):
    app.register_blueprint(midway_bp)


def _properties():
    try:
        data = json.load(open(_REGISTRY, encoding='utf-8'))
        return [{'slug': p['slug'], 'label': p.get('label', p['slug']), 'note': p.get('note', '')}
                for p in data.get('properties', []) if p.get('module') == 'midway']
    except Exception:
        logger.exception('failed to read property registry')
        return []


def _valid_slug(slug):
    return slug in {p['slug'] for p in _properties()}


def _deal_paths(slug):
    base = os.path.join(_MIDWAY_ROOT, 'data', slug)
    src = os.path.join(_MIDWAY_ROOT, 'source_docs', slug)
    return {
        'db': os.path.join(base, 'midway.db'),
        'text': os.path.join(base, 'ocr_text'),
        'tenant_src': os.path.join(src, 'tenant_packages'),
        'psa_src': os.path.join(src, 'psa'),
    }


def _summary(slug):
    """Compact diligence summary for a deal's warehouse (or None if not built)."""
    db = _deal_paths(slug)['db']
    if not os.path.exists(db):
        return None
    import sqlite3
    c = sqlite3.connect(db)

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
    return {'slug': slug, 'tenants_abstracted': tenants_abstracted, 'facts': facts,
            'deals': deals, 'missing': missing,
            'rea_prohibited': rea_total, 'rea_grocery_flag': rea_grocery}


def _run_generate(job_id, slug, force):
    job = _JOBS[job_id]
    paths = _deal_paths(slug)

    def step(s, detail=''):
        job['step'] = s
        job['detail'] = detail

    try:
        import diligence_report
        warnings = []
        if force or not os.path.exists(paths['db']):
            import ingest, abstract_facts, missing_docs, extract_psa, extract_rea
            step('ingesting', 'OCR + registering tenant documents (this can take minutes)…')
            ingest.run(src=paths['tenant_src'], db_path=paths['db'], text_dir=paths['text'])
            for label, detail, fn, kw in [
                ('abstracting', 'Extracting lease-abstract facts with the local model…',
                 abstract_facts.run, {'db_path': paths['db']}),
                ('diligence', 'Detecting missing documents…', missing_docs.run,
                 {'db_path': paths['db']}),
                ('psa', 'Extracting PSA deal economics…', extract_psa.run,
                 {'db_path': paths['db'], 'src': paths['psa_src']}),
                ('rea', 'Extracting REA prohibited uses…', extract_rea.run,
                 {'db_path': paths['db'], 'src': paths['psa_src']}),
            ]:
                step(label, detail)
                try:
                    fn(**kw)
                except Exception as e:      # noqa: BLE001
                    logger.exception('Midway %s step failed', label)
                    warnings.append(f'{label}: {str(e)[:100]}')

        if not os.path.exists(paths['db']):
            raise RuntimeError('No warehouse for this property yet. Run the extraction '
                               'pipeline locally first (see midway_db/README.md).')

        step('reporting', 'Generating the Disposition Diligence Report…')
        os.makedirs(os.path.dirname(paths['db']), exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(os.path.dirname(paths['db']),
                           f'Midway_Disposition_Diligence_{slug}_{ts}.docx')
        diligence_report.build(paths['db'], out)

        summary = _summary(slug) or {}
        summary['generated_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        summary['docx_name'] = os.path.basename(out)
        if warnings:
            summary['warnings'] = warnings
        job.update(status='done', step='complete', detail='Report ready.', summary=summary)
        _LATEST[slug] = {'docx': out, 'summary': summary}
    except Exception as e:
        logger.exception('Midway generate failed')
        job.update(status='error', error=str(e), traceback=traceback.format_exc())


# ── Page ──────────────────────────────────────────────────────────────────
@midway_bp.route('/')
def midway_index():
    props = _properties()
    first = props[0]['slug'] if props else None
    summary = _summary(first) if first else None
    return render_template('midway.html', properties=props, summary=summary)


# ── API ───────────────────────────────────────────────────────────────────
@midway_bp.route('/api/properties')
def api_properties():
    return jsonify(_properties())


@midway_bp.route('/api/summary')
def api_summary():
    slug = request.args.get('slug', '')
    if not _valid_slug(slug):
        return jsonify({}), 404
    return jsonify(_summary(slug) or {})


@midway_bp.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json(silent=True) or request.form
    slug = (data.get('slug') or '').strip()
    if not _valid_slug(slug):
        return jsonify({'error': 'Select a property first.'}), 400
    force = str(data.get('force') or '').lower() in ('1', 'true', 'yes', 'on')
    for jid, j in _JOBS.items():
        if j.get('status') == 'running':
            return jsonify({'job_id': jid, 'already_running': True}), 202
    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {
        'id': job_id, 'status': 'running', 'step': 'queued', 'detail': 'Starting…',
        'error': None, 'slug': slug, 'force': force,
        'started': datetime.datetime.now().isoformat(timespec='seconds'),
    }
    threading.Thread(target=_run_generate, args=(job_id, slug, force), daemon=True).start()
    return jsonify({'job_id': job_id})


@midway_bp.route('/api/status/<job_id>')
def api_status(job_id):
    job = _JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'Unknown job'}), 404
    return jsonify({k: v for k, v in job.items() if k != 'traceback'})


@midway_bp.route('/api/download')
def api_download():
    slug = request.args.get('slug', '')
    latest = _LATEST.get(slug)
    if not latest or not latest.get('docx') or not os.path.exists(latest['docx']):
        abort(404)
    return send_file(latest['docx'], as_attachment=True,
                     download_name=os.path.basename(latest['docx']))
