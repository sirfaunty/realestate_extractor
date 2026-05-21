"""
Distribution & Surplus Cash Module — partnership waterfall modeling.

Models the LLC distribution waterfall (§5.2 escrow recapture → preferred
return → pari passu), surplus cash note amortization, capital account
tracking, and per-partner return metrics (IRR, EM, Cash-on-Cash).
"""

from ..base import AbstractModule


class DistributionModule(AbstractModule):

    @property
    def name(self):
        return 'distribution'

    @property
    def display_name(self):
        return 'Distribution & Surplus Cash'

    @property
    def description(self):
        return 'Partnership waterfall, surplus cash note, and distribution analysis'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_distribution_routes
        register_distribution_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Distribution & Surplus Cash',
            'url': '/distribution/',
            'icon': 'dollar-sign',
            'section': 'analysis',
        }]


module_instance = DistributionModule()
