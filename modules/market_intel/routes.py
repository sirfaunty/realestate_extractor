"""
routes.py — Flask blueprint for the Market Intelligence module.

Routes registered at /market-intel:
  GET  /market-intel/                           Dashboard — market list with scores
  GET  /market-intel/market/<name>              Market detail brief
  GET  /market-intel/compare                    Market comparison (query: ?markets=A,B,C)
  GET  /market-intel/api/markets                API — market list with scores
  GET  /market-intel/api/market/<name>          API — full market brief JSON
  GET  /market-intel/api/market/<name>/cap-rates API — cap rate series
  GET  /market-intel/api/market/<name>/sales    API — sales activity
  GET  /market-intel/api/market/<name>/pricing  API — pricing trends
  GET  /market-intel/api/compare                API — market comparison
"""

import json
import logging
from flask import Blueprint, jsonify, request, render_template_string

logger = logging.getLogger(__name__)

market_intel_bp = Blueprint('market_intel', __name__, url_prefix='/market-intel')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from .engine import MarketIntelEngine
        _engine = MarketIntelEngine()
    return _engine


def register_market_intel_routes(app):
    """Register the market intelligence blueprint."""
    app.register_blueprint(market_intel_bp)


# =============================================================================
# Pages
# =============================================================================

@market_intel_bp.route('/')
def index():
    """Dashboard — market list with scorecard rankings."""
    try:
        eng = _get_engine()
        markets = eng.get_markets_with_scores()
    except Exception as e:
        logger.error(f"Market intel index error: {e}", exc_info=True)
        markets = []

    return render_template_string(_DASHBOARD_HTML, markets=markets)


@market_intel_bp.route('/market/<path:market_name>')
def market_detail(market_name):
    """Market detail brief — full intelligence page."""
    try:
        eng = _get_engine()
        brief = eng.build_market_brief(market_name)
    except Exception as e:
        logger.error(f"Market brief error for {market_name}: {e}", exc_info=True)
        return render_template_string(_ERROR_HTML, error=str(e), market=market_name), 500

    return render_template_string(
        _MARKET_DETAIL_HTML,
        brief=brief,
        cap_rates_json=json.dumps(brief.cap_rates.annual_series if brief.cap_rates else []),
        sales_json=json.dumps(
            [{"year": y, "deals": d, "volume": brief.sales.annual_volume.get(y, 0)}
             for y, d in sorted(brief.sales.annual_deals.items())]
            if brief.sales else []
        ),
        pricing_json=json.dumps(brief.pricing.annual_series if brief.pricing else []),
    )


@market_intel_bp.route('/compare')
def compare_page():
    """Market comparison page."""
    market_csv = request.args.get('markets', '')
    market_names = [m.strip() for m in market_csv.split(',') if m.strip()]

    if not market_names:
        # Show market picker
        try:
            eng = _get_engine()
            available = eng.get_markets()
        except Exception:
            available = []
        return render_template_string(_COMPARE_PICKER_HTML, available=available)

    try:
        eng = _get_engine()
        rows = eng.compare_markets(market_names)
    except Exception as e:
        logger.error(f"Compare error: {e}", exc_info=True)
        return render_template_string(_ERROR_HTML, error=str(e), market=','.join(market_names)), 500

    return render_template_string(_COMPARE_HTML, rows=rows, market_names=market_names)


# =============================================================================
# API endpoints
# =============================================================================

@market_intel_bp.route('/api/markets')
def api_markets():
    try:
        eng = _get_engine()
        return jsonify(eng.get_markets_with_scores())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@market_intel_bp.route('/api/market/<path:market_name>')
def api_market_brief(market_name):
    try:
        eng = _get_engine()
        brief = eng.build_market_brief(market_name)
        return jsonify({
            'market': brief.market,
            'overview': {
                'properties': brief.overview.property_count,
                'units': brief.overview.total_units,
                'submarkets': brief.overview.submarkets,
                'submarket_list': brief.overview.submarket_list,
                'class_distribution': brief.overview.class_distribution,
                'vintage_distribution': brief.overview.vintage_distribution,
                'avg_year_built': brief.overview.avg_year_built,
                'avg_units': brief.overview.avg_units,
            } if brief.overview else None,
            'cap_rates': {
                'latest_median': brief.cap_rates.latest_median,
                'latest_spread': brief.cap_rates.latest_spread,
                'yoy_change_bps': brief.cap_rates.yoy_change_bps,
                'five_year_avg': brief.cap_rates.five_year_avg,
                'annual_series': brief.cap_rates.annual_series,
            } if brief.cap_rates else None,
            'sales': {
                'total_transactions': brief.sales.total_transactions,
                'total_volume': brief.sales.total_volume,
                'avg_price_per_unit': brief.sales.avg_price_per_unit,
                'median_cap_rate': brief.sales.median_cap_rate,
                'annual_deals': brief.sales.annual_deals,
                'recent_transactions': brief.sales.recent_transactions[:10],
                'top_buyers': brief.sales.top_buyers[:5],
                'top_sellers': brief.sales.top_sellers[:5],
            } if brief.sales else None,
            'pricing': {
                'latest_median_ppu': brief.pricing.latest_median_ppu,
                'latest_median_ppsf': brief.pricing.latest_median_ppsf,
                'yoy_ppu_change_pct': brief.pricing.yoy_ppu_change_pct,
                'annual_series': brief.pricing.annual_series,
                'by_class': brief.pricing.by_class,
            } if brief.pricing else None,
            'scorecard': brief.scorecard,
            'top_owners': brief.top_owners,
            'signals': brief.signals,
        })
    except Exception as e:
        logger.error(f"API market brief error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@market_intel_bp.route('/api/market/<path:market_name>/cap-rates')
def api_cap_rates(market_name):
    try:
        eng = _get_engine()
        cr = eng.get_cap_rate_summary(market_name)
        return jsonify({
            'market': market_name,
            'latest_median': cr.latest_median,
            'yoy_change_bps': cr.yoy_change_bps,
            'annual_series': cr.annual_series,
            'quarterly_series': cr.quarterly_series,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@market_intel_bp.route('/api/market/<path:market_name>/sales')
def api_sales(market_name):
    min_year = request.args.get('min_year', type=int)
    try:
        eng = _get_engine()
        sa = eng.get_sales_activity(market_name, min_year=min_year)
        return jsonify({
            'market': market_name,
            'total_transactions': sa.total_transactions,
            'total_volume': sa.total_volume,
            'annual_deals': sa.annual_deals,
            'annual_volume': sa.annual_volume,
            'recent_transactions': sa.recent_transactions,
            'top_buyers': sa.top_buyers,
            'top_sellers': sa.top_sellers,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@market_intel_bp.route('/api/market/<path:market_name>/pricing')
def api_pricing(market_name):
    try:
        eng = _get_engine()
        pt = eng.get_pricing_trends(market_name)
        return jsonify({
            'market': market_name,
            'latest_median_ppu': pt.latest_median_ppu,
            'yoy_ppu_change_pct': pt.yoy_ppu_change_pct,
            'annual_series': pt.annual_series,
            'by_class': pt.by_class,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@market_intel_bp.route('/api/compare')
def api_compare():
    market_csv = request.args.get('markets', '')
    market_names = [m.strip() for m in market_csv.split(',') if m.strip()]
    if not market_names:
        return jsonify({'error': 'Provide ?markets=A,B,C'}), 400
    try:
        eng = _get_engine()
        return jsonify(eng.compare_markets(market_names))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# HTML Templates
# =============================================================================

_STYLE = """
<style>
:root {
  --bg:      #0f1419;
  --surface: #1a1f2e;
  --border:  #2d3548;
  --text:    #e6edf3;
  --muted:   #8b949e;
  --accent:  #58a6ff;
  --green:   #3fb950;
  --red:     #f85149;
  --yellow:  #d29922;
  --orange:  #db6d28;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.container{max-width:1400px;margin:0 auto;padding:24px}
.topbar{background:var(--surface);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;gap:16px}
.topbar h1{font-size:18px;font-weight:600}
.topbar .breadcrumb{color:var(--muted);font-size:13px}
h2{font-size:17px;font-weight:600;margin:24px 0 12px;color:var(--accent)}
h3{font-size:15px;font-weight:600;margin:16px 0 8px}
.subtitle{color:var(--muted);margin-bottom:20px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:24px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
.card .label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px}
.card .value{font-size:24px;font-weight:700;margin-top:4px}
.card .delta{font-size:12px;margin-top:2px}
.card .delta.positive{color:var(--green)}
.card .delta.negative{color:var(--red)}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}
th{background:#151a26;padding:10px 12px;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted);border-bottom:1px solid var(--border)}
td{padding:10px 12px;border-bottom:1px solid var(--border);font-size:13px}
tr:hover td{background:rgba(88,166,255,0.04)}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.pill.positive{background:rgba(63,185,80,0.15);color:var(--green)}
.pill.negative{background:rgba(248,81,73,0.15);color:var(--red)}
.pill.neutral{background:rgba(139,148,158,0.15);color:var(--muted)}
.signal-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-bottom:8px;display:flex;align-items:center;gap:12px}
.signal-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.signal-dot.positive{background:var(--green)}
.signal-dot.negative{background:var(--red)}
.signal-dot.neutral{background:var(--yellow)}
.chart-container{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:24px}
.bar{display:inline-block;height:18px;border-radius:3px;min-width:2px}
.search-box{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:8px 12px;color:var(--text);font-size:14px;width:300px}
.search-box:focus{outline:none;border-color:var(--accent)}
.btn{background:var(--accent);color:#fff;border:none;padding:8px 16px;border-radius:6px;font-size:13px;cursor:pointer;font-weight:500}
.btn:hover{opacity:0.9}
</style>
"""

# ─── Dashboard ──────────────────────────────────────────────────────

_DASHBOARD_HTML = """
<!DOCTYPE html>
<html><head><title>Market Intelligence — Capactive</title>""" + _STYLE + """
<script>
function filterTable() {
    const q = document.getElementById('search').value.toLowerCase();
    document.querySelectorAll('#markets-table tbody tr').forEach(tr => {
        tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
}
</script>
</head><body>
<div class="topbar">
    <h1>Market Intelligence</h1>
    <span class="breadcrumb">{{ markets|length }} markets</span>
</div>
<div class="container">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2 style="margin:0">Markets</h2>
        <div style="display:flex;gap:12px;align-items:center">
            <input class="search-box" id="search" placeholder="Filter markets..." oninput="filterTable()">
            <a class="btn" href="/market-intel/compare">Compare Markets</a>
        </div>
    </div>
    <table id="markets-table">
    <thead><tr>
        <th>Market</th><th>Properties</th><th>Units</th><th>Submarkets</th>
        <th>Score</th><th>Rank</th>
        <th>D&amp;S</th><th>Occ</th><th>Rent</th>
        <th></th>
    </tr></thead>
    <tbody>
    {% for m in markets %}
    <tr>
        <td><a href="/market-intel/market/{{ m.market }}"><strong>{{ m.market }}</strong></a></td>
        <td>{{ "{:,}".format(m.properties) }}</td>
        <td>{{ "{:,}".format(m.units) }}</td>
        <td>{{ m.submarkets }}</td>
        <td>{% if m.score is not none %}{{ "%.2f"|format(m.score) }}{% else %}-{% endif %}</td>
        <td>{% if m.rank is not none %}#{{ m.rank }}{% else %}-{% endif %}</td>
        <td>{% if m.ds_score is not none %}{{ "%.2f"|format(m.ds_score) }}{% else %}-{% endif %}</td>
        <td>{% if m.occ_score is not none %}{{ "%.2f"|format(m.occ_score) }}{% else %}-{% endif %}</td>
        <td>{% if m.rent_score is not none %}{{ "%.2f"|format(m.rent_score) }}{% else %}-{% endif %}</td>
        <td><a href="/market-intel/market/{{ m.market }}">View &rarr;</a></td>
    </tr>
    {% endfor %}
    </tbody></table>
</div>
</body></html>
"""

# ─── Market Detail ──────────────────────────────────────────────────

_MARKET_DETAIL_HTML = """
<!DOCTYPE html>
<html><head><title>{{ brief.market }} — Market Intelligence</title>""" + _STYLE + """
</head><body>
<div class="topbar">
    <h1><a href="/market-intel" style="color:var(--muted)">Market Intelligence</a></h1>
    <span class="breadcrumb">/ {{ brief.market }}</span>
</div>
<div class="container">

<!-- Signals -->
{% if brief.signals %}
<h2>Market Signals</h2>
{% for s in brief.signals %}
<div class="signal-box">
    <span class="signal-dot {{ s.sentiment }}"></span>
    <span>{{ s.text }}</span>
    <span class="pill {{ s.sentiment }}" style="margin-left:auto">{{ s.type }}</span>
</div>
{% endfor %}
{% endif %}

<!-- Overview KPIs -->
{% if brief.overview %}
<h2>Market Overview</h2>
<div class="grid">
    <div class="card">
        <div class="label">Properties</div>
        <div class="value">{{ "{:,}".format(brief.overview.property_count) }}</div>
    </div>
    <div class="card">
        <div class="label">Total Units</div>
        <div class="value">{{ "{:,}".format(brief.overview.total_units) }}</div>
    </div>
    <div class="card">
        <div class="label">Submarkets</div>
        <div class="value">{{ brief.overview.submarkets }}</div>
    </div>
    <div class="card">
        <div class="label">Avg Vintage</div>
        <div class="value">{{ brief.overview.avg_year_built }}</div>
    </div>
    <div class="card">
        <div class="label">Avg Size</div>
        <div class="value">{{ brief.overview.avg_units }} units</div>
    </div>
</div>

<!-- Class Distribution -->
{% if brief.overview.class_distribution %}
<h3>Building Class Distribution</h3>
<div class="card" style="margin-bottom:24px">
{% set max_cls = brief.overview.class_distribution.values()|max %}
{% for cls, cnt in brief.overview.class_distribution.items() %}
<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
    <span style="width:60px;font-size:13px">{{ cls }}</span>
    <span class="bar" style="width:{{ (cnt / max_cls * 300)|int }}px;background:var(--accent)"></span>
    <span style="font-size:12px;color:var(--muted)">{{ cnt }}</span>
</div>
{% endfor %}
</div>
{% endif %}
{% endif %}

<!-- Cap Rates -->
{% if brief.cap_rates %}
<h2>Cap Rate Trends</h2>
<div class="grid">
    <div class="card">
        <div class="label">Latest Median Cap Rate</div>
        <div class="value">{% if brief.cap_rates.latest_median %}{{ "%.2f%%"|format(brief.cap_rates.latest_median * 100) }}{% else %}-{% endif %}</div>
    </div>
    <div class="card">
        <div class="label">YoY Change</div>
        <div class="value">{% if brief.cap_rates.yoy_change_bps is not none %}{{ "%+.0f"|format(brief.cap_rates.yoy_change_bps) }}bps{% else %}-{% endif %}</div>
        {% if brief.cap_rates.yoy_change_bps is not none %}
        <div class="delta {{ 'negative' if brief.cap_rates.yoy_change_bps > 0 else 'positive' }}">
            {{ "expanding" if brief.cap_rates.yoy_change_bps > 0 else "compressing" }}
        </div>
        {% endif %}
    </div>
    <div class="card">
        <div class="label">5-Year Average</div>
        <div class="value">{% if brief.cap_rates.five_year_avg %}{{ "%.2f%%"|format(brief.cap_rates.five_year_avg * 100) }}{% else %}-{% endif %}</div>
    </div>
    <div class="card">
        <div class="label">P25-P75 Spread</div>
        <div class="value">{% if brief.cap_rates.latest_spread %}{{ "%.0f"|format(brief.cap_rates.latest_spread * 10000) }}bps{% else %}-{% endif %}</div>
    </div>
</div>

{% if brief.cap_rates.annual_series %}
<div class="chart-container">
<h3>Annual Cap Rate Series</h3>
<table>
<thead><tr><th>Year</th><th>Median</th><th>Mean</th><th>P25</th><th>P75</th><th>Deals</th></tr></thead>
<tbody>
{% for s in brief.cap_rates.annual_series[-10:] %}
<tr>
    <td>{{ s.year }}</td>
    <td>{% if s.median %}{{ "%.2f%%"|format(s.median * 100) }}{% else %}-{% endif %}</td>
    <td>{% if s.mean %}{{ "%.2f%%"|format(s.mean * 100) }}{% else %}-{% endif %}</td>
    <td>{% if s.p25 %}{{ "%.2f%%"|format(s.p25 * 100) }}{% else %}-{% endif %}</td>
    <td>{% if s.p75 %}{{ "%.2f%%"|format(s.p75 * 100) }}{% else %}-{% endif %}</td>
    <td>{{ s.n_deals or '-' }}</td>
</tr>
{% endfor %}
</tbody></table>
</div>
{% endif %}
{% endif %}

<!-- Sales Activity -->
{% if brief.sales and brief.sales.total_transactions > 0 %}
<h2>Sales Activity</h2>
<div class="grid">
    <div class="card">
        <div class="label">Total Transactions</div>
        <div class="value">{{ "{:,}".format(brief.sales.total_transactions) }}</div>
    </div>
    <div class="card">
        <div class="label">Total Volume</div>
        <div class="value">${{ "{:,.0f}".format(brief.sales.total_volume / 1e6) }}M</div>
    </div>
    <div class="card">
        <div class="label">Avg $/Unit</div>
        <div class="value">${{ "{:,.0f}".format(brief.sales.avg_price_per_unit) }}</div>
    </div>
    <div class="card">
        <div class="label">Median Cap Rate</div>
        <div class="value">{% if brief.sales.median_cap_rate %}{{ "%.2f%%"|format(brief.sales.median_cap_rate * 100) }}{% else %}-{% endif %}</div>
    </div>
</div>

<!-- Annual deals -->
{% if brief.sales.annual_deals %}
<div class="chart-container">
<h3>Annual Deal Volume</h3>
{% set max_deals = brief.sales.annual_deals.values()|max %}
{% for year in brief.sales.annual_deals|sort %}
<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
    <span style="width:50px;font-size:13px">{{ year }}</span>
    <span class="bar" style="width:{{ (brief.sales.annual_deals[year] / max_deals * 400)|int }}px;background:var(--accent)"></span>
    <span style="font-size:12px;color:var(--muted)">{{ brief.sales.annual_deals[year] }} deals</span>
</div>
{% endfor %}
</div>
{% endif %}

<!-- Recent transactions -->
{% if brief.sales.recent_transactions %}
<h3>Recent Transactions</h3>
<table>
<thead><tr><th>Property</th><th>Date</th><th>Price</th><th>Cap Rate</th><th>$/Unit</th><th>Units</th><th>Buyer</th></tr></thead>
<tbody>
{% for t in brief.sales.recent_transactions[:15] %}
<tr>
    <td><strong>{{ t.name or t.address or '-' }}</strong><br><span style="color:var(--muted);font-size:12px">{{ t.city or '' }}</span></td>
    <td>{{ t.sale_date or '-' }}</td>
    <td>{% if t.price %}${{ "{:,.0f}".format(t.price) }}{% else %}-{% endif %}</td>
    <td>{% if t.cap_rate %}{{ "%.2f%%"|format(t.cap_rate * 100) }}{% else %}-{% endif %}</td>
    <td>{% if t.ppu %}${{ "{:,.0f}".format(t.ppu) }}{% else %}-{% endif %}</td>
    <td>{{ t.units or '-' }}</td>
    <td style="font-size:12px">{{ t.buyer or '-' }}</td>
</tr>
{% endfor %}
</tbody></table>
{% endif %}

<!-- Top buyers/sellers -->
{% if brief.sales.top_buyers %}
<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:24px">
<div>
<h3>Top Buyers</h3>
<table>
<thead><tr><th>Buyer</th><th>Deals</th><th>Volume</th><th>Units</th></tr></thead>
<tbody>
{% for b in brief.sales.top_buyers[:8] %}
<tr>
    <td>{{ b.name }}</td>
    <td>{{ b.deals }}</td>
    <td>${{ "{:,.0f}".format(b.volume / 1e6) }}M</td>
    <td>{{ "{:,}".format(b.units) }}</td>
</tr>
{% endfor %}
</tbody></table>
</div>
{% if brief.sales.top_sellers %}
<div>
<h3>Top Sellers</h3>
<table>
<thead><tr><th>Seller</th><th>Deals</th><th>Volume</th><th>Units</th></tr></thead>
<tbody>
{% for s in brief.sales.top_sellers[:8] %}
<tr>
    <td>{{ s.name }}</td>
    <td>{{ s.deals }}</td>
    <td>${{ "{:,.0f}".format(s.volume / 1e6) }}M</td>
    <td>{{ "{:,}".format(s.units) }}</td>
</tr>
{% endfor %}
</tbody></table>
</div>
{% endif %}
</div>
{% endif %}
{% endif %}

<!-- Pricing Trends -->
{% if brief.pricing and brief.pricing.annual_series %}
<h2>Pricing Trends</h2>
<div class="grid">
    <div class="card">
        <div class="label">Latest $/Unit</div>
        <div class="value">{% if brief.pricing.latest_median_ppu %}${{ "{:,.0f}".format(brief.pricing.latest_median_ppu) }}{% else %}-{% endif %}</div>
        {% if brief.pricing.yoy_ppu_change_pct is not none %}
        <div class="delta {{ 'positive' if brief.pricing.yoy_ppu_change_pct > 0 else 'negative' }}">
            {{ "%+.1f%%"|format(brief.pricing.yoy_ppu_change_pct) }} YoY
        </div>
        {% endif %}
    </div>
    <div class="card">
        <div class="label">Latest $/SF</div>
        <div class="value">{% if brief.pricing.latest_median_ppsf %}${{ "{:,.0f}".format(brief.pricing.latest_median_ppsf) }}{% else %}-{% endif %}</div>
    </div>
</div>

<div class="chart-container">
<h3>Annual Pricing Series</h3>
<table>
<thead><tr><th>Year</th><th>Median $/Unit</th><th>Median $/SF</th><th>Deals</th><th>Volume</th></tr></thead>
<tbody>
{% for s in brief.pricing.annual_series[-10:] %}
<tr>
    <td>{{ s.year }}</td>
    <td>{% if s.median_ppu %}${{ "{:,.0f}".format(s.median_ppu) }}{% else %}-{% endif %}</td>
    <td>{% if s.median_ppsf %}${{ "{:,.0f}".format(s.median_ppsf) }}{% else %}-{% endif %}</td>
    <td>{{ s.n_deals or '-' }}</td>
    <td>{% if s.volume %}${{ "{:,.0f}".format(s.volume / 1e6) }}M{% else %}-{% endif %}</td>
</tr>
{% endfor %}
</tbody></table>
</div>

{% if brief.pricing.by_class %}
<h3>Pricing by Building Class</h3>
<table>
<thead><tr><th>Class</th><th>Median $/Unit</th><th>Median $/SF</th><th>Deals</th></tr></thead>
<tbody>
{% for cls, data in brief.pricing.by_class.items() %}
<tr>
    <td>{{ cls }}</td>
    <td>{% if data.median_ppu %}${{ "{:,.0f}".format(data.median_ppu) }}{% else %}-{% endif %}</td>
    <td>{% if data.median_ppsf %}${{ "{:,.0f}".format(data.median_ppsf) }}{% else %}-{% endif %}</td>
    <td>{{ data.n_deals or '-' }}</td>
</tr>
{% endfor %}
</tbody></table>
{% endif %}
{% endif %}

<!-- Scorecard -->
{% if brief.scorecard %}
<h2>Scorecard Summary</h2>
<div class="grid">
    <div class="card">
        <div class="label">Final Score</div>
        <div class="value">{{ "%.2f"|format(brief.scorecard.final_score) if brief.scorecard.final_score is not none else '-' }}</div>
    </div>
    <div class="card">
        <div class="label">Rank</div>
        <div class="value">#{{ brief.scorecard.rank if brief.scorecard.rank is not none else '-' }}</div>
    </div>
    <div class="card">
        <div class="label">D&amp;S</div>
        <div class="value">{{ "%.2f"|format(brief.scorecard.ds_score) if brief.scorecard.get('ds_score') is not none else '-' }}</div>
    </div>
    <div class="card">
        <div class="label">Occupancy</div>
        <div class="value">{{ "%.2f"|format(brief.scorecard.occ_score) if brief.scorecard.get('occ_score') is not none else '-' }}</div>
    </div>
    <div class="card">
        <div class="label">Rent Growth</div>
        <div class="value">{{ "%.2f"|format(brief.scorecard.rent_score) if brief.scorecard.get('rent_score') is not none else '-' }}</div>
    </div>
</div>
<p style="color:var(--muted);font-size:13px">
    <a href="/scorecard/market/{{ brief.market }}">View full scorecard breakdown &rarr;</a>
</p>
{% endif %}

<!-- Top Owners -->
{% if brief.top_owners %}
<h2>Top Owners</h2>
<table>
<thead><tr><th>Owner</th><th>Properties</th><th>Units</th><th>Avg Vintage</th></tr></thead>
<tbody>
{% for o in brief.top_owners %}
<tr>
    <td>{{ o.owner }}</td>
    <td>{{ o.properties }}</td>
    <td>{{ "{:,}".format(o.units) }}</td>
    <td>{{ o.avg_vintage or '-' }}</td>
</tr>
{% endfor %}
</tbody></table>
{% endif %}

<!-- Submarkets -->
{% if brief.overview and brief.overview.submarket_list %}
<h2>Submarkets</h2>
<table>
<thead><tr><th>Submarket</th><th>Properties</th><th>Units</th></tr></thead>
<tbody>
{% for s in brief.overview.submarket_list %}
<tr>
    <td>{{ s.name }}</td>
    <td>{{ "{:,}".format(s.properties) }}</td>
    <td>{{ "{:,}".format(s.units) }}</td>
</tr>
{% endfor %}
</tbody></table>
{% endif %}

</div>
</body></html>
"""

# ─── Comparison ─────────────────────────────────────────────────────

_COMPARE_PICKER_HTML = """
<!DOCTYPE html>
<html><head><title>Compare Markets — Capactive</title>""" + _STYLE + """
<script>
function goCompare() {
    const checks = document.querySelectorAll('input[name=mkt]:checked');
    const names = Array.from(checks).map(c => c.value);
    if (names.length < 2) { alert('Select at least 2 markets'); return; }
    window.location = '/market-intel/compare?markets=' + encodeURIComponent(names.join(','));
}
</script>
</head><body>
<div class="topbar">
    <h1><a href="/market-intel" style="color:var(--muted)">Market Intelligence</a></h1>
    <span class="breadcrumb">/ Compare</span>
</div>
<div class="container">
<h2>Select Markets to Compare</h2>
<p class="subtitle">Choose 2-5 markets for side-by-side analysis</p>
<div style="max-height:500px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:16px;background:var(--surface)">
{% for m in available %}
<label style="display:block;padding:4px 0;cursor:pointer">
    <input type="checkbox" name="mkt" value="{{ m.market }}">
    {{ m.market }} ({{ "{:,}".format(m.units) }} units)
</label>
{% endfor %}
</div>
<br>
<button class="btn" onclick="goCompare()">Compare Selected</button>
</div>
</body></html>
"""

_COMPARE_HTML = """
<!DOCTYPE html>
<html><head><title>Market Comparison — Capactive</title>""" + _STYLE + """
</head><body>
<div class="topbar">
    <h1><a href="/market-intel" style="color:var(--muted)">Market Intelligence</a></h1>
    <span class="breadcrumb">/ Compare: {{ market_names|join(', ') }}</span>
</div>
<div class="container">
<h2>Market Comparison</h2>
<table>
<thead><tr>
    <th>Metric</th>
    {% for r in rows %}<th>{{ r.market }}</th>{% endfor %}
</tr></thead>
<tbody>
<tr><td>Properties</td>{% for r in rows %}<td>{{ "{:,}".format(r.properties) }}</td>{% endfor %}</tr>
<tr><td>Units</td>{% for r in rows %}<td>{{ "{:,}".format(r.units) }}</td>{% endfor %}</tr>
<tr><td>Submarkets</td>{% for r in rows %}<td>{{ r.submarkets }}</td>{% endfor %}</tr>
<tr><td>Avg Vintage</td>{% for r in rows %}<td>{{ r.avg_vintage or '-' }}</td>{% endfor %}</tr>
<tr><td>Cap Rate (median)</td>{% for r in rows %}<td>{% if r.cap_rate %}{{ "%.2f%%"|format(r.cap_rate * 100) }}{% else %}-{% endif %}</td>{% endfor %}</tr>
<tr><td>Cap Rate YoY (bps)</td>{% for r in rows %}<td>{% if r.cap_rate_yoy_bps is not none %}{{ "%+.0f"|format(r.cap_rate_yoy_bps) }}{% else %}-{% endif %}</td>{% endfor %}</tr>
<tr><td>Cap Rate Spread</td>{% for r in rows %}<td>{% if r.cap_rate_spread %}{{ "%.0f"|format(r.cap_rate_spread * 10000) }}bps{% else %}-{% endif %}</td>{% endfor %}</tr>
<tr><td>Total Sales Volume</td>{% for r in rows %}<td>${{ "{:,.0f}".format(r.total_sales_volume / 1e6) }}M</td>{% endfor %}</tr>
<tr><td>Total Deals</td>{% for r in rows %}<td>{{ "{:,}".format(r.total_deals) }}</td>{% endfor %}</tr>
<tr><td>Avg $/Unit</td>{% for r in rows %}<td>${{ "{:,.0f}".format(r.avg_ppu) }}</td>{% endfor %}</tr>
<tr><td>Median $/Unit</td>{% for r in rows %}<td>{% if r.median_ppu %}${{ "{:,.0f}".format(r.median_ppu) }}{% else %}-{% endif %}</td>{% endfor %}</tr>
<tr><td>$/Unit YoY</td>{% for r in rows %}<td>{% if r.ppu_yoy_pct is not none %}{{ "%+.1f%%"|format(r.ppu_yoy_pct) }}{% else %}-{% endif %}</td>{% endfor %}</tr>
{% if rows[0].score is not none %}
<tr><td><strong>Score</strong></td>{% for r in rows %}<td><strong>{% if r.score is not none %}{{ "%.2f"|format(r.score) }}{% else %}-{% endif %}</strong></td>{% endfor %}</tr>
<tr><td><strong>Rank</strong></td>{% for r in rows %}<td><strong>{% if r.rank is not none %}#{{ r.rank }}{% else %}-{% endif %}</strong></td>{% endfor %}</tr>
{% endif %}
</tbody></table>
<br>
<a href="/market-intel/compare" class="btn">New Comparison</a>
</div>
</body></html>
"""

# ─── Error / Not Found ──────────────────────────────────────────────

_ERROR_HTML = """
<!DOCTYPE html>
<html><head><title>Error — Market Intelligence</title>""" + _STYLE + """
</head><body>
<div class="topbar">
    <h1><a href="/market-intel" style="color:var(--muted)">Market Intelligence</a></h1>
    <span class="breadcrumb">/ Error</span>
</div>
<div class="container">
    <div class="card" style="border-color:var(--red)">
        <h3 style="color:var(--red)">Error loading market data</h3>
        <p style="color:var(--muted);margin-top:8px">{{ error }}</p>
        <p style="margin-top:16px"><a href="/market-intel">Back to dashboard</a></p>
    </div>
</div>
</body></html>
"""
