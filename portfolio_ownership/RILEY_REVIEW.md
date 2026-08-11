# Riley Review List

Standing list of items awaiting Riley's review or input. Add as they come
up; strike through when resolved.

## Open

1. **Lease Abstract Compendium samples (2026-07-29)** — first output of the
   new Capactive Deliverables module, generated from the verified KA master
   + MRI rent-roll module (read-only, no LLM at build time):
   - `data/deliverables/Hadley_Five_Lease_Abstract_Compendium_2026-07-29.docx`
     (7 tenancies, 707 provisions)
   - `data/deliverables/Maplewood_Square_I_Lease_Abstract_Compendium_2026-07-29.docx`
     (13 tenancies, 748 provisions)

   Questions for Riley:
   - Does the per-tenancy summary block carry the right fields (and is
     blending lease-paper terms with rent-roll economics labeled clearly
     enough)?
   - Provision ordering: Article/Section-form numerically first, then sweep
     categories A–Z — match his working style?
   - Refi-impact flags: rendered as red callouts under each provision —
     sufficient, or should the compendium open with a refi-impact summary
     section?
   - Any category/provision he'd expect that's missing vs his own
     deliverable format.
   - **Length:** the full edition renders every provision verbatim
     (~359 pages for Maplewood). Is that the right artifact, or is the new
     Summary edition (tenancy summaries + provision index, ~15-25 pages —
     see `..._Compendium_Summary_2026-07-29.docx`) closer to what he
     circulates? Both formats now generate from the same engine.

2. **Refinance Diligence Package samples (2026-07-29)** — second
   Deliverables output, from the loan + lease layers:
   - `data/deliverables/Maplewood_Square_I_Refinance_Diligence_Package_2026-07-29.docx`
     (Thrivent facility maturing 11/15/2026 — timely test case)
   - `data/deliverables/Northcourt_Commons_Refinance_Diligence_Package_2026-07-29.docx`
     (3 facilities)

   Questions for Riley:
   - Sections: executive snapshot → facilities (balance/balloon/confidence
     notes) → loan provisions by lender category → lease rollover vs
     maturity → lender-relevant lease provisions → open items. Right order
     and coverage for what a lender's counsel requests?
   - The lender-relevant lease-provision net (SNDA/assignment/termination/
     co-tenancy/exclusives/renewals/guaranty) pulled 1,285 items for
     Northcourt — too broad? Which categories does he actually include?
   - Confidence annotations (e.g. "principal confidence:
     face_amount_unverified_no_words") — keep in the client artifact or
     internal-only?

3. **Valley West Office (OSBORN-3017) harvest** — still the only
   undelivered property build (noted during the merge queue).

4. **"Late Rent & Bad Debt Module" README packaging error** — the module
   ships the RE Tax README instead of its own (content unaffected; bd_
   tables verified merged in the master). Flag so he can fix the package.

5. **Possible stale SF in the master — Vixen Nails, Normandale
   (KAINC-1015)** (2026-08-01): master lease_lease says 868 SF, but the
   lease's expansion amendment states 868 existing + 442 expansion =
   **1,244 combined**, and the MRI rent roll (snapshot 2026-06-30) carries
   1,244. The Capactive re-import caught the disagreement. Master's 868
   looks pre-expansion — Riley to confirm and correct in the next master
   revision. (Same question may apply to other missed-SF tenancies where
   rent roll ≠ master: green_goods 1,672?, max_salon 2,574 — the latter is
   rent-math-derived, no SF statement in the instrument.)

6. **Cottage Grove roster questions** (2026-08-02, from the 33/33 pilot):
   is the "TD Retail Group" lease Highnorth Dispensary's paper (its 225
   master provisions have no staged doc; the TD Retail lease is unmapped)?
   And should the Ho King predecessor chain (1990 lease + 7 amendments,
   succeeded by Vangz via the 2024 assignment/8th amendment) be attached
   to the vangz tenancy for chain completeness?

7. **Reconcile flags from the re-import** (accumulating; each fired
   automatically as paper-vs-rent-roll disagreements): Best Buy MSII —
   paper 3/31/2036 vs rent roll 3/31/2031 (option horizon vs committed
   term?); Anytime Fitness CMX — paper 12/31/2024 vs current 12/31/2029
   (renewal); Kaleidoscope CMX — paper 10/31/2028 vs rent roll 10/31/2026;
   Rogan's Shoes CMX — two-suite split in master (2,746 + 50A) vs one
   combined lease (engine reads 7,000 combined); Cassini + Gentle
   Dentistry CGP — pre-amendment paper dates. None are errors in the
   engine; all are judgment calls on which source governs. Added
   2026-08-02: Ellie Family Services (Market Street) — master 2,666 SF,
   lease states 3,869 (expansion? gross vs usable?); Wink Eye, Edward
   Jones, Restaurants No Limit — paper dates predate current rent-roll
   expirations (renewals).

## Resolved

*(nothing yet)*
