# Cloud Sync Plan — Remote Access to Extracted Data

_Status: decided, not yet built. Revisit after the current client's format feedback closes._
_Last updated: 2026-07-08_

## Goal

Let multiple users access and work with **finalized** extracted data remotely,
without weakening the on-device security story that customers care about.

## Why "local" matters (the customer concern)

Customers' real fear is source financials being mined into third-party (Anthropic)
servers. Our architecture already answers this: **extraction runs against a local
Ollama model** (`LocalLLMClient`, `OLLAMA_URL`) — document content is processed
on-device and is never sent to Anthropic or any external API during extraction.
This is literally true, not just "we trust it's secure," and should be stated
plainly in security materials. The cloud DB becomes the *only* point where any
data leaves the device.

## Decisions (locked)

- **One-way sync.** Local is the system of record. Cloud is a read-only mirror
  for access/analysis. No write-back for now.
- **Editing stays local.** All corrections/edits happen on the local node.
- **Only "final" data syncs.** Cloud receives finalized, user-approved output
  only — never source documents, never intermediate extraction.
- **Sync unit = a completed/finalized run.** Leverage existing `extraction_runs`
  / `run_id` / `is_current` versioning; push at run granularity (deltas, not
  full-DB dumps). Preserves the A/B comparison story in the cloud too.

## Data boundary

- **Never leaves device:** source cash flows, rent rolls, and any document the
  local model reads; intermediate extraction state.
- **Syncs to cloud (when finalized):** structured result tables only
  (properties, portfolios, cash_flow_fact, rent_roll, etc. — the published output).

## Store options (decide at build time)

- **Local SQLite → managed Postgres** (Supabase / Neon / RDS). Best if the cloud
  side hosts the app/API for many concurrent users. `org_id` scoping is the
  multi-tenant foundation.
- **Turso / libSQL** (distributed SQLite w/ embedded replicas) or **Litestream**.
  Best if we want to stay SQLite and just get a synced cloud replica with minimal
  schema rework. Turso fits "local + sync to cloud, many readers" almost exactly.

## Security bar ("no different than any SaaS")

- Tenant isolation — enforce `org_id` on every row/query (Postgres RLS or
  per-tenant schemas).
- Encryption in transit (TLS) and at rest.
- Auth per user, ideally SSO; read-only roles for "review and pull" users.
- Audit log of access/exports (customers in this space usually ask).
- Explicit publish step so data enters the cloud only when marked final locally.

## Messaging / housekeeping

The startup banner and "all data stays on this device" language need a small
tweak once sync exists, e.g.: _"Source data and extraction stay on-device; only
finalized results you choose to publish sync to your team's cloud."_ Keeps the
strong claim honest.

## Sequencing — when to build

Waiting is cheap (a managed Postgres + a run-level push job is a modest build);
building early is expensive (security surface, ops, cost, two-sided migrations
against a moving schema). Build the cloud DB only when ALL are true:

1. A **second user actually needs remote access** — a real person who can't get
   what they need from a local file we hand them. (Today we have demand for the
   *deliverable*, not yet for *shared remote data*.)
2. The **local schema has settled** — output tables stop changing per client
   feedback. (We just added a new output style; more may come.)
3. **"Final" is a defined state locally** — a finalize/publish step exists so only
   approved records sync.
4. **The customer's actual security bar is known** — hosting region, SSO, audit,
   data residency — set by a real requirement, not a guess.

## Next milestone (before any cloud work)

Build the **finalize/publish concept locally** — the sync unit. Useful on its own
and the foundation the cloud sync depends on. `run_id` / `is_current` is ~80%
there. Cloud comes after the current client signs off on format and a second party
needs the data remotely.
