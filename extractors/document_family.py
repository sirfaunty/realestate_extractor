"""Which documents in a tenancy's file are rent EVIDENCE.

A landlord's folder for one tenancy holds more than instruments: the
property-management system's lease summary report, the leasing team's
net-effective-rent sheet (a deal worksheet with a rent/CAM/gross table per
period), approval e-mails, and the tenancy's earlier leases filed as
archived or terminated. Only instruments (lease, amendments, assignments,
renewal letters) say what the rent IS; the worksheets restate it, often for
a deal that changed before signing, and an archived lease's table belongs to
a term that ended. Fed into one rent derivation they appear as rent tables
that cannot be placed in time, which (correctly) blocks a high-confidence
answer -- on real portfolios more than half the files were worksheets, so
every answer was blocked (2026-10-23).

The family is decided from the document's own text; the file name and
folder are hints, kept beside the verdict (archived / terminated folders).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# the packet cover sheet a property manager staples on a scanned lease: it
# LISTS the worksheet names as checklist items, so it must be read first
_CHECKLIST = re.compile(r'LEASE\s+DOCUMENT\s+CHECKLIST', re.I)
# "NET EFFECTIVE RENT SHEET", with the letter-spacing and dropped letters
# that OCR makes of a bold header ("NETE. :CTIVE ENT SHEET", "Ni:T EFFECTIVE")
_NER = re.compile(r'N\s*[Ii:]?\s*[EI]?\s*[TY]\s*E?\W{0,3}'
                  r'(?:E\s*F\s*F\s*E\s*C\s*T\s*I\s*V\s*E|\.?\s*:?\s*C\s*T\s*I\s*V\s*E)'
                  r'\W{0,3}(?:R\s*E\s*N\s*T|E\s*N\s*T)\W{0,3}S\s*H\s*E\s*E\s*T', re.I)
# the worksheet's own column headers and totals, for a sheet whose title
# line OCR lost entirely
_NER_BODY = re.compile(r'YEAR\s+P\.?\s*S\.?\s*F\.?\s+MONTHLY\s+ANNUALLY|'
                       r'NET\s+RENT\s+OPERATING\s+COSTS\s+GROSS\s+RENT|'
                       r'TOTAL\s+(?:EFFECTIVE\s+)?CONTRACT\s+RENT|'
                       r'MOS\s+VACANT|CARRY\s+COST', re.I)
# the management system's lease summary report: its title plus the report
# header fields. A lease's own "Summary of Basic Lease Terms" page has the
# title words but never the report fields.
_SUMMARY = re.compile(r'LEASE\s+SUMMARY', re.I)
_SUMMARY_FIELDS = re.compile(r'MRI\s+Lease\s+ID|From\s+Date\s+To\s+Date|'
                             r'PREPARED\s+BY\s*:.{0,80}LEASED\s+BY\s*:', re.I | re.S)
# case-sensitive on purpose: the folder word ends where the next CamelCase
# word begins ("ArchivedLeases"), and "Archer" / "determination" never match
_ARCHIVED = re.compile(r'(?<![A-Za-z])(?:[Aa]rchived?|ARCHIVED?|[Tt]erminated|TERMINATED|'
                       r'[Ss]uperseded|[Oo]ld\s+[Ll]eases?|OLD\s+LEASES?|[Ee]xpired|EXPIRED)(?![a-z])')

INSTRUMENT = 'instrument'
NER_SHEET = 'ner_sheet'
LEASE_SUMMARY = 'lease_summary'


def rent_chain_family(text: str, page_count: int | None = None) -> str:
    """INSTRUMENT, NER_SHEET or LEASE_SUMMARY from the document's opening
    text (the first two or three pages are enough; pass the whole text
    freely). page_count, when known, keeps a long lease that merely quotes
    a worksheet header from being read as one."""
    head = re.sub(r'\s+', ' ', text or '')[:8000]
    if _CHECKLIST.search(head[:1500]):
        return INSTRUMENT          # a scanned packet: cover sheet, then the lease
    if _NER.search(head[:6000]):
        return NER_SHEET
    if _NER_BODY.search(head[:3000]) and (page_count is None or page_count <= 6):
        return NER_SHEET
    if (_SUMMARY.search(head[:2500]) and _SUMMARY_FIELDS.search(head[:3000])
            and (page_count is None or page_count <= 10)):
        return LEASE_SUMMARY
    return INSTRUMENT


def archived_hint(filename: str) -> bool:
    """The file was filed under an archived / terminated / old folder (the
    folder names travel in the file name when a tree is flattened)."""
    return bool(_ARCHIVED.search(filename or ''))


@dataclass
class ChainSelection:
    text: str = ''
    kept: list = field(default_factory=list)        # indices into docs, in order
    dropped: dict = field(default_factory=dict)     # index -> reason
    flags: list = field(default_factory=list)


def assemble_rent_chain(docs: list) -> ChainSelection:
    """docs: [(filename, text, page_count or None), ...] in document order.
    Instruments make the chain; worksheets and summaries are dropped (a
    tenancy with only those keeps nothing: flag summaries_only -- a
    worksheet is not the lease). Instruments filed as archived / terminated
    stay in the chain but go FIRST: an archived folder often holds the
    original signed lease whose schedule still governs, while its tables,
    placed before the current paper, can no longer read as a later
    schedule that supersedes the answer (measured 2026-10-23: dropping them
    lost 7 correct rents; ordering them first kept the 48 new high answers)."""
    sel = ChainSelection()
    fam = [rent_chain_family(t, n) for _, t, n in docs]
    arch = [archived_hint(f) for f, _, _ in docs]
    instruments = [i for i, f in enumerate(fam) if f == INSTRUMENT]
    current = [i for i in instruments if not arch[i]]
    older = [i for i in instruments if arch[i]]
    for i, f in enumerate(fam):
        if f != INSTRUMENT:
            sel.dropped[i] = f
    sel.kept = older + current
    if sel.dropped:
        sel.flags.append('summaries_ignored')
    if older and current:
        sel.flags.append('archived_first')
    if older and not current:
        sel.flags.append('archived_only')
    if docs and not instruments:
        sel.flags.append('summaries_only')
    sel.text = '\n\n'.join(docs[i][1] for i in sel.kept)
    return sel
