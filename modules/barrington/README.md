# Portfolio Cash Flow module (`barrington`)

A no-code Capactive module that turns a portfolio's stored documents into a
portfolio **cash-flow / capital / lease-rollover** model and an **Excel
deliverable**, with NOI tied out to source. All processing runs on-device.

## How it works

1. **Ingestion** (one-time, per portfolio) — `barrington_db/ingest_into_capactive.py`
   creates the portfolio + properties and registers the source documents in
   Capactive's normal document store (so they appear on the dashboard, classified,
   like any other property).
2. **Generation** — the page (`/barrington`) lists portfolios. Pick one and click
   **Generate Deliverable**. A background job:
   - gathers that portfolio's stored documents from the org database,
   - stages cash flows / rent rolls into temp dirs (original filenames preserved —
     the engine matches assets by filename),
   - runs the `barrington_db` cash-flow engine (`build`),
   - validates the NOI tie-out, rolls up the portfolio, and
   - exports an `.xlsx` workbook (downloadable from the page).

## Pieces

- `modules/barrington/` — the Capactive module (this folder): blueprint routes +
  the `Portfolio Cash Flow` page (`web/templates/barrington.html`).
- `barrington_db/` — the standalone cash-flow engine (extractors, schema, models,
  reports) plus `export_excel.py` (workbook builder) and
  `ingest_into_capactive.py` (Capactive ingestion).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/barrington` | The page |
| GET  | `/barrington/api/portfolios` | List portfolios for the selector |
| POST | `/barrington/api/generate` | Start a build for `{portfolio_id}` (one at a time) |
| GET  | `/barrington/api/status/<job_id>` | Poll job progress |
| GET  | `/barrington/api/download` | Download the latest workbook |

## Notes / known gaps (from the engine)

The cash-flow engine's `reference.py` is calibrated for the Barrington office
portfolio (asset list + per-asset extraction config). Documented data gaps carry
through to the deliverable: OHP I/II capital is a combined subtotal (split pending),
Wacker TI/LC double-count across committed/estimated sections, and the Wacker rent
roll isn't parsed yet. NOI and base-building figures tie out.
