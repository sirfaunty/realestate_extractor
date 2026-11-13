# Capactive — Pitch Brief for the Site (November 2026 refresh)

_Handoff doc for the marketing/site session. This replaces the August 2026
brief. Everything here is accurate to the shipped product as of 13 Nov 2026;
the claims guardrails at the end mark where the line is. Audience for this
doc: whoever rewrites the site. Audience for the site: commercial real
estate owners, operators, asset managers, and the analysts who keep their
numbers._

_What changed since August, in one paragraph: the product went from "an
extractor with modules" to a lease roster that reads a building's paper
and tells you what each tenancy owes today, reconciles that against your
rent roll, and hands the cited result to your own systems. Extraction is
now deterministic rules first, with the local AI pass optional. It was
hardened on a real portfolio of 70+ buildings of scanned, decades-old
leases, measured against the owner's own records, and the measurements
below are the ones we can defend._

---

## One-liner

**Capactive reads a building's paper trail and tells you what every
tenancy owes today, with the page it came from, without your documents
ever leaving your hands.**

## Tagline (established, use verbatim)

**See the signal. Make the move.**

## The problem (why anyone cares)

A commercial property runs on hundreds of documents: leases and their
amendments, extensions, option letters, deal sheets, loan agreements, rent
rolls. The questions owners ask every month are simple. What does this
tenant actually owe? When does the lease really end? Does the rent roll
agree with the paper? The answers are buried in scanned PDFs, often
decades old, and the standard options are painful: pay someone to
abstract by hand, trust the rent roll and hope, or paste confidential
financials into a cloud AI tool.

## What Capactive does (the story, in three beats)

1. **Read.** Capactive ingests your documents on your own computer: clean
   PDFs, rough scans, broken text layers, paper from the 1990s. It
   segments each lease into its instruments (the lease, every amendment,
   extension, expansion, option letter) and reads them by deterministic
   rules. Same paper in, same answer out, every time. An optional AI pass
   runs locally and never calls out; by default it is off.

2. **Verify.** The product's center is the **Lease Roster**: one row per
   tenancy with square footage, commencement, expiration and current rent,
   each with a confidence and the words it came from. A rent is shown only
   when it is high-confidence; anything less is routed to review with the
   one action that resolves it (confirm a commencement, pick a document).
   Upload your rent roll and Capactive reconciles it: rents that tie are
   confirmed, differences are flagged and explained (which step in the
   papers the roll's figure stands on), and your own affiliates and
   account lines are labelled as such instead of as missing leases.
   Possible duplicates are flagged, never merged.

3. **Move.** Verified data becomes working intelligence: dashboards, lease
   and loan analytics, client-ready deliverables (lease abstract
   compendiums, lender diligence packages), and, new, a **cited data
   package** your analysts can load straight into the warehouse or model
   they already run. Every row still carries its document and page.

## Key capabilities (site-friendly phrasing)

- **Private by architecture, not by promise.** Documents are read on your
  own machine. No third-party AI service ever sees them. Results and
  source pages live in your instance, on your infrastructure or in one
  dedicated to you, never a shared cloud.
- **Deterministic first, AI optional.** The reader is rules, not a model
  guessing. The result is repeatable and auditable. Local AI is there as
  an explicit, opt-in pass for the gaps rules leave.
- **Every number quotes its source.** Each value carries the page and the
  verbatim words it was read from. Where the paper prints nothing,
  Capactive says so instead of inventing a figure.
- **Honest by design.** A rent is shown only at high confidence. When the
  paper and the rent roll disagree, the difference is surfaced with its
  reason. Nothing is smoothed over.
- **Current rent from the paper itself.** The rent schedule is placed
  across the whole chain of instruments: base lease, amendments,
  extensions, expansions, exercised options. Unexercised option periods
  are left out until you say otherwise.
- **Rent-roll reconciliation in one upload.** Match tenancies to the roll,
  confirm what ties, flag what does not, and show where each roll figure
  stands in the papers.
- **Built for real scans.** Image-only pages get OCR. A page whose first
  read does not add up is read again, and again at higher resolution, and
  a read stands only when its lines tie to the total printed on the page.
- **Deal sheets and suite history.** Net-effective-rent deal sheets are
  read line by line and tied to their printed totals. Monthly rent rolls
  become a suite-by-suite occupancy history.
- **Feeds your warehouse.** A property publishes as a self-contained,
  cited data package (documents, page text, tenancies, rent steps,
  provisions, options) that another system can merge safely: nothing is
  edited in place, and a half-written publish is never read.
- **Deliverables in seconds.** Lease abstract compendiums (full or
  executive edition) and refinance diligence packages from verified data,
  each fact still carrying its page.
- **A whole team on one source of truth.** Extraction runs on licensed,
  device-bound desks. Everyone else, analysts, partners, lenders' counsel,
  works in the browser, including on a phone, with the source page one
  click away. Invites by email, roles per user, every change versioned,
  every sign-in and permission change in the audit trail.
- **Runs where you want it.** Your infrastructure or a private instance we
  manage for you. Either way single-tenant: your instance holds your data
  and nobody else's. Managed instances for US customers are hosted in the
  US.

## Proof points (anonymized; do not name the client, buildings or tenants)

All from measured harness runs against the owner's own records, not from
reading code. Round to the phrasing shown; do not quote more precision.

- **Scale.** Validated on a real multi-state commercial portfolio of more
  than 70 buildings: scanned leases going back decades, monthly rent
  rolls, thousands of deal sheets.
- **Rent, the headline.** When Capactive shows a current rent as
  high-confidence, it agrees with the owner's rent roll about 98% of the
  time (205 of 210). Everything below that bar is routed to review, not
  shown as fact.
- **Rent from paper alone.** Three in four tenancies (391 of 524) have
  their current rent read correctly from the documents alone, before any
  human confirmation. The rest are flagged with the one action that
  resolves them.
- **Deal sheets.** About 92% of roughly 2,900 net-effective-rent deal
  sheets read line by line tie to the total printed on the sheet
  (2,659 tie; the rest are flagged, most of them hand-annotated).
- **Suite history.** 54,000+ suite-months of occupancy rebuilt from monthly
  rent rolls, with square footage matching the owner's records 100%.
- **Caught real errors.** Discrepancies surfaced in the client's own
  records, found because Capactive flags instead of smoothing.

## Who it is for (three doors on the same product)

- **Owner / principal.** Wants the roster and the rent-roll check without
  trusting a chatbot with the files. Leads with privacy and the one-click
  source page.
- **Asset manager / analyst.** Wants current rent, expirations and options
  by tenancy, reconciled monthly. Leads with the roster and reconciliation.
- **Data / finance team with its own warehouse.** Does not want another
  silo. Leads with the cited data package: Capactive feeds the model they
  already run.

## Differentiation in one sentence each

- vs. cloud AI tools (paste-into-a-chatbot): they require sending
  confidential financials to someone else's servers and give answers with
  no receipt; Capactive never sends anything and quotes the page.
- vs. manual abstraction services: weeks and per-document fees become
  minutes, with citations a human can spot-check instead of redo.
- vs. the rent roll as the only truth: the roll is what you believe; the
  paper is what you signed. Capactive shows where they differ.
- vs. generic document management: DMs store documents; Capactive reads
  them into a roster, reconciles it, and hands it to your systems.

## Suggested site map (few pages, clean)

1. **Home.** Tagline, one-liner, the three beats (Read / Verify / Move),
   the 98% proof line, single CTA ("Request a demo").
2. **How it works.** The three beats expanded, one screenshot each: the
   roster with a confirmed rent and a row routed to review; the rent-roll
   reconciliation with a flagged difference; a compendium page with its
   citation. Seat model in one sentence: extraction desks plus browser
   access for everyone else.
3. **For data teams.** New page. The cited package: what tables it holds,
   that every row cites a document and page, that a publish is atomic.
   Short, technical, confident. This is the page an analyst forwards.
4. **Security & Trust.** On-device reading, your instance only, US hosting
   for managed US customers, single-tenant, device-bound extraction seats,
   invite-only access, audit trail. This page closes deals in this
   market; keep it short.
5. **Contact / Request demo.** Form or email. No pricing page yet; pricing
   is per conversation.

Screenshots come from the staging demo organization only, which runs a
fictional portfolio ("Harbor Point Plaza" and friends). Never from a client
instance.

## Voice & style

- Tone: confident, plain-spoken, no AI hype. The buyer is skeptical of AI
  vendors. Lead with control, repeatability and the receipt, not with
  "AI-powered". Where the old site said "AI extraction", say "reads" or
  "reads by rule".
- Tagline appears verbatim; don't riff variants of it.
- Palette as shipped in the product: deep blue `#185FA5` (primary),
  lighter blue `#3A8FD4` (accent), dark navy/white wordmark ("capactive",
  lowercase), ascending-bars logo mark.
- Words to prefer: verified, cited, reconciled, yours, on your machine.
  Words to avoid: revolutionary, magic, "chat with your documents",
  "AI-powered" as a lead.

## Claims guardrails (the site must stay inside these)

- OK: "documents are read on your own device"; "no third-party AI ever
  sees your documents"; "results live only in your instance".
- NOT OK any more: "documents never leave your device" as a blanket claim.
  Source pages sync to the org's web instance so the team can click
  through to them. Say *processed* on your device, *held* in your instance.
- OK: "AI is optional and local". NOT OK: "AI-free" (the optional pass
  exists) or "AI reads your leases" as the main claim (rules do).
- OK: the proof numbers above, with "on a validation portfolio" or similar.
  NOT OK: "99% accurate" or any accuracy figure not in this brief.
- OK: "works in your phone's browser". NOT OK: "mobile app".
- OK: "a cited data package your data team can load". NOT OK: "integrates
  with [named system]", "API", "integrations marketplace". It is a file
  package, not a live connection.
- OK: single-tenant, your infrastructure or ours, US hosting for managed
  US customers, audit trail, invite-only access.
- NOT yet: SOC 2 or any compliance acronym (nothing is certified; say
  "built for security-conscious owners"), self-serve signup, multi-tenant
  SaaS, pricing.
- Do not name the validation client, any building, tenant or person.
  Fictional names only, and only the demo organization's.
