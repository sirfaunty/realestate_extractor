"""
Scorecard module routes — market scoring UI + API.
"""

import json
import logging
from flask import Blueprint, jsonify, request, render_template_string

from .engine import ScorecardEngine
from .tilt_engine import ScorecardConfig

logger = logging.getLogger(__name__)

scorecard_bp = Blueprint('scorecard', __name__, url_prefix='/scorecard')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from warehouse.engine import WarehouseEngine
        wh = WarehouseEngine()
        wh.connect()
        _engine = ScorecardEngine(wh)
    return _engine


def register_scorecard_routes(app):
    """Register the scorecard blueprint with the Flask app."""
    app.register_blueprint(scorecard_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@scorecard_bp.route('/')
def index():
    """Scorecard dashboard — market rankings overview."""
    eng = _get_engine()
    rankings = eng.get_scored_markets()
    config = eng.get_config()
    return render_template_string(INDEX_HTML, rankings=rankings, config=config)


@scorecard_bp.route('/market/<market_name>')
def market_detail(market_name):
    """Market scorecard detail page."""
    eng = _get_engine()
    score = eng.get_market_score(market_name)
    explanation = eng.explain_score(market_name)
    history = eng.get_score_history(market_name)
    return render_template_string(MARKET_HTML,
                                  market=market_name, score=score,
                                  explanation=explanation, history=history)


@scorecard_bp.route('/config')
def config_page():
    """Scoring configuration explorer."""
    eng = _get_engine()
    config = eng.get_config()
    return render_template_string(CONFIG_HTML, config=config)


# ─── API ───────────────────────────────────────────────────────────

@scorecard_bp.route('/api/rankings')
def api_rankings():
    """Get market rankings."""
    eng = _get_engine()
    tier = request.args.get('tier')
    limit = int(request.args.get('limit', 100))
    return jsonify(eng.get_rankings(tier=tier, limit=limit))


@scorecard_bp.route('/api/market/<market_name>')
def api_market(market_name):
    """Get market score detail."""
    eng = _get_engine()
    score = eng.get_market_score(market_name)
    if not score:
        return jsonify({'error': f'No scores for {market_name}'}), 404
    return jsonify(score)


@scorecard_bp.route('/api/market/<market_name>/explain')
def api_explain(market_name):
    """Get score explanation for a market."""
    eng = _get_engine()
    explanation = eng.explain_score(market_name)
    if not explanation:
        return jsonify({'error': f'No scores for {market_name}'}), 404
    return jsonify(explanation)


@scorecard_bp.route('/api/market/<market_name>/history')
def api_history(market_name):
    """Get score history for a market."""
    eng = _get_engine()
    return jsonify(eng.get_score_history(market_name))


@scorecard_bp.route('/api/config')
def api_config():
    """Get the scoring configuration."""
    eng = _get_engine()
    return jsonify(eng.get_config())


@scorecard_bp.route('/api/score', methods=['POST'])
def api_score():
    """Trigger a scoring run using warehouse data."""
    eng = _get_engine()
    data = request.get_json(silent=True) or {}

    config = ScorecardConfig()
    if 'ds_weight' in data:
        config.ds_weight = float(data['ds_weight'])
    if 'occ_weight' in data:
        config.occ_weight = float(data['occ_weight'])
    if 'rg_weight' in data:
        config.rg_weight = float(data['rg_weight'])
    if 'analysis_duration' in data:
        config.analysis_duration_years = int(data['analysis_duration'])

    result = eng.score_from_warehouse(config)
    return jsonify(result)


@scorecard_bp.route('/api/scenario', methods=['POST'])
def api_scenario():
    """Run scenario comparison for a market."""
    eng = _get_engine()
    data = request.get_json(silent=True) or {}
    market = data.get('market')
    scenarios = data.get('scenarios', [])

    if not market:
        return jsonify({'error': 'market required'}), 400
    if not scenarios:
        return jsonify({'error': 'scenarios required'}), 400

    results = eng.compare_scenarios(market, scenarios)
    return jsonify(results)


# ─── HTML Templates ────────────────────────────────────────────────

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Market Scorecard</title>
<style>
  :root { --bg: #0f1419; --surface: #1a1f2e; --border: #2d3548;
          --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
          --green: #3fb950; --red: #f85149; --yellow: #d29922; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); padding: 24px; }
  h1 { font-size: 24px; margin-bottom: 8px; }
  .subtitle { color: var(--muted); margin-bottom: 24px; }
  .actions { margin-bottom: 24px; display: flex; gap: 12px; align-items: center; }
  .btn { padding: 8px 16px; border-radius: 6px; border: 1px solid var(--border);
         background: var(--surface); color: var(--accent); cursor: pointer;
         text-decoration: none; font-size: 14px; }
  .btn:hover { background: var(--border); }
  .btn-primary { background: var(--accent); color: #000; border-color: var(--accent); }
  .config-summary { background: var(--surface); border: 1px solid var(--border);
                    border-radius: 8px; padding: 16px; margin-bottom: 24px;
                    display: flex; gap: 24px; flex-wrap: wrap; }
  .config-item { display: flex; flex-direction: column; }
  .config-label { color: var(--muted); font-size: 12px; text-transform: uppercase; }
  .config-value { font-size: 18px; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; background: var(--surface);
          border-radius: 8px; overflow: hidden; }
  th { text-align: left; padding: 12px 16px; background: var(--border);
       color: var(--muted); font-size: 12px; text-transform: uppercase; }
  td { padding: 10px 16px; border-top: 1px solid var(--border); }
  tr:hover td { background: rgba(88, 166, 255, 0.05); }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .score-bar { display: inline-block; height: 8px; border-radius: 4px;
               min-width: 4px; max-width: 120px; }
  .score-pos { background: var(--green); }
  .score-neg { background: var(--red); }
  .score-val { font-family: 'SF Mono', monospace; font-size: 13px; }
  .rank-badge { display: inline-block; width: 32px; text-align: center;
                font-weight: 700; color: var(--yellow); }
  .empty { color: var(--muted); text-align: center; padding: 48px; }
  nav { margin-bottom: 24px; }
  nav a { margin-right: 16px; color: var(--muted); }
  nav a.active { color: var(--accent); border-bottom: 2px solid var(--accent);
                 padding-bottom: 4px; }
</style>
</head>
<body>
<nav>
  <a href="/" >Home</a>
  <a href="/scorecard" class="active">Scorecard</a>
  <a href="/inventory">Inventory</a>
  <a href="/comps">Sales Comps</a>
  <a href="/warehouse">Warehouse</a>
</nav>

<h1>Market Scorecard</h1>
<p class="subtitle">MF Fundamental scores — demand/supply, occupancy, rent growth</p>

<div class="config-summary">
  <div class="config-item">
    <span class="config-label">D&S Weight</span>
    <span class="config-value">{{ '%.0f' | format(config.category_weights.demand_supply * 100) }}%</span>
  </div>
  <div class="config-item">
    <span class="config-label">Occ Weight</span>
    <span class="config-value">{{ '%.0f' | format(config.category_weights.occupancy * 100) }}%</span>
  </div>
  <div class="config-item">
    <span class="config-label">Rent Weight</span>
    <span class="config-value">{{ '%.0f' | format(config.category_weights.rent_growth * 100) }}%</span>
  </div>
  <div class="config-item">
    <span class="config-label">Duration</span>
    <span class="config-value">{{ config.analysis_duration_years }}yr</span>
  </div>
  <div class="config-item">
    <span class="config-label">Mom Knob</span>
    <span class="config-value">{{ config.momentum.knob }}</span>
  </div>
</div>

<div class="actions">
  <button class="btn btn-primary" onclick="runScoring()">Run Scoring</button>
  <a href="/scorecard/config" class="btn">View Config</a>
  <span id="status" style="color: var(--muted); font-size: 13px;"></span>
</div>

{% if rankings %}
<table>
<thead>
<tr>
  <th>Rank</th><th>Market</th><th>MF Score</th>
  <th>D&S</th><th>Occ</th><th>Rent</th><th>Visual</th>
</tr>
</thead>
<tbody>
{% for r in rankings %}
<tr>
  <td><span class="rank-badge">{{ r.rank or '-' }}</span></td>
  <td><a href="/scorecard/market/{{ r.market }}">{{ r.market }}</a></td>
  <td class="score-val">{{ '%.4f' | format(r.final_score or 0) }}</td>
  <td class="score-val">{{ '%.3f' | format(r.ds_score or 0) }}</td>
  <td class="score-val">{{ '%.3f' | format(r.occ_score or 0) }}</td>
  <td class="score-val">{{ '%.3f' | format(r.rent_score or 0) }}</td>
  <td>
    {% set score = r.final_score or 0 %}
    {% set width = [([score|abs * 40, 120]|min), 4]|max %}
    <span class="score-bar {{ 'score-pos' if score >= 0 else 'score-neg' }}"
          style="width: {{ width|int }}px;"></span>
  </td>
</tr>
{% endfor %}
</tbody>
</table>
{% else %}
<div class="empty">
  <p>No markets scored yet.</p>
  <p style="margin-top: 8px;">Click <strong>Run Scoring</strong> to score markets from warehouse data.</p>
</div>
{% endif %}

<script>
async function runScoring() {
  const status = document.getElementById('status');
  status.textContent = 'Scoring...';
  try {
    const resp = await fetch('/scorecard/api/score', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: '{}'});
    const data = await resp.json();
    if (data.error) {
      status.textContent = 'Error: ' + data.error;
    } else {
      status.textContent = 'Scored ' + data.markets_scored + ' markets. Reloading...';
      setTimeout(() => location.reload(), 500);
    }
  } catch(e) {
    status.textContent = 'Error: ' + e.message;
  }
}
</script>
</body>
</html>
"""


MARKET_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{{ market }} — Scorecard</title>
<style>
  :root { --bg: #0f1419; --surface: #1a1f2e; --border: #2d3548;
          --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
          --green: #3fb950; --red: #f85149; --yellow: #d29922; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); padding: 24px; }
  h1 { font-size: 24px; margin-bottom: 4px; }
  .subtitle { color: var(--muted); margin-bottom: 24px; }
  nav { margin-bottom: 24px; }
  nav a { margin-right: 16px; color: var(--muted); text-decoration: none; }
  nav a:hover { color: var(--accent); }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 8px; padding: 20px; margin-bottom: 20px; }
  .card h2 { font-size: 16px; color: var(--muted); margin-bottom: 12px;
             text-transform: uppercase; letter-spacing: 1px; }
  .score-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 16px; }
  .score-item { text-align: center; }
  .score-label { color: var(--muted); font-size: 12px; text-transform: uppercase;
                 margin-bottom: 4px; }
  .score-big { font-size: 32px; font-weight: 700; font-family: 'SF Mono', monospace; }
  .score-pos { color: var(--green); }
  .score-neg { color: var(--red); }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; color: var(--muted); font-size: 12px;
       text-transform: uppercase; border-bottom: 1px solid var(--border); }
  td { padding: 8px 12px; border-bottom: 1px solid var(--border);
       font-family: 'SF Mono', monospace; font-size: 13px; }
  .empty { color: var(--muted); text-align: center; padding: 32px; }
  a { color: var(--accent); text-decoration: none; }
</style>
</head>
<body>
<nav>
  <a href="/scorecard">← Back to Rankings</a>
  <a href="/comps/market/{{ market }}">Sales Comps</a>
  <a href="/inventory">Inventory</a>
</nav>

<h1>{{ market }}</h1>
<p class="subtitle">Market Scorecard Detail</p>

{% if score %}
<div class="card">
  <h2>Overall Score</h2>
  <div class="score-grid">
    <div class="score-item">
      <div class="score-label">MF Fundamental</div>
      <div class="score-big {{ 'score-pos' if (score.final_score or 0) >= 0 else 'score-neg' }}">
        {{ '%.4f' | format(score.final_score or 0) }}
      </div>
    </div>
    <div class="score-item">
      <div class="score-label">Rank</div>
      <div class="score-big" style="color: var(--yellow);">
        #{{ score.rank or '-' }}
      </div>
    </div>
    <div class="score-item">
      <div class="score-label">D&S Score</div>
      <div class="score-big {{ 'score-pos' if (score.ds_score or 0) >= 0 else 'score-neg' }}">
        {{ '%.4f' | format(score.ds_score or 0) }}
      </div>
    </div>
    <div class="score-item">
      <div class="score-label">Occ Score</div>
      <div class="score-big {{ 'score-pos' if (score.occ_score or 0) >= 0 else 'score-neg' }}">
        {{ '%.4f' | format(score.occ_score or 0) }}
      </div>
    </div>
    <div class="score-item">
      <div class="score-label">Rent Score</div>
      <div class="score-big {{ 'score-pos' if (score.rent_score or 0) >= 0 else 'score-neg' }}">
        {{ '%.4f' | format(score.rent_score or 0) }}
      </div>
    </div>
  </div>
</div>

{% if explanation and explanation.components %}
<div class="card">
  <h2>Score Breakdown</h2>
  <table>
  <thead><tr><th>Component</th><th>Score</th><th>Weight</th><th>Contribution</th></tr></thead>
  <tbody>
  {% for name, comp in explanation.components.items() %}
  <tr>
    <td style="font-family: inherit; text-transform: capitalize;">{{ name | replace('_', ' ') }}</td>
    <td>{{ '%.4f' | format(comp.score or 0) }}</td>
    <td>{{ '%.0f' | format(comp.weight * 100) }}%</td>
    <td>{{ '%.4f' | format(comp.contribution or 0) }}</td>
  </tr>
  {% endfor %}
  </tbody>
  </table>
</div>
{% endif %}

{% if score.period_scores %}
<div class="card">
  <h2>Period Scores</h2>
  <table>
  <thead><tr><th>Period</th><th>MF Score</th><th>D&S</th><th>Occ</th><th>Rent</th></tr></thead>
  <tbody>
  {% for ps in score.period_scores %}
  <tr>
    <td style="font-family: inherit;">{{ ps.period }}</td>
    <td>{{ '%.4f' | format(ps.mf_score or 0) }}</td>
    <td>{{ '%.4f' | format(ps.ds_score or 0) }}</td>
    <td>{{ '%.4f' | format(ps.occ_score or 0) }}</td>
    <td>{{ '%.4f' | format(ps.rent_score or 0) }}</td>
  </tr>
  {% endfor %}
  </tbody>
  </table>
</div>
{% endif %}

{% if score.tier_scores %}
<div class="card">
  <h2>Tier Scores</h2>
  <table>
  <thead><tr><th>Tier</th><th>Final</th><th>D&S</th><th>Occ</th><th>Rent</th></tr></thead>
  <tbody>
  {% for ts in score.tier_scores %}
  <tr>
    <td style="font-family: inherit;">{{ ts.tier }}</td>
    <td>{{ '%.4f' | format(ts.final_score or 0) }}</td>
    <td>{{ '%.4f' | format(ts.ds_score or 0) }}</td>
    <td>{{ '%.4f' | format(ts.occ_score or 0) }}</td>
    <td>{{ '%.4f' | format(ts.rent_score or 0) }}</td>
  </tr>
  {% endfor %}
  </tbody>
  </table>
</div>
{% endif %}

{% if history and history|length > 1 %}
<div class="card">
  <h2>Score History</h2>
  <table>
  <thead><tr><th>Date</th><th>Score</th><th>Rank</th><th>D&S</th><th>Rent</th></tr></thead>
  <tbody>
  {% for h in history %}
  <tr>
    <td style="font-family: inherit;">{{ h.scored_at }}</td>
    <td>{{ '%.4f' | format(h.final_score or 0) }}</td>
    <td>{{ h.rank or '-' }}</td>
    <td>{{ '%.4f' | format(h.ds_score or 0) }}</td>
    <td>{{ '%.4f' | format(h.rent_score or 0) }}</td>
  </tr>
  {% endfor %}
  </tbody>
  </table>
</div>
{% endif %}

{% else %}
<div class="card empty">
  <p>No scores available for {{ market }}.</p>
  <p style="margin-top: 8px;"><a href="/scorecard">← Back to rankings</a></p>
</div>
{% endif %}

</body>
</html>
"""


CONFIG_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Scorecard Configuration</title>
<style>
  :root { --bg: #0f1419; --surface: #1a1f2e; --border: #2d3548;
          --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); padding: 24px; }
  h1 { font-size: 24px; margin-bottom: 24px; }
  nav { margin-bottom: 24px; }
  nav a { margin-right: 16px; color: var(--muted); text-decoration: none; }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 8px; padding: 20px; margin-bottom: 20px; }
  .card h2 { font-size: 14px; color: var(--muted); margin-bottom: 12px;
             text-transform: uppercase; letter-spacing: 1px; }
  .config-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .config-row { display: flex; justify-content: space-between; padding: 6px 0;
                border-bottom: 1px solid var(--border); }
  .config-key { color: var(--muted); }
  .config-val { font-family: 'SF Mono', monospace; color: var(--accent); }
  pre { background: var(--bg); padding: 16px; border-radius: 6px;
        overflow-x: auto; font-size: 13px; color: var(--text); }
</style>
</head>
<body>
<nav>
  <a href="/scorecard">← Back to Scorecard</a>
</nav>

<h1>Scoring Configuration</h1>

<div class="card">
  <h2>Category Weights</h2>
  {% for k, v in config.category_weights.items() %}
  <div class="config-row">
    <span class="config-key">{{ k | replace('_', ' ') | title }}</span>
    <span class="config-val">{{ '%.0f' | format(v * 100) }}%</span>
  </div>
  {% endfor %}
</div>

<div class="card">
  <h2>Period Weights</h2>
  {% for k, v in config.period_weights.items() %}
  <div class="config-row">
    <span class="config-key">{{ k }}</span>
    <span class="config-val">{{ '%.0f' | format(v * 100) }}%</span>
  </div>
  {% endfor %}
</div>

<div class="card">
  <h2>Signal Indicators</h2>
  {% for name, ind in config.indicators.items() %}
  <div class="config-row">
    <span class="config-key">{{ name | title }}</span>
    <span class="config-val">cap={{ ind.cap }}, w={{ ind.w_impact }}, floor={{ ind.floor }}</span>
  </div>
  {% endfor %}
</div>

<div class="card">
  <h2>Momentum Config</h2>
  {% for period, mc in config.momentum.config.items() %}
  <div class="config-row">
    <span class="config-key">{{ period }}</span>
    <span class="config-val">HL={{ mc.hl_steps }}, tilt={{ mc.max_tilt }}, qtrs={{ mc.hl_qtrs }}</span>
  </div>
  {% endfor %}
  <div class="config-row" style="margin-top: 8px;">
    <span class="config-key">Momentum Knob</span>
    <span class="config-val">{{ config.momentum.knob }}</span>
  </div>
</div>

<div class="card">
  <h2>Other Settings</h2>
  <div class="config-row">
    <span class="config-key">Analysis Duration</span>
    <span class="config-val">{{ config.analysis_duration_years }} years</span>
  </div>
  <div class="config-row">
    <span class="config-key">Occupancy Blend</span>
    <span class="config-val">{{ '%.0f' | format(config.occupancy_blend.actual * 100) }}% actual /
      {{ '%.0f' | format(config.occupancy_blend.effective * 100) }}% effective</span>
  </div>
  <div class="config-row">
    <span class="config-key">Z-Score Clamp</span>
    <span class="config-val">[{{ config.z_clamp.floor }}, {{ config.z_clamp.cap }}]</span>
  </div>
</div>

<div class="card">
  <h2>Full Config (JSON)</h2>
  <pre>{{ config | tojson(indent=2) }}</pre>
</div>

</body>
</html>
"""
