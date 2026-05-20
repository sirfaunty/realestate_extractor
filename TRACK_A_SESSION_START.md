# Track A Session Startup — Document Extractor

## Pre-flight (do these before starting the session)

```bash
cd ~/Desktop/realestate_extractor

# 1. Clean stale warehouse lock
rm -f data/warehouse.duckdb.wal

# 2. Install deps (if not already)
pip3 install pydantic pyyaml numpy-financial python-dateutil

# 3. Push and checkout branch (if not already done)
bash git_push.sh
git checkout track-a/extractor-training
```

## Session prompt

Paste this into the new session:

---

Read TRACK_A_EXTRACTOR.md for full context. Here's what we're doing:

**Goal:** Finish Chamberlain document import, then onboard a new property end-to-end to stress-test and improve the extractor.

**Immediate steps:**
1. Start the app: `CAPACTIVE_DEV_MODE=1 python3 run.py --port 8080`
2. Check what Chamberlain docs are already ingested — inspect `data/org_dev.db` and the Chamberlain property page
3. Identify extraction gaps (missing docs, bad classifications, low-quality extractions)
4. Fix whatever surfaces — parser coverage, classifier accuracy, synthesis gaps
5. Then onboard a fresh property with new docs to find the next round of issues

**Key rules:**
- Always use `python3` not `python`
- Dev mode: `CAPACTIVE_DEV_MODE=1 python3 run.py --port 8080`
- DB path: `data/org_dev.db`
- The sandbox CANNOT reach localhost:11434 (Ollama) — LLM-dependent analysis must be run locally by me
- Don't touch anything in `warehouse/` or `modules/inventory/` or `modules/sales_comps/` — Track B owns those
- The proforma module and warehouse blueprint load on startup but are wrapped in try/except — they won't block the app if deps are missing

**Known past issues (all fixed, but watch for regressions):**
- sqlite3.Row objects don't support `.get()` — must cast to dict first
- Column name mismatches between SQL queries and actual DB schema
- 2025A expense extraction was 0% (subtotal detection issue)
- Cross-sheet double-counting in columnar parser
- Classifier missed budget docs, T-12s
- html.escape() crashes on None values

---
