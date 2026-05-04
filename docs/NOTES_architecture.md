# Capactive Document Extractor — Architecture Notes

## Three-Layer Architecture

### Layer 1 — Extractor Engine (Built)
Local Flask app. Ingests PDFs, runs OCR + LLM extraction, stores structured data in SQLite. Runs entirely on-device. No internet required. This is the engine room.

**Status:** Built and functional. Needs Property model integration (documents currently standalone).

### Layer 2 — Property Intelligence Dashboard
Front-end that sits on top of extracted data. Lets users work with their portfolio organized around individual property assets. This is the customer-facing product.

**Three data buckets per property:**

1. **Operations** — Tenant roster, lease terms, rent payment status, historical rent, operating expenses. Applies to both multifamily and commercial/industrial.

2. **Debt** — Loan agreement details, guarantees, service requirements, covenants, landlord details/approvals, principal + interest schedules.

3. **Valuation / NOI** — Asset performance derived from buckets 1 + 2. Working towards Net Operating Income. How the property is actually performing.

**Deployment:** Runs locally in mode 1, can be hosted in modes 2/3. Built as Flask blueprint initially, structured for separation later.

### Layer 3 — Platform Admin / Billing (Separate Codebase)
Capactive's internal tool for managing all customers. Separate app, separate repo.

- Org/customer management across all deployments
- License key generation and delivery
- Subscription management and payment collection (Stripe)
- Deployment mode toggle per customer
- User authentication oversight
- Usage analytics across all orgs

**Status:** Not yet built. Separate project. Current in-app admin panel (admin.html etc.) is the *customer's* org admin — distinct from this.


## Three Deployment Modes

### Mode 1 — Fully Local ("Self-Hosted")
- Extractor + Dashboard + Database all on customer's machine/server
- No data leaves their network
- Multi-user: runs as local server (same LAN / VPN), not per-person instances
- Customer manages their own infrastructure
- License key validates locally against encrypted license file

### Mode 2 — Customer-Hosted Database
- Extractor runs locally
- Data syncs to a database the customer controls (their AWS/Azure/etc.)
- Dashboard can be local or hosted, pointing at their DB
- Customer owns the infrastructure, Capactive provides software
- License key validates against customer's deployment

### Mode 3 — Capactive-Hosted
- Extractor runs locally
- Data syncs to Capactive-managed database
- Dashboard hosted by Capactive
- Easiest option for most customers, best for recurring revenue
- License key validates against Capactive platform

**Key principle across all modes:** Extraction (AI/LLM processing) always happens locally. The data that reaches any database is structured output — no different from Salesforce data. The promise is that raw documents never touch a cloud AI service.

**Deployment mode is set per-org in Layer 3 admin.** Stored as a flag on the org profile. Affects whether sync layer is active and where data routes.


## Asset Hierarchy

```
Portfolio
  └── Property
        └── Building
              └── Unit
```

### Portfolio
- Logical grouping of properties
- Examples: "Midwest Industrial Portfolio", "Southeast MF Fund II"
- Loans can attach at this level (cross-collateralized)
- Optional — a property can exist without a portfolio

### Property
- Single asset at an address
- Examples: "Parkview Apartments", "123 Industrial Blvd"
- Has a property_type: multifamily, industrial, commercial, office, retail, mixed_use
- Operating statements roll up here
- Primary level for valuation/NOI analysis

### Building
- For multi-structure properties
- Examples: "Building A", "North Tower"
- Optional — single-building properties skip this or have one implicit building
- Has its own address if different from property

### Unit
- Leasable space
- Examples: Apt 204, Suite 100, Warehouse Bay C
- Leases attach here
- Rent roll line items tie to units

### Document Linking
- Documents get a `property_id` (required) and optionally `building_id` or `unit_id`
- Rent roll: property-level doc, line items link to units
- Lease: links to unit (or property if unit not yet created)
- Loan doc: links to property, or portfolio if cross-collateralized
- Operating statement: property level
- General ledger: property level


## Sync Layer (Future)

For modes 2 and 3, need a mechanism to push local SQLite data to remote database.

Options to evaluate:
- **Change tracking + API push**: Track inserts/updates in local DB, batch push via REST API
- **SQLite replication**: Tools like Litestream or rqlite
- **Export + import**: Periodic full or incremental export to remote

This comes after Layer 2 is functional in local mode.


## Tech Stack Decisions

- **Layer 1 + 2:** Python / Flask / SQLite / Jinja templates
- **Layer 3:** Likely separate stack — could be Flask + PostgreSQL + Stripe, or a modern framework. TBD.
- **All layers:** Capactive branding (navy ramp, Inter font, ascending bar logo)
