"""
Organization and User Configuration for Capactive Document Extractor.

Manages multi-tenant configuration:
- Organization profiles with database routing
- Feature flags and entitlements per org
- User management within organizations
- Settings persistence

Each organization gets its own SQLite database file,
ensuring complete data isolation between tenants.
"""

import os
import json
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ─── Data Models ─────────────────────────────────────────────────────

@dataclass
class FeatureFlags:
    """Controls which features are available to an organization."""
    max_users: int = 5
    max_documents_per_month: int = 500
    document_types_enabled: List[str] = field(default_factory=lambda: [
        "lease", "loan", "closing", "guarantee",
        "rent_roll", "operating_statement", "general_ledger"
    ])
    ocr_enabled: bool = True
    llm_extraction_enabled: bool = True
    watch_mode_enabled: bool = True
    batch_processing_enabled: bool = True
    csv_export_enabled: bool = True
    api_access_enabled: bool = False
    custom_templates_enabled: bool = False
    max_pages_per_document: int = 500

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'FeatureFlags':
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class UserProfile:
    """A user within an organization."""
    user_id: str
    email: str
    display_name: str
    role: str = "member"        # admin, member, viewer
    is_active: bool = True
    created_at: str = ""
    last_login: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class OrgProfile:
    """An organization (tenant) profile."""
    org_id: str
    org_name: str
    org_key: str                 # license key
    db_path: str                 # path to this org's SQLite database
    plan: str = "standard"       # starter, standard, professional, enterprise
    is_active: bool = True
    created_at: str = ""
    features: FeatureFlags = field(default_factory=FeatureFlags)
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


# ─── Plan Definitions ────────────────────────────────────────────────

PLAN_FEATURES = {
    "starter": FeatureFlags(
        max_users=2,
        max_documents_per_month=100,
        document_types_enabled=["lease", "rent_roll", "operating_statement"],
        ocr_enabled=True,
        llm_extraction_enabled=True,
        watch_mode_enabled=False,
        batch_processing_enabled=True,
        csv_export_enabled=True,
        api_access_enabled=False,
        custom_templates_enabled=False,
        max_pages_per_document=200,
    ),
    "standard": FeatureFlags(
        max_users=5,
        max_documents_per_month=500,
        ocr_enabled=True,
        llm_extraction_enabled=True,
        watch_mode_enabled=True,
        batch_processing_enabled=True,
        csv_export_enabled=True,
        api_access_enabled=False,
        custom_templates_enabled=False,
        max_pages_per_document=500,
    ),
    "professional": FeatureFlags(
        max_users=20,
        max_documents_per_month=2000,
        ocr_enabled=True,
        llm_extraction_enabled=True,
        watch_mode_enabled=True,
        batch_processing_enabled=True,
        csv_export_enabled=True,
        api_access_enabled=True,
        custom_templates_enabled=True,
        max_pages_per_document=1000,
    ),
    "enterprise": FeatureFlags(
        max_users=999,
        max_documents_per_month=99999,
        ocr_enabled=True,
        llm_extraction_enabled=True,
        watch_mode_enabled=True,
        batch_processing_enabled=True,
        csv_export_enabled=True,
        api_access_enabled=True,
        custom_templates_enabled=True,
        max_pages_per_document=9999,
    ),
}


# ─── Config Store ────────────────────────────────────────────────────

CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    org_id          TEXT PRIMARY KEY,
    org_name        TEXT NOT NULL,
    org_key         TEXT NOT NULL UNIQUE,
    db_path         TEXT NOT NULL,
    plan            TEXT DEFAULT 'standard',
    is_active       BOOLEAN DEFAULT 1,
    created_at      TEXT,
    features        TEXT,
    metadata        TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(org_id),
    email           TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    password_hash   TEXT,
    role            TEXT DEFAULT 'member',
    is_active       BOOLEAN DEFAULT 1,
    created_at      TEXT,
    last_login      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_org ON users(email, org_id);
CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);
"""


class ConfigStore:
    """
    Central configuration store for multi-tenant management.

    This is a separate SQLite database from the per-org extraction databases.
    It stores org profiles, user accounts, and licensing information.
    """

    def __init__(self, config_path: str = "capactive_config.db",
                 data_dir: str = "data"):
        self.config_path = config_path
        self.data_dir = data_dir
        self.conn = None

    def connect(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.conn = sqlite3.connect(self.config_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(CONFIG_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add columns for schema evolution on existing databases."""
        cols = [row[1] for row in self.conn.execute("PRAGMA table_info(users)")]
        if 'password_hash' not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")

    def close(self):
        if self.conn:
            self.conn.close()

    # ─── Organization Management ─────────────────────────────────────

    def create_org(self, org_id: str, org_name: str, org_key: str,
                   plan: str = "standard", metadata: Dict = None) -> OrgProfile:
        """Create a new organization with its own database."""
        # Generate database path
        db_path = os.path.join(self.data_dir, f"org_{org_id}.db")

        features = PLAN_FEATURES.get(plan, PLAN_FEATURES["standard"])

        org = OrgProfile(
            org_id=org_id,
            org_name=org_name,
            org_key=org_key,
            db_path=db_path,
            plan=plan,
            features=features,
            metadata=metadata or {},
        )

        self.conn.execute("""
            INSERT INTO organizations (org_id, org_name, org_key, db_path, plan,
                                       is_active, created_at, features, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (org.org_id, org.org_name, org.org_key, org.db_path, org.plan,
              org.is_active, org.created_at,
              json.dumps(features.to_dict()),
              json.dumps(org.metadata)))
        self.conn.commit()

        return org

    def get_org(self, org_id: str) -> Optional[OrgProfile]:
        """Get organization by ID."""
        cur = self.conn.execute(
            "SELECT * FROM organizations WHERE org_id = ?", (org_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_org(row)

    def get_org_by_key(self, org_key: str) -> Optional[OrgProfile]:
        """Get organization by license key."""
        cur = self.conn.execute(
            "SELECT * FROM organizations WHERE org_key = ?", (org_key,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_org(row)

    def list_orgs(self, active_only: bool = True) -> List[OrgProfile]:
        """List all organizations."""
        query = "SELECT * FROM organizations"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at DESC"
        cur = self.conn.execute(query)
        return [self._row_to_org(row) for row in cur.fetchall()]

    def update_org(self, org_id: str, **kwargs):
        """Update organization fields."""
        allowed = ['org_name', 'plan', 'is_active', 'metadata']
        updates = []
        values = []
        for key, value in kwargs.items():
            if key in allowed:
                if key == 'metadata':
                    value = json.dumps(value)
                updates.append(f"{key} = ?")
                values.append(value)

        if 'plan' in kwargs:
            # Update features based on new plan
            features = PLAN_FEATURES.get(kwargs['plan'], PLAN_FEATURES["standard"])
            updates.append("features = ?")
            values.append(json.dumps(features.to_dict()))

        if updates:
            values.append(org_id)
            self.conn.execute(
                f"UPDATE organizations SET {', '.join(updates)} WHERE org_id = ?",
                values)
            self.conn.commit()

    def deactivate_org(self, org_id: str):
        """Deactivate an organization (soft delete)."""
        self.update_org(org_id, is_active=False)

    # ─── User Management ─────────────────────────────────────────────

    def create_user(self, org_id: str, user_id: str, email: str,
                    display_name: str, role: str = "member",
                    password_hash: str = None) -> UserProfile:
        """Create a user within an organization."""
        # Check user limit
        org = self.get_org(org_id)
        if org:
            current_users = len(self.list_users(org_id))
            if current_users >= org.features.max_users:
                raise ValueError(
                    f"User limit reached ({org.features.max_users}) for "
                    f"plan '{org.plan}'. Upgrade to add more users."
                )

        user = UserProfile(
            user_id=user_id,
            email=email,
            display_name=display_name,
            role=role,
        )

        self.conn.execute("""
            INSERT INTO users (user_id, org_id, email, display_name, password_hash,
                               role, is_active, created_at, last_login)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user.user_id, org_id, user.email, user.display_name,
              password_hash, user.role, user.is_active,
              user.created_at, user.last_login))
        self.conn.commit()

        return user

    def get_user(self, user_id: str) -> Optional[Dict]:
        """Get user by ID, including org info."""
        cur = self.conn.execute("""
            SELECT u.*, o.org_name, o.plan
            FROM users u JOIN organizations o ON u.org_id = o.org_id
            WHERE u.user_id = ?
        """, (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_users(self, org_id: str, active_only: bool = True) -> List[Dict]:
        """List users in an organization."""
        query = "SELECT * FROM users WHERE org_id = ?"
        params = [org_id]
        if active_only:
            query += " AND is_active = 1"
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Look up a user by email address (across all orgs)."""
        cur = self.conn.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1", (email,))
        row = cur.fetchone()
        return dict(row) if row else None

    def update_user_password(self, user_id: str, password_hash: str):
        """Update a user's password hash."""
        self.conn.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (password_hash, user_id))
        self.conn.commit()

    def update_user_login(self, user_id: str):
        """Record user login timestamp."""
        self.conn.execute(
            "UPDATE users SET last_login = ? WHERE user_id = ?",
            (datetime.now().isoformat(), user_id))
        self.conn.commit()

    def deactivate_user(self, user_id: str):
        """Deactivate a user (soft delete)."""
        self.conn.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    # ─── Database Routing ────────────────────────────────────────────

    def get_org_db_path(self, org_id: str) -> Optional[str]:
        """Get the database path for an organization."""
        org = self.get_org(org_id)
        return org.db_path if org else None

    def get_org_features(self, org_id: str) -> Optional[FeatureFlags]:
        """Get feature flags for an organization."""
        org = self.get_org(org_id)
        return org.features if org else None

    # ─── Helpers ─────────────────────────────────────────────────────

    def _row_to_org(self, row) -> OrgProfile:
        features_data = json.loads(row['features']) if row['features'] else {}
        metadata = json.loads(row['metadata']) if row['metadata'] else {}
        return OrgProfile(
            org_id=row['org_id'],
            org_name=row['org_name'],
            org_key=row['org_key'],
            db_path=row['db_path'],
            plan=row['plan'],
            is_active=bool(row['is_active']),
            created_at=row['created_at'],
            features=FeatureFlags.from_dict(features_data),
            metadata=metadata,
        )
