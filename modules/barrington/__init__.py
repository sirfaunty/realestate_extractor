"""
Barrington / Portfolio Cash Flow module.

Wraps the standalone `barrington_db` extractor (cash flow + rent roll ->
SQLite -> portfolio cash flow / capital / lease-rollover model) in a no-code
UI: a user uploads the source cash-flow and rent-roll documents, the module
rebuilds the database on this device, validates the NOI tie-out, and produces
the Excel deliverable for download.

All processing runs locally on the host that serves the app.
"""

from ..base import AbstractModule


class BarringtonModule(AbstractModule):

    @property
    def name(self):
        return 'barrington'

    @property
    def display_name(self):
        return 'Portfolio Cash Flow'

    @property
    def description(self):
        return ('Build a portfolio cash-flow / capital / lease-rollover model from '
                'source cash flows and rent rolls, and export an Excel deliverable.')

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_barrington_routes
        register_barrington_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Portfolio Cash Flow',
            'url': '/portfolio-cashflow',
            'icon': 'cash-flow',
            'section': 'analytics',
        }]


module_instance = BarringtonModule()
