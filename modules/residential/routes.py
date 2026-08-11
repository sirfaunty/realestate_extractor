"""
Residential Portfolio routes — read-only UI over the KA residential package.

Pages: portfolio index (roster, NOI bridge, value programs), asset detail
(quarterly trends, valuation matrix, comps, concessions), discrepancy report.
Chamberlain cross-links to Capactive's existing deal-analytics pages.
"""

import html as _html
import logging
from flask import Blueprint, render_template_string

from .engine import ResidentialEngine, KEY_TO_NAME, CAP_RATES, VALUE_PROGRAMS, HEADLINES

logger = logging.getLogger(__name__)

resi_bp = Blueprint('residential', __name__, url_prefix='/residential')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = ResidentialEngine()
    return _engine


def register_residential_routes(app):
    app.register_blueprint(resi_bp)


_STYLE = """
<style>
:root {
  --bg:#0f1419; --panel:#1a1f2e; --border:#2d3548; --accent:#3A8FD4;
  --green:#10B981; --red:#f85149; --amber:#d29922;
  --text:#e6edf3; --muted:#8b949e; --text2:#c9d1d9;
  --font:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;
  --mono:'JetBrains Mono',ui-monospace,Consolas,monospace;
}
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:var(--font);
       margin:0; padding:20px 28px; font-size:14px; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.crumb { font-size:13px; color:var(--muted); margin-bottom:14px; }
h1 { font-size:22px; margin:0 0 4px; }
h2 { font-size:16px; margin:26px 0 10px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
.cards { display:flex; gap:14px; flex-wrap:wrap; margin:16px 0 22px; }
.card { background:var(--panel); border:1px solid var(--border); border-radius:8px;
        padding:14px 20px; min-width:170px; }
.card .v { font-size:17px; font-weight:600; font-family:var(--mono); }
.card .l { font-size:11px; color:var(--muted); text-transform:uppercase;
           letter-spacing:.5px; margin-top:2px; }
table { border-collapse:collapse; width:100%; background:var(--panel);
        border:1px solid var(--border); border-radius:8px; overflow:hidden; }
th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.5px;
     color:var(--muted); padding:9px 12px; border-bottom:1px solid var(--border);
     background:rgba(255,255,255,0.02); }
td { padding:8px 12px; border-bottom:1px solid var(--border); vertical-align:top; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:rgba(58,143,212,0.05); }
.num { text-align:right; font-family:var(--mono); }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px;
         border:1px solid var(--border); color:var(--text2); }
.badge.fc { color:var(--amber); border-color:var(--amber); }
.detail { max-width:680px; white-space:normal; overflow-wrap:anywhere; }
.note { color:var(--muted); font-size:12px; margin-top:10px; }
pre.md { background:var(--panel); border:1px solid var(--border); border-radius:8px;
         padding:18px; white-space:pre-wrap; font-size:13px; line-height:1.5;
         font-family:var(--font); color:var(--text2); }
.missing { background:var(--panel); border:1px solid var(--border); border-radius:8px;
           padding:30px; max-width:640px; }
</style>
"""


def _page(title, body, crumb_tail=''):
    crumb = ("<div class='crumb'><a href='/'>Capactive</a> &rsaquo; "
             "<a href='/residential/'>Residential Portfolio</a>" + crumb_tail + "</div>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{title} — Capactive</title>{_STYLE}</head><body>"
            + crumb + body + "</body></html>")


def _money(v):
    if v is None:
        return '—'
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v, scale=1.0):
    if v in (None, ''):
        return '—'
    try:
        return f"{float(v)*scale:.1f}%"
    except (TypeError, ValueError):
        return str(v)


@resi_bp.route('/')
def index():
    eng = _get_engine()
    if not eng.available():
        return _page('Residential', "<div class='missing'><b>Residential package not found.</b>"
                     "<p class='note'>Expected portfolio_ownership/residential/handoff_package/"
                     " in the workspace.</p></div>")
    roster = eng.roster()
    bridge, totals = eng.noi_bridge()

    cards = ''.join(f"<div class='card'><div class='v'>{v}</div><div class='l'>{l}</div></div>"
                    for l, v in HEADLINES)

    rrows = ''.join(f"""
      <tr>
        <td><a href='/residential/asset/{r['key']}'>{r['name']}</a></td>
        <td class='num'>{r['units'] or '—'}</td>
        <td class='num'>{_money(r['noi_2026b'])}</td>
        <td class='num'>{_pct(r['physical_pct'], 100)}</td>
        <td class='num'>{r['end_occ_pct'] or '—'}</td>
        <td class='detail note'>{_html.escape((r['mgmt_history'] or '')[:90])}</td>
      </tr>""" for r in roster)

    brows = ''.join(f"""
      <tr><td>{b['name']}</td>
      <td class='num'>{_money(b['noi_2026b'])}</td>
      <td class='num'>{_money(b['noi_2027f'])} <span class='badge fc'>F</span></td>
      <td class='num'>{_money(b['noi_2028f'])} <span class='badge fc'>F</span></td></tr>"""
      for b in bridge)
    trow = (f"<tr><td><b>Portfolio</b></td><td class='num'><b>{_money(totals.get('2026b'))}</b></td>"
            f"<td class='num'><b>{_money(totals.get('2027f'))}</b></td>"
            f"<td class='num'><b>{_money(totals.get('2028f'))}</b></td></tr>") if totals else ''

    vrows = ''.join(f"<tr><td>{p}</td><td class='num'>{v}</td><td class='detail'>{s}</td></tr>"
                    for p, v, s in VALUE_PROGRAMS)

    body = f"""
      <h1>Residential Portfolio</h1>
      <div class='sub'>KA residential — 6 multifamily assets + Arbors senior living, 1,350 units.
        Actuals per KA internal accounting; <span class='badge fc'>F</span> = proforma forecast
        (data doctrine per <a href='/residential/discrepancies'>discrepancy review</a>).</div>
      <div class='cards'>{cards}</div>
      <h2>Assets</h2>
      <table><thead><tr><th>Asset</th><th class='num'>Units</th><th class='num'>2026B NOI</th>
        <th class='num'>Budgeted phys. occ.</th><th class='num'>Latest occ. %</th>
        <th>Management history</th></tr></thead><tbody>{rrows}</tbody></table>
      <h2>NOI bridge 2026B → 2028F</h2>
      <table><thead><tr><th>Asset</th><th class='num'>2026B</th><th class='num'>2027F</th>
        <th class='num'>2028F</th></tr></thead><tbody>{brows}{trow}</tbody></table>
      <h2>Value programs (tracked, not in forecasts)</h2>
      <table><thead><tr><th>Program</th><th class='num'>Annual value</th><th>Status</th>
        </tr></thead><tbody>{vrows}</tbody></table>
      <p class='note'>Source: KA residential handoff package (build chains + internal accounting
        + weekly leasing reports). Read-only.</p>
    """
    return _page('Residential Portfolio', body)


@resi_bp.route('/asset/<key>')
def asset(key):
    eng = _get_engine()
    if not eng.available():
        return index()
    a = eng.asset(key)
    if a is None:
        return _page('Residential', f"<h1>Unknown asset {_html.escape(key)}</h1>"), 404

    q = eng.quarterly(key)
    qrows = ''.join(f"""
      <tr><td>{r['quarter']}</td>
      <td class='num'>{r['avg_occupancy_pct'] or '—'}</td>
      <td class='num'>{r['total_tours'] or '—'}</td>
      <td class='num'>{r['total_leases'] or '—'}</td>
      <td class='num'>{r['net_absorption'] or '—'}</td>
      <td class='num'>{r['avg_renewal_pct'] or '—'}</td>
      <td class='num'>{r['weeks_with_concessions'] or '0'}</td>
      <td class='num'>{_money(r['end_quarter_annualized_ner']) if r['end_quarter_annualized_ner'] else '—'}</td>
      </tr>""" for r in q)
    q_html = (f"<h2>Quarterly operating trend</h2>"
              f"<table><thead><tr><th>Quarter</th><th class='num'>Avg occ %</th>"
              f"<th class='num'>Tours</th><th class='num'>Leases</th>"
              f"<th class='num'>Net abs.</th><th class='num'>Renewal %</th>"
              f"<th class='num'>Concession wks</th><th class='num'>Annualized NER</th>"
              f"</tr></thead><tbody>{qrows}</tbody></table>") if q else ''

    vm = eng.valuation_matrix(key)
    vm_html = ''
    if vm:
        caps_head = ''.join(f"<th class='num'>{c*100:.2f}%</th>" for c in CAP_RATES)
        vrows = ''.join(
            "<tr><td>" + r['period'] +
            (" <span class='badge fc'>F</span>" if 'forecast' in r['tag'] else '') +
            f"</td><td class='num'>{_money(r['noi'])}</td>" +
            ''.join(f"<td class='num'>{_money(r['values'][c])}</td>" for c in CAP_RATES) +
            "</tr>" for r in vm)
        vm_html = (f"<h2>Valuation matrix (NOI ÷ cap)</h2>"
                   f"<table><thead><tr><th>Period</th><th class='num'>NOI</th>{caps_head}"
                   f"</tr></thead><tbody>{vrows}</tbody></table>")

    comps = eng.comps(key, limit=10)
    comps_html = ''
    if comps:
        crows = ''.join(f"""
          <tr><td>{_html.escape(str(c['comp'].get('address') or ''))},
                  {_html.escape(str(c['comp'].get('city') or ''))}</td>
          <td class='num'>{c['comp'].get('units') or '—'}</td>
          <td>{_html.escape(str(c['comp'].get('star') or ''))}</td>
          <td class='num'>{_money(c['comp'].get('sale_price'))}</td>
          <td class='num'>{str(c['comp'].get('sale_date') or '')[:10]}</td>
          <td class='num'>{round(c['meta'].get('dist_mi'), 1) if c.get('meta') else '—'} mi</td>
          </tr>""" for c in comps)
        comps_html = (f"<h2>Top scored sales comps ({len(comps)})</h2>"
                      f"<table><thead><tr><th>Address</th><th class='num'>Units</th><th>Class</th>"
                      f"<th class='num'>Sale price</th><th class='num'>Sale date</th>"
                      f"<th class='num'>Distance</th></tr></thead><tbody>{crows}</tbody></table>"
                      f"<p class='note'>CoStar Twin Cities extraction; 2025–26 comps may warrant "
                      f"a fresh search per the handoff.</p>")

    b = a['budget'] or {}
    cards = ''
    if b:
        cards = "<div class='cards'>" + ''.join(
            f"<div class='card'><div class='v'>{v}</div><div class='l'>{l}</div></div>"
            for l, v in [
                ('Units', b.get('units') or '—'),
                ('2026B NOI', _money(b.get('noi'))),
                ('2026B GPR', _money(b.get('gpr'))),
                ('2026B OpEx', _money(b.get('opex'))),
                ('Budgeted econ. occ.', _pct(b.get('budgeted_econ'), 100)),
            ]) + "</div>"

    s = a['summary'] or {}
    specials = s.get('Latest_Specials')
    specials_html = (f"<h2>Latest leasing note</h2><p class='detail note'>"
                     f"{_html.escape(specials)}</p>") if specials else ''

    xlink = ''
    if key == 'Chamberlain':
        xlink = ("<p class='note'><b>Capactive deal analytics for Chamberlain:</b> "
                 "<a href='/debt/'>Debt</a> · <a href='/distribution/'>Distribution</a> · "
                 "<a href='/tif-analysis/'>TIF</a> · <a href='/partnership/'>Partnership</a></p>")

    body = f"""
      <h1>{a['name']}</h1>
      <div class='sub'>{_html.escape((s.get('Management_History') or ''))}</div>
      {cards}{xlink}{q_html}{vm_html}{comps_html}{specials_html}
    """
    return _page(a['name'], body, crumb_tail=f" &rsaquo; {a['name']}")


@resi_bp.route('/discrepancies')
def discrepancies():
    eng = _get_engine()
    md = eng.discrepancy_report() if eng.available() else None
    if not md:
        return _page('Discrepancies', "<h1>Discrepancy report not found</h1>"), 404
    body = (f"<h1>Data source discrepancy report</h1>"
            f"<div class='sub'>Verbatim from the handoff package — the basis for the "
            f"internal-accounting-is-authoritative doctrine.</div>"
            f"<pre class='md'>{_html.escape(md)}</pre>")
    return _page('Discrepancy report', body, crumb_tail=' &rsaquo; Discrepancies')
