# Track A: Document Extractor — Chamberlain completion + new property onboarding

## Session Focus
1. Finish Chamberlain document import — get all remaining docs ingested and extraction verified
2. Onboard a new property end-to-end — this will stress-test classifier, parsers, and synthesis against fresh docs
3. Fix whatever breaks — parser coverage gaps, classifier misses, extraction accuracy issues will surface naturally from the new property
4. Build validation tooling as needed — compare extraction output against hand-validated data

## Key Context
- Dev mode: `CAPACTIVE_DEV_MODE=1 python3 run.py --port 8080`
- DB path: `data/org_dev.db`
- Always use `python3` not `python`
- Sandbox can't reach localhost:11434 (Ollama), so LLM-dependent analysis must be run locally
- Proforma module is live but depends on `pydantic pyyaml numpy-financial python-dateutil`
- Known past issues: sqlite3.Row .get() errors, column name mismatches in bridge.py (all fixed), 2025A expense gaps, cross-sheet double-counting

## Key Files
- `webapp.py` — main Flask app (~2400 lines)
- `batch_processor.py` — document ingestion pipeline
- `property_analyzer.py` — property analysis engine
- `financial_synthesis.py` — period-level financial synthesis
- `modules/proforma/` — proforma module with citation bridge
- `warehouse/` — DuckDB analytical warehouse (just built, wired at /warehouse) — don't touch, Track B owns this
