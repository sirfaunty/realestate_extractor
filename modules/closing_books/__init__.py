"""
Closing Books Module — Document extraction warehouse explorer.

Serves the Chamberlain Apartments closing-books warehouse built by
the partner's extraction pipeline. SQLite-backed, read-only:
  - 71 FileRecords across 6 closing-book PDFs
  - 379 ContentBlocks with extracted text, dollar amounts, dates
  - 156 ModuleMappings linking blocks to platform modules
  - 16 GapRecords, 16 DuplicateVerdicts, 48 SearchQueries, 20 Learnings
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
