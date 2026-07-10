# Midway disposition-diligence warehouse (`midway_db`)

Local, on-device engine for **retail-disposition lease diligence** — the second
retail workstream (a *sale*, vs. Southtown's lease/development analysis). Turns a
shopping center's tenant certification documents into a structured diligence
database (lease abstracts + REA/cross-parcel analysis + missing-document tracker)
and disposition deliverables. Built to be reusable for the next sale.

Source deal: Kraus-Anderson's partial sale of **Midway Marketplace** (St. Paul, MN)
— the two vacant anchor boxes (former Cub Foods 1440, At Home 1450) sold to
**GSC RE Holdings (H Mart affiliate)** for $16.5M, closed 6/18/2026. KA retains the
in-line strip, TJ Maxx (1410), and LA Fitness (1370, ground-leased).

## Data model

Ported from the partner's `midway_psa.db` (24 tables). Core layers:

- **Lease abstraction** — `lease_tenant`, `lease_document_file`, `lease_abstract`
  (an entity-attribute-value model: each row is a `(tenant, field, value)` fact with
  `source_page` + `confidence`).
- **PSA / agreement** — `agreement`, `broker`, `closing_document`, `contingency`,
  `cost_allocation`, `due_diligence_item`, `financial_term`, `key_date_deadline`.
- **REA / cross-parcel** — `rea_instrument`, `rea_prohibited_use`, `rea_siteplan_fact`,
  `cross_parcel_provision`, `no_change_area_finding`, `site_anchor_map`.
- **Trackers** — `missing_document`, `rent_roll`, `representation`, `party`, `section`.

## Critical provenance caveat

Every tenant package received so far is the **certification layer** — estoppels,
SNDAs, NERS/checklists, transmittal letters — **not the executed leases**. So the
73 gold abstracts rest on *secondary/certifying* documents, not gold-standard lease
text. Any deliverable must carry this caveat. Highest-value next input: the seller's
"fully executed documents" folder.

## How the partner produced it

The 73 abstracts were **hand/AI-authored** (see `record_*.py` audit scripts in the
handoff) by reading each tenant's certification docs and writing the structured
facts. Local re-extraction therefore means: OCR the scanned docs, then run the local
model to extract the `(tenant, field, value, source, confidence)` facts per
instrument — validated against the 73 gold facts.

## Phased build

- **P0 (this) — consolidate + scaffold.** Canonical project, ported `schema.sql`,
  gold DB staged for tie-out, source docs gitignored.
- **P1 — document ingestion + OCR.** Tenant PDFs → text (digital where possible,
  OCR where scanned), registered like `lease_document_file`.
- **P2 — structured abstraction.** Local model → `lease_abstract` facts, validated
  vs. the 73 gold facts.
- **P3 — diligence layer.** Missing-document tracker, due-diligence items,
  REA/cross-parcel analysis.
- **P4 — no-code module + deliverables.** A "Disposition Diligence" Capactic page:
  ingest a center's tenant docs → extract locally → lease-abstract dump + missing-doc
  report.

## Security posture

Same as Barrington/Southtown: **source documents and any text-laden DB never leave
the device.** `data/` and `source_docs/` are gitignored; only code is committed.
Extraction runs locally (OCR + local model); nothing is sent to any external API.

## Canonical source

Of the 11 handoff zips, `Midway_Marketplace_COMPLETE` is the clean master package
(README_MASTER + the 24-table `midway_psa.db`). The larger archives are mostly the
raw source PDFs; the two 206 MB "Diligence Package" zips are duplicates.
