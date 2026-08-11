"""
Portfolio Ownership routes — read-only UI over the KA portfolio warehouse.

Server-rendered pages (no async loaders): portfolio index, property detail
with lease roster + provision browser, open-items register, portfolio search.
"""

import logging
from flask import Blueprint, request, render_template_string

from .engine import OwnershipEngine, STATUS_GROUP_ORDER, SEVERITY_ORDER

logger = logging.getLogger(__name__)

own_bp = Blueprint('portfolio_ownership', __name__, url_prefix='/portfolio-ownership')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = OwnershipEngine()
    return _engine


def register_portfolio_ownership_routes(app):
    app.register_blueprint(own_bp)


# ─── Shared style ────────────────────────────────────────────────────

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
.crumb a { color:var(--accent); }
h1 { font-size:22px; margin:0 0 4px; }
h2 { font-size:16px; margin:26px 0 10px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:18px; }
.cards { display:flex; gap:14px; flex-wrap:wrap; margin:16px 0 22px; }
.card { background:var(--panel); border:1px solid var(--border); border-radius:8px;
        padding:14px 20px; min-width:150px; }
.card .v { font-size:22px; font-weight:600; font-family:var(--mono); }
.card .l { font-size:11px; color:var(--muted); text-transform:uppercase;
           letter-spacing:.5px; margin-top:2px; }
.card .v.hi { color:var(--red); }
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
.badge.current { color:var(--green); border-color:var(--green); }
.badge.former { color:var(--muted); }
.badge.license { color:var(--accent); border-color:var(--accent); }
.badge.pipeline { color:var(--amber); border-color:var(--amber); }
.badge.vacant { color:var(--red); border-color:var(--red); }
.badge.HIGH,.badge.BLOCKER,.badge.DEFECT { color:var(--red); border-color:var(--red); }
.badge.MED,.badge.WARN { color:var(--amber); border-color:var(--amber); }
.badge.LOW,.badge.INFO,.badge.UNSPECIFIED { color:var(--muted); }
.cite { font-family:var(--mono); font-size:11px; color:var(--muted); }
.detail { max-width:720px; white-space:normal; overflow-wrap:anywhere; }
form.filters { display:flex; gap:10px; flex-wrap:wrap; margin:12px 0 16px; align-items:center; }
select, input[type=text] { background:#fff; color:#1A1A2E; border:1px solid var(--border);
        border-radius:6px; padding:6px 10px; font-size:13px; font-family:var(--font);
        max-width:280px; }
button { background:var(--accent); color:#fff; border:none; border-radius:6px;
         padding:7px 16px; font-size:13px; cursor:pointer; font-family:var(--font); }
.pager { margin:14px 0; color:var(--muted); font-size:13px; }
.pager a { margin:0 6px; }
.note { color:var(--muted); font-size:12px; margin-top:10px; }
.missing { background:var(--panel); border:1px solid var(--border); border-radius:8px;
           padding:30px; max-width:640px; }
</style>
"""

_CRUMB = ("<div class='crumb'><a href='/'>Capactive</a> &rsaquo; "
          "<a href='/portfolio-ownership/'>Portfolio Ownership</a>{tail}</div>")


def _page(title, body, crumb_tail=''):
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{title} — Capactive</title>{_STYLE}</head><body>"
            + _CRUMB.format(tail=crumb_tail) + body + "</body></html>")


def _missing_page():
    return _page("Portfolio Ownership", """
      <h1>Portfolio Ownership</h1>
      <div class='missing'>
        <p><b>Master warehouse not found.</b></p>
        <p class='note'>Expected <span class='cite'>portfolio_ownership/…/portfolio_warehouse.db</span>
        inside the workspace. Extract the KA master package there and reload.</p>
      </div>""")


# ─── Pages ───────────────────────────────────────────────────────────

@own_bp.route('/')
def index():
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    s = eng.portfolio_summary()
    props = eng.properties()
    rollovers = eng.rollovers(months=12)

    rows = ''.join(f"""
      <tr>
        <td><a href='/portfolio-ownership/property/{p['property_id']}'>{p['name']}</a></td>
        <td class='cite'>{p['property_id']}</td>
        <td>{p['fund'] or '—'}</td>
        <td>{p['product_type'] or '—'}</td>
        <td class='num'>{p['provisions']:,}</td>
        <td class='num'>{p['leases']}</td>
        <td class='num'>{p['current_leases']}</td>
        <td class='num'>{p['prop_docs']}</td>
      </tr>""" for p in props)

    ro_rows = ''.join(f"""
      <tr>
        <td><a href='/portfolio-ownership/property/{r['property_id']}'>{r['property_id']}</a></td>
        <td>{r['trade_name'] or r['tenant_key']}</td>
        <td>{r['suite'] or r['suite_id'] or '—'}</td>
        <td class='num'>{(r['sf'] or 0) and f"{int(r['sf']):,}" or '—'}</td>
        <td class='num'>{r['expiration']}</td>
        <td class='num'>{r['months_to_exp']}</td>
      </tr>""" for r in rollovers[:15])

    body = f"""
      <h1>Portfolio Ownership</h1>
      <div class='sub'>KA portfolio master — page-cited lease &amp; ownership extraction.
        Read-only; maintained by the aggregation workflow.</div>
      <div class='cards'>
        <div class='card'><div class='v'>{s['covered_properties']}</div>
          <div class='l'>Properties w/ lease layer (of {s['spine_properties']} spine)</div></div>
        <div class='card'><div class='v'>{s['provisions']:,}</div><div class='l'>Provisions (page-cited)</div></div>
        <div class='card'><div class='v'>{s['leases']}</div><div class='l'>Lease rows</div></div>
        <div class='card'><div class='v'>{s['prop_docs']}</div><div class='l'>Property-level docs</div></div>
        <div class='card'><div class='v hi'>{s['open_high']}</div>
          <div class='l'><a href='/portfolio-ownership/open-items'>High-sev open items</a>
          ({s['open_items']:,} total)</div></div>
      </div>
      <form class='filters' action='/portfolio-ownership/search' method='get'>
        <input type='text' name='q' placeholder='Search all provisions portfolio-wide…' style='min-width:340px'>
        <button>Search provisions</button>
        <a href='/portfolio-ownership/open-items' style='margin-left:12px'>Open-items register →</a>
        <a href='/portfolio-ownership/financials' style='margin-left:12px'>Financials →</a>
        <a href='/portfolio-ownership/loans' style='margin-left:12px'>Loans →</a>
        <a href='/portfolio-ownership/vacancy' style='margin-left:12px'>Vacancy →</a>
      </form>
      <h2>Covered properties</h2>
      <table><thead><tr><th>Property</th><th>Key</th><th>Fund</th><th>Type</th>
        <th class='num'>Provisions</th><th class='num'>Leases</th>
        <th class='num'>Current</th><th class='num'>Prop docs</th></tr></thead>
        <tbody>{rows}</tbody></table>
      <h2>Rollovers — next 12 months ({len(rollovers)})</h2>
      <table><thead><tr><th>Property</th><th>Tenant</th><th>Suite</th>
        <th class='num'>SF</th><th class='num'>Expiration</th><th class='num'>Months</th></tr></thead>
        <tbody>{ro_rows or "<tr><td colspan='6' class='note'>None inside 12 months.</td></tr>"}</tbody></table>
    """
    return _page('Portfolio Ownership', body)


@own_bp.route('/property/<property_id>')
def property_page(property_id):
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    info = eng.property_info(property_id)
    if info is None:
        return _page('Not found', f"<h1>Unknown property</h1><p class='note'>{property_id}</p>"), 404

    leases = eng.leases(property_id)
    cats = eng.provision_categories(property_id)
    tenants = eng.tenants(property_id)
    docs = eng.prop_documents(property_id)

    tenant = request.args.get('tenant') or None
    category = request.args.get('category') or None
    q = request.args.get('q') or None
    page = max(int(request.args.get('page', 1) or 1), 1)
    per = 50
    provs, total = eng.provisions(property_id, tenant, category, q,
                                  limit=per, offset=(page - 1) * per)

    # Lease roster grouped by normalized status
    groups = {}
    for l in leases:
        groups.setdefault(l['group'], []).append(l)
    roster = ''
    for g in STATUS_GROUP_ORDER:
        if g not in groups:
            continue
        rows = ''.join(f"""
          <tr>
            <td><a href='?tenant={l['tenant_key']}'>{l['trade_name'] or l['tenant_key']}</a></td>
            <td>{l['suite'] or l['suite_id'] or '—'}</td>
            <td><span class='badge {l['group']}'>{l['status'] or '—'}</span></td>
            <td class='num'>{(l['sf'] or 0) and f"{int(l['sf']):,}" or '—'}</td>
            <td class='num'>{l['expiration'] or '—'}</td>
            <td class='num'>{l['months_to_exp'] if l['months_to_exp'] is not None else ''}</td>
            <td class='num'>{l['provisions']:,}</td>
            <td class='cite'>{(l['source'] or '')[:46]} {l['source_pages'] or ''}</td>
          </tr>""" for l in groups[g])
        roster += (f"<h2>{g.title()} leases ({len(groups[g])})</h2>"
                   f"<table><thead><tr><th>Tenant</th><th>Suite</th><th>Status</th>"
                   f"<th class='num'>SF</th><th class='num'>Expiration</th>"
                   f"<th class='num'>Mo.</th><th class='num'>Provisions</th>"
                   f"<th>Citation</th></tr></thead><tbody>{rows}</tbody></table>")

    cat_opts = ''.join(
        f"<option value=\"{c['category']}\" {'selected' if c['category'] == category else ''}>"
        f"{c['category'][:60]} ({c['n']})</option>" for c in cats)
    ten_opts = ''.join(
        f"<option value=\"{t['tenant_key']}\" {'selected' if t['tenant_key'] == tenant else ''}>"
        f"{(t['trade_name'] or t['tenant_key'] or '(none)')[:40]} ({t['n']})</option>"
        for t in tenants)

    prov_rows = ''.join(f"""
      <tr>
        <td>{p['tenant_key'] or '—'}</td>
        <td>{(p['category'] or '')[:48]}</td>
        <td class='detail'>{(p['detail'] or '')[:600]}</td>
        <td class='cite'>{(p['source'] or '')[:44]}<br>{p['source_pages'] or ''}</td>
      </tr>""" for p in provs)

    pages = max((total + per - 1) // per, 1)
    qs_base = f"tenant={tenant or ''}&category={category or ''}&q={q or ''}"
    pager = f"<div class='pager'>{total:,} provisions — page {page}/{pages}"
    if page > 1:
        pager += f" <a href='?{qs_base}&page={page-1}'>&larr; prev</a>"
    if page < pages:
        pager += f" <a href='?{qs_base}&page={page+1}'>next &rarr;</a>"
    pager += "</div>"

    doc_rows = ''.join(f"""
      <tr><td>{d['doc_type'] or '—'}</td><td>{(d['title'] or '')[:70]}</td>
      <td>{(d['parties'] or '')[:60]}</td><td>{d['effective_date'] or '—'}</td>
      <td class='num'>{d['pages'] or '—'}</td><td class='num'>{d['provisions']}</td>
      <td class='cite'>{d['recorded_ref'] or ''}</td></tr>""" for d in docs)
    docs_html = (f"<h2>Property-level documents ({len(docs)})</h2>"
                 f"<table><thead><tr><th>Type</th><th>Title</th><th>Parties</th><th>Effective</th>"
                 f"<th class='num'>Pages</th><th class='num'>Provisions</th><th>Recorded</th>"
                 f"</tr></thead><tbody>{doc_rows}</tbody></table>") if docs else ''

    meta = ' · '.join(str(x) for x in [
        info.get('owning_entity'), info.get('fund'), info.get('product_type'),
        info.get('lender') and f"Lender: {info.get('lender')}",
        info.get('property_manager') and f"Mgr: {info.get('property_manager')}"] if x)

    body = f"""
      <h1>{info.get('property_name') or property_id}</h1>
      <div class='sub'><span class='cite'>{property_id}</span>{' · ' + meta if meta else ''}</div>
      {roster}
      <h2>Provisions</h2>
      <form class='filters' method='get'>
        <select name='tenant'><option value=''>All tenants</option>{ten_opts}</select>
        <select name='category'><option value=''>All categories</option>{cat_opts}</select>
        <input type='text' name='q' value='{q or ''}' placeholder='Search text…'>
        <button>Filter</button>
        <a href='/portfolio-ownership/property/{property_id}'>reset</a>
      </form>
      {pager}
      <table><thead><tr><th>Tenant</th><th>Category</th><th>Provision</th><th>Citation</th></tr></thead>
      <tbody>{prov_rows or "<tr><td colspan='4' class='note'>No provisions match.</td></tr>"}</tbody></table>
      {pager}
      {docs_html}
    """
    return _page(info.get('property_name') or property_id, body,
                 crumb_tail=f" &rsaquo; {info.get('property_name') or property_id}")


@own_bp.route('/open-items')
def open_items():
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    module = request.args.get('module') or None
    severity = request.args.get('severity') or None
    q = request.args.get('q') or None
    page = max(int(request.args.get('page', 1) or 1), 1)
    per = 100
    rows, total = eng.open_items(module, severity, q, limit=per, offset=(page - 1) * per)
    mods, sevs = eng.open_item_facets()

    mod_opts = ''.join(
        f"<option value='{m['module_id']}' {'selected' if m['module_id'] == module else ''}>"
        f"{m['module_id']} ({m['n']:,})</option>" for m in mods)
    sev_opts = ''.join(
        f"<option value='{s['sev']}' {'selected' if s['sev'] == severity else ''}>"
        f"{s['sev']} ({s['n']:,})</option>" for s in sevs)

    trs = ''.join(f"""
      <tr>
        <td><span class='badge {r['sev']}'>{r['sev']}</span></td>
        <td class='cite'>{r['module_id']} / {r['item_id']}</td>
        <td>{(r['category'] or '')[:44]}</td>
        <td class='detail'>{(r['description'] or '')[:500]}</td>
        <td class='cite'>{r['source_table'] or ''}</td>
      </tr>""" for r in rows)

    pages = max((total + per - 1) // per, 1)
    qs = f"module={module or ''}&severity={severity or ''}&q={q or ''}"
    pager = f"<div class='pager'>{total:,} items — page {page}/{pages}"
    if page > 1:
        pager += f" <a href='?{qs}&page={page-1}'>&larr; prev</a>"
    if page < pages:
        pager += f" <a href='?{qs}&page={page+1}'>next &rarr;</a>"
    pager += "</div>"

    body = f"""
      <h1>Open-items register</h1>
      <div class='sub'>Honest gaps from every module — flagged, never smoothed.</div>
      <form class='filters' method='get'>
        <select name='module'><option value=''>All modules</option>{mod_opts}</select>
        <select name='severity'><option value=''>All severities</option>{sev_opts}</select>
        <input type='text' name='q' value='{q or ''}' placeholder='Search…'>
        <button>Filter</button>
      </form>
      {pager}
      <table><thead><tr><th>Sev</th><th>Item</th><th>Category</th><th>Description</th>
      <th>Source</th></tr></thead><tbody>{trs}</tbody></table>
      {pager}
    """
    return _page('Open items', body, crumb_tail=' &rsaquo; Open items')


def _money(v):
    if v is None:
        return '—'
    try:
        return f"{v:,.0f}"
    except (TypeError, ValueError):
        return str(v)


@own_bp.route('/financials')
def financials():
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    props = eng.fin_properties()
    rows = ''.join(f"""
      <tr>
        <td><a href='/portfolio-ownership/financials/{p['entity_code']}'>{p['name']}</a></td>
        <td class='cite'>{p['entity_code']}{(' / ' + p['property_key']) if p['property_key'] else ''}</td>
        <td>{p['fund'] or '—'}</td>
        <td class='num'>FY{p['fy_min']}–{p['fy_max']}</td>
        <td class='num'>{_money(p['latest_revenue'])}</td>
        <td class='num'>{f"{p['occupied_pct']*100:.0f}%" if p['occupied_pct'] is not None else '—'}</td>
        <td class='cite'>{p['occ_asof'] or ''}</td>
      </tr>""" for p in props)
    body = f"""
      <h1>Historical Financials</h1>
      <div class='sub'>fin_ module — {len(props)} properties, operating statements FY2017–2026.
        “Rental revenue” sums only categories explicitly labeled REVENUE/INCOME
        (no computed NOI — the account-class map is partial; flag, don't fabricate).</div>
      <table><thead><tr><th>Property</th><th>Keys</th><th>Fund</th>
        <th class='num'>Coverage</th><th class='num'>Latest-FY revenue (labeled)</th>
        <th class='num'>Occupancy</th><th>As of</th></tr></thead><tbody>{rows}</tbody></table>
    """
    return _page('Financials', body, crumb_tail=' &rsaquo; Financials')


@own_bp.route('/financials/<entity_code>')
def financials_property(entity_code):
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    years, matrix, occ = eng.fin_property_matrix(entity_code)
    if not years:
        return _page('Financials', f"<h1>No financials for {entity_code}</h1>"), 404
    spine = eng._spine_by_entity().get(entity_code, {})
    name = spine.get('property_name') or entity_code

    yhead = ''.join(f"<th class='num'>FY{y}</th>" for y in years)
    mrows = ''.join(
        "<tr><td>" + m['category'][:52] + "</td>" +
        ''.join(f"<td class='num'>{_money(m['by_year'].get(y))}</td>" for y in years) +
        "</tr>" for m in matrix)

    occ_rows = ''.join(f"""
      <tr><td>{o['report_date']}</td>
      <td class='num'>{f"{o['occupied_pct']*100:.1f}%" if o['occupied_pct'] is not None else '—'}</td>
      <td class='num'>{_money(o['occupied_sqft'])}</td>
      <td class='num'>{_money(o['vacant_sqft'])}</td>
      <td class='num'>{_money(o['total_sqft'])}</td></tr>""" for o in occ)
    occ_html = (f"<h2>Occupancy snapshots ({len(occ)})</h2>"
                f"<table><thead><tr><th>Report date</th><th class='num'>Occupied %</th>"
                f"<th class='num'>Occ. SF</th><th class='num'>Vacant SF</th>"
                f"<th class='num'>Total SF</th></tr></thead><tbody>{occ_rows}</tbody></table>"
                ) if occ else ''

    body = f"""
      <h1>{name} — Financials</h1>
      <div class='sub'><span class='cite'>{entity_code}</span> · annual totals as recorded
        in operating statements (no derived rollups)</div>
      <table><thead><tr><th>Category</th>{yhead}</tr></thead><tbody>{mrows}</tbody></table>
      {occ_html}
    """
    return _page(f'{name} financials', body,
                 crumb_tail=f" &rsaquo; <a href='/portfolio-ownership/financials'>Financials</a>"
                            f" &rsaquo; {name}")


@own_bp.route('/loans')
def loans():
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    facs = eng.loan_facilities()
    rows = ''.join(f"""
      <tr>
        <td><a href='/portfolio-ownership/loans/{f['facility_id']}'>{(f['property_name'] or f['property_key'] or '—')}</a></td>
        <td>{(f['lender'] or '—')}</td>
        <td>{(f['borrower'] or '—')}</td>
        <td class='num'>{_money(f['original_principal'])}</td>
        <td class='num'>{_money(f['latest_balance'])}</td>
        <td class='cite'>{f['balance_asof'] or ''}</td>
        <td class='num'>{f['rate'] if f['rate'] is not None else '—'}</td>
        <td class='num'>{f['maturity'] or '—'}</td>
        <td>{'✓ (' + str(f['collateral_property_count']) + ')' if f['is_cross_collateralized'] else ''}</td>
      </tr>""" for f in facs)
    body = f"""
      <h1>Loan Facilities</h1>
      <div class='sub'>loan_ module — {len(facs)} facilities across 38 properties;
        terms resolved from closing files with document evidence.</div>
      <table><thead><tr><th>Property</th><th>Lender</th><th>Borrower</th>
        <th class='num'>Original</th><th class='num'>Latest balance</th><th>As of</th>
        <th class='num'>Rate %</th><th class='num'>Maturity</th><th>Cross-coll.</th>
        </tr></thead><tbody>{rows}</tbody></table>
    """
    return _page('Loans', body, crumb_tail=' &rsaquo; Loans')


@own_bp.route('/loans/<facility_id>')
def loan_detail(facility_id):
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    fac = eng.loan_facility_detail(facility_id)
    if fac is None:
        return _page('Loans', f"<h1>Unknown facility {facility_id}</h1>"), 404

    def kv(rows_, valcols):
        out = ''
        for r in rows_:
            val = next((r[c] for c in valcols if r.get(c) is not None), '—')
            extra = ' <span class="badge WARN">conflict</span>' if r.get('conflict') else ''
            out += (f"<tr><td>{r['field']}</td><td class='num'>{val}{extra}</td>"
                    f"<td class='detail cite'>{(r.get('evidence') or '')[:260]}</td>"
                    f"<td class='cite'>{(r.get('source_file') or r.get('source_doc_type') or '')[:40]}</td></tr>")
        return out

    terms_html = (f"<h2>Resolved terms</h2><table><thead><tr><th>Field</th><th class='num'>Value</th>"
                  f"<th>Evidence</th><th>Source</th></tr></thead>"
                  f"<tbody>{kv(fac['terms'], ['value_num','value_text'])}</tbody></table>"
                  ) if fac['terms'] else ''
    facts_html = (f"<h2>Facts</h2><table><thead><tr><th>Field</th><th class='num'>Value</th>"
                  f"<th>Evidence</th><th>Source</th></tr></thead>"
                  f"<tbody>{kv(fac['facts'], ['value_num','value_text'])}</tbody></table>"
                  ) if fac['facts'] else ''

    bal_rows = ''.join(f"<tr><td>{b['asof_date']}</td><td class='num'>{_money(b['balance'])}</td>"
                       f"<td>{b['basis'] or ''}</td><td class='cite'>{(b['note'] or '')[:60]}</td></tr>"
                       for b in fac['balances'])
    bal_html = (f"<h2>Balances</h2><table><thead><tr><th>As of</th><th class='num'>Balance</th>"
                f"<th>Basis</th><th>Note</th></tr></thead><tbody>{bal_rows}</tbody></table>"
                ) if fac['balances'] else ''

    prov_rows = ''.join(f"<tr><td>{(p['category'] or '')[:40]}</td>"
                        f"<td class='detail'>{(p['why_it_matters'] or '')[:300]}</td>"
                        f"<td class='detail cite'>{(p['evidence'] or '')[:260]}</td>"
                        f"<td class='cite'>{(p['doc_type'] or '')[:30]}</td></tr>"
                        for p in fac['provisions'])
    prov_html = (f"<h2>Abstract provisions ({len(fac['provisions'])})</h2>"
                 f"<table><thead><tr><th>Category</th><th>Why it matters</th><th>Evidence</th>"
                 f"<th>Doc</th></tr></thead><tbody>{prov_rows}</tbody></table>"
                 ) if fac['provisions'] else ''

    body = f"""
      <h1>{fac.get('property_name') or fac['facility_id']} — Loan</h1>
      <div class='sub'><span class='cite'>{fac['facility_id']}</span>
        · Lender: {fac.get('lender') or '—'} · Borrower: {fac.get('borrower') or '—'}
        {'· CROSS-COLLATERALIZED (' + str(fac.get('collateral_property_count')) + ' properties)'
         if fac.get('is_cross_collateralized') else ''}</div>
      {terms_html}{facts_html}{bal_html}{prov_html}
    """
    return _page('Loan detail', body,
                 crumb_tail=f" &rsaquo; <a href='/portfolio-ownership/loans'>Loans</a>"
                            f" &rsaquo; {fac['facility_id']}")


@own_bp.route('/vacancy')
def vacancy():
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    suite_latest, summary_latest, props, suites = eng.vac_snapshot()
    srows = ''.join(f"""
      <tr><td>{s['property']}</td><td>{s['unit'] or '—'}</td>
      <td class='num'>{_money(s['vacant_sf'])}</td><td class='num'>{s['date_vacated'] or '—'}</td>
      <td class='num'>{s['months_vacant'] if s['months_vacant'] is not None else '—'}</td>
      <td class='num'>{_money(s['carry_cost'])}</td><td class='num'>{_money(s['quoted_rent'])}</td>
      </tr>""" for s in suites)
    prows = ''.join(f"""
      <tr><td>{p['property']}</td><td class='cite'>{p['property_key'] or ''}</td>
      <td class='num'>{_money(p['rentable_sf'])}</td><td class='num'>{_money(p['rented_sf'])}</td>
      <td class='num'>{_money(p['vacant_sf'])}</td>
      <td class='num'>{f"{p['pct_vacant']*100:.1f}%" if p['pct_vacant'] is not None else '—'}</td>
      </tr>""" for p in props)
    body = f"""
      <h1>Vacancy</h1>
      <div class='sub'>vac_ module — suite-level reports are current
        (latest <b>{suite_latest}</b>); the property-level summary is a
        <b>{summary_latest}</b> era rollup, kept for history.</div>
      <h2>Vacant suites as of {suite_latest} ({len(suites)})</h2>
      <table><thead><tr><th>Property</th><th>Unit</th><th class='num'>Vacant SF</th>
        <th class='num'>Vacated</th><th class='num'>Months</th><th class='num'>Carry cost</th>
        <th class='num'>Quoted rent</th></tr></thead><tbody>{srows}</tbody></table>
      <h2>Property summary — historical ({summary_latest}, {len(props)})</h2>
      <table><thead><tr><th>Property</th><th>Key</th><th class='num'>Rentable SF</th>
        <th class='num'>Rented SF</th><th class='num'>Vacant SF</th><th class='num'>% Vacant</th>
        </tr></thead><tbody>{prows}</tbody></table>
    """
    return _page('Vacancy', body, crumb_tail=' &rsaquo; Vacancy')


@own_bp.route('/search')
def search():
    eng = _get_engine()
    if not eng.available():
        return _missing_page()
    q = (request.args.get('q') or '').strip()
    rows = eng.search_provisions(q, limit=200) if len(q) >= 3 else []
    trs = ''.join(f"""
      <tr>
        <td><a href='/portfolio-ownership/property/{r['property_id']}'>{r['property_id']}</a></td>
        <td>{r['tenant_key'] or '—'}</td>
        <td>{(r['category'] or '')[:44]}</td>
        <td class='detail'>{(r['detail'] or '')[:500]}</td>
        <td class='cite'>{(r['source'] or '')[:40]}<br>{r['source_pages'] or ''}</td>
      </tr>""" for r in rows)
    body = f"""
      <h1>Provision search</h1>
      <div class='sub'>Full-text across all {'' if not q else f"— {len(rows)} matches for “{q}”"}
        page-cited provisions, portfolio-wide.</div>
      <form class='filters' method='get'>
        <input type='text' name='q' value='{q}' placeholder='e.g. exclusive, co-tenancy, ROFR…'
               style='min-width:340px'>
        <button>Search</button>
      </form>
      {"<p class='note'>Enter at least 3 characters.</p>" if q and len(q) < 3 else ''}
      <table><thead><tr><th>Property</th><th>Tenant</th><th>Category</th><th>Provision</th>
      <th>Citation</th></tr></thead>
      <tbody>{trs or "<tr><td colspan='5' class='note'>No results.</td></tr>"}</tbody></table>
    """
    return _page('Provision search', body, crumb_tail=' &rsaquo; Search')
