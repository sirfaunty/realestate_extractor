"""
Sales Comps module routes — transaction search, cap rates, pricing, ownership UI + API.
"""

import logging
from flask import Blueprint, jsonify, request, render_template_string

from .engine import SalesCompsEngine

logger = logging.getLogger(__name__)

comps_bp = Blueprint('sales_comps', __name__, url_prefix='/comps')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from warehouse.engine import WarehouseEngine
        wh = WarehouseEngine()
        wh.connect()
        _engine = SalesCompsEngine(wh)
    return _engine


def register_sales_comps_routes(app):
    app.register_blueprint(comps_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@comps_bp.route('/')
def index():
    eng = _get_engine()
    markets = eng.list_markets()
    total_deals = sum(m['deals'] for m in markets)
    total_markets = len(markets)
    return render_template_string(INDEX_HTML, markets=markets,
                                  total_deals=total_deals, total_markets=total_markets)


@comps_bp.route('/search')
def search():
    eng = _get_engine()
    filters = {
        'market': request.args.get('market'),
        'min_year': request.args.get('min_year', type=int),
        'max_year': request.args.get('max_year', type=int),
        'min_price': request.args.get('min_price', type=float),
        'max_price': request.args.get('max_price', type=float),
        'building_class': request.args.get('class'),
        'buyer': request.args.get('buyer'),
        'seller': request.args.get('seller'),
        'property_name': request.args.get('q'),
        'sort_by': request.args.get('sort', 'sale_date'),
        'sort_dir': request.args.get('dir', 'DESC'),
    }
    # Remove None values
    active = {k: v for k, v in filters.items() if v is not None}
    results = eng.search_transactions(**active, limit=100) if active else []
    return render_template_string(SEARCH_HTML, results=results, filters=filters)


@comps_bp.route('/market/<market_name>')
def market_detail(market_name):
    eng = _get_engine()
    summary = eng.get_market_summary(market_name)
    recent = eng.search_transactions(market=market_name, sort_by='sale_date', limit=25)
    cap_trend = eng.get_cap_rate_trend(market=market_name, period_type='year')
    return render_template_string(MARKET_HTML, summary=summary, recent=recent, cap_trend=cap_trend)


@comps_bp.route('/transaction/<transaction_id>')
def transaction_detail(transaction_id):
    eng = _get_engine()
    txn = eng.get_transaction(transaction_id)
    if not txn:
        return "Transaction not found", 404
    # Get ownership for this property
    ownership = eng.get_ownership_history(txn['property_id']) if txn.get('property_id') else []
    return render_template_string(TRANSACTION_HTML, txn=txn, ownership=ownership)


@comps_bp.route('/owner/<owner_name>')
def owner_detail(owner_name):
    eng = _get_engine()
    portfolio = eng.get_owner_portfolio(owner_name)
    return render_template_string(OWNER_HTML, owner=owner_name, portfolio=portfolio)


@comps_bp.route('/cap-rates')
def cap_rates():
    eng = _get_engine()
    market = request.args.get('market')
    trend = eng.get_cap_rate_trend(market=market, period_type='year')
    snapshot = eng.get_cap_rate_snapshot() if not market else []
    return render_template_string(CAP_RATES_HTML, trend=trend, snapshot=snapshot,
                                  selected_market=market)


# ─── API ───────────────────────────────────────────────────────────

@comps_bp.route('/api/search')
def api_search():
    eng = _get_engine()
    results = eng.search_transactions(
        market=request.args.get('market'),
        min_year=request.args.get('min_year', type=int),
        max_year=request.args.get('max_year', type=int),
        min_price=request.args.get('min_price', type=float),
        max_price=request.args.get('max_price', type=float),
        building_class=request.args.get('class'),
        asset_class=request.args.get('asset_class'),
        buyer=request.args.get('buyer'),
        seller=request.args.get('seller'),
        property_name=request.args.get('q'),
        sort_by=request.args.get('sort', 'sale_date'),
        limit=request.args.get('limit', 100, type=int),
    )
    return jsonify({'transactions': results, 'count': len(results)})


@comps_bp.route('/api/transaction/<transaction_id>')
def api_transaction(transaction_id):
    eng = _get_engine()
    txn = eng.get_transaction(transaction_id)
    if not txn:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(txn)


@comps_bp.route('/api/comps')
def api_comps():
    eng = _get_engine()
    comps = eng.find_comps(
        market=request.args.get('market', ''),
        num_units=request.args.get('units', type=int),
        year_built=request.args.get('year_built', type=int),
        building_class=request.args.get('class'),
        limit=request.args.get('limit', 25, type=int),
    )
    return jsonify({'comps': comps, 'count': len(comps)})


@comps_bp.route('/api/cap-rates')
def api_cap_rates():
    eng = _get_engine()
    trend = eng.get_cap_rate_trend(
        market=request.args.get('market'),
        period_type=request.args.get('period', 'year'),
        is_clean=request.args.get('clean', 'true').lower() == 'true',
        asset_class=request.args.get('asset_class'),
    )
    return jsonify({'cap_rates': trend, 'count': len(trend)})


@comps_bp.route('/api/cap-rates/snapshot')
def api_cap_snapshot():
    eng = _get_engine()
    snapshot = eng.get_cap_rate_snapshot(
        period=request.args.get('period'),
        is_clean=request.args.get('clean', 'true').lower() == 'true',
    )
    return jsonify({'snapshot': snapshot, 'count': len(snapshot)})


@comps_bp.route('/api/pricing')
def api_pricing():
    eng = _get_engine()
    trend = eng.get_pricing_trend(
        market=request.args.get('market'),
        building_class=request.args.get('class'),
    )
    return jsonify({'pricing': trend, 'count': len(trend)})


@comps_bp.route('/api/market/<market_name>')
def api_market(market_name):
    eng = _get_engine()
    return jsonify(eng.get_market_summary(market_name))


@comps_bp.route('/api/markets')
def api_markets():
    eng = _get_engine()
    return jsonify({'markets': eng.list_markets()})


@comps_bp.route('/api/ownership/<property_id>')
def api_ownership(property_id):
    eng = _get_engine()
    history = eng.get_ownership_history(property_id)
    return jsonify({'property_id': property_id, 'history': history})


@comps_bp.route('/api/owners/search')
def api_owner_search():
    eng = _get_engine()
    q = request.args.get('q', '')
    if not q:
        return jsonify({'error': 'q parameter required'}), 400
    results = eng.search_owners(q)
    return jsonify({'owners': results, 'count': len(results)})


@comps_bp.route('/api/owner/<owner_name>/portfolio')
def api_owner_portfolio(owner_name):
    eng = _get_engine()
    portfolio = eng.get_owner_portfolio(owner_name)
    return jsonify({'owner': owner_name, 'portfolio': portfolio, 'count': len(portfolio)})


# ─── HTML Templates ───────────────────────────────────────────────

_STYLE = """
<style>
:root { --bg: #0d1117; --surface: #161b22; --border: #30363d; --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff; --green: #3fb950; --orange: #d29922; --red: #f85149; }
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
.badge.blue { background: rgba(88,166,255,0.2); color: var(--accent); }
input[type="text"], input[type="number"], select { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 8px 12px; border-radius: 6px; font-size: 14px; }
input:focus, select:focus { outline: none; border-color: var(--accent); }
.btn { padding: 8px 16px; background: var(--accent); color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
.btn:hover { opacity: 0.9; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; align-items: flex-end; }
.filters .field { display: flex; flex-direction: column; gap: 4px; }
.filters .field label { font-size: 11px; color: var(--text2); text-transform: uppercase; }
.filters input, .filters select { width: 140px; }
</style>
"""

INDEX_HTML = """
<!DOCTYPE html><html><head><title>Sales Comps — Capactive</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/warehouse">Warehouse</a>
        <a href="/inventory">Inventory</a>
        <a href="/comps">Sales Comps</a>
        <a href="/comps/search">Search</a>
        <a href="/comps/cap-rates">Cap Rates</a>
    </div>
    <h1>Sales Comps</h1>
    <p class="subtitle">{{ "{:,}".format(total_deals) }} transactions across {{ total_markets }} markets</p>
    <div class="grid">
        <div class="card"><div class="label">Total Deals</div><div class="value green">{{ "{:,}".format(total_deals) }}</div></div>
        <div class="card"><div class="label">Markets</div><div class="value">{{ total_markets }}</div></div>
    </div>
    <h2>Markets by Deal Volume</h2>
    <table>
        <tr><th>Market</th><th class="right">Deals</th><th class="right">Median Price</th><th class="right">Years</th></tr>
        {% for m in markets[:30] %}
        <tr>
            <td><a href="/comps/market/{{ m.market }}">{{ m.market }}</a></td>
            <td class="right mono">{{ "{:,}".format(m.deals) }}</td>
            <td class="right mono">${{ "{:,}".format(m.median_price) }}</td>
            <td class="right mono">{{ m.min_year }}–{{ m.max_year }}</td>
        </tr>
        {% endfor %}
    </table>
</div>
</body></html>
"""

SEARCH_HTML = """
<!DOCTYPE html><html><head><title>Search — Sales Comps</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/comps">← Sales Comps</a>
    </div>
    <h1>Transaction Search</h1>
    <form class="filters" method="GET" action="/comps/search">
        <div class="field"><label>Property/Address</label><input type="text" name="q" value="{{ filters.property_name or '' }}" style="width:200px"></div>
        <div class="field"><label>Market</label><input type="text" name="market" value="{{ filters.market or '' }}"></div>
        <div class="field"><label>Min Year</label><input type="number" name="min_year" value="{{ filters.min_year or '' }}" style="width:80px"></div>
        <div class="field"><label>Max Year</label><input type="number" name="max_year" value="{{ filters.max_year or '' }}" style="width:80px"></div>
        <div class="field"><label>Class</label><input type="text" name="class" value="{{ filters.building_class or '' }}" style="width:60px"></div>
        <div class="field"><label>Buyer</label><input type="text" name="buyer" value="{{ filters.buyer or '' }}"></div>
        <div class="field"><label>Seller</label><input type="text" name="seller" value="{{ filters.seller or '' }}"></div>
        <div class="field"><label>&nbsp;</label><button class="btn" type="submit">Search</button></div>
    </form>
    {% if results %}
    <p class="subtitle">{{ results|length }} results</p>
    <table>
        <tr><th>Date</th><th>Property</th><th>Market</th><th class="right">Price</th><th class="right">Cap Rate</th><th class="right">$/Unit</th><th class="right">Units</th><th>Class</th><th>Buyer</th></tr>
        {% for t in results %}
        <tr>
            <td class="mono">{{ t.sale_date or t.sale_year or '—' }}</td>
            <td><a href="/comps/transaction/{{ t.transaction_id }}">{{ t.property_name or t.property_address or '(unnamed)' }}</a></td>
            <td style="font-size:12px">{{ t.market or '—' }}</td>
            <td class="right mono">${{ "{:,.0f}".format(t.sale_price) if t.sale_price else '—' }}</td>
            <td class="right mono">{{ "{:.2f}%".format(t.cap_rate_actual) if t.cap_rate_actual else '—' }}</td>
            <td class="right mono">{{ "${:,.0f}".format(t.price_per_unit) if t.price_per_unit else '—' }}</td>
            <td class="right mono">{{ t.num_units or '—' }}</td>
            <td>{{ t.building_class or '—' }}</td>
            <td style="font-size:12px">{{ t.buyer_name or '—' }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}
</div>
</body></html>
"""

MARKET_HTML = """
<!DOCTYPE html><html><head><title>{{ summary.market }} — Sales Comps</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/">← Platform</a>
        <a href="/comps">← Sales Comps</a>
        <a href="/comps/search?market={{ summary.market }}">Search This Market</a>
    </div>
    <h1>{{ summary.market }}</h1>
    <div class="grid">
        <div class="card"><div class="label">Deals</div><div class="value green">{{ "{:,}".format(summary.deals) }}</div></div>
        <div class="card"><div class="label">Total Volume</div><div class="value">${{ "{:,.0f}".format((summary.total_volume or 0)/1e6) }}M</div></div>
        <div class="card"><div class="label">Median Price</div><div class="value">${{ "{:,.0f}".format((summary.median_price or 0)/1e6) }}M</div></div>
        <div class="card"><div class="label">Median Cap</div><div class="value">{{ "{:.2f}".format(summary.median_cap_rate) if summary.median_cap_rate else '—' }}%</div></div>
        <div class="card"><div class="label">Median $/Unit</div><div class="value">${{ "{:,.0f}".format(summary.median_ppu or 0) }}</div></div>
        <div class="card"><div class="label">Years</div><div class="value" style="font-size:18px">{{ summary.min_year }}–{{ summary.max_year }}</div></div>
    </div>

    {% if summary.year_over_year %}
    <h2>Year-over-Year</h2>
    <table>
        <tr><th>Year</th><th class="right">Deals</th><th class="right">Volume</th><th class="right">Cap Rate</th><th class="right">$/Unit</th></tr>
        {% for y in summary.year_over_year %}
        <tr>
            <td class="mono">{{ y.year }}</td>
            <td class="right mono">{{ "{:,}".format(y.deals) }}</td>
            <td class="right mono">${{ "{:,.0f}".format((y.volume or 0)/1e6) }}M</td>
            <td class="right mono">{{ "{:.2f}%".format(y.cap_rate) if y.cap_rate else '—' }}</td>
            <td class="right mono">{{ "${:,.0f}".format(y.ppu) if y.ppu else '—' }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;">
        <div>
            <h2>Top Buyers</h2>
            <table>
                <tr><th>Buyer</th><th class="right">Deals</th><th class="right">Volume</th></tr>
                {% for b in summary.top_buyers %}
                <tr>
                    <td><a href="/comps/owner/{{ b.name }}">{{ b.name }}</a></td>
                    <td class="right mono">{{ b.deals }}</td>
                    <td class="right mono">${{ "{:,.0f}".format((b.volume or 0)/1e6) }}M</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        <div>
            <h2>Top Sellers</h2>
            <table>
                <tr><th>Seller</th><th class="right">Deals</th><th class="right">Volume</th></tr>
                {% for s in summary.top_sellers %}
                <tr>
                    <td><a href="/comps/owner/{{ s.name }}">{{ s.name }}</a></td>
                    <td class="right mono">{{ s.deals }}</td>
                    <td class="right mono">${{ "{:,.0f}".format((s.volume or 0)/1e6) }}M</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>

    {% if recent %}
    <h2>Recent Transactions</h2>
    <table>
        <tr><th>Date</th><th>Property</th><th class="right">Price</th><th class="right">Cap</th><th class="right">$/Unit</th><th>Buyer</th></tr>
        {% for t in recent %}
        <tr>
            <td class="mono">{{ t.sale_date or '—' }}</td>
            <td><a href="/comps/transaction/{{ t.transaction_id }}">{{ t.property_name or t.property_address or '—' }}</a></td>
            <td class="right mono">${{ "{:,.0f}".format(t.sale_price) if t.sale_price else '—' }}</td>
            <td class="right mono">{{ "{:.2f}%".format(t.cap_rate_actual) if t.cap_rate_actual else '—' }}</td>
            <td class="right mono">{{ "${:,.0f}".format(t.price_per_unit) if t.price_per_unit else '—' }}</td>
            <td style="font-size:12px">{{ t.buyer_name or '—' }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}
</div>
</body></html>
"""

TRANSACTION_HTML = """
<!DOCTYPE html><html><head><title>{{ txn.property_name or 'Transaction' }} — Sales Comps</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/comps">← Sales Comps</a>
        {% if txn.market %}<a href="/comps/market/{{ txn.market }}">← {{ txn.market }}</a>{% endif %}
    </div>
    <h1>{{ txn.property_name or 'Transaction ' + txn.transaction_id }}</h1>
    <p class="subtitle">{{ txn.property_address }}, {{ txn.city }}, {{ txn.state }}</p>
    <div class="grid">
        <div class="card"><div class="label">Sale Price</div><div class="value green">${{ "{:,.0f}".format(txn.sale_price) if txn.sale_price else '—' }}</div></div>
        <div class="card"><div class="label">Cap Rate</div><div class="value">{{ "{:.2f}%".format(txn.cap_rate_actual) if txn.cap_rate_actual else '—' }}</div></div>
        <div class="card"><div class="label">$/Unit</div><div class="value">{{ "${:,.0f}".format(txn.price_per_unit) if txn.price_per_unit else '—' }}</div></div>
        <div class="card"><div class="label">$/SF</div><div class="value">{{ "${:,.0f}".format(txn.price_per_sf) if txn.price_per_sf else '—' }}</div></div>
        <div class="card"><div class="label">Sale Date</div><div class="value" style="font-size:18px">{{ txn.sale_date or txn.sale_year or '—' }}</div></div>
        <div class="card"><div class="label">Units</div><div class="value">{{ txn.num_units or '—' }}</div></div>
        <div class="card"><div class="label">Built</div><div class="value">{{ txn.year_built or '—' }}</div></div>
        <div class="card"><div class="label">Class</div><div class="value">{{ txn.building_class or '—' }}</div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px;">
        <div class="card">
            <div class="label">Buyer</div>
            <div style="margin-top:8px;font-size:16px">{% if txn.buyer_name %}<a href="/comps/owner/{{ txn.buyer_name }}">{{ txn.buyer_name }}</a>{% else %}—{% endif %}</div>
        </div>
        <div class="card">
            <div class="label">Seller</div>
            <div style="margin-top:8px;font-size:16px">{% if txn.seller_name %}<a href="/comps/owner/{{ txn.seller_name }}">{{ txn.seller_name }}</a>{% else %}—{% endif %}</div>
        </div>
    </div>
    {% if ownership %}
    <h2>Ownership History</h2>
    <table>
        <tr><th>Owner</th><th>Acquired</th><th>Disposed</th><th class="right">Acq. Price</th><th class="right">Disp. Price</th><th class="right">Hold</th><th>Current</th></tr>
        {% for o in ownership %}
        <tr>
            <td>{% if o.owner_canonical %}<a href="/comps/owner/{{ o.owner_canonical }}">{{ o.owner_canonical }}</a>{% else %}—{% endif %}</td>
            <td class="mono">{{ o.acquisition_date or '—' }}</td>
            <td class="mono">{{ o.disposition_date or '—' }}</td>
            <td class="right mono">{{ "${:,.0f}".format(o.acquisition_price) if o.acquisition_price else '—' }}</td>
            <td class="right mono">{{ "${:,.0f}".format(o.disposition_price) if o.disposition_price else '—' }}</td>
            <td class="right mono">{{ "{:.0f} mo".format(o.hold_months) if o.hold_months else '—' }}</td>
            <td>{% if o.is_current %}<span class="badge green">Current</span>{% endif %}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}
</div>
</body></html>
"""

OWNER_HTML = """
<!DOCTYPE html><html><head><title>{{ owner }} — Sales Comps</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav"><a href="/comps">← Sales Comps</a></div>
    <h1>{{ owner }}</h1>
    <p class="subtitle">{{ portfolio|length }} properties</p>
    {% if portfolio %}
    <table>
        <tr><th>Property</th><th>Market</th><th>Acquired</th><th class="right">Price</th><th class="right">Units</th><th>Status</th></tr>
        {% for p in portfolio %}
        <tr>
            <td>{{ p.property_name or p.property_address or p.property_id }}</td>
            <td>{{ p.market or '—' }}</td>
            <td class="mono">{{ p.acquisition_date or '—' }}</td>
            <td class="right mono">{{ "${:,.0f}".format(p.acquisition_price) if p.acquisition_price else '—' }}</td>
            <td class="right mono">{{ p.num_units or '—' }}</td>
            <td>{% if p.is_current %}<span class="badge green">Holds</span>{% else %}<span class="badge blue">Sold</span>{% endif %}</td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <p style="color:var(--text2)">No ownership records found.</p>
    {% endif %}
</div>
</body></html>
"""

CAP_RATES_HTML = """
<!DOCTYPE html><html><head><title>Cap Rates — Sales Comps</title>""" + _STYLE + """</head><body>
<div class="container">
    <div class="nav">
        <a href="/comps">← Sales Comps</a>
    </div>
    <h1>Cap Rate Trends{{ ' — ' + selected_market if selected_market else ' — National' }}</h1>
    {% if trend %}
    <table>
        <tr><th>Period</th><th>Asset Class</th><th class="right">Deals</th><th class="right">Median</th><th class="right">Mean</th><th class="right">P25</th><th class="right">P75</th></tr>
        {% for t in trend %}
        <tr>
            <td class="mono">{{ t.period }}</td>
            <td>{{ t.asset_class }}</td>
            <td class="right mono">{{ t.n_deals or '—' }}</td>
            <td class="right mono">{{ "{:.2f}%".format(t.cap_rate_median) if t.cap_rate_median else '—' }}</td>
            <td class="right mono">{{ "{:.2f}%".format(t.cap_rate_mean) if t.cap_rate_mean else '—' }}</td>
            <td class="right mono">{{ "{:.2f}%".format(t.cap_rate_p25) if t.cap_rate_p25 else '—' }}</td>
            <td class="right mono">{{ "{:.2f}%".format(t.cap_rate_p75) if t.cap_rate_p75 else '—' }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}
</div>
</body></html>
"""
