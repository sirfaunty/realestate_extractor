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
- **P1 (done) — document ingestion + OCR** (`ingest.py`). Registers each tenant PDF
  in `lease_document_file` (tenant from folder, `doc_role` from filename, text-layer
  probe) and extracts text — digital via **PyMuPDF**, scanned via **RapidOCR** (ONNX,
  no system Tesseract/poppler). Pip-only. Verified: 8 tenants, 24 files, 9 digital /
  15 scanned; OCR pulls clean text from scanned estoppels/SNDAs.

  ```bash
  pip install pymupdf rapidocr-onnxruntime pdfplumber
  python ingest.py --no-ocr     # fast: digital text only, register scanned as needs_ocr
  python ingest.py              # full: OCR the scanned docs too (a few minutes)
  ```
- **P2 (done) — structured abstraction** (`abstract_facts.py` + `score_facts.py`).
  For each tenant, feeds the fact-dense certification text (estoppel + checklist,
  fallback correspondence/SNDA) to the local Ollama model and extracts a **core set of
  standard lease-abstract fields** (instrument type, tenant entity, landlord, premises,
  rent, term, renewals, use, assignment, deposit, notice address) as
  `(tenant, field, value, source, confidence)` facts. Scored against the 73 gold facts
  via a synonym map (figure recall + token overlap).

  **Validation (real):** direct inspection confirms the extraction is accurate — LA
  Fitness 11/11 facts correct, every Dollar Tree fact correct *per its estoppel*. The
  `score_facts` numbers are a **directional sanity check only**, and understate quality
  for three structural reasons, none of them extraction errors: (1) tenants the gold
  never abstracted (Clear Channel/Comcast) have nothing to compare; (2) the gold's
  freeform 50-field vocabulary doesn't align 1:1 with the clean core schema (e.g. our
  `lease_date` = execution date vs. gold `commencement`); (3) estoppels are point-in-
  time snapshots while gold reflects the current post-amendment state. Where the
  taxonomies do align (LA Fitness) the score is 100%.

  **Scope note:** the gold's ~50 fields include a bespoke **analytical layer**
  (rentroll reconciliations, date-discrepancy flags, ownership-chain synthesis, the
  co-tenancy / REA / entity FLAGs) that requires cross-document reasoning + deal
  knowledge. That stays the **human diligence layer** (or a later enhancement) — the
  same boundary we drew for Southtown's cross-provision synthesis on an 8B model.

  ```bash
  python abstract_facts.py                    # extract facts for all tenants (local model)
  python abstract_facts.py --only "LA Fitness"  # spot-check one tenant
  python score_facts.py --built data/midway.db --gold data/gold_midway_psa.db
  ```
- **P3 (in progress) — diligence layer.**
  - **Missing-document tracker** (`missing_docs.py`) — *done*. Deterministic gap
    analysis: flags each tenant whose executed lease/amendments are missing (has the
    certification layer but no executed instrument), writing rows into
    `missing_document`. Validated against gold (same core tenants flagged); the
    operative-instrument exception (parking services agreement) is handled.
  - **PSA + REA extraction** — *next*. Local-model extraction of the Cub/At Home
    purchase agreements (financial terms, key dates, closing conditions) and the REA
    (prohibited uses, cross-parcel provisions), same pattern as P2. The bespoke
    analytical confirmations in the gold tracker stay human-judgment.

    ```bash
    python missing_docs.py     # detect + record missing executed instruments
    ```
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
