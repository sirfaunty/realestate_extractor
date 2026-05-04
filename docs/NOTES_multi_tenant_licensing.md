# Multi-Tenant & Licensing — Architecture Notes

## Context
The tool will be licensed to multiple organizations. Each customer gets a separate database. Need org-level and user-level access keys, with support for both on-premises and hosted deployment models.

## Key Decisions

### Data Isolation
- **SQLite-per-customer model** — each org gets its own database file
- Physical separation eliminates cross-tenant data leakage risk
- Config layer maps org key → database path, settings, feature entitlements

### Licensing & Access Control
- **Org key**: unlocks the system for an organization
  - Defines enabled document types
  - Sets user seat limits
  - Controls processing volume limits (for tiered pricing)
  - Gates features (watch mode, LLM extraction, etc.)
- **User keys**: sit under the org key
  - Track who processed what
  - Enable audit trails (important for enterprise RE customers)

### Deployment Models
- **On-premises**: org key validates locally against an encrypted license file — no phone-home
- **Hosted**: same codebase with an API layer on top; org/user keys become auth tokens
- Same extraction engine underneath either way

## Modules to Build

1. **`config.py`** — Org-level settings and database routing
   - Org profile (name, key, DB path, feature flags)
   - User management within org
   - Database path resolution

2. **`licensing.py`** — Key validation and feature gating
   - Org key generation and validation
   - User key generation and validation
   - Encrypted local license file for on-prem
   - Feature entitlement checks
   - Expiration handling

3. **`usage.py`** — Processing volume tracking
   - Per-org document counts and processing volume
   - Per-user activity log
   - Audit trail (who processed what, when)
   - Volume limit enforcement for tiered pricing

## Integration Points
- Hook into `batch_processor.py` at the processing entry point
- Validate keys before any extraction runs
- Log usage after each successful extraction
- Core extraction logic (`extraction_engine.py`) stays clean and untouched

## Pricing Considerations to Discuss
- Per-document vs. per-seat vs. tiered volume pricing
- Which features are base vs. premium (e.g., watch mode, LLM extraction, OCR)
- On-prem vs. hosted pricing differential
