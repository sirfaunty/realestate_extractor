"""
Closing Books Module — Document extraction warehouse explorer.

Serves a deal's closing-books warehouse (SQLite, read-only): file records,
content blocks with extracted text / amounts / dates, module mappings, gap
records, duplicate verdicts, search queries and learnings. Path resolution:
see engine._default_db() — the warehouse itself is deployment data.
"""

from ..base import AbstractModule


class ClosingBooksModule(AbstractModule):

    @property
    def name(self):
        return 'closing_books'

    @property
    def display_name(self):
        return 'Closing Books'

    @property
    def description(self):
        return 'Document extraction warehouse — closing-book files, content blocks, module mappings, gaps'

    @property
    def version(self):
        return '0.1.0'

    def register_routes(self, app):
        from .routes import register_closing_books_routes
        register_closing_books_routes(app)

    def get_nav_items(self):
        return [{
            'label': 'Closing Books',
            'url': '/closing-books',
            'icon': 'file-text',
            'section': 'documents',
        }]


module_instance = ClosingBooksModule()
