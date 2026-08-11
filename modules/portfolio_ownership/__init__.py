"""
Portfolio Ownership Module — read-only UI over the KA portfolio warehouse.

Surfaces the aggregated, page-cited lease & ownership extraction
(portfolio_warehouse.db): per-property lease rosters, the provision browser
with citations, the property-level document layer, rollovers, the
portfolio-wide open-items register, and cross-portfolio provision search.

The master DB is maintained exclusively by the aggregation workflow
(merge harnesses + verify suite); this module never writes to it.
"""

from ..base import AbstractModule


class PortfolioOwnershipModule(AbstractModule):

    @property
    def name(self):
        return 'portfolio_ownership'

    @property
    def display_name(self):
        return 'Portfolio Ownership'

    @property
    def description(self):
        return ('Page-cited lease & ownership document extraction across the '
                'KA portfolio — provisions, rollovers, open items')

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_portfolio_ownership_routes
        register_portfolio_ownership_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Portfolio Ownership',
            'url': '/portfolio-ownership',
            'icon': 'file-text',
            'section': 'portfolio',
        }]


module_instance = PortfolioOwnershipModule()
