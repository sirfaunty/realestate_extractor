"""
Warehouse API routes — Flask endpoints for the analytical warehouse.

Provides REST API + a dashboard page for exploring warehouse data.
"""

import json
import logging
from flask import Blueprint, jsonify, request, render_template_string

from .engine import WarehouseEngine

logger = logging.getLogger(__name__)

warehouse_bp = Blueprint('warehouse', __name__, url_prefix='/warehouse')

# Lazy-init singleton
_wh = None


def _get_wh():
    global _wh
    if _wh is None:
        _wh = WarehouseEngine()
        _wh.connect()
    return _wh


# ─── API Routes ────────────────────────────────────────────────────

@warehouse_bp.route('/api/summary')
def api_summary():
    """Warehouse summary statistics."""
    wh = _get_wh()
    return jsonify(wh.summary())


@warehouse_bp.route('/api/ingestions')
def api_ingestions():
    """List all data ingestion records."""
    wh = _get_wh()
    return jsonify(wh.get_ingestion_log())


@warehouse_bp.route('/api/property/<property_id>')
def api_property(property_id):
    """Get property details + z-scores."""
    wh = _get_wh()
    props = wh.find_property(address=None, name=None, market=None)  # need direct lookup
    # Direct lookup
    row = wh.conn.execute("""
        SELECT * FROM dim_property
        WHERE property_id = ? AND valid_to = '9999-12-31'
    """, [property_id]).fetchone()

    if not row:
        return jsonify({'error': 'Property not found'}), 404

    cols = [d[0] for d in wh.conn.description]
    prop = dict(zip(cols, row))

    # Get z-scores
    peer_cut = request.args.get('peer_cut')
    scores = wh.get_property_zscores(property_id, peer_cut=peer_cut)

    return jsonify({'property': prop, 'zscores': scores})


@warehouse_bp.route('/api/property/search')
def api_property_search():
    """Search properties by address, name, or market."""
    wh = _get_wh()
    results = wh.find_property(
        address=request.args.get('address'),
        name=request.args.get('name'),
        market=request.args.get('market'),
    )
    return jsonify({'results': results, 'count': len(results)})


@warehouse_bp.route('/api/zscores/<property_id>')
def api_zscores(property_id):
    """Get z-scores for a specific property."""
    wh = _get_wh()
    scores = wh.get_property_zscores(
        property_id,
        peer_cut=request.args.get('peer_cut'),
    )
    return jsonify({'property_id': property_id, 'scores': scores, 'count': len(scores)})


@warehouse_bp.route('/api/cap-rates')
def api_cap_rates():
    """Get cap rate aggregates."""
    wh = _get_wh()
    market = request.args.get('market')
    period_type = request.args.get('period_type', 'year')
    is_clean = request.args.get('clean', 'true').lower() == 'true'

    caps = wh.get_cap_rates(market=market, period_type=period_type, is_clean=is_clean)
    return jsonify({'cap_rates': caps, 'count': len(caps)})


@warehouse_bp.route('/api/sales-comps')
def api_sales_comps():
    """Query sales transactions."""
    wh = _get_wh()
    comps = wh.get_sales_comps(
        market=request.args.get('market'),
        property_id=request.args.get('property_id'),
        min_year=request.args.get('min_year', type=int),
    )
    return jsonify({'transactions': comps, 'count': len(comps)})


@warehouse_bp.route('/api/markets')
def api_markets():
    """List all markets with property counts."""
    wh = _get_wh()
    rows = wh.conn.execute("""
        SELECT market, count(*) as property_count,
               count(DISTINCT submarket) as submarket_count,
               avg(TRY_CAST(num_units AS DOUBLE)) as avg_units
        FROM dim_property
        WHERE market IS NOT NULL AND valid_to = '9999-12-31'
        GROUP BY market
        ORDER BY property_count DESC
    """).fetchall()
    cols = ['market', 'property_count', 'submarket_count', 'avg_units']
    return jsonify({'markets': [dict(zip(cols, r)) for r in rows]})


@warehouse_bp.route('/api/peer-cuts')
def api_peer_cuts():
    """List available peer cut dimensions."""
    wh = _get_wh()
    rows = wh.conn.execute("""
        SELECT DISTINCT peer_cut, count(*) as n
        FROM fact_property_zscore
        GROUP BY peer_cut
        ORDER BY peer_cut
    """).fetchall()
    return jsonify({'peer_cuts': [{'name': r[0], 'count': r[1]} for r in rows]})


@warehouse_bp.route('/api/identity-bridge', methods=['POST'])
def api_identity_bridge():
    """Find CoStar PropertyID for a Capactive property."""
    wh = _get_wh()
    data = request.json
    costar_id = wh.property_identity_bridge(
        capactive_property_id=data['capactive_id'],
        address=data['address'],
        city=data['city'],
        state=data['state'],
    )
    return jsonify({
        'capactive_id': data['capactive_id'],
        'costar_property_id': costar_id,
        'matched': costar_id is not None,
    })


# ─── Dashboard Page ────────────────────────────────────────────────

@warehouse_bp.route('/')
def dashboard():
    """Warehouse dashboard — overview of all analytical data."""
    wh = _get_wh()
    summary = wh.summary()

    # Get scored markets
    scored_markets = wh.conn.execute("""
        SELECT p.market,
               count(DISTINCT z.property_id) as scored,
               count(DISTINCT p.property_id) as total
        FROM dim_property p
        LEFT JOIN fact_property_zscore z ON p.property_id = z.property_id
        WHERE p.market IS NOT NULL AND p.valid_to = '9999-12-31'
        GROUP BY p.market
        HAVING scored > 0
        ORDER BY scored DESC
    """).fetchall()

    # Top sales comp markets
    top_sales = wh.conn.execute("""
        SELECT market, count(*) as deals,
               CAST(median(sale_price) AS BIGINT) as med_price,
               min(sale_year) as min_year, max(sale_year) as max_year
        FROM fact_sales_transaction
        WHERE market IS NOT NULL AND sale_price IS NOT NULL
        GROUP BY market
        ORDER BY deals DESC
        LIMIT 15
    """).fetchall()

    # Ingestion log
    ingestions = wh.get_ingestion_log()

    return render_template_string(DASHBOARD_HTML,
                                  summary=summary,
                                  scored_markets=scored_markets,
                                  top_sales=top_sales,
                                  ingestions=ingestions)


DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Warehouse — Capactive</title>
<style>
:root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --orange: #d29922; --red: #f85149;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 14px; }
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }
h1 { font-size: 24px; margin-bottom: 6px; }
h2 { font-size: 18px; margin: 24px 0 12px; color: var(--accent); }
.subtitle { color: var(--text2); margin-bottom: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
.card .label { color: var(--text2); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
.card .value.green { color: var(--green); }
table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
th { background: var(--border); text-align: left; padding: 8px 12px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); }
td { padding: 8px 12px; border-top: 1px solid var(--border); }
tr:hover td { background: rgba(88,166,255,0.05); }
.mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }
.right { text-align: right; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.nav { display: flex; gap: 16px; margin-bottom: 24px; }
.nav a { padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px; }
.nav a:hover { border-color: var(--accent); text-decoration: none; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.badge.green { background: rgba(63,185,80,0.2); color: var(--green); }
.badge.orange { background: rgba(210,153,34,0.2); color: var(--orange); }
</style>
</head>
<body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/warehouse">Warehouse</a>
        <a href="/warehouse/api/summary">API</a>
    </div>

    <h1>Analytical Warehouse</h1>
    <p class="subtitle">DuckDB-backed bitemporal data store — Zone A/B/C architecture</p>

    <div class="grid">
        <div class="card">
            <div class="label">Properties</div>
            <div class="value green">{{ "{:,}".format(summary.dim_property) }}</div>
        </div>
        <div class="card">
            <div class="label">Z-Score Rows</div>
            <div class="value">{{ "{:,}".format(summary.fact_property_zscore) }}</div>
        </div>
        <div class="card">
            <div class="label">Sales Transactions</div>
            <div class="value">{{ "{:,}".format(summary.fact_sales_transaction) }}</div>
        </div>
        <div class="card">
            <div class="label">Cap Rate Aggs</div>
            <div class="value">{{ "{:,}".format(summary.fact_cap_rate_aggregate) }}</div>
        </div>
        <div class="card">
            <div class="label">Pricing Aggs</div>
            <div class="value">{{ "{:,}".format(summary.fact_pricing_aggregate) }}</div>
        </div>
        <div class="card">
            <div class="label">Ownership Records</div>
            <div class="value">{{ "{:,}".format(summary.fact_ownership) }}</div>
        </div>
        <div class="card">
            <div class="label">Markets</div>
            <div class="value">{{ summary.markets }}</div>
        </div>
        <div class="card">
            <div class="label">Ingestions</div>
            <div class="value">{{ summary.ingestion_count }}</div>
        </div>
    </div>

    <h2>Z-Score Coverage by Market</h2>
    <table>
        <tr><th>Market</th><th class="right">Scored Properties</th><th class="right">Total Properties</th><th class="right">Coverage</th></tr>
        {% for market, scored, total in scored_markets %}
        <tr>
            <td><a href="/warehouse/api/property/search?market={{ market }}">{{ market }}</a></td>
            <td class="right mono">{{ "{:,}".format(scored) }}</td>
            <td class="right mono">{{ "{:,}".format(total) }}</td>
            <td class="right"><span class="badge {{ 'green' if scored/total > 0.5 else 'orange' }}">{{ "%.0f"|format(scored/total*100) }}%</span></td>
        </tr>
        {% endfor %}
    </table>

    <h2>Top Sales Comp Markets</h2>
    <table>
        <tr><th>Market</th><th class="right">Deals</th><th class="right">Median Price</th><th class="right">Years</th></tr>
        {% for market, deals, med_price, min_yr, max_yr in top_sales %}
        <tr>
            <td><a href="/warehouse/api/sales-comps?market={{ market }}">{{ market }}</a></td>
            <td class="right mono">{{ "{:,}".format(deals) }}</td>
            <td class="right mono">${{ "{:,}".format(med_price) }}</td>
            <td class="right mono">{{ min_yr|int }}–{{ max_yr|int }}</td>
        </tr>
        {% endfor %}
    </table>

    <h2>Ingestion Log (Zone A)</h2>
    <table>
        <tr><th>#</th><th>Source</th><th>Vintage</th><th>Knowledge Date</th><th class="right">Records</th><th>Ingested At</th></tr>
        {% for ing in ingestions %}
        <tr>
            <td class="mono">{{ ing.ingestion_id }}</td>
            <td>{{ ing.source }}</td>
            <td>{{ ing.source_vintage or '—' }}</td>
            <td class="mono">{{ ing.knowledge_date }}</td>
            <td class="right mono">{{ "{:,}".format(ing.record_count or 0) }}</td>
            <td class="mono" style="color:var(--text2)">{{ ing.ingested_at }}</td>
        </tr>
        {% endfor %}
    </table>

    <h2>API Endpoints</h2>
    <table>
        <tr><th>Endpoint</th><th>Description</th><th>Parameters</th></tr>
        <tr><td class="mono">/warehouse/api/summary</td><td>Warehouse stats</td><td>—</td></tr>
        <tr><td class="mono">/warehouse/api/markets</td><td>All markets with counts</td><td>—</td></tr>
        <tr><td class="mono">/warehouse/api/property/search</td><td>Search properties</td><td>address, name, market</td></tr>
        <tr><td class="mono">/warehouse/api/property/&lt;id&gt;</td><td>Property detail + z-scores</td><td>peer_cut</td></tr>
        <tr><td class="mono">/warehouse/api/zscores/&lt;id&gt;</td><td>Z-scores for property</td><td>peer_cut</td></tr>
        <tr><td class="mono">/warehouse/api/cap-rates</td><td>Cap rate aggregates</td><td>market, period_type, clean</td></tr>
        <tr><td class="mono">/warehouse/api/sales-comps</td><td>Sales transactions</td><td>market, property_id, min_year</td></tr>
        <tr><td class="mono">/warehouse/api/peer-cuts</td><td>Available peer cuts</td><td>—</td></tr>
        <tr><td class="mono">/warehouse/api/ingestions</td><td>Ingestion log</td><td>—</td></tr>
        <tr><td class="mono">/warehouse/api/identity-bridge</td><td>SQLite↔CoStar bridge</td><td>POST: capactive_id, address, city, state</td></tr>
    </table>
</div>
</body>
</html>
"""
