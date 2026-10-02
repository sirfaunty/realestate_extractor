# Portfolio Cash Flow module (`portfolio_cashflow`)

A no-code Capactive module that turns a portfolio's stored documents into a
portfolio **cash-flow / capital / lease-rollover** model and an **Excel
deliverable**, with NOI tied out to source. All processing runs on-device.

## How it works

1. **Ingestion** (one-time, per portfolio) — the engine's ingestion script
   creates the portfolio + properties and registers the source documents in
   Capactive's normal document store (so they appear on the dashboard, classified,
   like any other property).
2. **Generation** — the page (`/portfolio-cashflow`) lists portfolios. Pick one and
   click **Generate Deliverable**. A background job:
   - gathers that portfolio's stored documents from the org database,
   - stages cash flows / rent rolls into temp dirs (original filenames preserved —
     the engine matches assets by filename),
   - runs the cash-flow engine (`build`),
   - validates the NOI tie-out, rolls up the portfolio, and
   - exports an `.xlsx` workbook (downloadable from the page).

## Pieces

- `modules/portfolio_cashflow/` — the Capactive module (this folder): blueprint
  routes + the `Portfolio Cash Flow` page (`web/templates/portfolio_cashflow.html`).
- The cash-flow engine (extractors, schema, models, reports, `export_excel.py`)
  is engagement-specific and lives outside this repo; `modules/bespoke_engines.py`
  locates it via the gitignored `bespoke_engines.json`. Without it, the page shows
  its empty state.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/portfolio-cashflow` | The page |
| GET  | `/portfolio-cashflow/api/portfolios` | List portfolios for the selector |
| POST | `/portfolio-cashflow/api/generate` | Start a build for `{portfolio_id}` (one at a time) |
| GET  | `/portfolio-cashflow/api/status/<job_id>` | Poll job progress |
| GET  | `/portfolio-cashflow/api/download` | Download the latest workbook |
