"""
Office Inventory Module — Office property Z-score benchmarking.

Serves pre-built office property Z-score data from a DuckDB database.
Scores 8 performance measures across 7 co-equal peer lenses for
~5,000 office properties in the Minneapolis MSA.

Core capabilities:
  - Property-level Z-score lookup (pre-computed, stored in DuckDB)
  - Peer lens exploration (status, construction, tenancy, submarket, etc.)
  - Tenant data per building
  - Demographic Z-scores per cohort
  - Geo panel time series (FRED, ACS, BLS)
  - Peer cell health monitoring (engine-stable vs collapsed)
"""

from ..base import AbstractModule


class OfficeModule(AbstractModule):

    @property
    def name(self):
        return 'office'

    @property
    def display_name(self):
        return 'Office Inventory'

    @property
    def description(self):
        return 'Office property Z-score benchmarking across 8 measures and 7 peer lenses'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_office_routes
        register_office_routes(app)

    def get_property_tabs(self):
        return [{
            'label': 'Office Z-Scores',
            'url_suffix': 'office-zscores',
            'icon': 'bar-chart',
        }]

    def get_nav_items(self):
        return [{
            'label': 'Office Inventory',
            'url': '/office',
            'icon': 'building',
            'section': 'analytics',
        }]


module_instance = OfficeModule()
