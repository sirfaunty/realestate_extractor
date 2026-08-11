# #77 Re-Import Pilot — Hadley Five (OSBORN-3007)
2026-07-27 · Capactive engine vs Riley's master as source of truth

## What was run

Six primary lease documents (252 pages) staged from Riley's own source set and
run through Capactive's stock pipeline on the local machine:
Phase 1 ingest (21s — all six carry digital text layers; no OCR needed) →
Phase 2 analysis (12 min: rule-based extractors + llama3.1:8b gap-fill).
Output tied out read-only against the master's verified Hadley layer
(7 tenancies, 707 page-cited provisions) by `tie_out.py`.

## Baseline result

**Scoreable field tie-out: 1/14 (7%).**
(Tenant identity, expiration, and SF for the tenancies where the master
carries truth values. Rent reported informationally — the master's Hadley
rent fields are NULL, so nothing was scored against nothing.)

Depth: 3–23 clauses + 3–9 terms per document vs the master's 60–167
provisions per tenancy — roughly an order of magnitude below the bar.

## Diagnosis (all three causes are addressable)

1. **LLM pass timed out on all six documents.** The engine sends large text
   blocks; the client caps at 90s; 40–52-page leases exceed it on this
   hardware/model. Everything scored is therefore rule-based-only.
2. **Single-pass extraction reads the wrong region of a lease chain.**
   Subway's extracted date (6/1/2007) is the original commencement; the
   governing expiration (5/31/2027) lives in later amendments. Riley's method
   reads the full chain article-by-article — which is why the master gets
   this right.
3. **No segmentation → no depth.** Rule-based clause spotting caught 3–23
   clauses; the master's per-article + sweep extraction yields 60–167 per
   tenancy.

## The fix already exists in this repo

`southtown_db/segment_lease.py` + `abstract_lease.py` — the local abstraction
engine built for Southtown: deterministic segmentation (verified 113/113
against the gold warehouse), then per-provision Ollama calls with a large
context window per small segment. Zero timeouts, full article-level depth,
"only the lease language governs" discipline.

**Gap to generalize:** that segmenter reads .docx heading styles; the
portfolio corpus is flat PDF text. The needed piece is a text-pattern
segmenter (Article/Section/Amendment regex over the page text — the same
structure visible in the master's `Article N:` provision categories),
plus chain-awareness (later amendments supersede).

## Segmenter built + validated (2026-07-28)

`segment_text.py` — the flat-PDF generalization, validated against the
master's own provision categories (namespace-aware: Article-form vs
Section-form):

- **Structure recovery: 142/157 (90%) heading numbers, 120 title matches.**
- 179 segments across the 6 documents; only 6 exceed the 10K-char LLM tier
  (median segment ~900–2,600 chars — no timeouts at these sizes).
- Instrument chains detected: Subway correctly splits into original lease →
  Amendment #18604 → Second Amendment → Estoppel → SNDA — the chain that
  single-pass extraction misread.
- Handles both Hadley lease families: `ARTICLE N` (title on next line) and
  numbered-paragraph (`1. PREMISES:`, OCR paren form `12.) INSURANCE.`),
  with TOC/index-page suppression (banner + density + neighbor expansion)
  and a preamble segment for cover/fundamental-provision pages.
- **Known data gap, flag-don't-fabricate:** page 3 of the three
  Osborne-form leases (CBD+Vape, iLOVE, Maple Leaf) has no text layer —
  that page holds Articles 1–3. OCR pass (Midway P1 machinery) can recover
  it at wiring time. Remaining misses (4, 8, 28, 33 in the numbered docs;
  Section 1 in Subway) are OCR-noise headings.

## Per-segment extraction runs (2026-07-28)

`extract_segments.py` — targeted per-segment prompts (llama3.1:8b), OCR
backfill of text-less pages, chain-aware expiration (later instruments
supersede; auto-renewals rolled forward to the master's as-of date), and
computed expirations (commencement + stated term) — every computed value
labeled as computed. `tie_out.py` upgraded to STRICT scoring: when the
segmented run is present, only its single declared answer per field counts.

| Run | Score | Notes |
|-----|-------|-------|
| Baseline (single-pass, all LLM timeouts) | 1/14 (7%) | rule-based only |
| Round 2 (segmented, strict) | 8/14 (57%) | zero LLM timeouts, 4-13s/call |
| Round 3 (dedup + dba net + honest frame) | 9/14 (64%) | **9/10 (90%) document-derivable** |
| Round 4 (content-aware re-OCR, doc1 p3) | 10/14 (71%) | **10/10 (100%) document-derivable** |

Round 4 validated 2026-07-28: multi-DPI re-OCR with content-aware selection
(SF/date hits, not length) recovered the "1,975 square feet" line at 300dpi.
Refinement queued for analyzer wiring: suppress computed expirations when
the lease defines commencement contingently ("earlier of" pattern) — better
null than a wrong computed date.

Highlights: Subway ties out completely — identity, SF, and the chained
expiration (original lease → Amendment → Second Amendment 5/31/2019 →
auto-renewed 2×48mo → **5/31/2027**, exactly the master's value). Great
Clips resolves both identities (Newport Clippers, Inc. legal + d/b/a Great
Clips trade).

## Key finding: document-derivability boundary

Four of the 14 scoreables **cannot be produced from the staged instruments
by any extractor** — verified by inspection, annotated in the harness
(`NOT_DOC_DERIVABLE`):

- eastgate_tobacco + ilove_nails expirations: the Osborne-form leases define
  commencement as "the earlier of (a) the date Tenant opens for business, or
  (b) 90 days after delivery of possession" (Exhibit D) — a contingent,
  operational fact with no calendar date in the instrument.
- lees_liquor identity + expiration: the staged lease names the original
  tenant (Kee Ho Han / MGM Liquor Warehouse); the assignment to Lee's is not
  in the staged set. "Lee" appears nowhere in the document text.

Riley resolved these from his wider source set (rent roll / financial
source data / assignment instruments) — the master's own lease_lease rows
carry NULL commencement, confirming the values came from outside the lease
paper. **Campaign implication:** document extraction alone hits a ~10/14
ceiling on Hadley; full parity requires ingesting the "Portfolio Financial
Source Data" and remaining instrument folders — the same two-source
discipline the aggregator handoff mandates.

## Platform integration (2026-07-28, same day)

The validated pattern now lives in the Capactive engine proper:
`extractors/lease_segmenter.py` + a `segment_first` path in
`extraction_engine.py` for lease documents (automatic fallback to the
legacy path when segmentation recovers <4 segments). Verified through the
real analyzer (`run_analysis.py --db data/pilot_hadley.db`):

- **Same score as the pilot script: 10/14 (71%), 10/10 (100%)
  document-derivable** — the pattern survived generalization intact.
- 325s for 6 documents, zero LLM timeouts (original Phase 2: 12 min,
  all 6 whole-document calls timed out).
- Depth: 181 clauses (16–41 per doc, typed + section-ref'd) vs the
  legacy 3–23 — one provision record per recovered segment.
- Contingent-commencement suppression confirmed: flagged nulls, no
  fabricated computed dates.

## Roadmap for the campaign

1. ~~Engine upgrade: segmenter + per-segment extraction~~ **DONE — and
   generalized into the Capactive analyzer** (extractors/lease_segmenter.py,
   segment_first engine path, legacy fallback).
2. **Depth parity next:** per-segment provision abstraction (the master
   carries 60–167 provisions per tenancy; the pilot's targeted pass only
   chases scoreable fields so far).
3. **Ingest the operational sources** (rent roll / financial source data)
   to close the non-doc-derivable fields per the two-source discipline.
4. **Scale** to a second verified property (e.g. Maplewood, 890 provisions)
   before opening the 48-property source library.

## Assets

- `sources/` — 6 staged lease PDFs (subset of Riley's Part2 source set)
- `data/pilot_hadley.db` — pilot extraction output (isolated from org DBs)
- `tie_out.py` — repeatable scoring harness (read-only on both DBs)
- Truth: master OSBORN-3007 layer (81,971-provision master, 259 checks ALL PASS)
