"""
Deliverables Module — document generation from the verified KA portfolio
warehouse and canonical module DBs (read-only sources).

First deliverable: the per-property Lease Abstract Compendium (.docx) —
every tenancy summarized and every page-cited provision rendered with its
source citation and refi-impact flags. Follows the Southtown compendium
precedent; renders verified data only (no LLM at build time).
"""

from ..base import AbstractModule


class DeliverablesModule(AbstractModule):

    @property
    def name(self):
        return 'deliverables'

    @property
    def display_name(self):
        return 'Deliverables'

    @property
    def description(self):
        return ('Generate client-ready documents from the verified portfolio '
                'warehouse — lease abstract compendiums, with citations')

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_deliverables_routes
        register_deliverables_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Deliverables',
            'url': '/deliverables',
            'icon': 'file-output',
            'section': 'portfolio',
        }]


module_instance = DeliverablesModule()
