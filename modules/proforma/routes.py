"""
Flask routes for the Proforma module.

Routes:
  GET  /property/<id>/proforma           — proforma dashboard with drillback
  GET  /property/<id>/proforma/drillback — self-contained drillback HTML
  GET  /property/<id>/proforma/data.json — raw cited data as JSON API
"""

import json
import logging
from datetime import datetime

from flask import (
    render_template, request, redirect, url_for,
    flash, jsonify, session, Response
)

logger = logging.getLogger(__name__)


def register_proforma_routes(app):
    """Register proforma routes with the Flask app."""

    # Import here to avoid circular imports at module load time
    from ...webapp import login_required, get_org_db

    @app.route('/property/<int:property_id>/proforma')
    @login_required
    def proforma_dashboard(property_id):
        """Proforma dashboard with inline drillback citations."""
        org_id = session['org_id']
        db = get_org_db(org_id)
        try:
            prop = db.get_property(property_id)
            if not prop:
                flash('Property not found.', 'error')
                return redirect(url_for('properties'))

            from .bridge import generate_drillback_data
            drillback = generate_drillback_data(db, property_id)

            # Prepare template data
            registry = drillback['registry']
            financials = drillback['financials']

            # Build period summary for the NOI table
            periods = {}
            for period_key, period_data in financials['periods'].items():
                income = period_data['income']
                expense = period_data['expense']
                noi = income - expense
                periods[period_key] = {
                    'income': income,
                    'expense': expense,
                    'noi': noi,
                    'income_citation_count': len(period_data['income_citations']),
                    'expense_citation_count': len(period_data['expense_citations']),
                }

            # Sort periods naturally
            sorted_periods = sorted(periods.items(), key=lambda x: x[0])

            # Build source documents list for the registry panel
            source_docs = []
            for doc_id, doc in registry.documents.items():
                source_docs.append({
                    'id': doc_id,
                    'title': doc.title,
                    'doc_type': doc.doc_type.value,
                    'authority_tier': doc.authority_tier.value,
                    'file_path': str(doc.file_path) if doc.file_path else None,
                })

            # Group income/expense items by period for detail tables
            income_by_period = {}
            for item in financials['income_items']:
                p = item['period']
                if p not in income_by_period:
                    income_by_period[p] = []
                income_by_period[p].append({
                    'line_item': item['line_item'],
                    'amount': item['amount'].value,
                    'is_subtotal': item['is_subtotal'],
                    'citation': _citation_to_dict(item['amount'].primary_citation, registry),
                })

            expense_by_period = {}
            for item in financials['expense_items']:
                p = item['period']
                if p not in expense_by_period:
                    expense_by_period[p] = []
                expense_by_period[p].append({
                    'line_item': item['line_item'],
                    'amount': item['amount'].value,
                    'is_subtotal': item['is_subtotal'],
                    'citation': _citation_to_dict(item['amount'].primary_citation, registry),
                })

            # Financial terms grouped by category
            terms_by_category = {}
            for term in financials['financial_terms']:
                cat = term.get('category', 'other') or 'other'
                if cat not in terms_by_category:
                    terms_by_category[cat] = []
                terms_by_category[cat].append({
                    'term_name': term['term_name'],
                    'value': term['value'].value,
                    'citation': _citation_to_dict(term['value'].primary_citation, registry),
                })

            return render_template('proforma_dashboard.html',
                property=prop,
                periods=sorted_periods,
                source_docs=source_docs,
                income_by_period=income_by_period,
                expense_by_period=expense_by_period,
                terms_by_category=terms_by_category,
                rent_roll_count=len(financials['rent_roll']),
                total_docs=len(registry),
                total_citations=sum(
                    len(p['income_citations']) + len(p['expense_citations'])
                    for p in financials['periods'].values()
                ),
            )
        finally:
            db.close()

    @app.route('/property/<int:property_id>/proforma/drillback')
    @login_required
    def proforma_drillback(property_id):
        """Serve the self-contained drillback HTML.

        If the chamberlain engine has generated a DrillBack.html output,
        serve it directly. Otherwise generate a citation-traced view
        from extracted data.
        """
        org_id = session['org_id']
        db = get_org_db(org_id)
        try:
            prop = db.get_property(property_id)
            if not prop:
                return Response('Property not found', status=404)

            from .bridge import generate_drillback_data
            drillback = generate_drillback_data(db, property_id)
            html_content = _render_drillback_html(prop, drillback)
            return Response(html_content, mimetype='text/html')
        finally:
            db.close()

    @app.route('/property/<int:property_id>/proforma/data.json')
    @login_required
    def proforma_data_json(property_id):
        """Return cited financial data as JSON for API consumers."""
        org_id = session['org_id']
        db = get_org_db(org_id)
        try:
            from .bridge import generate_drillback_data
            drillback = generate_drillback_data(db, property_id)
            registry = drillback['registry']
            financials = drillback['financials']

            # Serialize — convert Cited objects to plain dicts
            data = {
                'property': drillback['property'],
                'source_documents': {
                    doc_id: {
                        'title': doc.title,
                        'doc_type': doc.doc_type.value,
                        'authority_tier': doc.authority_tier.value,
                    }
                    for doc_id, doc in registry.documents.items()
                },
                'periods': {
                    k: {'income': v['income'], 'expense': v['expense'],
                         'noi': v['income'] - v['expense']}
                    for k, v in financials['periods'].items()
                },
                'income_item_count': len(financials['income_items']),
                'expense_item_count': len(financials['expense_items']),
                'financial_term_count': len(financials['financial_terms']),
                'rent_roll_count': len(financials['rent_roll']),
            }
            return jsonify(data)
        finally:
            db.close()


def _citation_to_dict(citation, registry):
    """Convert a Citation to a template-friendly dict."""
    doc = registry.get(citation.source_document_id)
    return {
        'source_doc_id': citation.source_document_id,
        'source_title': doc.title if doc else citation.source_document_id,
        'authority_tier': doc.authority_tier.value if doc else 'unknown',
        'doc_type': doc.doc_type.value if doc else 'unknown',
        'locator': citation.locator.render(),
        'verbatim_text': citation.verbatim_text,
        'confidence': citation.confidence,
        'extraction_method': citation.extraction_method,
    }


def _render_drillback_html(prop, drillback):
    """Render a self-contained drillback HTML page from extracted data."""
    import html as html_mod

    registry = drillback['registry']
    financials = drillback['financials']
    periods = financials['periods']
    sorted_periods = sorted(periods.items(), key=lambda x: x[0])

    # Build docs JSON for the JS popover
    docs_json = {}
    for doc_id, doc in registry.documents.items():
        docs_json[doc_id] = {
            'title': doc.title,
            'tier': doc.authority_tier.value,
            'dtype': doc.doc_type.value,
        }

    def cite_span(label, amount, citation):
        """Render a clickable citation span."""
        disp = f"${abs(amount):,.0f}" if amount >= 0 else f"(${abs(amount):,.0f})"
        loc = citation.locator.render()
        payload = json.dumps({
            'label': label,
            'doc': citation.source_document_id,
            'loc': loc,
            'verb': citation.verbatim_text or '',
        })
        return f'<span class="cite" onclick=\'showPop(event,{payload})\'>{html_mod.escape(disp)}</span>'

    # Build HTML
    lines = []
    lines.append('<!DOCTYPE html><html><head><meta charset="utf-8">')
    lines.append(f'<title>Drillback — {html_mod.escape(prop["name"])}</title>')
    lines.append('<style>')
    lines.append(_DRILLBACK_CSS)
    lines.append('</style></head><body>')
    lines.append('<div class="wrap">')

    # Header
    lines.append(f'<h1>{html_mod.escape(prop["name"])} — Citation Drillback</h1>')
    lines.append(f'<p class="sub">Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} '
                 f'| {len(registry)} source documents '
                 f'| {len(sorted_periods)} periods</p>')

    # KPI cards
    total_income = sum(p['income'] for _, p in sorted_periods)
    total_expense = sum(p['expense'] for _, p in sorted_periods)
    total_citations = sum(
        len(p['income_citations']) + len(p['expense_citations'])
        for _, p in sorted_periods
    )
    lines.append('<div class="kpis">')
    lines.append(f'<div class="kpi"><div class="v">{len(registry)}</div><div class="l">Source Documents</div></div>')
    lines.append(f'<div class="kpi"><div class="v">{total_citations}</div><div class="l">Traced Citations</div></div>')
    lines.append(f'<div class="kpi"><div class="v">{len(sorted_periods)}</div><div class="l">Financial Periods</div></div>')
    lines.append(f'<div class="kpi"><div class="v">{len(financials["financial_terms"])}</div><div class="l">Extracted Terms</div></div>')
    lines.append('</div>')

    # NOI Timeline table with citations
    lines.append('<h2>NOI Timeline — Every Figure Traced to Source</h2>')
    lines.append('<table><thead><tr><th>Period</th><th>Income</th><th>Expenses</th><th>NOI</th><th>Citations</th></tr></thead><tbody>')
    for period_key, pdata in sorted_periods:
        income = pdata['income']
        expense = pdata['expense']
        noi = income - expense
        cite_count = len(pdata['income_citations']) + len(pdata['expense_citations'])
        lines.append(f'<tr><td>{html_mod.escape(period_key)}</td>')
        lines.append(f'<td>${income:,.0f}</td>')
        lines.append(f'<td>${expense:,.0f}</td>')
        noi_class = '' if noi >= 0 else ' style="color:#c0392b"'
        lines.append(f'<td{noi_class}>${noi:,.0f}</td>')
        lines.append(f'<td>{cite_count}</td></tr>')
    lines.append('</tbody></table>')

    # Income detail with citations
    lines.append('<h2>Income Detail — Cited Line Items</h2>')
    for item in financials['income_items']:
        if item['is_subtotal']:
            continue
        c = item['amount'].primary_citation
        label = f"{item['line_item']} ({item['period']})"
        amt = item['amount'].value
        span = cite_span(label, amt, c)
        lines.append(f'<div class="row"><span class="lab">{html_mod.escape(item["line_item"])} '
                     f'<span class="period-tag">{html_mod.escape(item["period"])}</span></span>'
                     f'<span class="val">{span}</span></div>')

    # Expense detail with citations
    lines.append('<h2>Expense Detail — Cited Line Items</h2>')
    for item in financials['expense_items']:
        if item['is_subtotal']:
            continue
        c = item['amount'].primary_citation
        label = f"{item['line_item']} ({item['period']})"
        amt = item['amount'].value
        span = cite_span(label, amt, c)
        lines.append(f'<div class="row"><span class="lab">{html_mod.escape(item["line_item"])} '
                     f'<span class="period-tag">{html_mod.escape(item["period"])}</span></span>'
                     f'<span class="val">{span}</span></div>')

    # Financial terms
    if financials['financial_terms']:
        lines.append('<h2>Financial Terms — Extracted with Source</h2>')
        for term in financials['financial_terms']:
            c = term['value'].primary_citation
            doc = registry.get(c.source_document_id)
            tier = doc.authority_tier.value if doc else 'unknown'
            loc = c.locator.render()
            tname = term["term_name"] or "(unnamed term)"
            lines.append(f'<div class="row"><span class="lab">{html_mod.escape(tname)}'
                         f' <span class="chip" onclick=\'showPop(event,{json.dumps({"label":tname,"doc":c.source_document_id,"loc":loc,"verb":c.verbatim_text or ""})})\'>'
                         f'src</span></span>'
                         f'<span class="val">{html_mod.escape(str(term["value"].value))}</span></div>')

    # Source document registry
    lines.append('<h2>Source Document Registry</h2>')
    lines.append('<table><thead><tr><th>Document</th><th>Type</th><th>Authority</th></tr></thead><tbody>')
    for doc_id, doc in sorted(registry.documents.items(), key=lambda x: x[1].authority_tier.rank, reverse=True):
        tier_class = f'tier-{doc.authority_tier.value}'
        lines.append(f'<tr><td>{html_mod.escape(doc.title)}</td>')
        lines.append(f'<td>{html_mod.escape(doc.doc_type.value)}</td>')
        lines.append(f'<td><span class="{tier_class}">{doc.authority_tier.value.upper()}</span></td></tr>')
    lines.append('</tbody></table>')

    # Footer
    lines.append(f'<footer>Capactive Document Extractor — Proforma Module v0.1.0 — '
                 f'{datetime.now().strftime("%Y-%m-%d %H:%M")}</footer>')
    lines.append('</div>')

    # Popover + backdrop
    lines.append('<div id="bd" class="backdrop" onclick="hidePop()"></div>')
    lines.append('<div id="pop" class="popover"></div>')

    # JavaScript
    lines.append('<script>')
    lines.append(f'const docs = {json.dumps(docs_json)};')
    lines.append(_DRILLBACK_JS)
    lines.append('</script>')
    lines.append('</body></html>')

    return '\n'.join(lines)


_DRILLBACK_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
       margin: 0; padding: 0; color: #1f2a44; background: #f5f6f8; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 32px 24px 80px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 36px 0 12px; padding-bottom: 6px;
     border-bottom: 2px solid #1f2a44; }
.sub { color: #5a6477; font-size: 14px; margin: 0 0 4px; }
.kpis { display: flex; flex-wrap: wrap; gap: 14px; margin: 20px 0; }
.kpi { background: #fff; border: 1px solid #d9dde5; border-radius: 8px;
       padding: 14px 18px; min-width: 150px; }
.kpi .v { font-size: 22px; font-weight: 700; }
.kpi .l { font-size: 12px; color: #5a6477; text-transform: uppercase;
          letter-spacing: .04em; margin-top: 4px; }
table { border-collapse: collapse; width: 100%; background: #fff;
        font-size: 13px; border: 1px solid #d9dde5; margin-bottom: 24px; }
th, td { padding: 7px 10px; text-align: right; border-bottom: 1px solid #eceef2; }
th { background: #1f2a44; color: #fff; font-weight: 600; }
th:first-child, td:first-child { text-align: left; }
tr:hover td { background: #f0f3f9; }
.cite { color: #2f6fd0; cursor: pointer; text-decoration: none;
        border-bottom: 1px dotted #2f6fd0; }
.cite:hover { background: #eaf1fb; }
.chip { display: inline-block; font-size: 11px; background: #eaf1fb;
        color: #2f6fd0; border-radius: 10px; padding: 1px 7px; margin-left: 6px;
        cursor: pointer; border: 1px solid #c7dbf5; }
.chip:hover { background: #d6e6fb; }
.tier-primary { color: #1a7f37; font-weight: 600; }
.tier-secondary { color: #9a6700; font-weight: 600; }
.tier-tertiary { color: #8250df; }
.popover { position: fixed; z-index: 1000; max-width: 420px; background: #fff;
           border: 1px solid #b0b7c3; border-radius: 8px; padding: 14px 16px;
           box-shadow: 0 8px 28px rgba(0,0,0,.18); font-size: 13px;
           display: none; }
.popover h4 { margin: 0 0 6px; font-size: 13px; }
.popover .verb { font-style: italic; color: #444; background: #f5f6f8;
                 padding: 6px 8px; border-radius: 4px; margin: 6px 0;
                 word-break: break-word; }
.popover .meta { color: #5a6477; font-size: 12px; }
.backdrop { position: fixed; inset: 0; z-index: 999; display: none; }
.row { display: flex; justify-content: space-between; padding: 5px 0;
       border-bottom: 1px solid #eceef2; }
.row .lab { color: #3a4256; }
.row .val { font-weight: 600; }
.period-tag { display: inline-block; font-size: 11px; background: #e8ecf4;
              color: #3a4256; border-radius: 4px; padding: 1px 5px; margin-left: 4px; }
footer { margin-top: 50px; color: #8a93a5; font-size: 12px; }
"""

_DRILLBACK_JS = """
function showPop(e, payload) {
  e.stopPropagation();
  const pop = document.getElementById('pop');
  const bd = document.getElementById('bd');
  const d = docs[payload.doc] || {title: payload.doc, tier:'?', dtype:'?'};
  pop.innerHTML = '<h4>' + esc(payload.label) + '</h4>'
    + '<div class="meta"><b>Source:</b> ' + esc(d.title) + '</div>'
    + '<div class="meta"><b>Authority:</b> <span class="tier-' + d.tier + '">'
      + d.tier.toUpperCase() + '</span> &nbsp; <b>Type:</b> ' + esc(d.dtype) + '</div>'
    + '<div class="meta"><b>Locator:</b> ' + esc(payload.loc || '(unspecified)') + '</div>'
    + (payload.verb ? '<div class="verb">"' + esc(payload.verb) + '"</div>' : '')
    + (payload.note ? '<div class="meta">' + esc(payload.note) + '</div>' : '');
  pop.style.display = 'block';
  bd.style.display = 'block';
  const x = Math.min(e.clientX, window.innerWidth - 440);
  const y = Math.min(e.clientY + 12, window.innerHeight - 200);
  pop.style.left = Math.max(10, x) + 'px';
  pop.style.top = Math.max(10, y) + 'px';
}
function hidePop() {
  document.getElementById('pop').style.display = 'none';
  document.getElementById('bd').style.display = 'none';
}
function esc(s) { return (s == null ? '' : String(s))
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;'); }
"""
