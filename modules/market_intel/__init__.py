"""
Market Intelligence Module — Capactive platform module for brokerage-grade market analysis.

Synthesizes warehouse data (cap rates, pricing, sales volume, z-scores, ownership)
with scorecard rankings to produce market intelligence briefs and dashboards.

Data sources:
  - DuckDB warehouse: fact_cap_rate_aggregate, fact_pricing_aggregate,
    fact_sales_transaction, fact_property_zscore, fact_ownership, dim_property
  - Scorecard module: market rankings, tilt scores, component breakdowns

Depends on: warehouse (Phase 1), scorecard (Module 3)
"""

from ..base import AbstractModule


class MarketIntelModule(AbstractModule):

    @property
    def name(self):
        return 'market_intel'

    @property
    def display_name(self):
        return 'Market Intelligence'

    @property
    def description(self):
        return 'Brokerage-grade market analysis: cap rate trends, sales activity, pricing, scorecard integration'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_market_intel_routes
        register_market_intel_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Market Intelligence',
            'url': '/market-intel',
            'icon': 'bar-chart',
            'section': 'analytics',
        }]

    def get_property_tabs(self):
        return [{
            'label': 'Market Context',
            'url_suffix': 'market-context',
            'icon': 'globe',
        }]


module_instance = MarketIntelModule()
