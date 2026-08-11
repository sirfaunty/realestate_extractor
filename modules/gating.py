"""
Module gating — per-org module activation (tier / entitlement enforcement).

An org's FeatureFlags.modules_enabled controls which platform modules it can
reach. "*" (the default, and the enterprise plan) means everything, so
existing orgs and the dev org are unaffected until a plan or an admin
override narrows the set.

Enforcement is central: one before_request hook maps request paths to module
names via MODULE_ROUTES and blocks disabled modules with a friendly page.
A context processor exposes `module_enabled(name)` so base.html can hide nav
entries for disabled modules.
"""

import logging
from flask import request, session, g, render_template_string

logger = logging.getLogger(__name__)

# Module name (modules/__init__.py INSTALLED_MODULES) -> URL prefixes it owns.
MODULE_ROUTES = {
    'inventory': ['/inventory'],
    'sales_comps': ['/comps'],
    'scorecard': ['/scorecard'],
    'lease_analysis': ['/leases'],
    'market_intel': ['/market-intel'],
    'office': ['/office'],
    'closing_books': ['/closing-books'],
    'tif_analysis': ['/tif-analysis'],
    'distribution': ['/distribution'],
    'debt_analysis': ['/debt'],
    'partnership_dashboard': ['/partnership'],
    'barrington': ['/portfolio-cashflow'],
    'southtown': ['/lease-abstraction'],
    'midway': ['/disposition-diligence'],
    'portfolio_ownership': ['/portfolio-ownership'],
    'residential': ['/residential'],
    'deliverables': ['/deliverables'],
    # 'proforma' has no blueprint prefix of its own — auth-gated in core routes.
}

# Display groupings for the admin UI (order matters).
MODULE_GROUPS = [
    ('Deal Documents', ['closing_books', 'tif_analysis', 'distribution',
                        'debt_analysis', 'partnership_dashboard', 'barrington',
                        'southtown', 'midway']),
    ('Market Analytics', ['inventory', 'sales_comps', 'scorecard',
                          'lease_analysis', 'market_intel', 'office']),
    ('Portfolio', ['portfolio_ownership', 'residential', 'deliverables']),
]

_LOCKED_HTML = """
<!DOCTYPE html><html><head><meta charset='utf-8'><title>Module not enabled</title>
<style>
body { font-family: Inter, system-ui, sans-serif; background:#F7F8FA; color:#1A1A2E;
       display:flex; align-items:center; justify-content:center; min-height:90vh; margin:0; }
.card { background:#fff; border:1px solid #E2E4E8; border-radius:12px; padding:40px 48px;
        max-width:520px; box-shadow:0 4px 16px rgba(0,0,0,0.06); }
h1 { font-size:20px; margin:0 0 10px; }
p { color:#5a5a6e; font-size:14px; line-height:1.6; }
a { color:#185FA5; }
.badge { display:inline-block; background:#E6F1FB; color:#0B3D6B; border-radius:12px;
         padding:2px 12px; font-size:12px; margin-bottom:14px; }
</style></head><body><div class='card'>
<div class='badge'>{{ module_label }}</div>
<h1>This module isn't enabled for your organization</h1>
<p>Your current plan doesn't include access to this module. An administrator can
review available modules under <a href='/admin/modules'>Admin &rsaquo; Modules</a>,
or contact your Capactive representative about upgrading.</p>
<p><a href='/'>&larr; Back to dashboard</a></p>
</div></body></html>
"""


def _prefix_map():
    out = {}
    for mod, prefixes in MODULE_ROUTES.items():
        for p in prefixes:
            out[p] = mod
    return out


_PREFIXES = _prefix_map()


def get_enabled_modules():
    """Resolve the current org's enabled-module set. Cached per request.

    Returns a set of module names, or None meaning 'everything' ("*").
    Fails open (None) if config can't be resolved — gating must never take
    the whole app down.
    """
    if hasattr(g, '_enabled_modules'):
        return g._enabled_modules
    enabled = None
    try:
        try:  # circular-safe at call time; same dual-path pattern as the module registry
            from realestate_extractor.webapp import get_config_store
        except ImportError:
            from webapp import get_config_store
        org_id = session.get('org_id', 'dev')
        features = get_config_store().get_org_features(org_id)
        if features is not None:
            mods = list(features.modules_enabled or [])
            enabled = None if '*' in mods else set(mods)
    except Exception as e:  # fail open
        logger.debug(f'module gating: falling open ({e})')
        enabled = None
    g._enabled_modules = enabled
    return enabled


def module_enabled(name: str) -> bool:
    enabled = get_enabled_modules()
    return True if enabled is None else name in enabled


def register_gating(app):
    """Attach the enforcement hook and template helper to the app."""

    @app.before_request
    def _module_gate():
        path = request.path or '/'
        for prefix, mod in _PREFIXES.items():
            if path == prefix or path.startswith(prefix + '/'):
                if not module_enabled(mod):
                    label = mod.replace('_', ' ').title()
                    logger.info(f'module gate: blocked {path} (module {mod}) '
                                f'for org {session.get("org_id", "dev")}')
                    return render_template_string(_LOCKED_HTML, module_label=label), 403
                break
        return None

    @app.context_processor
    def _inject_module_helper():
        return {'module_enabled': module_enabled}

    logger.info(f'Module gating registered ({len(_PREFIXES)} route prefixes)')
