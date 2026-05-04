"""
Role-Based Access Control for Capactive Document Extractor.

Manages:
- Permission scopes (what data/features a user can access)
- Access levels (none, read, edit)
- Role templates (admin, analyst, operator, viewer)
- Per-user permission overrides
- Permission checking for routes and templates

Permissions are stored in the central config database alongside
org/user records.
"""

import json
import sqlite3
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict


# ─── Permission Scopes ──────────────────────────────────────────────
#
# Each scope controls access to a section of the application.
# Scopes are organized hierarchically with dot notation.
# Add new scopes here as the product grows.
#

SCOPES = {
    # Property Intelligence (Layer 2)
    'property.operations': {
        'label': 'Operations',
        'description': 'Tenant roster, rent roll, lease terms, operating expenses',
        'category': 'Property Data',
    },
    'property.debt': {
        'label': 'Debt',
        'description': 'Loan terms, guarantees, covenants, debt service',
        'category': 'Property Data',
    },
    'property.valuation': {
        'label': 'Valuation / NOI',
        'description': 'Revenue, expenses, NOI, cap rate, DSCR',
        'category': 'Property Data',
    },
    'property.documents': {
        'label': 'Documents',
        'description': 'View and manage documents linked to properties',
        'category': 'Property Data',
    },
    'property.units': {
        'label': 'Buildings & Units',
        'description': 'Manage physical asset structure — buildings and units',
        'category': 'Property Data',
    },

    # Extraction (Layer 1)
    'extraction.upload': {
        'label': 'Upload & Process',
        'description': 'Upload PDFs and run extraction',
        'category': 'Extraction',
    },
    'extraction.batch': {
        'label': 'Batch Processing',
        'description': 'Run batch folder processing',
        'category': 'Extraction',
    },
    'extraction.review': {
        'label': 'Review Queue',
        'description': 'Approve and link documents to properties',
        'category': 'Extraction',
    },

    # Admin
    'admin.users': {
        'label': 'User Management',
        'description': 'Add, edit, and deactivate users',
        'category': 'Admin',
    },
    'admin.settings': {
        'label': 'Settings & License',
        'description': 'Organization settings, license, audit trail',
        'category': 'Admin',
    },
}

# Ordered list for UI display
SCOPE_ORDER = [
    'property.operations', 'property.debt', 'property.valuation',
    'property.documents', 'property.units',
    'extraction.upload', 'extraction.batch', 'extraction.review',
    'admin.users', 'admin.settings',
]

# Access levels
LEVELS = ['none', 'read', 'edit']


# ─── Role Templates ─────────────────────────────────────────────────

ROLE_TEMPLATES = {
    'admin': {
        'label': 'Admin',
        'description': 'Full access to everything',
        'permissions': {scope: 'edit' for scope in SCOPES},
    },
    'operator': {
        'label': 'Operator',
        'description': 'Manage properties and extraction, read financial data',
        'permissions': {
            'property.operations': 'edit',
            'property.debt': 'read',
            'property.valuation': 'read',
            'property.documents': 'edit',
            'property.units': 'edit',
            'extraction.upload': 'edit',
            'extraction.batch': 'edit',
            'extraction.review': 'edit',
            'admin.users': 'none',
            'admin.settings': 'none',
        },
    },
    'analyst': {
        'label': 'Analyst',
        'description': 'Read-only access to property data, no extraction or admin',
        'permissions': {
            'property.operations': 'read',
            'property.debt': 'read',
            'property.valuation': 'read',
            'property.documents': 'read',
            'property.units': 'read',
            'extraction.upload': 'none',
            'extraction.batch': 'none',
            'extraction.review': 'none',
            'admin.users': 'none',
            'admin.settings': 'none',
        },
    },
    'viewer': {
        'label': 'Viewer',
        'description': 'Read-only access to all property data',
        'permissions': {
            'property.operations': 'read',
            'property.debt': 'read',
            'property.valuation': 'read',
            'property.documents': 'read',
            'property.units': 'read',
            'extraction.upload': 'none',
            'extraction.batch': 'none',
            'extraction.review': 'read',
            'admin.users': 'none',
            'admin.settings': 'none',
        },
    },
}


# ─── Database Schema ─────────────────────────────────────────────────

PERMISSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_permissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    org_id      TEXT NOT NULL,
    role_template TEXT DEFAULT 'viewer',
    overrides   TEXT,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, org_id)
);

CREATE INDEX IF NOT EXISTS idx_perms_user ON user_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_perms_org ON user_permissions(org_id, user_id);
"""


# ─── Permission Store ────────────────────────────────────────────────

class PermissionStore:
    """
    Manages user permissions in the config database.

    Permissions = role_template defaults + per-user overrides.
    """

    def __init__(self, db_path: str = "capactive_config.db"):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(PERMISSIONS_SCHEMA)
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()

    def get_user_permissions(self, user_id: str, org_id: str) -> Dict[str, str]:
        """
        Get resolved permissions for a user.

        Returns dict of {scope: level} with role template defaults
        merged with any per-user overrides.
        """
        cur = self.conn.execute(
            "SELECT * FROM user_permissions WHERE user_id = ? AND org_id = ?",
            (user_id, org_id))
        row = cur.fetchone()

        if row:
            role = row['role_template'] or 'viewer'
            overrides = json.loads(row['overrides']) if row['overrides'] else {}
        else:
            role = 'viewer'
            overrides = {}

        # Start with role template defaults
        template = ROLE_TEMPLATES.get(role, ROLE_TEMPLATES['viewer'])
        permissions = dict(template['permissions'])

        # Apply overrides
        for scope, level in overrides.items():
            if scope in SCOPES and level in LEVELS:
                permissions[scope] = level

        return permissions

    def get_user_role(self, user_id: str, org_id: str) -> str:
        """Get the role template for a user."""
        cur = self.conn.execute(
            "SELECT role_template FROM user_permissions WHERE user_id = ? AND org_id = ?",
            (user_id, org_id))
        row = cur.fetchone()
        return row['role_template'] if row else 'viewer'

    def get_user_overrides(self, user_id: str, org_id: str) -> Dict[str, str]:
        """Get just the per-user overrides (not the full resolved permissions)."""
        cur = self.conn.execute(
            "SELECT overrides FROM user_permissions WHERE user_id = ? AND org_id = ?",
            (user_id, org_id))
        row = cur.fetchone()
        if row and row['overrides']:
            return json.loads(row['overrides'])
        return {}

    def set_user_role(self, user_id: str, org_id: str, role: str):
        """Set a user's role template (clears overrides)."""
        if role not in ROLE_TEMPLATES:
            raise ValueError(f"Unknown role: {role}")

        self.conn.execute("""
            INSERT INTO user_permissions (user_id, org_id, role_template, overrides, updated_at)
            VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, org_id) DO UPDATE SET
                role_template = ?, overrides = NULL, updated_at = CURRENT_TIMESTAMP
        """, (user_id, org_id, role, role))
        self.conn.commit()

    def set_user_override(self, user_id: str, org_id: str,
                          scope: str, level: str):
        """Set a per-user permission override for a specific scope."""
        if scope not in SCOPES:
            raise ValueError(f"Unknown scope: {scope}")
        if level not in LEVELS:
            raise ValueError(f"Unknown level: {level}")

        overrides = self.get_user_overrides(user_id, org_id)
        overrides[scope] = level

        # If this matches the role template default, remove the override
        role = self.get_user_role(user_id, org_id)
        template = ROLE_TEMPLATES.get(role, ROLE_TEMPLATES['viewer'])
        if template['permissions'].get(scope) == level:
            overrides.pop(scope, None)

        self.conn.execute("""
            INSERT INTO user_permissions (user_id, org_id, role_template, overrides, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, org_id) DO UPDATE SET
                overrides = ?, updated_at = CURRENT_TIMESTAMP
        """, (user_id, org_id, role, json.dumps(overrides) if overrides else None,
              json.dumps(overrides) if overrides else None))
        self.conn.commit()

    def set_bulk_overrides(self, user_id: str, org_id: str,
                           overrides: Dict[str, str]):
        """Set multiple permission overrides at once."""
        role = self.get_user_role(user_id, org_id)
        template = ROLE_TEMPLATES.get(role, ROLE_TEMPLATES['viewer'])

        # Only store overrides that differ from template
        clean = {}
        for scope, level in overrides.items():
            if scope in SCOPES and level in LEVELS:
                if template['permissions'].get(scope) != level:
                    clean[scope] = level

        self.conn.execute("""
            INSERT INTO user_permissions (user_id, org_id, role_template, overrides, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, org_id) DO UPDATE SET
                overrides = ?, updated_at = CURRENT_TIMESTAMP
        """, (user_id, org_id, role,
              json.dumps(clean) if clean else None,
              json.dumps(clean) if clean else None))
        self.conn.commit()

    def init_user_permissions(self, user_id: str, org_id: str,
                              role: str = None):
        """
        Initialize permissions for a new user.

        Maps the legacy 'role' field to a permission role template:
        - 'admin' → admin template
        - 'member' → operator template
        - 'viewer' → viewer template
        """
        if role == 'admin':
            template = 'admin'
        elif role == 'member':
            template = 'operator'
        elif role == 'viewer':
            template = 'viewer'
        else:
            template = 'viewer'

        self.set_user_role(user_id, org_id, template)

    def list_org_permissions(self, org_id: str) -> List[Dict]:
        """Get permissions for all users in an org."""
        cur = self.conn.execute("""
            SELECT * FROM user_permissions WHERE org_id = ?
        """, (org_id,))
        results = []
        for row in cur.fetchall():
            role = row['role_template'] or 'viewer'
            overrides = json.loads(row['overrides']) if row['overrides'] else {}
            template = ROLE_TEMPLATES.get(role, ROLE_TEMPLATES['viewer'])
            permissions = dict(template['permissions'])
            permissions.update({k: v for k, v in overrides.items()
                                if k in SCOPES and v in LEVELS})
            results.append({
                'user_id': row['user_id'],
                'org_id': row['org_id'],
                'role_template': role,
                'overrides': overrides,
                'permissions': permissions,
            })
        return results


# ─── Permission Checking Helpers ─────────────────────────────────────

def check_permission(permissions: Dict[str, str],
                     scope: str, required_level: str = 'read') -> bool:
    """
    Check if a permissions dict grants access to a scope at the required level.

    Level hierarchy: none < read < edit
    """
    user_level = permissions.get(scope, 'none')
    level_rank = {'none': 0, 'read': 1, 'edit': 2}
    return level_rank.get(user_level, 0) >= level_rank.get(required_level, 1)


def can_read(permissions: Dict[str, str], scope: str) -> bool:
    """Check if user has at least read access to a scope."""
    return check_permission(permissions, scope, 'read')


def can_edit(permissions: Dict[str, str], scope: str) -> bool:
    """Check if user has edit access to a scope."""
    return check_permission(permissions, scope, 'edit')


def get_scope_categories() -> Dict[str, List[Dict]]:
    """Get scopes organized by category for UI display."""
    categories = {}
    for scope_key in SCOPE_ORDER:
        scope = SCOPES[scope_key]
        cat = scope['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({
            'key': scope_key,
            'label': scope['label'],
            'description': scope['description'],
        })
    return categories
