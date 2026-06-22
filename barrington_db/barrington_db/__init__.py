"""
Barrington Portfolio — Cash Flow Database.

Modular SQLite-backed cash flow database for the Barrington office portfolio.
Each property has its own module (PropertyModule); the Portfolio class rolls
them up. The 2026 cash flow (operating + forecasted capital: TI, LC, and base-
building / LL work) is extracted from the April 2026 source documents; the
rent rolls drive lease-rollover analysis and the 2027-2028 forward model.
"""
from .db import connect, init_db
from .property_module import PropertyModule
from .portfolio import Portfolio

__all__ = ["connect", "init_db", "PropertyModule", "Portfolio"]
