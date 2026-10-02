"""
Disposition Diligence module.

Wraps a standalone engine (tenant certification docs -> OCR ->
structured lease-abstract warehouse + PSA/REA extraction + missing-document tracker
-> a Word Disposition Diligence Report) in a no-code UI: view the diligence summary,
optionally re-run the local extraction pipeline, and download the report.

All processing runs locally on the host that serves the app.
"""

from ..base import AbstractModule


class DispositionDiligenceModule(AbstractModule):

    @property
    def name(self):
        return 'disposition_diligence'

    @property
    def display_name(self):
        return 'Disposition Diligence'

    @property
    def description(self):
        return ('Turn a shopping-center disposition’s tenant certification documents '
                'into a structured diligence database (lease abstracts, missing-document '
                'tracker, PSA economics, REA prohibited uses) and a Word report — all '
                'extracted on-device.')

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_disposition_diligence_routes
        register_disposition_diligence_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Disposition Diligence',
            'url': '/disposition-diligence',
            'icon': 'document',
            'section': 'analytics',
        }]


module_instance = DispositionDiligenceModule()
