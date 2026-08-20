# Packaging & Delivery Design — Extraction Seats, Access Users, Dual Hosting

_Status: agreed direction, phased build. Supersedes the data-boundary decision
in `cloud-sync-plan.md` (PDFs now sync — see §3); builds on
`NOTES_multi_tenant_licensing.md`, which anticipated most of this shape._
_Last updated: 2026-08-19_

## 1. The model

Two classes of user, one product:

**Extraction seats** run Capactive software locally (OCR, Ollama, the
segment-first engine — the hardware-dependent, privacy-sensitive half).
Each extraction seat is tied to the org license and bound to a device. Local
processing pushes finalized results *and source PDFs* to the org's web
instance.

**Access seats** are web-only accounts on the org's instance: dashboards,
portfolio modules, deliverable generation, source-document viewing. Nothing
local, priced/limited separately from extraction seats.

The web instance is the same Flask app we run today, deployed either
**managed** (we operate it for the client) or **on client infrastructure**
(their VM/container host). One container image, different operator — per
`NOTES_multi_tenant_licensing.md`, "same extraction engine underneath either
way."

## 2. Why this fits the existing codebase

The split already exists internally; nothing fights it:

- The permission layer (`permissions.py`) separates `extraction.*` scopes
  from `property.*` scopes with role templates and per-user overrides — the
  two user classes are mostly *expressible today*.
- Analytics modules are read-only over databases and never care where
  extraction ran (proven operationally by the KA workflow and
  `export_for_mac.py`, which is a manual version of this sync).
- `licensing.py` (org keys, encrypted local license file for on-prem) and
  `usage.py` (volume tracking, audit) were built for exactly this.
- SQLite-per-org isolation means single-tenant hosting requires no schema
  work and no tenant-isolation engineering.
- `run_id` / `is_current` versioning gives the sync a natural unit (push
  finalized runs, not DB dumps).

## 3. Decision: source PDFs sync (reversal, with eyes open)

`cloud-sync-plan.md` locked "never source documents." **Reversed 2026-08-19:**
access users' trust in extracted data depends on one-click access to the
source page — provenance is the product. Source PDFs are first-class synced
artifacts.

Consequences we accept and mitigate:

- **Privacy story changes shape, stays strong.** Old: "documents never leave
  your device." New: "documents are *processed* on your device — no
  third-party AI ever reads them — and live only in *your* instance
  (yours-hosted or dedicated-to-you), never a shared cloud." The Ollama
  local-processing claim remains literally true and remains the
  differentiator.
- **We may host client documents** (managed option) → TLS everywhere,
  encryption at rest, backups, an explicit data-processing agreement, and
  the audit trail `usage.py` already provides. Client-infrastructure option
  exists precisely for orgs whose bar excludes third-party hosting.
- **Payloads are big** → PDFs upload once per document (content-hash
  dedupe), resumable, out-of-band from the small structured-data push.

## 4. Architecture

```
[Extraction seat: Capactive local]          [Org web instance (managed OR client-hosted)]
  ingest → OCR → segment → extract             Flask app (today's codebase, container)
  review/finalize locally                      org SQLite DBs + PDF store (volume)
        │                                      access users: dashboards/modules/deliverables
        └── push API (device token) ──────────►  /api/sync/*  (idempotent, run-granular)
```

- The extraction client is **this same codebase** on the client's machine.
  Sync is additive: a push module + device credentials, not a fork.
- One-way sync; local remains system of record; corrections local, re-push.
  (Unchanged from cloud-sync-plan.)
- Only finalized runs push. "Finalize" becomes an explicit local step
  (cloud-sync-plan's named milestone — still the correct first brick).

## 5. Licensing & seats

- **Org license (`org_key`)** — as today: plan tier → modules, volume.
  `FeatureFlags.max_users` splits into `max_extraction_seats` and
  `max_access_seats`.
- **Extraction seat** — device registration against the org instance issues
  a device token (license key + machine fingerprint). Validated on every
  push; revocable from the admin panel. On-prem/offline validation via the
  encrypted license file (`licensing.py`) for clients who run fully local.
- **Access seat** — ordinary account; role templates gate what they see.
  Current `analyst`/`viewer` templates are already access-seat shaped; add
  an `extractor` template (extraction.* edit, property.* none) for
  extraction-only humans.

## 6. Gap list (what actually has to be built)

1. **Machine auth** — no Bearer/API-token auth exists (session login only).
   Device registration + token issuance + `@device_required` decorator.
2. **Finalize/publish step** — local state marking a run pushable.
3. **Push API** — `/api/sync/manifest`, `/api/sync/run`, `/api/sync/pdf`
   (hash-deduped, resumable); idempotent receive keyed on run_id.
4. **Seat classes** — config/license split, enforcement at device
   registration and user creation, seat counts on the admin license page.
5. **Container packaging** — Dockerfile + compose (app, data volume, Caddy
   for TLS); backup story (Litestream or scheduled snapshot).
6. **Extraction client packaging** — Windows installer story bundling
   Python env + Tesseract + Ollama bootstrap; config pointing at the org
   instance URL + device credentials.
7. **Messaging updates** — startup banner / security copy per §3.

## 7. Phases

- **Phase 1 — user classes in the current app** (no infra): seat-class
  config, `extractor` role template, admin UX, license page seat counts,
  finalize/publish flag. Useful even if sync never ships; needed for the
  first packaged delivery regardless.
- **Phase 2 — sync**: device auth, push API incl. PDFs, receive-side
  upserts, sync status UI (the universal progress widget already exists to
  surface it).
- **Phase 3 — packaging**: container + TLS + backups; extraction-client
  installer; provisioning runbook for both hosting modes.
- **Phase 4 — managed operations**: per-client provisioning, monitoring,
  update channel, DPA/security one-pager for sales conversations.

## 8. Open questions (answer before the relevant phase)

- Pricing: per extraction seat vs per access seat vs bundled tiers
  (`NOTES_multi_tenant_licensing.md` pricing section still unresolved).
- Managed hosting provider + region; first client's data-residency/SSO bar.
- Do access users ever write (annotations, open-item dispositions)? If yes,
  a small write surface on the instance side needs designing — currently
  everything assumes read-only mirrors.
- Update cadence for extraction clients (auto-update vs manual installer).
