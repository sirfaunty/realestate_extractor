"""
Instruments: the events of a tenancy, one per instrument, each dated by ITS
OWN date.

A scanned lease file is often a packet: the lease, its amendments, an
assignment, a guaranty, estoppels, a commencement letter, filed together.
split_instruments(pages) finds where each instrument starts (its title at
the top of a page: "SECOND AMENDMENT TO LEASE", "ASSIGNMENT AND ASSUMPTION
OF LEASE", "GUARANTY", ...), classifies it, and reads its own date:

  - the date it is made / entered into / effective as of, in its opening;
  - never a date it RECITES ("amends the Lease dated June 1, 2009" is the
    original lease's date, kept as a recited date, not this instrument's);
  - failing an opening date, the latest date in its signature block, and
    the basis says so.

    split_instruments(pages) -> [Instrument]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .rent_derivation import parse_date

_MON = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?'
_ORDW = (r'(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|'
         r'fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|twenty[- ]\w+|thirtieth|'
         r'thirty[- ]first)')
# a written date: "March 1, 2014", "1st day of March, 2014", "the first day of March 2014", "3/1/14"
DATE = (r'(?:' + _MON + r'\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4}'
        r'|\d{1,2}(?:st|nd|rd|th)?\s+day\s+of\s+' + _MON + r'\s*,?\s*\d{4}'
        r'|' + _ORDW + r'\s+day\s+of\s+' + _MON + r'\s*,?\s*\d{4}'
        r'|\d{1,2}/\d{1,2}/\d{2,4})')
_DATE_RX = re.compile(DATE, re.I)

_ORDINALS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7,
             'eighth': 8, 'ninth': 9, 'tenth': 10, 'eleventh': 11, 'twelfth': 12}
_WORD_DAY = {w: i for i, w in enumerate(['zeroth', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth',
                                         'seventh', 'eighth', 'ninth', 'tenth', 'eleventh', 'twelfth',
                                         'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth',
                                         'eighteenth', 'nineteenth', 'twentieth'])}

# (event type, title pattern), most specific first. A title is matched near
# the top of a page.
TYPES = [
    ('NER worksheet', r'NET\s+EFFECTIVE\s+RENT\s+SHEET'),
    ('lease summary / abstract', r'LEASE\s+(?:SUMMARY|ABSTRACT)'),
    ('SNDA', r'SUBORDINATION|NON-?\s*DISTURBANCE'),
    ('estoppel', r'ESTOPPEL'),
    ('memorandum of lease', r'MEMORANDUM\s+OF\s+LEASE|SHORT\s+FORM\s+LEASE'),
    ('guaranty consent', r'CONSENT\s+(?:AND\s+REAFFIRMATION\s+)?OF\s+GUARANTOR|GUARANTOR.S\s+CONSENT'),
    ('guaranty', r'\bGUARANT(?:Y|EE)\b'),
    ('assignment / assumption', r'ASSIGNMENT|ASSUMPTION'),
    ('termination / surrender', r'TERMINATION|SURRENDER|CANCELLATION\s+OF\s+LEASE'),
    ('sublease', r'SUB-?\s*LEASE'),
    ('license agreement', r'LICENSE\s+AGREEMENT|LICENSE\b'),
    ('commencement', r'COMMENCEMENT\s+(?:DATE\s+)?(?:AGREEMENT|LETTER|CERTIFICATE|MEMORANDUM)|'
                     r'(?:CONFIRMATION|DECLARATION)\s+OF\s+(?:LEASE\s+)?COMMENCEMENT'),
    ('extension / renewal exercised', r'(?:EXTENSION|RENEWAL)\s+(?:AGREEMENT|OF\s+LEASE|AMENDMENT)|'
                                      r'NOTICE\s+OF\s+(?:EXERCISE|RENEWAL)|LEASE\s+EXTENSION'),
    ('expansion', r'EXPANSION\s+(?:AGREEMENT|AMENDMENT)'),
    ('relocation', r'RELOCATION\s+(?:AGREEMENT|AMENDMENT)'),
    ('rent deferral', r'(?:RENT\s+)?DEFERRAL\s+AGREEMENT'),
    ('amendment', r'\bAMENDMENT\b|\bMODIFICATION\b'),
    ('consent / approval', r'\bCONSENT\b'),
    ('original lease', r'^\s*(?:SHOPPING\s+CENTER\s+|RETAIL\s+|OFFICE\s+|STANDARD\s+|COMMERCIAL\s+|INDUSTRIAL\s+)?'
                       r'LEASE(?:\s+AGREEMENT)?\s*$|^\s*(?:THIS\s+)?LEASE\s+(?:AGREEMENT\s+)?(?:IS\s+)?MADE|'
                       r'^\s*LEASE\s+AGREEMENT\s+(?:between|by\s+and\s+between|dated|made)\b'),
]
_TYPES = [(t, re.compile(rx, re.I | re.M)) for t, rx in TYPES]
TITLE_LINES = 6           # an instrument's title sits in the first lines of its first page

# the instrument's own date, in its opening
# ("dated" alone is almost always a recital, "the Lease dated ...": it counts
# only as "This <instrument> ... dated")
_OWN = [re.compile(r'(?:made|entered\s+into|executed)\s+(?:and\s+entered\s+into\s+)?(?:as\s+of\s+|effective\s+)?'
                   r'(?:the\s+|this\s+)?(' + DATE + ')', re.I),
        re.compile(r'\bthis\s+(?:[\w-]+\s+){1,4}?dated\s+(?:as\s+of\s+)?(?:the\s+)?(' + DATE + ')', re.I),
        re.compile(r'(?:effective|as\s+of)\s+(?:the\s+|this\s+)?(' + DATE + ')', re.I),
        re.compile(r'\bthis\s+(' + DATE + ')', re.I)]
# a signing date: written beside signature words
_DAY_OF = (r'(?:\d{1,2}(?:st|nd|rd|th)?|' + _ORDW + r')\s+day\s+of\s+' + _MON + r'\s*,?\s*\d{4}')
_SIGNED = re.compile(r'(?:\bDated?\s*:|\bDate\s+(?:signed|executed)\s*:?|\bSigned\s+on|\bExecuted\s+on)\s*'
                     r'(?:the\s+)?(' + DATE + r')|\b(?:on\s+)?this\s+(' + _DAY_OF + ')', re.I)
# a RECITED date: the earlier instrument's ("the Lease dated June 1, 2009")
_RECITED = re.compile(r'\b(?:that\s+certain|the|said|original|such)\s+(?:\w+\s+){0,3}?'
                      r'(?:lease|amendment|agreement|guaranty)\s+(?:\w+\s+){0,3}?(?:dated|made|executed|entered\s+into)'
                      r'\s+(?:as\s+of\s+)?(?:the\s+)?$', re.I)     # never "THIS Lease ... made as of"


@dataclass
class Instrument:
    event_type: str
    ordinal: int | None          # 2 for "SECOND AMENDMENT"
    title: str                   # the title line as printed
    page_from: int
    page_to: int
    date: str | None             # ISO, the instrument's own date
    date_quote: str = ''         # verbatim words the date was read from
    date_page: int | None = None
    date_basis: str = ''         # 'opening' | 'signature block' | '' (null)
    date_null_reason: str = ''
    recited: list = field(default_factory=list)    # [(iso, quote)] dates it recites


def _iso(s: str) -> str | None:
    s = re.sub(r'\s+', ' ', s or '').strip()
    m = re.match(r'(?i)(' + _ORDW + r')\s+day\s+of\s+(' + _MON + r')\s*,?\s*(\d{4})', s)
    if m:
        word = m.group(1).lower().replace('-', ' ').split()
        n = _WORD_DAY.get(word[-1], 0) + (20 if word[0] == 'twenty' and len(word) > 1 else 0) \
            + (30 if word[0] == 'thirty' and len(word) > 1 else 0)
        if word[0] == 'thirtieth':
            n = 30
        s = f'{m.group(2)} {n}, {m.group(3)}'
    s = re.sub(r'(?i)(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+(' + _MON + r')\s*,?\s*(\d{4})', r'\2 \1, \3', s)
    s = re.sub(r'(?i)(\d)(?:st|nd|rd|th)\b', r'\1', s)
    d = parse_date(s)
    return d.isoformat() if isinstance(d, date) else None


def classify(title_block: str) -> tuple[str | None, str]:
    """(event type, title line) for the first lines of a page, or (None, '')."""
    lines = [ln.strip() for ln in (title_block or '').splitlines() if ln.strip()][:TITLE_LINES]
    for ln in lines:
        if len(ln) > 120:
            continue
        # a page headed "EXHIBIT D" is an attachment of the instrument it is filed in,
        # even when its title names a form ("COMMENCEMENT DATE CERTIFICATE")
        if re.match(r'(?i)\s*(?:EXHIBIT|RIDER|ATTACHMENT)\s*["“]?[A-Z0-9]{1,3}\b', ln):
            return None, ''
        for t, rx in _TYPES:
            if rx.search(ln) and (ln.isupper() or t == 'original lease' or len(ln) <= 60):
                return t, ln
    return None, ''


def _ordinal(title: str) -> int | None:
    m = re.search(r'(?i)\b(' + '|'.join(_ORDINALS) + r')\b', title or '')
    if m:
        return _ORDINALS[m.group(1).lower()]
    m = re.search(r'(?i)\b(\d{1,2})(?:st|nd|rd|th)\s+(?:amendment|modification)', title or '')
    return int(m.group(1)) if m else None


def _own_date(pages: list, p_from: int, p_to: int):
    """(iso, quote, page, basis, recited) for one instrument."""
    recited = []
    opening = '\n'.join(pages[p_from - 1:min(p_to, p_from + 1)])[:3000]
    flat = re.sub(r'\s+', ' ', opening)
    for m in _DATE_RX.finditer(flat):
        if _RECITED.search(flat[max(0, m.start() - 80):m.start()]):
            iso = _iso(m.group(0))
            if iso:
                recited.append((iso, flat[max(0, m.start() - 60):m.end()].strip()))
    for rx in _OWN:
        for m in rx.finditer(flat):
            if _RECITED.search(flat[max(0, m.start(1) - 80):m.start(1)]):
                continue
            iso = _iso(m.group(1))
            if iso:
                return iso, m.group(0).strip(), p_from, 'opening', recited
    # the signature block: the latest date written beside signature words
    # ("Date: 4/9/12", "Dated:", "By: ... Its: ...", a notary's "on this 9th
    # day of April, 2012") on the instrument's last two pages. A bare date
    # there is usually a term date (an expiration), never a signing date.
    tail = re.sub(r'\s+', ' ', '\n'.join(pages[max(p_from - 1, p_to - 2):p_to]))
    dates = []
    for m in _SIGNED.finditer(tail):
        iso = _iso(m.group(1) or m.group(2))
        if iso:
            dates.append((iso, m.group(0).strip()))
    if dates:
        d, q = max(dates)
        return d, q, p_to, 'signature block', recited
    return None, '', None, '', recited


def split_instruments(pages: list) -> list[Instrument]:
    starts = []
    for n, page in enumerate(pages, 1):
        t, title = classify(page or '')
        if t:
            starts.append((n, t, title))
    if not starts and any((p or '').strip() for p in pages):
        starts = [(1, 'lease document (unclassified)', '')]
    out = []
    for k, (n, t, title) in enumerate(starts):
        end = (starts[k + 1][0] - 1) if k + 1 < len(starts) else len(pages)
        iso, quote, dpage, basis, recited = _own_date(pages, n, end)
        out.append(Instrument(t, _ordinal(title) if t in ('amendment', 'extension / renewal exercised') else None,
                              title, n, end, iso, quote, dpage, basis,
                              '' if iso else 'no date printed in its opening or signature block', recited))
    return out
