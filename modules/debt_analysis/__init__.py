"""Debt & Loan Analysis module.

Provides amortization schedules, DSCR tracking, MIP analysis,
LTV monitoring, and refinance scenario modeling for the Chamberlain
HUD 223(f) acquisition loan.
"""

from modules.base import AbstractModule


class DebtAnalysisModule(AbstractModule):
    """Debt & Loan Analysis module."""

    @property
    def name(self):
        return 'debt_analysis'

    @property
    def display_name(self):
        return 'Debt & Loan Analysis'

    @property
    def description(self):
        return 'Amortization, DSCR tracking, MIP schedule, LTV analysis, and refinance scenarios'

    @property
    def version(self):
        return '1.0.0'

    def register_routes(self, app):
        from .routes import register_debt_routes
        register_debt_routes(app)

    def get_nav_items(self):
        return [
            {
                'label': 'Debt & Loan Analysis',
                'url': '/debt/',
                'icon': 'landmark',
                'section': 'analysis',
            }
        ]


module_instance = DebtAnalysisModule()
