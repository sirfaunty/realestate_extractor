# Scorecard data pipeline (offline tooling)

Archived from the standalone `capactive-scorecard` repo (Railway) on
2026-09-25 when the scorecard moved onto the Capactive instance. These
are NOT imported by the web app — they are the research scripts that
refresh the scorecard's external inputs:

- `fred_data_loader.py` — FRED economic series (needs a FRED API key)
- `census_acs_loader.py` — Census ACS demographics
- `cbsa_crosswalk.py` — market ↔ CBSA mapping
- `external_data_integrator.py` — joins the above into the demographic
  inputs consumed by `../demo_engine.py` / `../reference_data.json`
- `run_scorecard.py` — original CLI runner (pre-module)
- `workbook_scores.json` — legacy workbook tie-out reference

Imports are the original flat style (`from data_loader import …`); run
them from this directory with the engine files on PYTHONPATH, or port to
package-relative imports if they become part of a scheduled refresh.
