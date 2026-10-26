"""
Lease depth: every article of an instrument verbatim (layer A) and a
category sweep across its text (layer B), each with the file page it is on.

Layer A: an instrument's top-level divisions in order. Leases number them
several ways ("ARTICLE 3" / "ARTICLE III" / "ARTICLE THREE", "SECTION 3",
"3." with a title, "3.1"); the outermost style the document actually uses is
its article level. Each article's body is the text from its heading to the
next heading, verbatim, with the page it starts and ends on. Table-of-
contents pages (dotted leaders, a column of page numbers) are skipped so the
operative clause is captured, not its TOC entry.

Layer B: one row per (category, page) where a category's language appears
(percentage rent, exclusive use, co-tenancy, condemnation, renewal option,
assignment, guaranty, holdover, ...): a verbatim excerpt around the first hit
on that page.

    split_articles(pages)  -> [Article]
    sweep(pages)           -> [Hit]
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORDNUM = {w: i for i, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen '
    'seventeen eighteen nineteen twenty'.split())}
_ROMAN = re.compile(r'^(?=[IVXL]+$)L?X{0,3}(IX|IV|V?I{0,3})$')


def _roman(s: str) -> int | None:
    vals = {'I': 1, 'V': 5, 'X': 10, 'L': 50}
    if not s or not _ROMAN.match(s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = vals[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def _ordinal(tok: str) -> int | None:
    t = tok.strip().rstrip('.:')
    if t.isdigit():
        return int(t)
    r = _roman(t.upper())
    if r:
        return r
    return _WORDNUM.get(t.lower())


# heading styles, outermost first; each yields (ordinal token, rest of line)
_STYLES = [
    ('article', re.compile(r'^[ \t>*•\-]{0,6}ARTICLE\s+([0-9]{1,2}|[IVXL]{1,7}|[A-Za-z]{3,9})\b[.:\-\s]*(.*)$',
                           re.M | re.I)),
    ('section', re.compile(r'^[ \t>*•\-]{0,6}SECTION\s+([0-9]{1,2})(?!\.\d)\b[.:\-\s]*(.*)$', re.M | re.I)),
    ('numbered', re.compile(r'^[ \t>*•\-]{0,6}([0-9]{1,2})\.(?!\d)[ \t]+([A-Z][A-Za-z ,&/\'\-]{2,60}?)[.:]?(?:[ \t]|$)(.*)$',
                            re.M)),
]
_TOC_LINE = re.compile(r'(\.{4,}|_{4,}|\s{6,})\s*\d{1,3}\s*$')


@dataclass
class Article:
    ordinal: int
    style: str              # article | section | numbered
    heading: str            # the heading line as printed
    title: str              # its title words, if any ("PREMISES AND TERM")
    body: str               # verbatim text from the heading to the next heading
    page_from: int
    page_to: int


@dataclass
class Hit:
    category: str
    page: int
    excerpt: str            # verbatim slice of the page


def _is_toc(page: str) -> bool:
    lines = [ln for ln in (page or '').splitlines() if ln.strip()]
    if len(lines) < 6:
        return False
    toc = sum(1 for ln in lines if _TOC_LINE.search(ln))
    return toc >= 5 and toc / len(lines) >= 0.3 or bool(re.search(r'^\s*TABLE OF CONTENTS\s*$', page, re.M | re.I))


def _heads(flat: str, style_rx) -> list:
    out = []
    for m in style_rx.finditer(flat):
        n = _ordinal(m.group(1))
        if n is None or n == 0 or n > 99:
            continue
        out.append((m.start(), n, m))
    return out


def _sequence(heads: list) -> list:
    """The longest run of headings whose numbers go 1, 2, 3 ... in order,
    tolerating one number OCR lost or misread ("ARTICLE 11" read as
    "ARTICLE 1") between two that fit. A cross-reference ("Article 7" in a
    sentence) does not start a line, so it never extends a run."""
    runs = []
    for h in heads:
        for r in runs:
            last = r[-1][1]
            if h[1] in (last + 1, last + 2):
                r.append(h)
        if h[1] == 1:
            runs.append([h])
    return max(runs, key=len) if runs else []


_SECTION_ONE = re.compile(r'^[ \t>*•\-]{0,6}Section\s+1[.,:]\s', re.M | re.I)


def _with_section_restarts(flat: str, run: list) -> list:
    """Articles whose heading OCR lost still begin at 'Section 1.': a restart
    of the section numbering is an article boundary. Fill the gaps of an
    ARTICLE run with those restarts, numbered in order."""
    starts = [m.start() for m in _SECTION_ONE.finditer(flat)]
    if not run or len(starts) <= len(run):
        return run
    own = set()                 # each heading's own first 'Section 1.' belongs to it
    for at, _, _ in run:
        nxt = next((s for s in starts if s > at), None)
        if nxt is not None and nxt - at <= 300:
            own.add(nxt)
    heads = sorted([(at, n, m, True) for at, n, m in run]
                   + [(s, None, None, False) for s in starts if s not in own])
    out, n = [], 0
    for at, num, m, is_head in heads:
        n = num if is_head and num else n + 1
        out.append((at, n, m))
    return out


def split_articles(pages: list, min_run: int = 3) -> list[Article]:
    """The instrument's articles in order (see module doc). [] when no style
    yields a run of at least `min_run` headings numbered 1, 2, 3 ..."""
    usable = [('' if _is_toc(p) else (p or '')) for p in pages]
    starts, pos = [], 0
    for p in usable:
        starts.append(pos)
        pos += len(p) + 1
    flat = '\n'.join(usable)

    def page_at(i):
        k = 0
        for j, s in enumerate(starts):
            if s <= i:
                k = j
        return k + 1

    for style, rx in _STYLES:
        run = _sequence(_heads(flat, rx))
        if len(run) < min_run:
            continue
        if style == 'article':
            run = _with_section_restarts(flat, run)
        out = []
        for k, (at, n, m) in enumerate(run):
            end = run[k + 1][0] if k + 1 < len(run) else len(flat)
            body = flat[at:end].strip()
            if m is None:           # a boundary found at a 'Section 1.' restart: no heading printed
                out.append(Article(n, 'section_restart', '', '', body, page_at(at), page_at(max(at, end - 1))))
                continue
            line = m.group(0).strip()
            title = (m.group(2) or '').strip().rstrip('.:')
            if style == 'article' and not title:
                nxt = flat[m.end():m.end() + 120].lstrip('\n').split('\n', 1)[0].strip()
                title = nxt if nxt.isupper() and len(nxt) <= 80 else ''
            out.append(Article(n, style, line, title, body, page_at(at), page_at(max(at, end - 1))))
        return out
    return []


# Layer B categories: (name, pattern). Patterns are deliberately specific: a
# hit is a provision of that kind, not every use of a common word.
CATEGORIES = [
    ('Percentage rent', r'percentage rent|gross sales in excess of|breakpoint'),
    ('Exclusive use', r'exclusive (?:right|use)|shall not (?:lease|permit)[^.]{0,80}(?:for the (?:sale|operation)|to operate)'),
    ('Continuous operation / go-dark', r'continuous(?:ly)? (?:operat|open)|go dark|cease (?:to )?operat'),
    ('Co-tenancy / kick-out', r'co-?tenancy|kick-?out|anchor tenant[^.]{0,60}(?:ceases|vacates|closes)'),
    ('Condemnation / eminent domain', r'condemnation|eminent domain|taking by'),
    ('Renewal / extension option', r'option to (?:renew|extend)|renewal (?:term|option|period)|extension (?:term|option|period)'),
    ('ROFR / ROFO', r'right of first (?:refusal|offer|negotiation)'),
    ('Rent abatement', r'abate(?:ment)?|free rent|rent concession'),
    ('CAM / operating cost cap', r'common area (?:maintenance|charges|expenses)|operating (?:costs|expenses)|(?:cap|increase)[^.]{0,40}not (?:exceed|increase) more than'),
    ('Assignment / subletting', r'assign(?:ment)?[^.]{0,40}(?:sublet|subleas)|sublet|subleas'),
    ('Guaranty', r'guarant(?:y|ee|or)'),
    ('Parking', r'parking'),
    ('Hazardous materials', r'hazardous (?:materials|substances)|environmental law'),
    ('Relocation / substitution', r'relocat(?:e|ion) (?:the )?(?:premises|tenant)|substitute premises'),
    ('TI / allowance', r'tenant improvement allowance|improvement allowance|allowance of \$|landlord.s work'),
    ('Signage', r'\bsign(?:s|age)\b'),
    ('Security deposit', r'security deposit'),
    ('Holdover', r'hold(?:s|ing)? over|holdover|tenant at sufferance'),
    ('Insurance', r'insurance'),
    ('Late fee / default interest', r'late (?:charge|fee)|default rate|interest at the rate'),
    ('SNDA / estoppel', r'subordinat|estoppel|non-?disturbance|attornment'),
    ('Permitted use', r'permitted use|use of (?:the )?(?:leased )?premises|shall use the'),
    ('Early termination', r'early termination|right to terminate|terminate this lease'),
]
_CATS = [(name, re.compile(rx, re.I)) for name, rx in CATEGORIES]
EXCERPT = 300


def sweep(pages: list) -> list[Hit]:
    """One Hit per (category, page) with a verbatim excerpt around the first
    match on that page; TOC pages are skipped."""
    out = []
    for n, page in enumerate(pages, 1):
        if not page or _is_toc(page):
            continue
        for name, rx in _CATS:
            m = rx.search(page)
            if not m:
                continue
            a = max(0, m.start() - EXCERPT // 3)
            b = min(len(page), a + EXCERPT)
            while a > 0 and not page[a - 1].isspace() and a < m.start():
                a += 1
            while b < len(page) and not page[b].isspace() and b > m.end():
                b -= 1
            out.append(Hit(name, n, page[a:b].strip()))
    return out
