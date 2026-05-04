"""
Licensing Module for Capactive Document Extractor.

Handles:
- Organization and user key generation
- Key validation (local, no phone-home)
- Encrypted license files for on-premises deployment
- Feature entitlement checks
- Expiration and renewal management

For on-prem: keys validate against a locally stored encrypted license file.
For hosted: the same keys become authentication tokens validated server-side.
"""

import os
import json
import hmac
import hashlib
import secrets
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
from dataclasses import dataclass


# ─── Key Format ──────────────────────────────────────────────────────
#
# Org key:  CAP-{plan_code}-{org_hash}-{check}
#           e.g., CAP-STD-A7F3B2C1-9E4D
#
# User key: CU-{org_prefix}-{user_hash}-{check}
#           e.g., CU-A7F3-B8E2C1D4-5F6A
#
# ─────────────────────────────────────────────────────────────────────

PLAN_CODES = {
    "starter": "STR",
    "standard": "STD",
    "professional": "PRO",
    "enterprise": "ENT",
}

CODE_TO_PLAN = {v: k for k, v in PLAN_CODES.items()}

# This would be stored securely in production — env variable or HSM
_SIGNING_SECRET = os.environ.get(
    'CAPACTIVE_LICENSE_SECRET',
    'capactive-dev-signing-key-change-in-production'
)


@dataclass
class LicenseInfo:
    """Parsed and validated license information."""
    org_id: str
    org_name: str
    plan: str
    issued_at: str
    expires_at: str
    max_users: int
    max_documents_per_month: int
    features: Dict
    is_valid: bool = True
    is_expired: bool = False
    days_remaining: int = 0


# ─── Key Generation ──────────────────────────────────────────────────

def generate_org_key(org_id: str, plan: str = "standard") -> str:
    """
    Generate a license key for an organization.

    Format: CAP-{plan_code}-{8_hex}-{4_hex_check}
    """
    plan_code = PLAN_CODES.get(plan, "STD")

    # Generate org hash from ID + secret
    org_hash = _hmac_short(org_id, 8)

    # Generate check digits
    check_data = f"{plan_code}-{org_hash}"
    check = _hmac_short(check_data, 4)

    return f"CAP-{plan_code}-{org_hash}-{check}"


def generate_user_key(org_id: str, user_id: str) -> str:
    """
    Generate an access key for a user within an organization.

    Format: CU-{4_hex_org}-{8_hex_user}-{4_hex_check}
    """
    org_prefix = _hmac_short(org_id, 4)
    user_hash = _hmac_short(f"{org_id}:{user_id}", 8)
    check = _hmac_short(f"{org_prefix}-{user_hash}", 4)

    return f"CU-{org_prefix}-{user_hash}-{check}"


def generate_api_token(org_id: str, user_id: str) -> str:
    """
    Generate a bearer token for API access (hosted deployment).

    Returns a longer, URL-safe token suitable for HTTP headers.
    """
    payload = f"{org_id}:{user_id}:{datetime.now().isoformat()}"
    signature = hmac.new(
        _SIGNING_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    token_data = base64.urlsafe_b64encode(
        f"{payload}:{signature}".encode()
    ).decode()

    return f"cap_{token_data}"


# ─── Key Validation ──────────────────────────────────────────────────

def validate_org_key(key: str) -> Tuple[bool, Optional[str]]:
    """
    Validate an organization key format and checksum.

    Returns (is_valid, plan_name) tuple.
    """
    try:
        parts = key.split('-')
        if len(parts) != 4 or parts[0] != 'CAP':
            return False, None

        plan_code = parts[1]
        org_hash = parts[2]
        check = parts[3]

        # Verify plan code
        if plan_code not in CODE_TO_PLAN:
            return False, None

        # Verify check digits
        expected_check = _hmac_short(f"{plan_code}-{org_hash}", 4)
        if not hmac.compare_digest(check.upper(), expected_check.upper()):
            return False, None

        return True, CODE_TO_PLAN[plan_code]

    except Exception:
        return False, None


def validate_user_key(key: str) -> bool:
    """Validate a user key format and checksum."""
    try:
        parts = key.split('-')
        if len(parts) != 4 or parts[0] != 'CU':
            return False

        org_prefix = parts[1]
        user_hash = parts[2]
        check = parts[3]

        expected_check = _hmac_short(f"{org_prefix}-{user_hash}", 4)
        return hmac.compare_digest(check.upper(), expected_check.upper())

    except Exception:
        return False


def validate_api_token(token: str) -> Optional[Dict]:
    """
    Validate an API token and extract the payload.

    Returns dict with org_id and user_id, or None if invalid.
    """
    try:
        if not token.startswith('cap_'):
            return None

        token_data = base64.urlsafe_b64decode(token[4:]).decode()
        payload, signature = token_data.rsplit(':', 1)

        expected_sig = hmac.new(
            _SIGNING_SECRET.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return None

        parts = payload.split(':')
        return {
            'org_id': parts[0],
            'user_id': parts[1],
            'issued_at': parts[2] if len(parts) > 2 else None,
        }

    except Exception:
        return None


# ─── License File Management (On-Premises) ──────────────────────────

def create_license_file(org_id: str, org_name: str, plan: str,
                        valid_days: int = 365,
                        output_path: str = None) -> str:
    """
    Create an encrypted license file for on-premises deployment.

    The license file contains org info, plan, feature entitlements,
    and expiration date. It's signed so it can't be tampered with.

    Returns the path to the license file.
    """
    from .config import PLAN_FEATURES

    features = PLAN_FEATURES.get(plan, PLAN_FEATURES["standard"])
    now = datetime.now()

    license_data = {
        "org_id": org_id,
        "org_name": org_name,
        "plan": plan,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=valid_days)).isoformat(),
        "max_users": features.max_users,
        "max_documents_per_month": features.max_documents_per_month,
        "features": features.to_dict(),
        "version": "1.0",
    }

    # Serialize and sign
    payload = json.dumps(license_data, sort_keys=True)
    signature = hmac.new(
        _SIGNING_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    # Combine payload + signature
    license_content = base64.b64encode(
        json.dumps({
            "data": license_data,
            "signature": signature,
        }).encode()
    ).decode()

    # Write to file
    if not output_path:
        output_path = f"capactive_{org_id}.license"

    with open(output_path, 'w') as f:
        f.write(f"-----BEGIN CAPACTIVE LICENSE-----\n")
        # Write in 64-char lines
        for i in range(0, len(license_content), 64):
            f.write(license_content[i:i+64] + "\n")
        f.write(f"-----END CAPACTIVE LICENSE-----\n")

    return output_path


def read_license_file(filepath: str) -> Optional[LicenseInfo]:
    """
    Read and validate a license file.

    Returns LicenseInfo if valid, None if tampered or corrupt.
    """
    try:
        with open(filepath, 'r') as f:
            content = f.read()

        # Extract base64 content between markers
        lines = content.strip().split('\n')
        b64_lines = [l for l in lines
                     if not l.startswith('-----')]
        b64_content = ''.join(b64_lines)

        # Decode
        decoded = json.loads(base64.b64decode(b64_content))
        license_data = decoded['data']
        stored_signature = decoded['signature']

        # Verify signature
        payload = json.dumps(license_data, sort_keys=True)
        expected_sig = hmac.new(
            _SIGNING_SECRET.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(stored_signature, expected_sig):
            return None  # Tampered

        # Check expiration
        expires_at = datetime.fromisoformat(license_data['expires_at'])
        now = datetime.now()
        is_expired = now > expires_at
        days_remaining = max(0, (expires_at - now).days)

        return LicenseInfo(
            org_id=license_data['org_id'],
            org_name=license_data['org_name'],
            plan=license_data['plan'],
            issued_at=license_data['issued_at'],
            expires_at=license_data['expires_at'],
            max_users=license_data['max_users'],
            max_documents_per_month=license_data['max_documents_per_month'],
            features=license_data['features'],
            is_valid=not is_expired,
            is_expired=is_expired,
            days_remaining=days_remaining,
        )

    except Exception:
        return None


# ─── Feature Entitlement Checks ─────────────────────────────────────

class EntitlementChecker:
    """Check whether an org/user is entitled to use a feature."""

    def __init__(self, config_store=None, license_path: str = None):
        """
        Initialize with either a config store (hosted) or license file (on-prem).
        """
        self.config_store = config_store
        self.license_info = None

        if license_path and os.path.exists(license_path):
            self.license_info = read_license_file(license_path)

    def check_feature(self, org_id: str, feature: str) -> Tuple[bool, str]:
        """
        Check if a feature is available for an organization.

        Returns (allowed, reason) tuple.
        """
        features = self._get_features(org_id)
        if not features:
            return False, "Organization not found or license invalid"

        feature_checks = {
            'ocr': ('ocr_enabled', 'OCR is not available on your plan'),
            'llm_extraction': ('llm_extraction_enabled', 'LLM extraction is not available on your plan'),
            'watch_mode': ('watch_mode_enabled', 'Watch mode is not available on your plan'),
            'batch_processing': ('batch_processing_enabled', 'Batch processing is not available on your plan'),
            'csv_export': ('csv_export_enabled', 'CSV export is not available on your plan'),
            'api_access': ('api_access_enabled', 'API access is not available on your plan'),
            'custom_templates': ('custom_templates_enabled', 'Custom templates are not available on your plan'),
        }

        if feature in feature_checks:
            attr, msg = feature_checks[feature]
            if isinstance(features, dict):
                enabled = features.get(attr, False)
            else:
                enabled = getattr(features, attr, False)
            return (True, "OK") if enabled else (False, msg)

        return True, "OK"  # Unknown features are allowed by default

    def check_document_type(self, org_id: str, doc_type: str) -> Tuple[bool, str]:
        """Check if a document type is enabled for an org."""
        features = self._get_features(org_id)
        if not features:
            return False, "Organization not found"

        if isinstance(features, dict):
            enabled_types = features.get('document_types_enabled', [])
        else:
            enabled_types = features.document_types_enabled

        if doc_type in enabled_types:
            return True, "OK"
        return False, f"Document type '{doc_type}' is not available on your plan"

    def check_page_limit(self, org_id: str, page_count: int) -> Tuple[bool, str]:
        """Check if a document's page count is within limits."""
        features = self._get_features(org_id)
        if not features:
            return False, "Organization not found"

        if isinstance(features, dict):
            max_pages = features.get('max_pages_per_document', 500)
        else:
            max_pages = features.max_pages_per_document

        if page_count <= max_pages:
            return True, "OK"
        return False, f"Document exceeds page limit ({page_count} > {max_pages})"

    def _get_features(self, org_id: str):
        """Get features from either config store or license file."""
        if self.license_info and self.license_info.org_id == org_id:
            if self.license_info.is_expired:
                return None
            return self.license_info.features

        if self.config_store:
            return self.config_store.get_org_features(org_id)

        return None


# ─── Helpers ─────────────────────────────────────────────────────────

def _hmac_short(data: str, length: int) -> str:
    """Generate a short HMAC-based hash string (hex, uppercase)."""
    h = hmac.new(
        _SIGNING_SECRET.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()
    return h[:length].upper()
