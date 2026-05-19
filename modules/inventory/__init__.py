"""
Inventory Module — National property inventory with z-score peer benchmarking.

Provides property-level z-score analysis across ~150-265 metrics,
21 peer cut dimensions, and multiple geographic levels. Backed by
the DuckDB analytical warehouse.

Core capabilities:
  - Property z-score lookup (pre-computed from warehouse)
  - Peer group exploration (who are my peers, how do I compare)
  - Market-level inventory stats
  - Property identity bridge (SQLite ↔ CoStar ↔ warehouse)
  - On-demand re-scoring via the embedded zscore engine
"""

from ..base import AbstractModule


class InventoryModule(AbstractModule):

    @property
    def name(self):
        return 'inventory'

    @property
    def display_name(self):
        return 'National Inventory'

    @property
    def description(self):
        return 'Property z-score benchmarking across 150+ metrics and 21 peer dimensions'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_inventory_routes
        register_inventory_routes(app)

    def get_property_tabs(self):
        return [{
            'label': 'Z-Scores',
            'url_suffix': 'zscores',
            'icon': 'bar-chart',
        }]

    def get_nav_items(self):
        return [{
            'label': 'Inventory',
            'url': '/inventory',
            'icon': 'database',
            'section': 'analytics',
        }]


module_instance = InventoryModule()
