"""
Sales Comps Module — Transaction search, cap rate trends, pricing analytics, ownership history.

Backed by the DuckDB warehouse:
  - 23,675 sales transactions across 412 markets (2000–2026)
  - Cap rate aggregates (national, market, submarket × year/quarter)
  - Pricing aggregates ($/unit, $/SF by class/vintage/market)
  - Ownership history (37K records, 11.5K unique owners)
"""

from ..base import AbstractModule


class SalesCompsModule(AbstractModule):

    @property
    def name(self):
        return 'sales_comps'

    @property
    def display_name(self):
        return 'Sales Comps'

    @property
    def description(self):
        return 'Transaction comps, cap rate trends, pricing analytics, and ownership history'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_sales_comps_routes
        register_sales_comps_routes(app)

    def get_property_tabs(self):
        return [{
            'label': 'Comps',
            'url_suffix': 'comps',
            'icon': 'dollar-sign',
        }]

    def get_nav_items(self):
        return [{
            'label': 'Sales Comps',
            'url': '/comps',
            'icon': 'trending-up',
            'section': 'analytics',
        }]


module_instance = SalesCompsModule()
