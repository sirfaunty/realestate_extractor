# START HERE — Partner Handoff

This package gets you up to speed on the Chamberlain residential source-document work and frames it as the foundation for the broader ownership platform you're going to build.

## What this is

We (KA residential asset management) spent ~18 months reverse-engineering one asset — Chamberlain Apartments, LLC — from primary source documents up, producing an audit-grade leadership diagnostic memo. That work is a working prototype of the pipeline the platform needs to generalize: ingest source documents → extract and structure → database with citations → abstract per lens → generate deliverables.

You're taking this foundation to build the real thing: a platform that extracts source documents, databases the extracted information (with citations and links back to the source documents themselves), abstracts the relevant information for each lens (leadership, operational, loan, partnership, legal, leasing, management), and gives owners complete end-to-end control of their portfolio.

## Read in this order

1. **`PARTNER_ONBOARDING.md`** — The main document. What we did, the core insight, the pipeline stages, the hard-won lessons, a suggested build sequence, and the north star. Read this first and in full.

2. **`DATA_MODEL_AND_ARCHITECTURE.md`** — The technical bridge. Translates the Chamberlain specifics into a generalized entity schema, the relationship graph, the citation object spec, source-authority tiers, document-to-lens mapping, and the open design questions the single-asset prototype let us dodge.

3. **The companion zip — `chamberlain_memo_v3_handoff.zip`** — The full Chamberlain working materials: source documents, analysis notes (which implicitly define the seed data model), the deliverable-generation code, the actual 44-page memo, and the complete working transcripts. Its own `MANIFEST.md` explains the contents. Use `02_source_data/` and `03_refi_documents/` as extraction test fixtures and `06_analysis_notes/` as the seed schema.

## The one-sentence version

Chamberlain proved that the valuable layer in ownership data isn't the dashboard — it's the reconciliation layer that connects every number to the governing document that controls it, with citations precise enough to survive a CEO, a GC, or opposing counsel. The platform's job is to be that layer, at portfolio scale, across every lens.
