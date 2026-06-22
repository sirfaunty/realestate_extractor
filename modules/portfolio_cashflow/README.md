# Portfolio Cash Flow module (`portfolio_cashflow`)

A no-code Capactive module that turns a portfolio's stored documents into a
portfolio **cash-flow / capital / lease-rollover** model and an **Excel
deliverable**, with NOI tied out to source. All processing runs on-device.

## How it works

1. **Ingestion** (one-time, per portfolio) — `portfolio_cashflow_db/ingest_into_capactive.py`
   creates the portfolio + properties and registers the source documents in
   Capactive's normal document store (so they appear on the dashboard, classified,
   like any other property).
2. **Generation** — the page (`/portfolio_cashflow`) lists portfolios. Pick one and click
   **Generate Deliverable**. A background job:
   - gathers that portfolio's stored documents from the org database,
   - stages cash flows / rent rolls into temp dirs (original filenames preserved —
     the engine matches assets by filename),
   - runs the `portfolio_cashflow_db` cash-flow engine (`build`),
   - validates the NOI tie-out, rolls up the portfolio, and
   - exports an `.xlsx` workbook (downloadable from the page).

## Pieces

- `modules/portfolio_cashflow/` — the Capactive module (this folder): blueprint routes +
  the `Portfolio Cash Flow` page (`web/templates/portfolio_cashflow.html`).
- `portfolio_cashflow_db/` — the standalone cash-flow engine (extractors, schema, models,
  reports) plus `export_excel.py` (workbook builder) and
  `ingest_into_capactive.py` (Capactive ingestion).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/portfolio_cashflow` | The page |
| GET  | `/portfolio_cashflow/api/portfolios` | List portfolios for the selector |
| POST | `/portfolio_cashflow/api/generate` | Start a build for `{portfolio_id}` (one at a time) |
| GET  | `/portfolio_cashflow/api/status/<job_id>` | Poll job progress |
| GET  | `/portfolio_cashflow/api/download` | Download the latest workbook |

## Notes / known gaps (from the engine)

The cash-flow engine's `reference.py` is calibrated for the Portfolio B office
portfolio (asset list + per-asset extraction config). Documented data gaps carry
through to the deliverable: OHP I/II capital is a combined subtotal (split pending),
Wacker TI/LC double-count across committed/estimated sections, and the Wacker rent
roll isn't parsed yet. NOI and base-building figures tie out.
