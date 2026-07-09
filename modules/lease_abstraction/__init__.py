"""
Center C / Lease Abstraction module.

Wraps the standalone `lease_abstraction_db` engine (lease .docx -> provision warehouse ->
local-model 3-tier abstracts -> Word compendium) in a no-code UI: pick a lease,
click generate, and the module segments the lease, abstracts every provision on
this device with the local model, and produces the Lease Abstract Compendium for
download.

All processing runs locally on the host that serves the app.
"""

from ..base import AbstractModule


class Center CModule(AbstractModule):

    @property
    def name(self):
        return 'lease_abstraction'

    @property
    def display_name(self):
        return 'Lease Abstraction'

    @property
    def description(self):
        return ('Turn a commercial lease into a searchable provision warehouse with '
                'three-tier abstracts, and export a Lease Abstract Compendium — all '
                'processed on-device by the local model.')

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_lease_abstraction_routes
        register_lease_abstraction_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Lease Abstraction',
            'url': '/lease_abstraction',
            'icon': 'document',
            'section': 'analytics',
        }]


module_instance = Center CModule()
