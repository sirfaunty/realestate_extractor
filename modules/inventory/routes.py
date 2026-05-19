"""
Inventory module routes — property z-score benchmarking UI + API.
"""

import json
import logging
from flask import Blueprint, jsonify, request, render_template_string

from .engine import InventoryEngine

logger = logging.getLogger(__name__)

inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from warehouse.engine import WarehouseEngine
        wh = WarehouseEngine()
        wh.connect()
        _engine = InventoryEngine(wh)
    return _engine


def register_inventory_routes(app):
    """Register the inventory blueprint with the Flask app."""
    app.register_blueprint(inventory_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@inventory_bp.route('/')
def index():
    """Inventory dashboard — scored markets overview."""
    eng = _get_engine()
    markets = eng.get_scored_markets()
    summary = eng.wh.summary()
    return render_template_string(INDEX_HTML, markets=markets, summary=summary)


@inventory_bp.route('/market/<market_name>')
def market_detail(market_name):
    """Market detail page — properties and stats for a market."""
    eng = _get_engine()
    stats = eng.get_market_stats(market_name)
    properties = eng.search_properties(market=market_name, scored_only=True, limit=100)
    return render_template_string(MARKET_HTML, stats=stats, properties=properties)


@inventory_bp.route('/property/<property_id>')
def property_detail(property_id):
    """Property z-score detail page."""
    eng = _get_engine()
    profile = eng.get_property_profile(property_id)
    if not profile:
        return render_template_string(NOT_FOUND_HTML, property_id=property_id), 404

    peer_cut = request.args.get('peer_cut')
    sort_by = request.args.get('sort', 'abs_z')
    scores = eng.get_zscores(property_id, peer_cut=peer_cut, sort_by=sort_by, limit=100)
    outliers = eng.get_outlier_metrics(property_id, threshold=2.0, peer_cut=peer_cut)
    return render_template_string(PROPERTY_HTML,
                                  profile=profile, scores=scores, outliers=outliers,
                                  current_peer_cut=peer_cut, current_sort=sort_by)


@inventory_bp.route('/search')
def search():
    """Property search page."""
    eng = _get_engine()
    q = request.args.get('q', '')
    market = request.args.get('market', '')
    results = []
    if q or market:
        results = eng.search_properties(query=q or None, market=market or None, limit=50)
    return render_template_string(SEARCH_HTML, query=q, market=market, results=results)


# ─── API ───────────────────────────────────────────────────────────

@inventory_bp.route('/api/property/<property_id>')
def api_property(property_id):
    eng = _get_engine()
    profile = eng.get_property_profile(property_id)
    if not profile:
        return jsonify({'error': 'Property not found'}), 404
    return jsonify(profile)


@inventory_bp.route('/api/property/<property_id>/zscores')
def api_zscores(property_id):
    eng = _get_engine()
    peer_cut = request.args.get('peer_cut')
    sort_by = request.args.get('sort', 'abs_z')
    limit = request.args.get('limit', 50, type=int)
    scores = eng.get_zscores(property_id, peer_cut=peer_cut, sort_by=sort_by, limit=limit)
    return jsonify({'property_id': property_id, 'scores': scores, 'count': len(scores)})


@inventory_bp.route('/api/property/<property_id>/outliers')
def api_outliers(property_id):
    eng = _get_engine()
    threshold = request.args.get('threshold', 2.0, type=float)
    peer_cut = request.args.get('peer_cut')
    outliers = eng.get_outlier_metrics(property_id, threshold=threshold, peer_cut=peer_cut)
    return jsonify(outliers)


@inventory_bp.route('/api/property/<property_id>/peers')
def api_peers(property_id):
    eng = _get_engine()
    peer_cut = request.args.get('peer_cut', 'Market × Size × Quality')
    peers = eng.get_peer_properties(property_id, peer_cut)
    return jsonify({'property_id': property_id, 'peer_cut': peer_cut,
                    'peers': peers, 'count': len(peers)})


@inventory_bp.route('/api/market/<market_name>')
def api_market(market_name):
    eng = _get_engine()
    return jsonify(eng.get_market_stats(market_name))


@inventory_bp.route('/api/markets')
def api_markets():
    eng = _get_engine()
    return jsonify({'markets': eng.get_scored_markets()})


@inventory_bp.route('/api/search')
def api_search():
    eng = _get_engine()
    results = eng.search_properties(
        query=request.args.get('q'),
        market=request.args.get('market'),
        min_units=request.args.get('min_units', type=int),
        max_units=request.args.get('max_units', type=int),
        building_class=request.args.get('class'),
        scored_only=request.args.get('scored_only', 'false').lower() == 'true',
        limit=request.args.get('limit', 50, type=int),
    )
    return jsonify({'results': results, 'count': len(results)})


@inventory_bp.route('/api/bridge', methods=['POST'])
def api_bridge():
    eng = _get_engine()
    data = request.json
    result = eng.bridge_property(
        capactive_id=data['capactive_id'],
        address=data['address'],
        city=data['city'],
        state=data['state'],
    )
    return jsonify({
        'matched': result is not None,
        'profile': result,
    })


# ─── HTML Templates ───────────────────────────────────────────────

_STYLE = """
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
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
.card .label { color: var(--text2); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
.card .value.green { color: var(--green); }
table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 24px; }
th { background: var(--border); text-align: left; padding: 8px 12px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2); }
td { padding: 8px 12px; border-top: 1px solid var(--border); }
tr:hover td { background: rgba(88,166,255,0.05); }
.mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }
.right { text-align: right; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.nav { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
.nav a { padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px; }
.nav a:hover { border-color: var(--accent); text-decoration: none; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.badge.green { background: rgba(63,185,80,0.2); color: var(--green); }
.badge.orange { background: rgba(210,153,34,0.2); color: var(--orange); }
.badge.red { background: rgba(248,81,73,0.2); color: var(--red); }
.badge.blue { background: rgba(88,166,255,0.2); color: var(--accent); }
.z-bar { display: inline-block; height: 12px; border-radius: 2px; min-width: 2px; }
.z-pos { background: var(--green); }
.z-neg { background: var(--red); }
input[type="text"], select { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 8px 12px; border-radius: 6px; font-size: 14px; }
input[type="text"]:focus, select:focus { outline: none; border-color: var(--accent); }
.search-bar { display: flex; gap: 8px; margin-bottom: 24px; }
.search-bar input { flex: 1; }
.btn { padding: 8px 16px; background: var(--accent); color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
.btn:hover { opacity: 0.9; }
.prop-header { display: flex; gap: 24px; align-items: flex-start; margin-bottom: 24px; }
.prop-header .info { flex: 1; }
.prop-header .stats { display: flex; gap: 12px; }
</style>
"""

INDEX_HTML = """
<!DOCTYPE html><html><head><title>Inventory — Capactive</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/warehouse">Warehouse</a>
        <a href="/inventory">Inventory</a>
        <a href="/inventory/search">Search</a>
    </div>
    <h1>National Inventory</h1>
    <p class="subtitle">Property z-score benchmarking across {{ "{:,}".format(summary.dim_property) }} properties</p>
    <div class="grid">
        <div class="card"><div class="label">Properties</div><div class="value green">{{ "{:,}".format(summary.dim_property) }}</div></div>
        <div class="card"><div class="label">Z-Score Rows</div><div class="value">{{ "{:,}".format(summary.fact_property_zscore) }}</div></div>
        <div class="card"><div class="label">Scored Markets</div><div class="value">{{ markets|length }}</div></div>
        <div class="card"><div class="label">Peer Stats</div><div class="value">{{ "{:,}".format(summary.fact_peer_group_stats) }}</div></div>
    </div>
    <h2>Scored Markets</h2>
    <table>
        <tr><th>Market</th><th class="right">Scored</th><th class="right">Total</th><th class="right">Coverage</th><th class="right">Peer Cuts</th><th class="right">Metrics</th></tr>
        {% for m in markets %}
        <tr>
            <td><a href="/inventory/market/{{ m.market }}">{{ m.market }}</a></td>
            <td class="right mono">{{ "{:,}".format(m.scored_properties) }}</td>
            <td class="right mono">{{ "{:,}".format(m.total_properties) }}</td>
            <td class="right"><span class="badge {{ 'green' if m.scored_properties/m.total_properties > 0.5 else 'orange' }}">{{ "%.0f"|format(m.scored_properties/m.total_properties*100) }}%</span></td>
            <td class="right mono">{{ m.peer_cuts }}</td>
            <td class="right mono">{{ m.metrics }}</td>
        </tr>
        {% endfor %}
    </table>
</div>
</body></html>
"""

MARKET_HTML = """
<!DOCTYPE html><html><head><title>{{ stats.market }} — Inventory</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/inventory">← Inventory</a>
        <a href="/inventory/search?market={{ stats.market }}">Search This Market</a>
    </div>
    <h1>{{ stats.market }}</h1>
    <div class="grid">
        <div class="card"><div class="label">Properties</div><div class="value green">{{ "{:,}".format(stats.total_properties) }}</div></div>
        <div class="card"><div class="label">Scored</div><div class="value">{{ "{:,}".format(stats.scored_properties) }}</div></div>
        <div class="card"><div class="label">Coverage</div><div class="value">{{ stats.coverage_pct }}%</div></div>
        <div class="card"><div class="label">Submarkets</div><div class="value">{{ stats.submarkets }}</div></div>
        <div class="card"><div class="label">Total Units</div><div class="value">{{ "{:,.0f}".format(stats.total_units or 0) }}</div></div>
        <div class="card"><div class="label">Avg Units</div><div class="value">{{ "{:.0f}".format(stats.avg_units or 0) }}</div></div>
    </div>
    {% if stats.building_classes %}
    <h2>Building Class Distribution</h2>
    <div class="grid">
        {% for cls, n in stats.building_classes.items() %}
        <div class="card"><div class="label">Class {{ cls }}</div><div class="value">{{ "{:,}".format(n) }}</div></div>
        {% endfor %}
    </div>
    {% endif %}
    <h2>Scored Properties</h2>
    <table>
        <tr><th>Property</th><th>Address</th><th class="right">Units</th><th class="right">Built</th><th>Class</th></tr>
        {% for p in properties %}
        <tr>
            <td><a href="/inventory/property/{{ p.property_id }}">{{ p.property_name or '(unnamed)' }}</a></td>
            <td>{{ p.address }}, {{ p.city }}</td>
            <td class="right mono">{{ p.num_units or '—' }}</td>
            <td class="right mono">{{ p.year_built or '—' }}</td>
            <td>{{ p.building_class or '—' }}</td>
        </tr>
        {% endfor %}
    </table>
</div>
</body></html>
"""

PROPERTY_HTML = """
<!DOCTYPE html><html><head><title>{{ profile.property_name or profile.property_id }} — Z-Scores</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/inventory">← Inventory</a>
        {% if profile.market %}<a href="/inventory/market/{{ profile.market }}">← {{ profile.market }}</a>{% endif %}
    </div>
    <div class="prop-header">
        <div class="info">
            <h1>{{ profile.property_name or 'Property ' + profile.property_id }}</h1>
            <p class="subtitle">{{ profile.address }}, {{ profile.city }}, {{ profile.state }} {{ profile.zip or '' }}</p>
        </div>
    </div>
    <div class="grid">
        <div class="card"><div class="label">CoStar ID</div><div class="value mono" style="font-size:18px">{{ profile.property_id }}</div></div>
        <div class="card"><div class="label">Units</div><div class="value">{{ profile.num_units or '—' }}</div></div>
        <div class="card"><div class="label">Built</div><div class="value">{{ profile.year_built or '—' }}</div></div>
        <div class="card"><div class="label">Class</div><div class="value">{{ profile.building_class or '—' }}</div></div>
        <div class="card"><div class="label">Z-Scores</div><div class="value green">{{ "{:,}".format(profile.total_zscores) }}</div></div>
        <div class="card"><div class="label">Market</div><div class="value" style="font-size:16px">{{ profile.market or '—' }}</div></div>
    </div>

    {% if profile.zscore_summary %}
    <h2>Z-Score Coverage by Peer Cut</h2>
    <table>
        <tr><th>Peer Cut</th><th class="right">Metrics</th><th class="right">Avg Z</th><th class="right">Min Z</th><th class="right">Max Z</th><th></th></tr>
        {% for c in profile.zscore_summary %}
        <tr>
            <td><a href="/inventory/property/{{ profile.property_id }}?peer_cut={{ c.peer_cut|urlencode }}">{{ c.peer_cut }}</a></td>
            <td class="right mono">{{ c.metrics }}</td>
            <td class="right mono">{{ "{:+.2f}".format(c.avg_z) if c.avg_z is not none else '—' }}</td>
            <td class="right mono">{{ "{:+.2f}".format(c.min_z) if c.min_z is not none else '—' }}</td>
            <td class="right mono">{{ "{:+.2f}".format(c.max_z) if c.max_z is not none else '—' }}</td>
            <td><a href="/inventory/property/{{ profile.property_id }}?peer_cut={{ c.peer_cut|urlencode }}">View →</a></td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}

    {% if outliers.strengths or outliers.weaknesses %}
    <h2>Significant Outliers (|z| > 2.0){{ ' — ' + current_peer_cut if current_peer_cut else '' }}</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
            <h3 style="color:var(--green);margin-bottom:8px;">Strengths (z > +2.0)</h3>
            {% if outliers.strengths %}
            <table>
                <tr><th>Metric</th><th class="right">Z</th><th class="right">Value</th><th class="right">Peer Mean</th></tr>
                {% for s in outliers.strengths[:15] %}
                <tr>
                    <td>{{ s.metric }}</td>
                    <td class="right mono" style="color:var(--green)">{{ "{:+.2f}".format(s.z_score) }}</td>
                    <td class="right mono">{{ "{:,.1f}".format(s.value) if s.value is not none else '—' }}</td>
                    <td class="right mono" style="color:var(--text2)">{{ "{:,.1f}".format(s.peer_mean) if s.peer_mean is not none else '—' }}</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}<p style="color:var(--text2)">None above threshold</p>{% endif %}
        </div>
        <div>
            <h3 style="color:var(--red);margin-bottom:8px;">Weaknesses (z < -2.0)</h3>
            {% if outliers.weaknesses %}
            <table>
                <tr><th>Metric</th><th class="right">Z</th><th class="right">Value</th><th class="right">Peer Mean</th></tr>
                {% for w in outliers.weaknesses[:15] %}
                <tr>
                    <td>{{ w.metric }}</td>
                    <td class="right mono" style="color:var(--red)">{{ "{:+.2f}".format(w.z_score) }}</td>
                    <td class="right mono">{{ "{:,.1f}".format(w.value) if w.value is not none else '—' }}</td>
                    <td class="right mono" style="color:var(--text2)">{{ "{:,.1f}".format(w.peer_mean) if w.peer_mean is not none else '—' }}</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}<p style="color:var(--text2)">None below threshold</p>{% endif %}
        </div>
    </div>
    {% endif %}

    {% if scores %}
    <h2>Z-Scores{{ ' — ' + current_peer_cut if current_peer_cut else ' (all peer cuts)' }}</h2>
    <table>
        <tr><th>Metric</th><th>Peer Cut</th><th>Peer Group</th><th class="right">Value</th><th class="right">Peer Mean</th><th class="right">N</th><th class="right">Z-Score</th><th></th></tr>
        {% for s in scores %}
        <tr>
            <td>{{ s.metric }}</td>
            <td style="color:var(--text2);font-size:12px">{{ s.peer_cut }}</td>
            <td style="color:var(--text2);font-size:12px">{{ s.peer_group_key }}</td>
            <td class="right mono">{{ "{:,.1f}".format(s.value) if s.value is not none else '—' }}</td>
            <td class="right mono" style="color:var(--text2)">{{ "{:,.1f}".format(s.peer_mean) if s.peer_mean is not none else '—' }}</td>
            <td class="right mono">{{ s.peer_n or '—' }}</td>
            <td class="right mono">
                {% if s.z_score is not none %}
                <span style="color:{{ 'var(--green)' if s.z_score > 0 else 'var(--red)' }}">{{ "{:+.2f}".format(s.z_score) }}</span>
                {% else %}—{% endif %}
            </td>
            <td>
                {% if s.z_score is not none %}
                <span class="z-bar {{ 'z-pos' if s.z_score > 0 else 'z-neg' }}" style="width:{{ [abs(s.z_score)*15, 80]|min }}px"></span>
                {% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}
</div>
</body></html>
"""

SEARCH_HTML = """
<!DOCTYPE html><html><head><title>Search — Inventory</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/inventory">← Inventory</a>
    </div>
    <h1>Property Search</h1>
    <form class="search-bar" method="GET" action="/inventory/search">
        <input type="text" name="q" placeholder="Property name or address..." value="{{ query }}">
        <input type="text" name="market" placeholder="Market..." value="{{ market }}" style="max-width:200px">
        <button class="btn" type="submit">Search</button>
    </form>
    {% if results %}
    <p class="subtitle">{{ results|length }} results</p>
    <table>
        <tr><th>Property</th><th>Address</th><th>Market</th><th class="right">Units</th><th class="right">Built</th><th>Class</th><th>Scored</th></tr>
        {% for p in results %}
        <tr>
            <td><a href="/inventory/property/{{ p.property_id }}">{{ p.property_name or '(unnamed)' }}</a></td>
            <td>{{ p.address }}, {{ p.city }}, {{ p.state }}</td>
            <td>{{ p.market or '—' }}</td>
            <td class="right mono">{{ p.num_units or '—' }}</td>
            <td class="right mono">{{ p.year_built or '—' }}</td>
            <td>{{ p.building_class or '—' }}</td>
            <td>{% if p.has_scores %}<span class="badge green">Yes</span>{% else %}<span class="badge orange">No</span>{% endif %}</td>
        </tr>
        {% endfor %}
    </table>
    {% elif query or market %}
    <p style="color:var(--text2);margin-top:24px;">No results found.</p>
    {% endif %}
</div>
</body></html>
"""

NOT_FOUND_HTML = """
<!DOCTYPE html><html><head><title>Not Found — Inventory</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav"><a href="/inventory">← Inventory</a></div>
    <h1>Property Not Found</h1>
    <p class="subtitle">No property with ID {{ property_id }} in the warehouse.</p>
    <p><a href="/inventory/search?q={{ property_id }}">Search for it →</a></p>
</div>
</body></html>
"""
