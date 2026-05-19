"""
Base class for Capactive platform modules.

Every module subclasses AbstractModule and implements:
- register_routes(app): add Flask routes
- Optional: get_nav_items(): return nav entries for the sidebar
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional


class AbstractModule(ABC):
    """Base class for all platform modules."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g., 'proforma')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name (e.g., 'Proforma Modeling')."""
        ...

    @property
    def description(self) -> str:
        """One-line description."""
        return ''

    @property
    def version(self) -> str:
        """Module version."""
        return '0.1.0'

    @abstractmethod
    def register_routes(self, app):
        """Register Flask routes with the app."""
        ...

    def get_nav_items(self) -> List[Dict]:
        """Return navigation items for this module.

        Each item: {
            'label': str,
            'url': str,
            'icon': str (optional),
            'section': str (e.g., 'property' for property-level nav),
        }
        """
        return []

    def get_property_tabs(self) -> List[Dict]:
        """Return tabs to add to the property detail page.

        Each item: {
            'label': str,
            'url_suffix': str (appended to /property/<id>/),
            'icon': str (optional),
        }
        """
        return []
