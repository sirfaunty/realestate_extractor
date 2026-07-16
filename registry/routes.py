"""
Registry HTTP API — read endpoints that feed the deal picker and (later) a
hierarchy editor. Kept intentionally small; mutations happen through the store.
"""

import logging
from flask import Blueprint, jsonify, request

from . import get_registry, resolve_deal, DEFAULT_DEAL

logger = logging.getLogger(__name__)

registry_bp = Blueprint('registry', __name__, url_prefix='/api/registry')


def register_registry_routes(app):
    """Register the registry API blueprint with the Flask app."""
    app.register_blueprint(registry_bp)


@registry_bp.route('/deals')
def api_deals():
    """List selectable deals for the deal-analytics picker, each with its
    fund/sub-fund/portfolio path (root-first) for optional grouping."""
    reg = get_registry()
    deals = []
    for d in reg.list_deals():
        path = [a['label'] for a in reversed(reg.ancestors(d['id']))]
        deals.append({'id': d['id'], 'label': d['label'], 'path': path})
    return jsonify({'deals': deals, 'default': DEFAULT_DEAL})


@registry_bp.route('/tree')
def api_tree():
    """Full entity tree (funds -> sub-funds -> portfolios -> deals/leases)."""
    return jsonify({'tree': get_registry().get_tree()})


@registry_bp.route('/resolve')
def api_resolve():
    """Resolve a ?deal= value to its registry entity (default-safe)."""
    return jsonify(resolve_deal(request.args.get('deal')))
