"""
Partnership Dashboard Module — unified executive summary.

Aggregates proforma, distribution, and debt data into a single
partner-facing view with cross-TIF-scenario comparison.
"""

from ..base import AbstractModule


class PartnershipDashboardModule(AbstractModule):

    @property
    def name(self):
        return 'partnership_dashboard'

    @property
    def display_name(self):
        return 'Partnership Dashboard'

    @property
    def description(self):
        return 'Unified executive summary combining returns, debt, and decision metrics'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_partnership_routes
        register_partnership_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Partnership Dashboard',
            'url': '/partnership/',
            'icon': 'users',
            'section': 'analysis',
        }]


module_instance = PartnershipDashboardModule()
