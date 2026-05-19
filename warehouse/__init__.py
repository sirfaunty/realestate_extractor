"""
Capactive Analytical Warehouse — DuckDB-based analytical data store.

Implements the bitemporal Zone A/B/C architecture:
  Zone A: raw_ingestion_log — provenance for every data load
  Zone B: fact tables — long/tidy bitemporal facts with knowledge_date FK
  Zone C: materialized views — backward-compatible wide-format surfaces

Sits alongside SQLite (which handles the web app's transactional data).
The warehouse is the read surface for all analytical modules.
"""
