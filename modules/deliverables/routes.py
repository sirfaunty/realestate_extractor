"""
Deliverables routes — generate verified-source documents from the KA
portfolio warehouse (read-only) into data/deliverables/, list and download.
"""

import logging
import os
import re
import time
from flask import Blueprint, jsonify, request, render_template_string, send_file

from .engine import compendium_data, lease_properties, OUTPUT_DIR
from .compendium import build_compendium, default_filename
from .refi_engine import refi_data, loan_lease_properties
from .refi_package import build_refi_package, refi_filename

logger = logging.getLogger(__name__)

deliv_bp = Blueprint('deliverables', __name__, url_prefix='/deliverables')


def register_deliverables_routes(app):
    app.register_blueprint(deliv_bp)


_PAGE = """
<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Deliverables — Capactive</title>
<style>
:root { --bg:#0f1419; --panel:#1a1f2e; --border:#2d3548; --accent:#3A8FD4;
  --green:#10B981; --red:#f85149; --text:#e6edf3; --muted:#8b949e;
  --font:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif; }
body { background:var(--bg); color:var(--text); font-family:var(--font);
  margin:0; padding:28px 36px; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:24px; }
h2 { font-size:15px; margin:26px 0 10px; }
table { border-collapse:collapse; width:100%; max-width:980px; }
th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--border);
  font-size:13px; }
th { color:var(--muted); font-weight:600; font-size:12px;
  text-transform:uppercase; letter-spacing:.04em; }
button { background:var(--accent); color:#fff; border:0; border-radius:6px;
  padding:6px 14px; font-size:12.5px; cursor:pointer; font-family:var(--font); }
button:disabled { background:var(--border); cursor:default; }
a { color:var(--accent); text-decoration:none; }
.badge { background:#22304a; border-radius:10px; padding:1px 10px; font-size:11px;
  color:var(--muted); }
#status { margin:14px 0; font-size:13px; color:var(--muted); min-height:18px; }
#status.busy { color:var(--accent); }
#status.err { color:var(--red); }
#status.ok { color:var(--green); }
.crumb { font-size:12.5px; margin-bottom:14px; }
.crumb a { color:var(--muted); }
</style></head><body>
<div class='crumb'><a href='/'>&larr; Dashboard</a></div>
<h1>Deliverables</h1>
<div class='sub'>Generated from the verified KA portfolio warehouse
(page-cited provisions) + the canonical MRI rent-roll module. Read-only
sources; documents land in <span class='badge'>data/deliverables/</span>.</div>

<div id='status'></div>

<h2>Lease Abstract Compendium — pick a property</h2>
<table><thead><tr><th>Property</th><th>Tenancies</th><th>Provisions</th>
<th></th></tr></thead><tbody>
{% for p in props %}
<tr><td>{{ p.name }}</td><td>{{ p.tenancies }}</td>
<td>{{ '{:,}'.format(p.provisions) }}</td>
<td>
  <button onclick="gen('{{ p.property_id }}', 'summary', this)">Generate summary</button>
  <button onclick="gen('{{ p.property_id }}', 'full', this)"
          style='background:#22304a'>Generate full</button>
</td></tr>
{% endfor %}
</tbody></table>
<div class='sub' style='margin-top:8px'>Summary = tenancy summaries + provision
index (briefing length). Full = every provision rendered verbatim with
citations (exhaustive record; large properties run hundreds of pages).
Downloads start automatically when generation finishes.</div>

<h2>Refinance Diligence Package — properties with loan + lease layers</h2>
<table><thead><tr><th>Property</th><th>Facilities</th><th>Tenancies</th>
<th></th></tr></thead><tbody>
{% for p in refi_props %}
<tr><td>{{ p.name }}</td><td>{{ p.facilities }}</td><td>{{ p.tenancies }}</td>
<td><button onclick="gen('{{ p.property_id }}', 'refi', this)">Generate package</button></td></tr>
{% endfor %}
</tbody></table>
<div class='sub' style='margin-top:8px'>Loan facilities with balances and
balloons, loan-document provisions by lender category, lease rollover crossed
against loan maturity, lender-relevant lease provisions, and open items.</div>

<h2>Generated documents</h2>
{% if files %}
<table><thead><tr><th>File</th><th>Size</th><th>Created</th></tr></thead><tbody>
{% for f in files %}
<tr><td><a href='/deliverables/download/{{ f.name }}'>{{ f.name }}</a></td>
<td>{{ f.size }}</td><td>{{ f.created }}</td></tr>
{% endfor %}
</tbody></table>
{% else %}<div class='sub'>Nothing generated yet.</div>{% endif %}

<script>
// While a build is in flight, block navigation with the browser's native
// "Leave site?" dialog — leaving would cancel the request and skip the
// auto-download.
function _guard(e) { e.preventDefault(); e.returnValue = ''; return ''; }

async function gen(pid, mode, btn) {
  const s = document.getElementById('status');
  const all = document.querySelectorAll('button');
  all.forEach(b => b.disabled = true);   // one build at a time
  window.addEventListener('beforeunload', _guard);
  s.className = 'busy';
  const steps = ['Reading verified lease layer', 'Joining rent-roll economics',
                 'Rendering document (large properties take up to a minute)'];
  let i = 0;
  s.textContent = steps[0] + '…';
  const tick = setInterval(() => {
    i = Math.min(i + 1, steps.length - 1);
    s.textContent = steps[i] + '…';
  }, 2500);
  try {
    const r = await fetch('/deliverables/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({property_id: pid, mode: mode})
    });
    const j = await r.json();
    clearInterval(tick);
    window.removeEventListener('beforeunload', _guard);
    if (j.ok) {
      s.className = 'ok';
      s.innerHTML = `Generated <a href='/deliverables/download/${j.file}'>` +
                    `${j.file}</a> — ${j.tenancies} tenancies, ` +
                    `${j.provisions.toLocaleString()} provisions in ${j.seconds}s` +
                    ` · download started`;
      // start the download immediately
      window.location.href = '/deliverables/download/' + j.file;
      setTimeout(() => location.reload(), 2500);
    } else {
      s.className = 'err';
      s.textContent = j.error || 'Generation failed';
      all.forEach(b => b.disabled = false);
    }
  } catch (e) {
    clearInterval(tick);
    window.removeEventListener('beforeunload', _guard);
    s.className = 'err';
    s.textContent = 'Generation failed: ' + e;
    all.forEach(b => b.disabled = false);
  }
}
</script>
</body></html>
"""


@deliv_bp.route('/')
def index():
    props = lease_properties()
    files = []
    if os.path.isdir(OUTPUT_DIR):
        for fn in sorted(os.listdir(OUTPUT_DIR),
                         key=lambda f: os.path.getmtime(os.path.join(OUTPUT_DIR, f)),
                         reverse=True):
            if not fn.lower().endswith('.docx'):
                continue
            fp = os.path.join(OUTPUT_DIR, fn)
            st = os.stat(fp)
            files.append({'name': fn,
                          'size': f'{st.st_size // 1024:,} KB',
                          'created': time.strftime('%Y-%m-%d %H:%M',
                                                   time.localtime(st.st_mtime))})
    return render_template_string(_PAGE, props=props,
                                  refi_props=loan_lease_properties(),
                                  files=files)


def _register_job(pid, mode):
    """Record this build in the app-wide jobs registry so the universal
    progress widget shows it on every page. Returns (jobs, job_id) or
    (None, None) if the registry is unavailable."""
    try:
        from webapp import jobs  # safe at call time; webapp fully loaded
        from flask import session
        from datetime import datetime
        job_id = f'deliverable-{int(time.time() * 1000)}'
        labels = {'full': 'Full compendium', 'summary': 'Summary compendium',
                  'refi': 'Refinance diligence package'}
        jobs[job_id] = {
            'org_id': session.get('org_id'),
            'type': 'deliverable',
            'filename': pid,          # refined to real doc name once known
            'status': 'processing',
            'progress': 0, 'total': 1,
            'step': 'rendering',
            'step_detail': labels.get(mode, mode),
            'started': datetime.now().isoformat(),
        }
        return jobs, job_id
    except Exception:
        logger.exception('could not register deliverable job')
        return None, None


def _finish_job(jobs, job_id, **fields):
    if jobs is None or job_id not in jobs:
        return
    jobs[job_id].update(fields)


@deliv_bp.route('/generate', methods=['POST'])
def generate():
    payload = request.get_json(silent=True) or {}
    pid = payload.get('property_id')
    mode = payload.get('mode', 'full')
    if mode not in ('full', 'summary', 'refi'):
        mode = 'full'
    if not pid:
        return jsonify({'ok': False, 'error': 'property_id required'}), 400
    jobs, job_id = _register_job(pid, mode)
    try:
        t0 = time.time()
        if mode == 'refi':
            data = refi_data(pid)
            if not data['facilities']:
                _finish_job(jobs, job_id, status='failed',
                            error=f'no loan facilities for {pid}')
                return jsonify({'ok': False,
                                'error': f'no loan facilities for {pid}'}), 404
            name = data['property'].get('property_name') or pid
            fname = refi_filename(name)
            _finish_job(jobs, job_id, filename=fname)
            out = os.path.join(OUTPUT_DIR, fname)
            rep = build_refi_package(data, out)
            logger.info(f'refi package generated: {out}')
            _finish_job(jobs, job_id, status='completed', progress=1,
                        step='complete', file=fname)
            return jsonify({'ok': True, 'file': fname,
                            'tenancies': rep['facilities'],
                            'provisions': rep['loan_provisions']
                            + rep['lease_provisions'],
                            'seconds': round(time.time() - t0, 1)})
        data = compendium_data(pid)
        if not data['tenancies']:
            _finish_job(jobs, job_id, status='failed',
                        error=f'no lease layer for {pid}')
            return jsonify({'ok': False,
                            'error': f'no lease layer for {pid}'}), 404
        name = data['property'].get('property_name') or pid
        fname = default_filename(name, mode)
        _finish_job(jobs, job_id, filename=fname)
        out = os.path.join(OUTPUT_DIR, fname)
        rep = build_compendium(data, out, mode=mode)
        logger.info(f'deliverable generated: {out} '
                    f'({rep["provisions"]} provisions)')
        _finish_job(jobs, job_id, status='completed', progress=1,
                    step='complete', file=fname)
        return jsonify({'ok': True, 'file': fname,
                        'tenancies': rep['tenancies'],
                        'provisions': rep['provisions'],
                        'seconds': round(time.time() - t0, 1)})
    except Exception as e:
        logger.exception('deliverable generation failed')
        _finish_job(jobs, job_id, status='failed', error=str(e))
        return jsonify({'ok': False, 'error': str(e)}), 500


@deliv_bp.route('/download/<path:fname>')
def download(fname):
    # safe: no traversal, only files that exist inside OUTPUT_DIR
    if not re.match(r'^[A-Za-z0-9._-]+\.docx$', fname):
        return 'invalid filename', 400
    fp = os.path.join(OUTPUT_DIR, fname)
    if not os.path.isfile(fp):
        return 'not found', 404
    return send_file(fp, as_attachment=True)
