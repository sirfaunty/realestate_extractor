"""
Residential Portfolio Module — KA residential assets (6 multifamily + Arbors).

Read-only surfaces over the residential handoff package: asset roster with
PM history, quarterly operating trends (occupancy/leasing/NER from weekly
report extractions), NOI bridge 2026B→2028F, cap-rate valuation matrices,
scored sales comps, value programs, and the data-source discrepancy report.

Data doctrine: KA internal accounting authoritative for actuals; proforma
only for forward-looks (labeled F). Chamberlain cross-links to Capactive's
deal-analytics pages.
"""

from ..base import AbstractModule


class ResidentialModule(AbstractModule):

    @property
    def name(self):
        return 'residential'

    @property
    def display_name(self):
        return 'Residential Portfolio'

    @property
    def description(self):
        return ('KA residential portfolio — operating trends, NOI bridge, '
                'valuation, comps across 7 assets / 1,350 units')

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_residential_routes
        register_residential_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Residential Portfolio',
            'url': '/residential',
            'icon': 'home',
            'section': 'portfolio',
        }]


module_instance = ResidentialModule()
