"""
routes.py — Flask blueprint for the Lease Analysis module.

Routes registered at /leases:
  GET  /leases/                          Dashboard — properties with lease data
  GET  /leases/property/<id>             Property lease analysis page
  GET  /leases/property/<id>/analysis    Full pricing analysis page
  GET  /leases/api/properties            API — list of properties with lease data
  GET  /leases/api/property/<id>/summary API — lightweight property summary
  GET  /leases/api/property/<id>/pricing API — full pricing results JSON
"""

import logging
from flask import Blueprint, jsonify, request, render_template_string

logger = logging.getLogger(__name__)

leases_bp = Blueprint('lease_analysis', __name__, url_prefix='/leases')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from ...webapp import get_org_db
        from flask import session
        db = get_org_db(session.get('org_id', 1))
        from .engine import LeaseAnalysisEngine
        _engine = LeaseAnalysisEngine(db)
    return _engine


def register_lease_analysis_routes(app):
    """Register the lease analysis blueprint with the Flask app."""
    app.register_blueprint(leases_bp)


# =============================================================================
# Pages
# =============================================================================

@leases_bp.route('/')
def index():
    """Dashboard — all properties with lease data."""
    try:
        eng = _get_engine()
        properties = eng.get_properties_with_lease_data()
        summaries = []
        for p in properties:
            try:
                s = eng.get_analysis_summary(p['id'])
                summaries.append(s)
            except Exception as e:
                logger.warning(f"Summary failed for property {p['id']}: {e}")
                summaries.append({
                    'property_id': p['id'],
                    'property_name': p.get('name', ''),
                    'has_data': False,
                })
    except Exception as e:
        logger.error(f"Lease analysis index error: {e}")
        properties = []
        summaries = []

    return render_template_string(
        _DASHBOARD_HTML,
        properties=properties,
        summaries=summaries,
        zip=zip,
    )


@leases_bp.route('/property/<int:property_id>')
def property_overview(property_id):
    """Property lease overview — rent roll and basic stats."""
    try:
        eng = _get_engine()
        prop = eng.get_property(property_id)
        if not prop:
            return render_template_string(_NOT_FOUND_HTML, property_id=property_id), 404
        summary = eng.get_analysis_summary(property_id)
        rr = eng.get_rent_roll(property_id)
    except Exception as e:
        logger.error(f"Property overview error for {property_id}: {e}")
        return render_template_string(
            _ERROR_HTML, error=str(e), property_id=property_id), 500

    return render_template_string(
        _PROPERTY_HTML,
        prop=prop,
        summary=summary,
        rent_roll=rr[:200],   # cap display at 200 rows
        total_rows=len(rr),
    )


@leases_bp.route('/property/<int:property_id>/analysis')
def property_analysis(property_id):
    """Full pricing analysis page with 7-layer signal breakdown."""
    scenario = request.args.get('scenario', 'new')

    try:
        eng = _get_engine()
        prop = eng.get_property(property_id)
        if not prop:
            return render_template_string(_NOT_FOUND_HTML, property_id=property_id), 404

        result = eng.run_full_analysis(property_id, scenario=scenario)
        pricing = result['pricing']
        summary = result['summary']
        port_vel = result['portfolio_velocity']
        port_gap = result['portfolio_gap']

        # Sort unit types by recommended rent desc
        pricing_rows = sorted(
            [
                {
                    'unit_type': ut,
                    'floor': p.floor,
                    'recommended': p.recommended,
                    'premium_pct': p.capped_premium * 100,
                    'posture': p.posture_unit_type,
                    'velocity_tier': p.velocity_tier or '-',
                    'gap_level': p.gap_level_tier or '-',
                    'seasonal': p.seasonal_multiplier,
                    'intrinsic': p.intrinsic_adjustment * 100,
                    'ut_avail': p.ut_avail,
                    'ut_total': p.ut_total,
                    'feasible': p.floor > 0,
                }
                for ut, p in pricing.items()
            ],
            key=lambda r: r['recommended'],
            reverse=True,
        )

    except Exception as e:
        logger.error(f"Full analysis error for {property_id}: {e}", exc_info=True)
        return render_template_string(
            _ERROR_HTML, error=str(e), property_id=property_id), 500

    return render_template_string(
        _ANALYSIS_HTML,
        prop=prop,
        summary=summary,
        pricing_rows=pricing_rows,
        port_vel=port_vel,
        port_gap=port_gap,
        scenario=scenario,
    )


# =============================================================================
# API endpoints
# =============================================================================

@leases_bp.route('/api/properties')
def api_properties():
    """List properties that have lease data."""
    try:
        eng = _get_engine()
        return jsonify(eng.get_properties_with_lease_data())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@leases_bp.route('/api/property/<int:property_id>/summary')
def api_summary(property_id):
    """Lightweight property summary (no full pipeline)."""
    try:
        eng = _get_engine()
        return jsonify(eng.get_analysis_summary(property_id))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@leases_bp.route('/api/property/<int:property_id>/pricing')
def api_pricing(property_id):
    """Full pricing results as JSON."""
    scenario = request.args.get('scenario', 'new')
    try:
        eng = _get_engine()
        result = eng.run_full_analysis(property_id, scenario=scenario)
        # Serialize pricing results
        pricing_out = {}
        for ut, p in result['pricing'].items():
            pricing_out[ut] = {
                'floor': p.floor,
                'recommended': p.recommended,
                'capped_premium': p.capped_premium,
                'scarcity_premium': p.scarcity_premium,
                'posture_unit_type': p.posture_unit_type,
                'velocity_tier': p.velocity_tier,
                'gap_level_tier': p.gap_level_tier,
                'gap_trend_tier': p.gap_trend_tier,
                'seasonal_multiplier': p.seasonal_multiplier,
                'intrinsic_adjustment': p.intrinsic_adjustment,
                'forward_exposure_pct': p.forward_exposure_pct,
                'ut_avail': p.ut_avail,
                'ut_total': p.ut_total,
                'fp_avail': p.fp_avail,
                'fp_total': p.fp_total,
                'property_avail': p.property_avail,
                'property_total': p.property_total,
                'feasible': p.floor > 0,
            }
        return jsonify({
            'property_id': property_id,
            'scenario': scenario,
            'summary': result['summary'],
            'pricing': pricing_out,
        })
    except Exception as e:
        logger.error(f"API pricing error for {property_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@leases_bp.route('/api/property/<int:property_id>/rent_roll')
def api_rent_roll(property_id):
    """Raw rent roll rows for a property."""
    try:
        eng = _get_engine()
        rr = eng.get_rent_roll(property_id)
        return jsonify({'property_id': property_id, 'count': len(rr), 'rent_roll': rr})
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
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.container{max-width:1280px;margin:0 auto;padding:24px}
.topbar{background:var(--surface);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;gap:16px}
.topbar h1{font-size:18px;font-weight:600}
.topbar .breadcrumb{color:var(--muted);font-size:13px}
h2{font-size:17px;font-weight:600;margin:24px 0 12px;color:var(--accent)}
h3{font-size:15px;font-weight:600;margin:16px 0 8px}
.subtitle{color:var(--muted);margin-bottom:20px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
.card-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
.card-value{font-size:22px;font-weight:600}
.card-sub{font-size:12px;color:var(--muted);margin-top:2px}
.green{color:var(--green)} .red{color:var(--red)} .yellow{color:var(--yellow)} .muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:var(--surface);color:var(--muted);font-weight:500;text-align:left;padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover td{background:rgba(255,255,255,.03)}
.tbl-wrap{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:auto;margin-bottom:24px}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:500}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-gray{background:rgba(139,148,158,.15);color:var(--muted)}
.prop-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center}
.prop-card-left h3{margin:0 0 4px;font-size:15px}
.prop-card-left .meta{font-size:12px;color:var(--muted)}
.prop-card-right{display:flex;gap:8px;align-items:center}
.btn{display:inline-block;padding:7px 14px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;border:1px solid var(--border);background:var(--surface);color:var(--text);text-decoration:none}
.btn:hover{border-color:var(--accent);color:var(--accent);text-decoration:none}
.btn-primary{background:var(--accent);color:#0f1419;border-color:var(--accent)}
.btn-primary:hover{background:#79b8ff;color:#0f1419}
.tabs{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:20px}
.tab{padding:10px 20px;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;color:var(--muted)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.signal-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.signal-chip{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 12px;font-size:12px}
.signal-chip .label{color:var(--muted);margin-right:4px}
.signal-chip .value{font-weight:600}
.empty{color:var(--muted);font-style:italic;text-align:center;padding:40px}
</style>
"""

_NAV = """
<div class="topbar">
  <div>
    <span class="breadcrumb"><a href="/">Home</a> / <a href="/leases">Lease Analysis</a>{extra}</span>
    <h1 style="margin-top:2px">{title}</h1>
  </div>
</div>
"""

_DASHBOARD_HTML = _STYLE + _NAV.replace('{extra}', '').replace('{title}', 'Lease Analysis') + """
<div class="container">
  <p class="subtitle">Properties with extracted rent-roll or lease documents</p>

  {% if not summaries %}
    <div class="card">
      <p class="empty">No properties with lease data found. Upload rent-roll documents to get started.</p>
    </div>
  {% else %}
    {% for prop, s in zip(properties, summaries) %}
    <div class="prop-card">
      <div class="prop-card-left">
        <h3>{{ prop.name }}</h3>
        <div class="meta">
          {{ prop.city }}, {{ prop.state }}
          {% if s.has_data %}
           &nbsp;·&nbsp; {{ s.total_units }} units
           &nbsp;·&nbsp; {{ s.occupancy_pct }}% occupied
           &nbsp;·&nbsp; {{ s.unit_type_count }} unit types
           &nbsp;·&nbsp; avg rent ${{ "{:,.0f}".format(s.avg_rent) }}
          {% else %}
           &nbsp;·&nbsp; <span class="muted">no rent-roll data</span>
          {% endif %}
        </div>
      </div>
      <div class="prop-card-right">
        {% if s.has_data %}
          <span class="badge badge-green">{{ s.vacant_units }} vacant</span>
          <a href="/leases/property/{{ prop.id }}" class="btn">Overview</a>
          <a href="/leases/property/{{ prop.id }}/analysis" class="btn btn-primary">Run Analysis</a>
        {% else %}
          <span class="badge badge-gray">No data</span>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  {% endif %}
</div>
"""

_PROPERTY_HTML = _STYLE + _NAV.replace(
    '{extra}', ' / {{ prop.name }}'
).replace('{title}', '{{ prop.name }} — Lease Overview') + """
<div class="container">

  <!-- Summary cards -->
  <div class="grid">
    <div class="card">
      <div class="card-label">Total Units</div>
      <div class="card-value">{{ summary.total_units }}</div>
    </div>
    <div class="card">
      <div class="card-label">Occupied</div>
      <div class="card-value green">{{ summary.occupancy_pct }}%</div>
    </div>
    <div class="card">
      <div class="card-label">Vacant</div>
      <div class="card-value {% if summary.vacant_units > 0 %}yellow{% else %}green{% endif %}">
        {{ summary.vacant_units }}
      </div>
    </div>
    <div class="card">
      <div class="card-label">Avg Rent</div>
      <div class="card-value">${{ "{:,.0f}".format(summary.avg_rent) }}</div>
      <div class="card-sub">${{ "{:,.0f}".format(summary.min_rent) }} – ${{ "{:,.0f}".format(summary.max_rent) }}</div>
    </div>
    <div class="card">
      <div class="card-label">Unit Types</div>
      <div class="card-value">{{ summary.unit_type_count }}</div>
    </div>
  </div>

  <!-- Actions -->
  <div style="display:flex;gap:10px;margin-bottom:24px">
    <a href="/leases/property/{{ prop.id }}/analysis?scenario=new" class="btn btn-primary">
      Run New-Lease Analysis
    </a>
    <a href="/leases/property/{{ prop.id }}/analysis?scenario=renewal" class="btn">
      Run Renewal Analysis
    </a>
    <a href="/leases/api/property/{{ prop.id }}/pricing" class="btn" target="_blank">
      API (JSON)
    </a>
  </div>

  <!-- Rent Roll -->
  <h2>Rent Roll</h2>
  {% if total_rows > 200 %}
  <p class="subtitle">Showing first 200 of {{ total_rows }} rows</p>
  {% endif %}

  {% if rent_roll %}
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Unit</th>
          <th>Unit Type</th>
          <th>Tenant</th>
          <th>Status</th>
          <th>Lease Start</th>
          <th>Lease End</th>
          <th>Monthly Rent</th>
          <th>Sqft</th>
        </tr>
      </thead>
      <tbody>
        {% for row in rent_roll %}
        <tr>
          <td>{{ row.unit_number or '-' }}</td>
          <td>{{ row.unit_type or '-' }}</td>
          <td class="muted">{{ row.tenant_name or '-' }}</td>
          <td>
            {% set s = (row.status or '').lower() %}
            {% if s in ('vacant', 'available') %}
              <span class="badge badge-red">{{ row.status }}</span>
            {% elif s == 'notice' %}
              <span class="badge badge-yellow">{{ row.status }}</span>
            {% elif s == 'occupied' %}
              <span class="badge badge-green">Occupied</span>
            {% else %}
              <span class="badge badge-gray">{{ row.status or '-' }}</span>
            {% endif %}
          </td>
          <td>{{ row.lease_start or '-' }}</td>
          <td>{{ row.lease_end or '-' }}</td>
          <td>{% if row.monthly_rent %}${{ "{:,.0f}".format(row.monthly_rent) }}{% else %}-{% endif %}</td>
          <td>{% if row.square_footage %}{{ "{:,.0f}".format(row.square_footage) }}{% else %}-{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="card"><p class="empty">No rent-roll data extracted yet.</p></div>
  {% endif %}
</div>
"""

_ANALYSIS_HTML = _STYLE + _NAV.replace(
    '{extra}', ' / <a href="/leases/property/{{ prop.id }}">{{ prop.name }}</a> / Analysis'
).replace('{title}', '{{ prop.name }} — Pricing Analysis') + """
<div class="container">

  <!-- Scenario tabs -->
  <div class="tabs" style="margin-top:16px">
    <a class="tab {% if scenario == 'new' %}active{% endif %}"
       href="/leases/property/{{ prop.id }}/analysis?scenario=new">New Lease</a>
    <a class="tab {% if scenario == 'renewal' %}active{% endif %}"
       href="/leases/property/{{ prop.id }}/analysis?scenario=renewal">Renewal</a>
  </div>

  <!-- Portfolio signal bar -->
  <div class="signal-row">
    <div class="signal-chip">
      <span class="label">Velocity</span>
      <span class="value">{{ port_vel.tier }}</span>
      <span class="muted">({{ port_vel.leases_signed }} leases / 90d)</span>
    </div>
    <div class="signal-chip">
      <span class="label">Gap Level</span>
      <span class="value">{{ port_gap.level_tier }}</span>
      <span class="muted">({{ "{:.1%}".format(port_gap.gap_pct) }} avg concession)</span>
    </div>
    <div class="signal-chip">
      <span class="label">Gap Trend</span>
      <span class="value">{{ port_gap.trend_tier }}</span>
    </div>
    <div class="signal-chip">
      <span class="label">As of</span>
      <span class="value">{{ summary.as_of }}</span>
    </div>
  </div>

  <!-- Summary cards -->
  <div class="grid">
    <div class="card">
      <div class="card-label">Unit Types Priced</div>
      <div class="card-value">{{ summary.unit_type_count }}</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Floor</div>
      <div class="card-value">${{ "{:,.0f}".format(summary.avg_floor) }}</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Recommended</div>
      <div class="card-value green">${{ "{:,.0f}".format(summary.avg_recommended) }}</div>
    </div>
    <div class="card">
      <div class="card-label">Leases in History</div>
      <div class="card-value">{{ summary.lease_count }}</div>
    </div>
  </div>

  <!-- Pricing table -->
  <h2>Pricing Recommendations — {{ scenario | capitalize }} Lease</h2>

  {% if pricing_rows %}
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Unit Type</th>
          <th>Floor</th>
          <th>Recommended</th>
          <th>Premium</th>
          <th>Posture</th>
          <th>Avail</th>
          <th>Velocity</th>
          <th>Gap</th>
          <th>Seasonal</th>
          <th>Intrinsic</th>
        </tr>
      </thead>
      <tbody>
        {% for r in pricing_rows %}
        <tr>
          <td><strong>{{ r.unit_type }}</strong></td>
          <td>
            {% if r.feasible %}${{ "{:,.0f}".format(r.floor) }}
            {% else %}<span class="muted">—</span>{% endif %}
          </td>
          <td>
            {% if r.feasible %}
              <strong class="green">${{ "{:,.0f}".format(r.recommended) }}</strong>
            {% else %}
              <span class="muted">manual</span>
            {% endif %}
          </td>
          <td>
            {% if r.feasible %}
              {% if r.premium_pct > 0 %}
                <span class="green">+{{ "{:.1f}".format(r.premium_pct) }}%</span>
              {% elif r.premium_pct < 0 %}
                <span class="red">{{ "{:.1f}".format(r.premium_pct) }}%</span>
              {% else %}
                <span class="muted">0.0%</span>
              {% endif %}
            {% else %}<span class="muted">—</span>{% endif %}
          </td>
          <td>
            {% set pos = r.posture %}
            {% if pos == 'Very Scarce' %}
              <span class="badge badge-green">{{ pos }}</span>
            {% elif pos == 'Scarce' %}
              <span class="badge badge-blue">{{ pos }}</span>
            {% elif pos == 'Normal' %}
              <span class="badge badge-gray">{{ pos }}</span>
            {% elif pos == 'Soft' %}
              <span class="badge badge-yellow">{{ pos }}</span>
            {% else %}
              <span class="badge badge-red">{{ pos }}</span>
            {% endif %}
          </td>
          <td>
            {% if r.ut_total > 0 %}
              {{ r.ut_avail }}/{{ r.ut_total }}
              <span class="muted">({{ "{:.0%}".format(r.ut_avail / r.ut_total) }})</span>
            {% else %}<span class="muted">—</span>{% endif %}
          </td>
          <td>
            {% set vt = r.velocity_tier %}
            {% if vt == 'very_strong' or vt == 'strong' %}
              <span class="badge badge-green">{{ vt }}</span>
            {% elif vt == 'normal' %}
              <span class="badge badge-gray">{{ vt }}</span>
            {% elif vt == 'stalled' %}
              <span class="badge badge-red">{{ vt }}</span>
            {% else %}
              <span class="badge badge-yellow">{{ vt }}</span>
            {% endif %}
          </td>
          <td>
            {% set gl = r.gap_level %}
            {% if gl == 'tight' or gl == 'light' %}
              <span class="green">{{ gl }}</span>
            {% elif gl == 'deep' or gl == 'elevated' %}
              <span class="red">{{ gl }}</span>
            {% else %}
              <span class="muted">{{ gl }}</span>
            {% endif %}
          </td>
          <td>
            {% if r.seasonal > 1.0 %}
              <span class="green">{{ "{:.3f}".format(r.seasonal) }}</span>
            {% elif r.seasonal < 1.0 %}
              <span class="yellow">{{ "{:.3f}".format(r.seasonal) }}</span>
            {% else %}
              <span class="muted">{{ "{:.3f}".format(r.seasonal) }}</span>
            {% endif %}
          </td>
          <td>
            {% if r.intrinsic > 0 %}
              <span class="green">+{{ "{:.2f}".format(r.intrinsic) }}%</span>
            {% elif r.intrinsic < 0 %}
              <span class="red">{{ "{:.2f}".format(r.intrinsic) }}%</span>
            {% else %}
              <span class="muted">—</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <p class="subtitle" style="margin-top:8px">
    Floor = break-even effective rent (downtime + make-ready + marketing + commission).
    Recommended = floor × (1 + premium), capped at +6% / -4%. Floor backstop: recommended ≥ floor always.
    <a href="/leases/api/property/{{ prop.id }}/pricing?scenario={{ scenario }}" target="_blank">JSON API</a>
  </p>

  {% else %}
  <div class="card"><p class="empty">
    No pricing results. The property may not have enough lease history or rent-roll data.
    Try uploading a rent-roll document and running extraction first.
  </p></div>
  {% endif %}

</div>
"""

_NOT_FOUND_HTML = _STYLE + """
<div class="container" style="padding-top:60px;text-align:center">
  <h2>Property not found</h2>
  <p class="muted" style="margin:12px 0">Property ID {{ property_id }} does not exist.</p>
  <a href="/leases" class="btn">Back to Lease Analysis</a>
</div>
"""

_ERROR_HTML = _STYLE + """
<div class="container" style="padding-top:60px">
  <h2 class="red">Analysis Error</h2>
  <p class="muted" style="margin:12px 0">
    Property {{ property_id }} — an error occurred while running the analysis.
  </p>
  <pre style="background:var(--surface);border:1px solid var(--border);border-radius:6px;
              padding:16px;color:var(--red);font-size:12px;white-space:pre-wrap">{{ error }}</pre>
  <div style="margin-top:16px;display:flex;gap:10px">
    <a href="/leases/property/{{ property_id }}" class="btn">Property Overview</a>
    <a href="/leases" class="btn">Dashboard</a>
  </div>
</div>
"""
