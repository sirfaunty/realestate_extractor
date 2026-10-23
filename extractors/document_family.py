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

# a worksheet or summary PAGE inside a lease packet (a scanned lease often
# has the deal sheet and the management-system summary stapled behind it):
# the sheet's title or its table header on the page; the summary's report
# fields on the page (never the words "lease summary" alone, which a
# lease's own summary-of-terms page uses)
_NER_PAGE = re.compile(r'YRS\s+MOS\s+PSF\s+MONTHLY\s+ANNUALLY|'
                       r'YEAR\s+P\.?\s*S\.?\s*F\.?,?\s+MONTHLY\s+ANNUALLY|'
                       r'NET\s+RENT\s+OPERATING\s+COSTS\s+GROSS\s+RENT|'
                       r'TOTAL\s+(?:EFFECTIVE\s+)?CONTRACT\s+RENT|'
                       r'MOS\s+VACANT|CARRY\s+COST', re.I)
_SUMMARY_PAGE = re.compile(r'MRI\s+Lease\s+ID|From\s+Date\s+To\s+Date\s+Annual\s+Amount|'
                           r'PREPARED\s+BY\s*:.{0,80}LEASED\s+BY\s*:|'
                           r'LEASE\s+SUMMARY\s+DATE\s*:', re.I | re.S)   # its "- CONTINUED" pages


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


def worksheet_page(page_text: str) -> str | None:
    """NER_SHEET or LEASE_SUMMARY when this one page is a worksheet / summary
    page (by its title, table header or report fields), else None."""
    t = re.sub(r'\s+', ' ', page_text or '')
    if _CHECKLIST.search(t[:1500]):
        return None            # the packet cover lists the worksheets by name
    if _NER.search(t[:1500]) or _NER_PAGE.search(t):
        return NER_SHEET
    if _SUMMARY_PAGE.search(t):
        return LEASE_SUMMARY
    return None


def instrument_pages(pages: list) -> tuple[list, int]:
    """The pages of an instrument that are its own text: worksheet and
    summary pages stapled into the packet are dropped. (kept pages, dropped
    count)."""
    kept = [p for p in pages if not worksheet_page(p)]
    return kept, len(pages) - len(kept)


def archived_hint(filename: str) -> bool:
    """The file was filed under an archived / terminated / old folder (the
    folder names travel in the file name when a tree is flattened)."""
    return bool(_ARCHIVED.search(filename or ''))


@dataclass
class ChainSelection:
    text: str = ''
    texts: list = field(default_factory=list)       # one entry per kept document, in order
    kept: list = field(default_factory=list)        # indices into docs, in order
    dropped: dict = field(default_factory=dict)     # index -> reason
    flags: list = field(default_factory=list)


def assemble_rent_chain(docs: list) -> ChainSelection:
    """docs: [(filename, pages), ...] in document order, pages = the
    document's page texts (a single string is taken as one page).
    Worksheet / summary pages inside an instrument are dropped too (flag
    worksheet_pages_removed).
    Instruments make the chain; worksheets and summaries are dropped (a
    tenancy with only those keeps nothing: flag summaries_only -- a
    worksheet is not the lease). Instruments filed as archived / terminated
    stay in the chain but go FIRST: an archived folder often holds the
    original signed lease whose schedule still governs, while its tables,
    placed before the current paper, can no longer read as a later
    schedule that supersedes the answer (measured 2026-10-23: dropping them
    lost 7 correct rents; ordering them first kept the 48 new high answers)."""
    sel = ChainSelection()
    docs = [(f, [p] if isinstance(p, str) else list(p)) for f, p in docs]
    # a document is a worksheet / summary when EVERY page of it is one; a
    # lease with sheets stapled behind it is an instrument whose sheet
    # pages are stripped below (a short lease followed by a sheet must not
    # read as a sheet)
    # A sheet filed with its e-mail or approval cover (short pages) is still
    # a sheet: the whole document reads as one and only a dense residual
    # page (a lease page is 2,500+ characters) makes it an instrument.
    fam = []
    for _, p in docs:
        ws = [worksheet_page(x) for x in p if x.strip()]
        residual = [len(x) for x in p if x.strip() and not worksheet_page(x)]
        whole = rent_chain_family('\n'.join(p), len(p))
        if ws and (all(ws) or (whole != INSTRUMENT and max(residual, default=0) < 2500)):
            fam.append(NER_SHEET if NER_SHEET in ws else LEASE_SUMMARY)
        else:
            fam.append(INSTRUMENT)
    arch = [archived_hint(f) for f, _ in docs]
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
    texts, removed = [], 0
    for i in sel.kept:
        pages, n = instrument_pages(docs[i][1])
        removed += n
        texts.append('\n'.join(pages))
    if removed:
        sel.flags.append('worksheet_pages_removed')
    sel.texts = texts
    sel.text = '\n\n'.join(texts)
    return sel
