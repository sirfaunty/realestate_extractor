# Capactive Platform Admin — Layer 3 Specification

## What This Document Is

This is a build specification for the Capactive internal admin panel (Layer 3). This application is a **separate codebase** from the Document Extractor (Layers 1 + 2). It is the tool the Capactive team uses to manage all customers, subscriptions, licensing, and deployment infrastructure. No customer ever sees this app.

Feed this document into a task to produce a working prototype.

---

## Context: The Three-Layer Architecture

### Layer 1 — Extractor Engine (Built, Separate Repo)
Local Flask app installed on customer machines. Ingests PDFs, runs OCR + local LLM extraction (Ollama llama3.1:8b), stores structured data in SQLite. Runs entirely on-device. No internet required.

### Layer 2 — Property Intelligence Dashboard (Built, Same Repo as Layer 1)
Customer-facing product that sits on top of extracted data. Organizes extracted document data around properties with three data buckets: Operations (tenants, rent, expenses), Debt (loans, guarantees, covenants), and Valuation/NOI (asset performance). Includes RBAC permissions, document review queue, asset hierarchy (Portfolio > Property > Building > Unit), and multi-tenant org/user management.

### Layer 3 — Platform Admin (THIS SPEC)
Capactive's internal operations tool. Manages all customers across all deployment modes. Handles licensing, billing, deployment configuration, and usage monitoring.

**Key principle:** Extraction always happens locally on the customer's machine. Raw documents never touch a cloud service. The data that syncs (in modes 2 and 3) is structured output only — no different from CRM data.

---

## Three Deployment Modes

Layer 3 must manage customers across three deployment configurations. The deployment mode is set per-org and affects how data flows.

### Mode 1 — Fully Local ("Self-Hosted")
- Everything runs on customer's machine or local server
- No data leaves their network
- Multi-user via LAN/VPN, not per-person instances
- License key validates locally against encrypted license file
- Capactive has no visibility into usage unless customer opts in
- **Layer 3's role:** Generate and deliver license keys. Track renewal dates. No live telemetry.

### Mode 2 — Customer-Hosted Database
- Extractor runs locally on customer machine
- Structured data syncs to a database the customer controls (their AWS, Azure, etc.)
- Dashboard can be local or hosted, pointing at their DB
- License key validates against customer's deployment
- **Layer 3's role:** License management + deployment config. Customer provides their DB connection details. Capactive provides software and support.

### Mode 3 — Capactive-Hosted
- Extractor runs locally on customer machine
- Structured data syncs to Capactive-managed database
- Dashboard hosted by Capactive (subdomain per customer, e.g. acme.capactive.com)
- **Layer 3's role:** Full management — provisioning, hosting, monitoring, billing. This is where recurring SaaS revenue lives.

---

## Existing Data Model (Layer 1 + 2)

Layer 3 needs to understand and interface with the existing customer-side data model. Here is what already exists in the extractor codebase.

### Plans and Feature Flags

The extractor already enforces feature limits based on a plan tier. Layer 3 is the source of truth for which plan a customer is on.

```
Plans: starter, standard, professional, enterprise

Feature flags per plan:
- max_users (2 / 5 / 20 / unlimited)
- max_documents_per_month (100 / 500 / 2000 / unlimited)
- document_types_enabled (subset or all 7 types)
- ocr_enabled (always true)
- llm_extraction_enabled (always true)
- watch_mode_enabled (false on starter)
- batch_processing_enabled (true on all)
- csv_export_enabled (true on all)
- api_access_enabled (professional+ only)
- custom_templates_enabled (professional+ only)
- max_pages_per_document (200 / 500 / 1000 / unlimited)
```

### License Key Format

The extractor validates license keys using HMAC-SHA256. Layer 3 must generate keys in this exact format:

```
Key format: CAP-{plan}-{org_id}-{expiry_YYYYMMDD}-{checksum}
Example:    CAP-professional-ORG001-20260101-a3f8b2c1

Checksum: first 8 chars of HMAC-SHA256(payload, secret_key)
Payload:  "{plan}:{org_id}:{expiry}"
Secret:   Shared between Layer 3 generator and Layer 1 validator
```

### Organization and User Records

The extractor stores org/user data in a central SQLite config database (`capactive_config.db`). Each org also gets its own separate SQLite database for document/property data.

```
Organization fields:
- org_id (UUID)
- org_name
- org_key (human-readable login key, e.g. "ACME-2024")
- db_path (path to org's SQLite database)
- plan (starter/standard/professional/enterprise)
- is_active (boolean)
- features (JSON blob of feature flags)
- metadata (JSON blob for extensibility)

User fields:
- user_id (UUID)
- org_id (FK)
- email
- display_name
- role (admin/member/viewer — maps to permission templates)
- user_key (login key)
- is_active (boolean)

Permission system:
- 10 permission scopes across 3 categories (Property Data, Extraction, Admin)
- 3 access levels: none, read, edit
- 4 role templates: admin, operator, analyst, viewer
- Per-user overrides stored as JSON
```

---

## Layer 3 Feature Requirements

### 1. Customer Management

The core CRUD for managing Capactive's customer base.

**Org listing page:**
- Searchable, filterable table of all organizations
- Columns: org name, plan, deployment mode, status (active/suspended/churned), MRR, user count, document count (if available), created date, last activity
- Filters: by plan, by deployment mode, by status
- Sort by any column
- Quick actions: view details, suspend, upgrade/downgrade

**Org detail page:**
- Full org profile with edit capability
- Deployment mode selector (local / customer-hosted / capactive-hosted)
- Plan selector with feature flag preview
- License key display and regeneration
- User list for this org (pulled from their config DB in modes 2/3, or manual tracking in mode 1)
- Usage stats (if available)
- Activity timeline (plan changes, license regenerations, support notes)
- Billing history (linked from Stripe)
- Notes field for internal team comments

**Org creation flow:**
1. Enter org name, contact info
2. Select plan
3. Select deployment mode
4. Auto-generate org_id, org_key
5. Generate license key
6. Provide onboarding package (license key + download link + setup instructions)

### 2. License Management

**License generation:**
- Generate valid license keys that the extractor will accept
- Must use the same HMAC secret as the extractor's validator
- Set expiry dates (typically 1 year from activation, renewable)
- Support for trial licenses (30/60/90 day, limited plan)

**License dashboard:**
- Upcoming expirations (30/60/90 day warnings)
- Expired licenses needing renewal
- Trial conversions pipeline
- License history per org (all keys ever generated, with dates and status)

**License lifecycle:**
- Active → Expiring Soon (automated email trigger at 30 days)
- Active → Expired (grace period TBD, then features degrade)
- Active → Revoked (manual action, immediate)
- Trial → Converted (upgrade to paid plan)
- Trial → Expired (follow up or close)

### 3. Billing and Subscriptions (Stripe Integration)

**Stripe integration requirements:**
- Create Stripe customers mapped to Capactive orgs
- Manage subscriptions (create, upgrade, downgrade, cancel)
- Handle payment methods
- Process invoices
- Handle failed payments and dunning

**Pricing model (to be confirmed, but build for this structure):**
- Monthly or annual billing per plan
- Per-org pricing (not per-user, though user limits exist per plan)
- Possible add-ons: additional users, additional document volume, priority support
- Trial period: 14 or 30 days, no credit card required initially

**Billing dashboard:**
- MRR / ARR overview
- Revenue by plan tier
- Churn rate and trends
- Upcoming renewals
- Failed payments requiring attention
- Revenue per deployment mode breakdown

### 4. Deployment Management

**For Mode 1 (Fully Local) customers:**
- Track software version installed
- License key delivery
- Renewal tracking
- No live telemetry (customer may opt in to anonymous usage reporting)

**For Mode 2 (Customer-Hosted DB) customers:**
- Track customer's DB connection details (encrypted storage)
- Monitor sync health (if heartbeat endpoint exists)
- Software version tracking
- License key delivery

**For Mode 3 (Capactive-Hosted) customers:**
- Provision customer subdomain (e.g., acme.capactive.com)
- Provision hosted database
- Manage hosted dashboard instance
- Monitor system health, storage usage, query performance
- Backup management
- Scale resources as needed

**Deployment dashboard:**
- Customer count by deployment mode
- Mode 3 infrastructure health
- Storage and resource usage across hosted customers
- Version adoption (what % of customers are on latest version)

### 5. Usage Analytics

**Data sources vary by deployment mode:**
- Mode 1: Opt-in anonymous reporting only (if built), otherwise manual
- Mode 2: Limited telemetry if sync layer reports back
- Mode 3: Full visibility since Capactive hosts the database

**Metrics to track (when available):**
- Documents processed per org per month
- Document types processed (distribution)
- Pages processed
- Active users per org
- Properties created
- Extraction success/failure rates
- Feature adoption (which features are actually used)

**Analytics dashboard:**
- Aggregate stats across all customers
- Per-customer usage drilldown
- Usage trends over time
- Plan utilization (are customers hitting their limits?)
- Customers approaching plan limits (upsell opportunities)

### 6. Internal Team Management

**Team access for Capactive employees:**
- Super admin: full access to everything
- Sales: can view customers, create trials, manage billing
- Support: can view customers, view usage, add notes, regenerate license keys
- Engineering: can view deployments, monitor infrastructure, manage Mode 3 instances

**Audit trail:**
- Log all admin actions (who did what, when)
- License key generations
- Plan changes
- Customer status changes
- Billing actions

---

## Tech Stack

### Recommended

- **Framework:** Flask (consistency with Layers 1 + 2, team familiarity) or FastAPI (if API-first is preferred)
- **Database:** PostgreSQL (production-grade, needed for multi-user admin team, Stripe webhook handling, analytics aggregation)
- **ORM:** SQLAlchemy (works with both Flask and FastAPI)
- **Auth:** Flask-Login with password hashing (bcrypt), or OAuth if integrating with Google Workspace
- **Payments:** Stripe Python SDK (`stripe` package)
- **Task queue:** Celery + Redis (for async jobs: email sending, usage aggregation, Mode 3 provisioning)
- **Email:** SendGrid or AWS SES (license delivery, expiration warnings, billing notices)
- **Hosting:** Standard cloud deployment (Render, Railway, AWS, etc.)

### Branding

Capactive brand identity carries over from Layers 1 + 2:

- **Colors:** Navy ramp (#0A1628 darkest → #6BB3E8 lightest), signal green #2ECC71, white backgrounds
- **Font:** Inter (sans-serif)
- **Logo:** Ascending bar icon (3 bars, left to right, increasing height) — SVG available in Layer 1+2 codebase at `web/templates/base.html`
- **Product name in sidebar:** "capactive" wordmark + "Platform Admin" descriptor
- **UI pattern:** Left sidebar navigation, card-based content areas, navy header accents, subtle borders

### Database Schema (Starting Point)

```sql
-- Core tables
CREATE TABLE organizations (
    id              SERIAL PRIMARY KEY,
    org_id          UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    org_name        TEXT NOT NULL,
    org_key         TEXT UNIQUE,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    plan            TEXT NOT NULL DEFAULT 'starter',
    deployment_mode TEXT NOT NULL DEFAULT 'local',  -- local, customer_hosted, capactive_hosted
    status          TEXT NOT NULL DEFAULT 'trial',   -- trial, active, suspended, churned
    stripe_customer_id  TEXT,
    license_key     TEXT,
    license_expires DATE,
    hosted_subdomain    TEXT,  -- for mode 3
    hosted_db_config    JSONB, -- for mode 3
    customer_db_config  JSONB, -- for mode 2 (encrypted)
    software_version    TEXT,
    notes           TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE org_contacts (
    id          SERIAL PRIMARY KEY,
    org_id      UUID REFERENCES organizations(org_id),
    name        TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    role        TEXT,  -- primary, billing, technical
    is_primary  BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE license_history (
    id          SERIAL PRIMARY KEY,
    org_id      UUID REFERENCES organizations(org_id),
    license_key TEXT NOT NULL,
    plan        TEXT NOT NULL,
    issued_at   TIMESTAMP DEFAULT NOW(),
    expires_at  DATE NOT NULL,
    revoked_at  TIMESTAMP,
    issued_by   TEXT,  -- admin user who generated it
    notes       TEXT
);

CREATE TABLE usage_snapshots (
    id              SERIAL PRIMARY KEY,
    org_id          UUID REFERENCES organizations(org_id),
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    documents_processed INTEGER DEFAULT 0,
    pages_processed     INTEGER DEFAULT 0,
    active_users        INTEGER DEFAULT 0,
    properties_count    INTEGER DEFAULT 0,
    storage_mb          FLOAT DEFAULT 0,
    metadata            JSONB DEFAULT '{}',
    captured_at         TIMESTAMP DEFAULT NOW()
);

-- Billing
CREATE TABLE subscriptions (
    id                  SERIAL PRIMARY KEY,
    org_id              UUID REFERENCES organizations(org_id),
    stripe_subscription_id  TEXT,
    plan                TEXT NOT NULL,
    billing_cycle       TEXT DEFAULT 'monthly',  -- monthly, annual
    amount_cents        INTEGER,
    currency            TEXT DEFAULT 'usd',
    status              TEXT DEFAULT 'active',  -- active, past_due, canceled, trialing
    trial_end           DATE,
    current_period_start DATE,
    current_period_end   DATE,
    canceled_at         TIMESTAMP,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE TABLE invoices (
    id              SERIAL PRIMARY KEY,
    org_id          UUID REFERENCES organizations(org_id),
    stripe_invoice_id   TEXT,
    amount_cents    INTEGER,
    status          TEXT,  -- paid, open, void, uncollectible
    due_date        DATE,
    paid_at         TIMESTAMP,
    pdf_url         TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Internal team
CREATE TABLE admin_users (
    id          SERIAL PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'support',  -- super_admin, sales, support, engineering
    is_active   BOOLEAN DEFAULT TRUE,
    last_login  TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE audit_log (
    id          SERIAL PRIMARY KEY,
    admin_user_id INTEGER REFERENCES admin_users(id),
    action      TEXT NOT NULL,
    entity_type TEXT,  -- org, license, subscription, admin_user
    entity_id   TEXT,
    details     JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- Mode 3 infrastructure
CREATE TABLE hosted_instances (
    id              SERIAL PRIMARY KEY,
    org_id          UUID REFERENCES organizations(org_id),
    subdomain       TEXT UNIQUE,
    db_host         TEXT,
    db_name         TEXT,
    app_url         TEXT,
    status          TEXT DEFAULT 'provisioning',  -- provisioning, active, suspended, terminated
    storage_used_mb FLOAT DEFAULT 0,
    last_health_check TIMESTAMP,
    config          JSONB DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT NOW()
);
```

---

## Page Structure

### Sidebar Navigation

```
capactive
Platform Admin

── Customers
   Dashboard
   All Organizations
   Trials

── Licensing
   License Dashboard
   Generate Key
   Expiring Soon

── Billing
   Revenue Overview
   Subscriptions
   Invoices
   Failed Payments

── Deployments
   Overview
   Hosted Instances (Mode 3)
   Infrastructure Health

── Analytics
   Usage Overview
   Per-Customer Usage
   Feature Adoption

── Settings
   Team Members
   Audit Trail
   Stripe Config
   HMAC Secret Management
```

### Priority Pages for MVP

Build these first, in this order:

1. **Login** — Email + password for Capactive team members
2. **Customer Dashboard** — Overview of all orgs with key stats
3. **Org Detail** — Full customer profile with plan/deployment/license management
4. **Create Organization** — Wizard for onboarding a new customer
5. **License Generator** — Generate and deliver license keys
6. **License Dashboard** — Expiration tracking and renewal pipeline
7. **Revenue Overview** — MRR, plan distribution, basic billing stats
8. **Team Management** — Add/manage Capactive admin users
9. **Audit Trail** — Log viewer with filters

Everything else is Phase 2.

---

## Stripe Integration Notes

### Webhook Events to Handle

```
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
invoice.paid
invoice.payment_failed
customer.created
customer.updated
```

### Pricing Structure (Suggested Starting Point)

```
Starter:        $299/mo  or  $2,990/yr  (save ~17%)
Standard:       $599/mo  or  $5,990/yr
Professional:   $1,499/mo or $14,990/yr
Enterprise:     Custom pricing, annual only
```

These are placeholders — the admin panel should make it easy to adjust pricing without code changes. Store pricing in the database or Stripe Products, not hardcoded.

---

## API Endpoints (If Building API-First)

```
# Auth
POST   /api/auth/login
POST   /api/auth/logout

# Organizations
GET    /api/orgs                    # list, with filters
POST   /api/orgs                    # create
GET    /api/orgs/:id                # detail
PUT    /api/orgs/:id                # update
POST   /api/orgs/:id/suspend        # suspend
POST   /api/orgs/:id/reactivate     # reactivate

# Licensing
POST   /api/orgs/:id/license        # generate new key
GET    /api/orgs/:id/license/history # key history
POST   /api/orgs/:id/license/revoke # revoke current key
GET    /api/licenses/expiring        # expiring within N days

# Billing
GET    /api/orgs/:id/subscription    # current subscription
POST   /api/orgs/:id/subscription    # create subscription
PUT    /api/orgs/:id/subscription    # change plan
DELETE /api/orgs/:id/subscription    # cancel
GET    /api/orgs/:id/invoices        # invoice history
GET    /api/billing/overview         # revenue dashboard data

# Usage
GET    /api/orgs/:id/usage           # usage stats for org
GET    /api/usage/aggregate          # aggregate stats

# Deployments (Mode 3)
POST   /api/orgs/:id/provision       # provision hosted instance
GET    /api/orgs/:id/instance        # instance status
POST   /api/orgs/:id/instance/health # health check

# Admin
GET    /api/admin/users              # team members
POST   /api/admin/users              # add team member
GET    /api/audit                    # audit log with filters

# Webhooks
POST   /api/webhooks/stripe          # Stripe webhook handler
```

---

## Open Questions

These should be decided before or during build:

1. **Auth for Capactive team** — Simple email/password, or SSO via Google Workspace?
2. **Mode 3 provisioning** — What infrastructure? Docker containers per customer? Shared database with schema isolation? Separate databases?
3. **Email service** — SendGrid, AWS SES, or Postmark for license delivery and billing notifications?
4. **Usage telemetry** — How do Mode 1/2 customers report usage? Opt-in ping endpoint? Manual entry? Skip for MVP?
5. **Pricing** — Confirm plan pricing before building Stripe Products
6. **Trial flow** — Credit card required? Auto-convert or manual?
7. **Customer portal** — Should customers have a self-service portal for billing/invoices, or is everything handled by Capactive team?
8. **HMAC secret management** — How is the license signing secret shared between Layer 3 (generator) and Layer 1 (validator)? Baked into the extractor binary? Environment variable?

---

## What NOT to Build

- **No customer-facing UI.** This is internal only. Customers interact with Layers 1 + 2.
- **No document processing.** That's Layer 1.
- **No property management.** That's Layer 2.
- **No data sync engine yet.** The sync layer between local extractors and hosted databases is a separate infrastructure project. Layer 3 manages the *configuration* of where data goes, but doesn't move data itself.
- **No mobile app.** Desktop web only for internal tool.
