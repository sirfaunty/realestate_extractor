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
    # Legacy total seat count — kept for stored-feature back-compat and as
    # a display total. New code uses the per-class limits below
    # (docs/PACKAGING_DESIGN.md §5).
    max_users: int = 7
    max_extraction_seats: int = 2   # local-software seats (device-bound, Phase 2)
    max_access_seats: int = 5       # web-only report/dashboard users
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
    # Platform modules enabled for this org. "*" = all modules (default keeps
    # existing orgs unrestricted). Otherwise a list of module names from
    # modules/__init__.py INSTALLED_MODULES (e.g. "scorecard", "residential").
    modules_enabled: List[str] = field(default_factory=lambda: ["*"])

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'FeatureFlags':
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        flags = cls(**filtered)
        # Back-compat: stored features written before the seat-class split
        # have max_users but no per-class limits. Derive a conservative
        # split (~1/3 extraction) so existing orgs keep working; admins can
        # re-apply the plan to adopt the canonical split.
        if 'max_extraction_seats' not in data and 'max_users' in data:
            total = max(int(data['max_users']), 1)
            flags.max_extraction_seats = max(total // 3, 1)
            flags.max_access_seats = max(total - flags.max_extraction_seats, 0)
        return flags


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
        max_users=3,
        max_extraction_seats=1,
        max_access_seats=2,
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
        modules_enabled=[],  # extractor core only
    ),
    "standard": FeatureFlags(
        max_users=7,
        max_extraction_seats=2,
        max_access_seats=5,
        max_documents_per_month=500,
        ocr_enabled=True,
        llm_extraction_enabled=True,
        watch_mode_enabled=True,
        batch_processing_enabled=True,
        csv_export_enabled=True,
        api_access_enabled=False,
        custom_templates_enabled=False,
        max_pages_per_document=500,
        modules_enabled=[  # deal-document modules
            "closing_books", "tif_analysis", "distribution", "debt_analysis",
            "partnership_dashboard", "barrington", "southtown", "midway",
        ],
    ),
    "professional": FeatureFlags(
        max_users=23,
        max_extraction_seats=3,
        max_access_seats=20,
        max_documents_per_month=2000,
        ocr_enabled=True,
        llm_extraction_enabled=True,
        watch_mode_enabled=True,
        batch_processing_enabled=True,
        csv_export_enabled=True,
        api_access_enabled=True,
        custom_templates_enabled=True,
        max_pages_per_document=1000,
        modules_enabled=[  # deal modules + market analytics + deliverables
            "closing_books", "tif_analysis", "distribution", "debt_analysis",
            "partnership_dashboard", "barrington", "southtown", "midway",
            "inventory", "sales_comps", "scorecard", "lease_analysis",
            "market_intel", "office", "deliverables",
        ],
    ),
    "enterprise": FeatureFlags(
        max_users=999,
        max_extraction_seats=999,
        max_access_seats=999,
        max_documents_per_month=99999,
        ocr_enabled=True,
        llm_extraction_enabled=True,
        watch_mode_enabled=True,
        batch_processing_enabled=True,
        csv_export_enabled=True,
        api_access_enabled=True,
        custom_templates_enabled=True,
        max_pages_per_document=9999,
        modules_enabled=["*"],  # everything, incl. portfolio_ownership + residential
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

-- Extraction devices (docs/PACKAGING_DESIGN.md §5): each row is a
-- registered extraction client. The token is stored hashed; the plaintext
-- is shown exactly once at registration. fingerprint pins on first
-- authenticated contact (trust-on-first-use) and is enforced afterwards.
CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(org_id),
    device_name     TEXT NOT NULL,
    token_hash      TEXT NOT NULL UNIQUE,
    fingerprint     TEXT,
    is_active       BOOLEAN DEFAULT 1,
    created_at      TEXT,
    created_by      TEXT,
    last_seen       TEXT
);

CREATE INDEX IF NOT EXISTS idx_devices_org ON devices(org_id);
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

    def set_org_features(self, org_id: str, features) -> None:
        """Persist an org's feature flags directly (module activation etc.).

        `features` may be a FeatureFlags or a plain dict. Unlike update_org,
        this does NOT change the plan — it records a per-org override.
        """
        data = features.to_dict() if hasattr(features, 'to_dict') else dict(features)
        self.conn.execute(
            "UPDATE organizations SET features = ? WHERE org_id = ?",
            (json.dumps(data), org_id))
        self.conn.commit()

    def deactivate_org(self, org_id: str):
        """Deactivate an organization (soft delete)."""
        self.update_org(org_id, is_active=False)

    # ─── User Management ─────────────────────────────────────────────

    def create_user(self, org_id: str, user_id: str, email: str,
                    display_name: str, role: str = "member",
                    password_hash: str = None) -> UserProfile:
        """Create a user within an organization."""
        # Check total user limit (extraction + access seats + 1 free admin).
        # Per-class enforcement happens at the admin routes, where the
        # role template is known (permissions.count_seats).
        org = self.get_org(org_id)
        if org:
            total_seats = (org.features.max_extraction_seats
                           + org.features.max_access_seats + 1)
            current_users = len(self.list_users(org_id))
            if current_users >= total_seats:
                raise ValueError(
                    f"User limit reached ({total_seats} incl. admin) for "
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

    # ─── Extraction Devices ──────────────────────────────────────────

    def register_device(self, org_id: str, device_name: str,
                        created_by: str = None) -> Dict:
        """Register an extraction device. Returns {'device_id', 'token'} —
        the ONLY time the plaintext token is available. Enforces the org's
        extraction-seat limit against active devices."""
        import secrets
        import hashlib
        org = self.get_org(org_id)
        if org:
            active = [d for d in self.list_devices(org_id) if d['is_active']]
            if len(active) >= org.features.max_extraction_seats:
                raise ValueError(
                    f"Extraction seat limit reached "
                    f"({org.features.max_extraction_seats}) for plan "
                    f"'{org.plan}'. Revoke a device or upgrade.")
        device_id = f"dev-{secrets.token_hex(6)}"
        token = f"cap_{secrets.token_urlsafe(32)}"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.conn.execute("""
            INSERT INTO devices (device_id, org_id, device_name, token_hash,
                                 is_active, created_at, created_by)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        """, (device_id, org_id, device_name, token_hash,
              datetime.now().isoformat(), created_by))
        self.conn.commit()
        return {'device_id': device_id, 'token': token}

    def list_devices(self, org_id: str) -> List[Dict]:
        cur = self.conn.execute(
            "SELECT device_id, org_id, device_name, fingerprint, is_active, "
            "created_at, created_by, last_seen FROM devices WHERE org_id = ?",
            (org_id,))
        return [dict(row) for row in cur.fetchall()]

    def revoke_device(self, device_id: str):
        self.conn.execute(
            "UPDATE devices SET is_active = 0 WHERE device_id = ?",
            (device_id,))
        self.conn.commit()

    def authenticate_device(self, token: str,
                            fingerprint: str = None) -> Optional[Dict]:
        """Resolve a plaintext token to an active device, enforcing
        fingerprint pinning (TOFU: first contact pins, later contacts must
        match). Returns the device row or None. Updates last_seen."""
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        cur = self.conn.execute(
            "SELECT * FROM devices WHERE token_hash = ? AND is_active = 1",
            (token_hash,))
        row = cur.fetchone()
        if not row:
            return None
        device = dict(row)
        if fingerprint:
            if device['fingerprint'] is None:
                self.conn.execute(
                    "UPDATE devices SET fingerprint = ? WHERE device_id = ?",
                    (fingerprint, device['device_id']))
                device['fingerprint'] = fingerprint
            elif device['fingerprint'] != fingerprint:
                return None   # token replayed from a different machine
        self.conn.execute(
            "UPDATE devices SET last_seen = ? WHERE device_id = ?",
            (datetime.now().isoformat(), device['device_id']))
        self.conn.commit()
        return device

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
