"""
TIF Analysis Module — Tax Increment Financing scenario modeling.

Translates Riley's 14-tab Chamberlain TIF Excel model into an interactive
platform module with multi-scenario comparison, breakeven analysis,
sensitivity sweeps, and the Ehlers reconciliation.
"""

from ..base import AbstractModule


class TIFAnalysisModule(AbstractModule):

    @property
    def name(self):
        return 'tif_analysis'

    @property
    def display_name(self):
        return 'TIF / Tax Analysis'

    @property
    def description(self):
        return 'Tax Increment Financing scenario modeling and appeal analysis'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_tif_routes
        register_tif_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'TIF / Tax Analysis',
            'url': '/tif-analysis/',
            'icon': 'calculator',
            'section': 'analysis',
        }]


module_instance = TIFAnalysisModule()
