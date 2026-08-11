# #77 Re-Import Campaign — Capactive engine vs Riley's verified masters

The long-term goal: re-import every source document through Capactive's own
engine, tying out to Riley's KA master (81,971 page-cited provisions,
259 checks ALL PASS) as the source of truth. Capactive is the foundation;
the masters are the bar.

## Per-property runbook (all native, in order)

```
venv/Scripts/python portfolio_ownership/re_import/ingest_property.py \
    --sources "portfolio_ownership/inbox/Lease Files/<Property>" \
    --db data/pilot_<name>.db --property "<Display Name>"

venv/Scripts/python portfolio_ownership/re_import/backfill_pages.py \
    --db data/pilot_<name>.db          # OCR pages the ingest skipped

venv/Scripts/python run_analysis.py --db data/pilot_<name>.db --property-id 1

venv/Scripts/python portfolio_ownership/re_import/tie_out_property.py \
    --db data/pilot_<name>.db --property-id <MASTER-ID>
```

The tie-out auto-maps docs to master tenant_keys by filename tokens, scores
strictly against the segmented extractor's single declared answer per field,
and auto-classifies expiration misses whose truth date is absent from the
document text (ops-data values the campaign closes by ingesting the
Portfolio Financial Source Data folder).

## Results

| Property | Master | Paper only | Two-source (paper + rent roll) |
|----------|--------|-----------|-------------------------------|
| Hadley Five (pilot) | OSBORN-3007, 707 prov | 10/14 (71%), 10/10 doc-derivable | **14/14 (100%)** — 3 ops-closed |
| Maplewood Square I | ENGELS-2010, 748 prov | 19/26 (73%), 19/22 doc-derivable | **25/26 (96%)** — 6 ops-closed |
| Northcourt Commons | KAINC-1016, 6,364 prov | 21/34 (62%) | **32/34 (94%)** — 16 ops-closed |
| Midway Marketplace | KAINC-1014, 1,697 prov | — | **13/15 (87%)** — 3 ops-closed |
| Normandale | KAINC-1015, 6,376 prov | — | **25/28 (89%)** — 8 ops-closed; 93% doc-derivable |
| Cottage Grove Plaza | OSBORN-3020, 2,359 prov | — | **33/33 (100%)** — 15 ops-closed |
| Crossing Meadows | CMX, 5,819 prov | — | **48/49 (98%)** — 22 ops-closed |
| Maplewood Square II | ENGELS-2011, 141 prov | — | **6/6 (100%)** — 2 ops-closed |
| Market Street (full) | KAINC-1013, 2,590 prov | — | **47/51 (92%)** — 24 ops-closed; complete 19-tenancy roster |

Market Street full roster (2026-08-02): first COMPLETE-roster property —
retail + office wings, all 19 tenancies scored. Office lease forms
segmented without new grammar. Three reconcile flags (Wink Eye, Edward
Jones, Restaurants No Limit — renewals vs paper). Misses: Mediacom
(1-provision license), two former-tenant FKA/name-variant identity cases,
and Ellie Family Services SF. That last one exposed a real ingest bug,
now fixed at both ends: the digital pipeline's empty-page early-stop
recorded only the pages it extracted (Ellie: a 46-page scan logged as 1
page), so backfill couldn't see the gap. Ingest now records the TRUE PDF
page count, and backfill self-heals legacy DBs by trusting the source
PDF. The repair pass on Market Street corrected THREE documents (1→46,
38→43, 41→46) and recovered 48 pages of previously invisible text —
that bug was silently costing pages on earlier properties too. Ellie
went 0 → 10 terms / 38 clauses; its SF now reads 3,869 vs master 2,666
(expansion or gross-vs-usable — added to the reconcile list).

Market Street Retail (2026-08-02): the encoding-garbage detector fired
six times AT INGEST (46-70% unprintable, auto-routed to OCR) — first
property needing zero repair rounds; the pathology pipeline is now fully
preventive. 40 min end-to-end for 457 pages. Only miss: Mediacom (6pp
utility license, 1 master provision). Wink Eye reconcile flag fired
(paper 10/31/2024 vs current 12/31/2030 — renewal). Office-side
tenancies (9 rosters) await staging from the Office folder zips.

Crossing Meadows (2026-08-02): biggest scoring surface yet (49 fields,
20 tenancies) and the honest speed benchmark with sleep disabled: 926
pages in 17 min ingest + 20 min analysis (~40 min/property total for a
mostly-scanned corpus). Three paper-vs-rent-roll reconcile flags fired
(Anytime Fitness renewal, Kaleidoscope, Rogan's 2001 paper date). Single
miss: Rogan's Shoes SF — the master splits Rogan's into TWO suites
(2,746 + suite 50A) while one 116pp lease covers both; the engine's
7,000 is plausibly the combined premises. Multi-suite tenancy handling
queued as a refinement. 10 former-tenant rosters have no staged docs
(their papers sit in Archived Scans/Terminated — stageable later).

Cottage Grove (2026-08-02): the nested-corpus milestone — first PERFECT
score, on the hardest corpus structure yet: 68 instruments across 11
tenancies, each tenant's chain shipped as separate FILES (Chuck & Don's:
1992 lease + 8 amendments + bankruptcy assignment to IPP Stores, 19 docs;
GNC: 11 incl. subleases; Sally Beauty: 7 incl. COVID abatement). The
per-tenant tie-out aggregation carried it; two paper-vs-rent-roll
reconcile flags fired correctly (Cassini, Gentle Dentistry — pre-
amendment paper dates vs current). Per-document error isolation proved
itself in production: one legacy-path crash (string confidence from the
LLM — now coerced) was contained without aborting the run. Unmapped and
queued for Riley: TD Retail Group lease (Highnorth Dispensary's paper?)
and the Ho King predecessor chain (succeeded by Vangz via the 8th
amendment/assignment, which tied out 3/3).

Normandale (2026-08-01): first property through the FIXED ingest — 581
pages in 5.3 minutes (vs Midway's 21 hours; two orders of magnitude).
Surfaced the second garbage-encoding variant: broken fonts render as
(cid:) via pdfplumber but raw control-character soup via PyMuPDF, so the
detector missed them — now detects by unprintable-character ratio
(catches both), and backfill auto-repairs by default. Post-repair all 12
docs segment (80–117 clauses; 1,115 total). Remaining misses are an SF
extraction cluster on this form family (5 SF misses incl. the recurring
'500 square feet' boilerplate artifact — targeted study queued) + 1
non-derivable expiration. Note: several rent-roll sqft values disagree
with the master's sf for the missed tenancies — a genuine two-source
reconciliation question, not just an extraction gap.

Midway (2026-08-01): thinnest truth layer yet — the master's KAINC-1014
roster carries no expirations/SF and mostly blank trade names (identity
scored from tenant_key tokens), and a third of the docs are licenses/
access agreements. Depth is the win: 1,026 clauses, LA Fitness 0 → 186 vs
master 197 after cid repair (its garbage layer passed the 3-page ingest
sample — detector now also samples mid-document). Also surfaced and fixed
the 21-hour ingest pathology: double tesseract pass, whole-document
300dpi RAM conversion, unbounded per-page time (now single-pass, batched,
250dpi, 90s cap). Misses: duplicate Comcast license (near-empty
instrument), Cub Foods (SUPERVALU-family legal tenant on a 380pp ground
lease — franchise-label case).

Northcourt (2026-07-30, third landlord family — KAINC): 19 docs, 1,004
pages. Taught the engine the (cid:) broken-font pathology: 7 docs shipped
garbage vector text layers that fooled digital-PDF detection (fixed at
ingest) and sent OCR into minutes-per-page overdrive when rendered at
200dpi with the junk glyph overlay (repair pass runs 150dpi, per-page
committed/resumable). Post-repair: 1,599 clauses (~100+/tenancy on the
KAINC form), identity 16/18, SF 13/14. Master's KAINC-1016 roster carries
no expirations, so this property scores identity+SF only. Remaining
misses: Great Clips identity (former tenant, absent from rent-roll
snapshot) and Ross Dress for Less identity+SF (national-retailer custom
form, segmentation-resistant — 9 clauses from 114pp; known outlier).

Two-source mode (2026-07-29): `tie_out_property.py` auto-joins Riley's
verified rent-roll module (`Portfolio Financial Source Data & Modules/
Financial Modules/Final Portfolio Rent Roll Module_7.10.26/database/
portfolio_rentroll.db` — 1,247 page-cited MRI rows, base rent ties EXACT,
snapshot 2026-06-30, same property_key convention as the master). Paper
answer scores first; the rent roll closes fields the instrument cannot
state (contingent commencements, assignments); paper/ops conflicts are
flagged for reconciliation, never silently resolved. The one remaining
miss portfolio-wide is Budget Blinds' identity (vacant suite — absent from
the rent-roll snapshot; d/b/a not stated near the tenant block).

## Engine capabilities earned per property

Hadley (Osborne forms): instrument-chain detection, ARTICLE/numbered-paragraph
segmentation with TOC suppression, per-segment prompts (no timeouts),
amendment chain + auto-renewal roll-forward, computed expirations with
contingent-commencement suppression, multi-DPI content-aware re-OCR.

Maplewood (ENGELS forms): OCR-robust TOC detection (banner anywhere,
mangled dot leaders), sublease/memorandum instruments, anti-hallucination
date verification (LLM dates must appear in the text read), landlord-confusion
guard, deterministic expiration fallback (stated-expiration scan in operative
instruments + rent-schedule-end scan), token-set identity scoring against the
master's full trade_name incl. parenthetical legal entities.

## Financial + Loan module folders — integration verdict (2026-07-29)

Inventoried "Portfolio Financial Source Data & Modules" and "Loan Agreements
and Portfolio Loan Module" against the KA master: the master already carries
every module layer (tax_, cam_, capex_, bd_, bud_, ins_, rr_, rr_stmt_,
loan_, recon_ prefixes), verified by row-count ties (rr_ derived tables
exact 8/8; loan_facility 61=61). **No second merge queue.** The folders are
source archives plus canonical fact DBs (e.g. the rent-roll module's
portfolio_rentroll.db, which the two-source tie-out reads directly).
Discrepancy noted: "Late Rent & Bad Debt Module" ships the RE Tax README
(packaging error, content unaffected — bd_ tables verified in master).

## National-retailer form study (2026-07-30)

Four Northcourt docs resisted the standard grammar; three new permanent
capabilities came out of the study (no regressions — Hadley 537,
Maplewood 1,051):

- **Inline decimal sections (Ross form):** digital extractions flow
  "N.N. Title." section starts mid-paragraph; lines are exploded at inline
  marks with an ascending (major, minor) filter screening date look-alikes.
  Ross: 9 → 242 provisions.
- **CAPS-heading fallback (Barnes form):** leases with standalone all-caps
  titles and no numbering at all. Barnes: 14 → 63.
- **Mid-package LEASE AGREEMENT boundaries (Great Clips form):** renewals
  shipped as complete second leases now split into their own instruments.
  Great Clips: 26 → 37 (its renewal amendments restart article numbering —
  that's the document's real structure).
- O'Reilly (21) is at its natural top-level grain; the master's 284
  includes sweep re-categorization.

## SF extraction study (2026-08-01)

Root cause of the Normandale SF cluster was mine: the mid-package LEASE
AGREEMENT boundary made cover pages their own tiny 'lease' instruments,
and target routing took the FIRST lease instrument — prompts ran against
covers. Fixed (primary = richest instrument) plus: scored SF candidate
net (operative premises statements beat boilerplate; combined-total
post-expansion language preferred), OCR digit normalization ("I,672"),
letter-space collapse ("s q u a r e  f e e t"), amendment/exhibit
last-resort scan. 13/13 cross-property regression sweep holds.

Substantive catch: Vixen Nails (Normandale) — the engine now extracts
1,244 SF (868 existing + 442 expansion per the amendment; rent roll
agrees at 1,244) while the MASTER carries 868. First instance of the
re-import catching staleness in the source of truth — flagged on the
Riley review list.

Final Normandale (2026-08-01, post-fix run): 25/28 (89%), 93%
document-derivable. Remaining misses fully explained: 1 non-derivable
expiration, Max Salon SF (rent-math-derived, not stated in instrument),
Vixen SF (engine correct at 1,244; master stale). The paper-vs-rent-roll
conflict flag fired in production for the first time (Vixen expiration
note). Also hardened this round: date-parse guards (day-first swap, no
KeyError) and per-document error isolation in the analyzer.

## Known open items

- Inbox copy gap: a handful of files in "Portfolio Financial Source Data"
  were skipped by Windows path-too-long during the copy (2026-07-29). If a
  source file referenced by a module's provenance ledger turns up missing,
  this is why — re-copy those files to shorter paths.

- Franchise identity labels: legal entity extracted correctly but brand not
  matched when neither trade name nor d/b/a appears near the tenant block
  (Budget Blinds/North Iowa Interiors, GNC/General Nutrition Corp, UPS OCR).
  Candidate fix: brand scan of use/signage clauses.
- Expirations absent from paper (contingent commencement forms): require the
  operational sources — rent roll / assignment instruments.
- ~~Depth parity~~ CLOSED 2026-07-29: section-level sub-provision splitting
  (Osborne/ENGELS "Section N." lines, GNC-form decimals "2.5. Percentage
  Rent.", numbered-form "N.N") + whole-instrument records for ancillary
  instruments (estoppel/SNDA/guaranty). Deterministic, page-cited, no LLM.
  Hadley 533 vs master 638 (84%); Maplewood 1,010 vs 705 (143% — finer
  than the master on lightly-abstracted vacated tenancies). Remaining gap
  is the master's topic-sweep re-categorization (same text, multiple
  category views), not missing content. Optional per-provision LLM
  summaries (Southtown 3-tier) remain a future enrichment lever.
- Next scale candidates: Cottage Grove Plaza (OSBORN-3020, 2,359 prov) or a
  KAINC-family property to cover the third landlord form family.
