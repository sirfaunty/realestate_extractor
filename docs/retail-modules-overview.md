# Capactic Retail Modules — Architecture & Handoff Overview

_Draft left for review. Covers the three retail workstreams built as no-code modules.
Everything runs on-device; source documents and text-laden databases stay local
(gitignored) and are never sent to any external API._

---

## The shape of the platform

Capactic is a local-first Flask app (`python run.py`, dev mode `CAPACTIVE_DEV_MODE=1`,
`http://127.0.0.1:5000`). Feature modules live under `modules/` and auto-register via
`modules/__init__.py` (`INSTALLED_MODULES`). Each retail module pairs:

- a **standalone engine** (a top-level `*_db/` project) that does the extraction and
  builds deliverables, runnable from the CLI; and
- a **Capactic module** (`modules/<name>/`) that wraps it in a no-code page.

Extraction uses a **local Ollama model** (`llama3.1:8b` @ `localhost:11434`) for the
interpretive parts and deterministic parsing (PyMuPDF, RapidOCR, openpyxl, python-docx)
everywhere the data is structured. Nothing leaves the device.

**Hardware constraint:** the dev machine has a 6 GB GPU (RTX 3050), which caps the
local model at the ~7–8B class. A 14B won't fit; `qwen2.5:7b` is the only realistic
lateral A/B. Consequently, cross-document *synthesis / analytical judgment* is scoped
to human review across all three modules — the engines extract the recurring,
single-document facts and flag the rest.

---

## Module 1 — Barrington (Portfolio Cash Flow)

- **Engine:** `barrington_db/` · **Module:** `modules/barrington/` · **Nav:** Portfolio Cash Flow
- **What:** ingest a portfolio's stored cash-flow + rent-roll documents → build a
  cash-flow / capital / lease-rollover model → export an Excel workbook, with NOI tied
  out to source.
- **Deliverable:** Excel (two styles — Portfolio Summary and Consolidated Cash Flow /
  client format), selectable on the page.
- **Status:** live; NOI ties out (Portfolio NOI $25.5M).

## Module 2 — Southtown (Lease Abstraction + Co-Tenancy/Returns)

- **Engine:** `southtown_db/` · **Module:** `modules/southtown/` · **Nav:** Lease Abstraction
- **What:** (a) segment a lease `.docx` into provisions (deterministic, ties out
  113/113) and abstract each into 3 tiers via the local model (85–89% figure recall);
  (b) a co-tenancy + development-returns engine driven by the rent-roll PDF and Brama's
  proforma (source-driven; ties out 12/12 — 65.49% base occupancy, YoC 5.66%/6.58%,
  TPC $41.35M).
- **Deliverables:** Word **Lease Abstract Compendium**; live formula Excel
  **Co-Tenancy & Returns Model** — both one-click from the page.
- **Run (CLI):** `southtown_db/` → `build_warehouse.py`, `abstract_lease.py`,
  `compendium_docx.py`, `returns_model.py`, `returns_xlsx.py`.

## Module 3 — Midway (Disposition Diligence)

- **Engine:** `midway_db/` · **Module:** `modules/midway/` · **Nav:** Disposition Diligence
- **What:** OCR a shopping-center disposition's tenant certification docs (PyMuPDF +
  RapidOCR, per-page) → structured lease-abstract warehouse (local model) → missing-
  document gap detection + PSA deal-economics extraction + REA prohibited-use schedule
  → a Word diligence report.
- **Validated:** LA Fitness 11/11 facts; PSA prices/earnest tie out to gold exactly
  (At Home $6M/$120K, Cub $10.5M/$200K); REA grocery-prohibition flag surfaced.
  Correspondence-only tenants are triaged (not abstracted) to avoid hallucination.
- **Deliverable:** Word **Disposition Diligence Report** (sale economics, tenant
  abstracts, missing-doc tracker, REA prohibited uses with grocery flag).
- **Run (CLI):** `midway_db/` → `ingest.py`, `abstract_facts.py`, `missing_docs.py`,
  `extract_psa.py`, `extract_rea.py`, `diligence_report.py`, `score_facts.py`.

---

## Security posture (all modules)

- Source documents and any text-laden DB are **gitignored** and never leave the device.
- Extraction (OCR + local model) runs on the host; no external API calls.
- Generated deliverables (`.xlsx` / `.docx`) are gitignored; only code is committed.
- Machine-generated abstracts are labeled **automated first draft for review**, not
  attorney work product / legal advice.

## Known limits / open threads

- **Local-model ceiling (8B / 6 GB GPU):** abstraction fidelity ~85–89%; the analytical
  layers (Southtown cross-provision synthesis; Midway amendment-chain, ownership-chain,
  REA cross-parcel, diligence confirmations) remain human review.
- **PSA extraction:** purchase price + earnest money are reliable; broker/closing detail
  is partial (long OCR'd doc).
- **Cloud sync:** design captured in `docs/cloud-sync-plan.md` — build when a second
  user needs remote access and the local schema has settled (one-way, finalized data
  only).
- **Not yet touched:** T-Mobile / UPS Store tenants (no docs provided); the fuller REA
  cross-parcel / no-change-area analysis.

## Repo map (retail work)

```
barrington_db/     Portfolio cash-flow engine + Excel exporters
southtown_db/      Lease segmentation + abstraction + co-tenancy/returns + deliverables
midway_db/         Disposition-diligence engine (OCR, abstraction, PSA/REA, report)
modules/barrington|southtown|midway/   the three no-code Capactic pages
docs/cloud-sync-plan.md                one-way cloud-sync plan (future)
docs/retail-modules-overview.md        this file
```
