"""
Lease Analysis Module — Capactive platform module for multifamily lease pricing.

Implements a 7-layer pricing model:
  1. Break-even floor (effective rent) from operating cost assumptions
  2. Three-level scarcity premium (property / floorplan / unit type)
  3. Forward exposure adjustment (rolling 30/60/90/180-day horizon)
  4. Leasing velocity multiplier (trailing 90-day absorption)
  5. Asking-vs-achieved gap multiplier (concession environment)
  6. Seasonality multiplier (asymmetric new vs. renewal, 24-mo trailing)
  7. Intrinsic unit-type adjustment (revealed trade-out / TTL / concession)

Delivered partner files (intrinsic.py, pricing.py, run_analysis.py) are
supported as standalone tools. This module integrates the pipeline with
the platform's SQLite database via LeaseAnalysisEngine.
"""

from ..base import AbstractModule


class LeaseAnalysisModule(AbstractModule):

    @property
    def name(self):
        return 'lease_analysis'

    @property
    def display_name(self):
        return 'Lease Analysis'

    @property
    def description(self):
        return '7-layer multifamily rent pricing: break-even floor, scarcity, velocity, gap, seasonality, intrinsic'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_lease_analysis_routes
        register_lease_analysis_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Lease Analysis',
            'url': '/leases',
            'icon': 'home',
            'section': 'analytics',
        }]

    def get_property_tabs(self):
        return [{
            'label': 'Leases',
            'url_suffix': 'leases',
            'icon': 'file-text',
        }]


module_instance = LeaseAnalysisModule()
