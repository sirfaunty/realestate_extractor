# Data Model & Architecture Reference

Companion to `PARTNER_ONBOARDING.md`. This translates what we learned on Chamberlain into a generalized schema and architecture sketch. It is a starting point for your technical design, not a finished spec.

---

## 1. THE CORE ENTITIES

Everything we did on Chamberlain reduces to a handful of entity types. These should generalize across the whole portfolio.

### Source Document
The atomic unit. Every fact in the system descends from one of these.
- `id`, `type` (closing_statement | loan_agreement | operating_agreement | amendment | gl_export | bank_statement | accountant_workpaper | email | management_agreement | rent_roll | ...)
- `asset_id`, `entity_id`, `deal_id` (what it belongs to)
- `date`, `effective_date`
- `provenance` (who provided it, when, original filename, file hash)
- `authority_tier` — **critical field.** Primary (signed/executed/recorded), Secondary (work product, drafts), Tertiary (informal/derived). Drives conflict resolution.
- `file_pointer` — link to the actual stored original. Never deleted.
- `extraction_status`, `completeness_flags`

### Extracted Fact
A single structured datum pulled from a Source Document.
- `id`, `source_document_id`
- `fact_type` (contribution | distribution | escrow_balance | loan_term | waterfall_provision | definition | journal_entry | covenant | lease_term | ...)
- `value`, `unit`, `as_of_date`
- `citation` — **first-class object, not metadata.** Page/cell reference + verbatim source text. See §3.
- `confidence`, `verified` (was this confirmed against a primary source?)
- `extraction_method` (auto | expert_entered | expert_corrected)

### Governing Provision
A special class of Extracted Fact: a term from a governing document that defines what *should* be true.
- `id`, `source_document_id` (the operating agreement, loan agreement, etc.)
- `provision_type` (distribution_waterfall | capital_call | covenant | consent_right | definition | fee_schedule | ...)
- `structured_logic` — the provision encoded in a form the reconciliation engine can evaluate
- `references` — links to defined terms and other provisions it depends on

### Reconciliation
The output of comparing actual practice (Extracted Facts) against requirements (Governing Provisions).
- `id`, `governing_provision_id`, `related_fact_ids[]`
- `status` (consistent | inconsistent | unverifiable | needs_review)
- `direction_of_impact`, `magnitude`
- `confidence`
- `narrative` — the human-readable finding

### Lens View
An abstraction of the fact base for a specific audience. Not stored data — a query/projection.
- `lens_type` (leadership | operational | loan | partnership | legal | leasing | management)
- `asset_scope` (single | portfolio)
- projects Extracted Facts + Reconciliations into lens-appropriate structure

### Deliverable
A generated, formatted, point-in-time output of a Lens View.
- `id`, `lens_view`, `generated_at`, `format` (pdf | dashboard | statement)
- `branding_profile`
- every figure in it carries a drill-path back through Lens View → Extracted Fact → Source Document → page

---

## 2. THE RELATIONSHIP GRAPH IS THE PRODUCT

The entities above are inert without the relationships. The Chamberlain work proved that the *value* is in edges like:

- `Distribution` —governed_by→ `Governing Provision` (a waterfall clause)
- `Contribution` —subject_to→ `Governing Provision` (a capital-call provision)
- `Escrow Balance` —referenced_by→ `Loan Covenant`
- `Journal Entry` —reconciles_to / contradicts→ `Bank Statement Line`
- `Governing Provision` —depends_on→ `Definition`
- `Extracted Fact` —cited_from→ `Source Document` (page-level)
- `Reconciliation` —compares→ {`Extracted Fact`, `Governing Provision`}

The reconciliation engine traverses this graph. The drill-down traceability traverses this graph. Build the graph deliberately; don't let it be an afterthought of a flat table schema.

---

## 3. CITATION OBJECT — THE NON-NEGOTIABLE

Every Extracted Fact carries a citation. Minimum viable citation:

```
citation {
  source_document_id
  locator        // page number, cell ref (e.g. "Sheet2!N194"), section number, paragraph
  verbatim_text  // the exact text the fact was extracted from
  extraction_date
}
```

Why each field:
- `source_document_id` + `locator` → enables drill-down to the exact spot
- `verbatim_text` → enables an adversary (GC, opposing counsel, auditor) to confirm the fact wasn't paraphrased into something it isn't
- this is what made the Chamberlain memo defensible; it is what makes the platform audit-grade

A figure in a leadership deliverable should be clickable down to: deliverable → lens view → extracted fact → citation → rendered source page with the cited text highlighted.

---

## 4. SOURCE AUTHORITY TIERS — CONFLICT RESOLUTION

We repeatedly got burned treating the wrong source as ground truth. Encode authority explicitly:

| Tier | Examples | Role |
|---|---|---|
| **Primary** | Signed closing statements, executed agreements, recorded instruments, bank statements | Ground truth. Wins conflicts. |
| **Secondary** | Accountant working files, GL exports, draft documents, internal models | Evidence of what was *done* or *believed* — not authoritative about what's *correct*. |
| **Tertiary** | Informal notes, summaries, emails describing events | Context and leads. Never the basis for a reported figure on its own. |

When two sources conflict, the engine surfaces the conflict and defaults to the higher tier — but never silently. A conflict between a primary and secondary source is itself a finding (it's how the Chamberlain GL-vs-bank discrepancy surfaced).

---

## 5. DOCUMENT TYPE → LENS MAPPING

Which document types feed which lenses. Use this to prioritize extraction development.

| Document Type | Primary Lens | Also Feeds |
|---|---|---|
| Operating Agreement + Amendments | Partnership/Legal | Leadership, Loan |
| Loan Agreement / Note / Closing Statement | Loan | Legal, Leadership, Partnership |
| HUD Regulatory Agreement | Legal | Loan, Operational |
| GL Export | Operational | Partnership, Leadership |
| Bank Statements / Reconciliations | Partnership/Operational | Loan, Leadership |
| Accountant Work Product | (verification only) | Partnership — as *evidence*, never authority |
| Management Agreement | Management | Legal, Operational |
| Rent Roll / Lease Abstracts | Leasing | Operational, Leadership |
| Sources & Uses / Development Budget | Loan/Partnership | Operational |
| Escrow Statements | Loan | Partnership |

The governing-document classes (operating agreement, loan agreement, regulatory agreement, management agreement) are the spine — build their extraction first.

---

## 6. THE RECONCILIATION ENGINE — WHAT CHAMBERLAIN TAUGHT

The hardest and most valuable component. From the Chamberlain work, the pattern:

1. **Encode the governing provision as evaluable logic.** Chamberlain's distribution waterfall (§5.2) had three tiers, each with conditions and split ratios, plus definitions that other clauses depended on. The engine needs the provision in a form it can actually run against the fact base — not just stored text.

2. **Build the "actual practice" timeline from primary-source facts.** Every contribution, distribution, escrow movement — dated, sourced, sequenced.

3. **Run the provision against the timeline.** What *should* each distribution have been under the governing logic? What *was* it?

4. **Flag divergence with direction, magnitude, and confidence.** Not just "mismatch" — which party it favors, how much, and how confident the system is (which depends on source authority and completeness).

5. **Surface the explanation when the evidence supports one.** On Chamberlain, the accountant's own work product documented the methodology error. The engine should connect that.

The engine's confidence must degrade gracefully with incompleteness. "This distribution can't be reconciled because we don't have bank confirmation for that period" is a valid, important output — not a failure.

---

## 7. INCOMPLETENESS AS A FIRST-CLASS STATE

The Chamberlain document set had real gaps: bank reconciliation didn't start until 1/8/2020; certain entity-level statements were never available; an email archive turned out to be the wrong date range. The platform must:

- Track *expected* documents per asset/deal/lens and flag what's missing
- Mark facts as `unverifiable` when the confirming source isn't present
- Never let a Lens View present an unverifiable fact as verified
- Make "what's missing" a queryable view for each lens — owners need to know the gaps as much as the facts

---

## 8. WHAT THE CHAMBERLAIN ARTIFACTS GIVE YOU

In the companion zip (`chamberlain_memo_v3_handoff.zip`):

- **`06_analysis_notes/02_KEY_NUMBERS_AND_DERIVATIONS.md`** — a worked example of the Extracted Fact + Citation model, by hand. Each figure has a source and a derivation. This *is* the schema, implicitly.
- **`06_analysis_notes/03_LLC_AGREEMENT_DISTRIBUTION_WATERFALL.md`** — a worked example of a Governing Provision encoded for reconciliation. Shows what "evaluable provision logic" needs to capture.
- **`06_analysis_notes/04_FINDINGS_FROM_REFI_DOCUMENTS.md`** — a worked example of Reconciliation outputs from a loan-lens document set.
- **`02_source_data/` + `03_refi_documents/`** — real test fixtures for the extraction layer. The refi package especially is a complete, realistic loan-lens document set.
- **`05_build_scripts/`** — a crude but working Deliverable generator (database-of-facts → branded PDF).
- **`01_current_memo/`** — the target deliverable quality bar.

Treat the analysis notes as the seed data model. They were written as analysis, but they encode — entity by entity, citation by citation — exactly what the platform's schema needs to hold.

---

## 9. OPEN DESIGN QUESTIONS WE DIDN'T HAVE TO SOLVE

Things the single-asset prototype let us dodge that the platform can't:

- **Entity hierarchy** — assets roll into entities roll into JVs roll into a portfolio. The schema needs this from the start; Chamberlain was flat.
- **Temporal versioning** — documents get amended (Chamberlain's operating agreement had amendments that changed key terms). Facts and provisions need effective-dating and version history.
- **Cross-asset normalization** — a "distribution" means the same thing across assets, but the chart of accounts and document conventions will differ by deal/sponsor. Need a normalization layer.
- **Multi-user / permissions** — different lenses for different audiences implies access control. Leadership sees everything; a leasing manager sees the leasing lens.
- **Update propagation** — when a new document arrives or a fact is corrected, every dependent Reconciliation and Lens View must recompute. Build for this; don't batch-rebuild.
- **Extraction QA loop** — the expert-correction loop (Lesson 6 in the onboarding doc) needs to be a designed workflow, with corrections feeding back to improve extraction.

None of these are blockers. But they're the difference between a prototype and the platform, so design for them early rather than retrofitting.
