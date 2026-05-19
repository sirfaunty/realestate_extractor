"""
Proforma Module — CRE financial projection modeling with citation drill-back.

Bridges Capactive's extracted document data into the chamberlain proforma
engine, producing forward projections where every number traces back to
its source document.
"""

from ..base import AbstractModule


class ProformaModule(AbstractModule):

    @property
    def name(self):
        return 'proforma'

    @property
    def display_name(self):
        return 'Proforma Modeling'

    @property
    def description(self):
        return 'CRE financial projections with source citation drill-back'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_proforma_routes
        register_proforma_routes(app)

    def get_property_tabs(self):
        return [{
            'label': 'Proforma',
            'url_suffix': 'proforma',
            'icon': 'chart-line',
        }]


module_instance = ProformaModule()
