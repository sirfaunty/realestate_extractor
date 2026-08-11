# Capactive — Project State

**Last updated: 2026-08-02.** Start here when picking the project up on a new
machine or after time away. (README.md / TRACK_A_*.md describe the original
extractor phase from May 2026 and are historical — this file supersedes them
for current state.)

---

## What Capactive is

A local-first Flask platform for real-estate document extraction and
analytics. Everything runs on the user's machine: SQLite databases, a local
Ollama LLM (`llama3.1:8b`), OCR via Tesseract/RapidOCR. No data leaves the
device.

Two halves:

1. **The extraction engine** — ingests documents (digital text, scans, broken
   text layers), segments leases into instruments and provisions, extracts
   structured terms with page citations.
2. **The platform** — 18 registered modules surfacing analytics and
   deliverables over the extracted data, gated per organization by plan tier.

## Running it

```bash
CAPACTIVE_DEV_MODE=1 venv/Scripts/python run.py     # Windows/Git Bash
# http://127.0.0.1:5000
```

Notes: Debug is off, so **template and Python changes need a restart**. Always
use the venv python (`venv/Scripts/python`) — system python lacks `requests`
and friends. Git Bash eats backslashes; use forward slashes in paths.

## Module inventory (modules/__init__.py → INSTALLED_MODULES)

Nav is grouped in `modules/gating.py::MODULE_GROUPS`:

- **Deal Documents** — closing_books, tif_analysis, distribution,
  debt_analysis, partnership_dashboard, barrington, southtown, midway
- **Market Analytics** — inventory, sales_comps, scorecard, lease_analysis,
  market_intel, office
- **Portfolio** — portfolio_ownership, residential, deliverables
- (plus `proforma`, auth-gated in core routes)

Each module is a package with `__init__.py` (AbstractModule subclass),
`engine.py` (read-only data access), and `routes.py` (blueprint).

### Module gating / plan tiers
`config.py::PLAN_FEATURES` defines per-tier module lists; `FeatureFlags
.modules_enabled` accepts `["*"]` (unrestricted, the default) or an explicit
list. Enforcement is one `before_request` hook in `modules/gating.py` mapping
URL prefixes to module names, with a friendly 403 lock page and a
`module_enabled(name)` template helper for hiding nav. Admins manage it at
**Admin → Modules** (`/admin/modules`). Fail-open by design.

## Recent work (June–August 2026)

**Portfolio Ownership module** — read-only UI over Riley's verified KA
portfolio warehouse (`portfolio_ownership/KA_OWNERSHIP_MODULE_MASTER_
20260724/portfolio_warehouse.db`, 632MB, 81,971 page-cited provisions across
21 properties). Property rosters, provision browser with citations, open
items, financials, loans, vacancy. Never writes to the master.

**Residential module** — KA residential package UI: roster, NOI bridge,
valuation matrix, comps, discrepancy report.

**Deliverables module** (`/deliverables`) — generates client-ready .docx from
verified data:
- *Lease Abstract Compendium* — full edition (every provision verbatim with
  citations) and summary edition (tenancy summaries + provision index).
- *Refinance Diligence Package* — loan facilities with balances/balloons,
  loan provisions by lender category, lease rollover vs loan maturity,
  open items. Available for the 11 properties with both loan and lease layers.
Output lands in `data/deliverables/`; downloads start automatically.

**Extraction engine upgrades** — segment-first lease extraction replacing
whole-document LLM passes: instrument-chain detection (lease → amendments →
assignments → estoppel/SNDA), six lease-form families, per-segment prompts
(no timeouts), amendment supersession and auto-renewal roll-forward,
flag-don't-fabricate verification (LLM dates must appear in the text they
were read from), broken-font detection and repair, per-document error
isolation. See `extractors/lease_segmenter.py`.

## The re-import campaign (#77)

Re-importing every source document through Capactive's own engine and tying
out against Riley's masters as the source of truth. **Full record:
`portfolio_ownership/re_import/CAMPAIGN.md`.**

Nine properties validated, 92–100% tie-out. Per-property runbook is four
commands (ingest → backfill → analyze → tie out); tooling lives in
`portfolio_ownership/re_import/`. `scoreboard.py` prints the live
cross-property table.

Scoring discipline: paper answer first, Riley's MRI rent-roll module closes
what the instrument cannot state, conflicts are flagged not resolved.

## Where data lives (NOT in git)

`.gitignore` excludes `data/` and `*.db`, and `portfolio_ownership/inbox/`
and the master warehouse are untracked. A fresh clone gets **code only**.
To run pilots or the portfolio modules on another machine you must copy:

- `portfolio_ownership/KA_OWNERSHIP_MODULE_MASTER_20260724/` (the master)
- `portfolio_ownership/inbox/` (Riley's source deliveries)
- `data/` (org DBs, pilot DBs, generated deliverables)

The UI modules degrade gracefully when the master is absent, but portfolio
pages will be empty.

## Open threads

- **Riley review** — `portfolio_ownership/RILEY_REVIEW.md` holds seven items
  awaiting his input: deliverable format calibration, a stale-SF finding in
  the master (Vixen Nails), roster questions, and a growing reconcile punch
  list of paper-vs-rent-roll disagreements the engine surfaced.
- **Campaign continuation** — remaining lease layers: the standalone-delivery
  properties (Shelby, Shorewood, Union Square, Valley West, Plaza 94,
  Gateways — sources inside the original merge-queue zips) and Cub Rochester.
- **Deliverable #3** — a property one-pager or portfolio rent-roll report;
  the engine/module scaffolding makes new deliverables cheap.
- **Multi-suite tenancies** — master sometimes splits one lease across two
  suites (Rogan's Shoes at Crossing Meadows); tie-out scores per tenancy.

## Conventions worth knowing

- The KA master is maintained **exclusively** by the aggregation workflow
  (merge harnesses + `verify_master_run.py`, 259 checks ALL PASS). Modules
  read; they never write.
- Merges run natively on Windows via `portfolio_ownership/apply_*.py` — the
  sandbox mount cannot commit SQLite transactions reliably.
- Long runs: keep the machine plugged in (sleep pauses processes; AC sleep is
  set to Never, battery sleeps in 3 minutes).
