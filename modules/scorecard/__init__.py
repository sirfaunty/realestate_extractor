"""
Scorecard Module — Market-level scoring with tilt engine pipeline.

Provides market scoring based on the partner's 11-step tilt engine:
  Signal Z → Category Z → Vol/Cat multipliers → Total Z →
  D&S/Occ/Rent → MF Fundamental → Momentum → Duration-weighted final

Backed by the DuckDB analytical warehouse for data storage and retrieval.
Depends on the inventory module for property-level z-score data.
"""

from ..base import AbstractModule


class ScorecardModule(AbstractModule):

    @property
    def name(self):
        return 'scorecard'

    @property
    def display_name(self):
        return 'Market Scorecard'

    @property
    def description(self):
        return 'Market-level scoring with tilt engine — demand/supply, occupancy, rent growth'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_scorecard_routes
        register_scorecard_routes(app)

    def get_property_tabs(self):
        return [{
            'label': 'Scorecard',
            'url_suffix': 'scorecard',
            'icon': 'trending-up',
        }]

    def get_nav_items(self):
        return [{
            'label': 'Scorecard',
            'url': '/scorecard',
            'icon': 'activity',
            'section': 'analytics',
        }]


module_instance = ScorecardModule()
