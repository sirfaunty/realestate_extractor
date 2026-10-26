"""
Citations: every value the engine publishes carries the words it came from.

For a value (an amount, an area, a date, a clause heading) and the page the
engine says it is on, find where that value is PRINTED and return a short
verbatim quote of the page around it. When the cited page does not print it,
the other pages of the same document are tried and the cite moves to the page
that does. When nothing prints it, the quote is empty and the caller records
why (null_reason) instead of a quote.

A quote is always a slice of the page text exactly as read (no re-typing), so
a consumer can re-find it on the page: case, whitespace, curly quotes and
line-break hyphenation aside, it is a substring of that page.

    Cite(page_no, quote, state)      state: 'printed' | 'moved' | 'not_found'
    cite_number(pages, value, page_hint, also=())
    cite_date(pages, iso, page_hint)
    cite_text(pages, words, page_hint)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

QUOTE_CHARS = 140           # longest quote
CONTEXT_WORDS = int(__import__('os').environ.get('CAPACTIVE_QUOTE_WORDS', 0))   # words kept either side of the value

_MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
           'september', 'october', 'november', 'december']
_ORD_WORDS = {1: 'first', 2: 'second', 3: 'third', 4: 'fourth', 5: 'fifth', 6: 'sixth', 7: 'seventh',
              8: 'eighth', 9: 'ninth', 10: 'tenth', 11: 'eleventh', 12: 'twelfth', 13: 'thirteenth',
              14: 'fourteenth', 15: 'fifteenth', 16: 'sixteenth', 17: 'seventeenth', 18: 'eighteenth',
              19: 'nineteenth', 20: 'twentieth', 30: 'thirtieth'}
for _d in range(21, 30):
    _ORD_WORDS[_d] = 'twenty[- ]' + _ORD_WORDS[_d - 20]
_ORD_WORDS[31] = 'thirty[- ]first'

# a printed number, tolerating OCR spaces around its separators ("1 ,301", "$1,843. 08")
_NUM = re.compile(r'(?<![\d.])\d{1,3}(?:\s?,\s?\d{3})+(?:\s?\.\s?\d+)?(?!\d)|(?<![\d.,])\d+(?:\s?\.\s?\d+)?(?![\d,])')


@dataclass
class Cite:
    page: int | None            # 1-based page of the file, None when not found
    quote: str                  # verbatim slice of that page ('' when not found)
    state: str                  # 'printed' (on the cited page) | 'moved' (on another page) | 'not_found'
    basis: str = 'printed'      # 'printed' | 'computed: <inputs>'


def _number(tok: str) -> float | None:
    try:
        return float(re.sub(r'[\s,]', '', tok))
    except ValueError:
        return None


def _quote_around(text: str, start: int, end: int) -> str:
    """text[start:end] with up to CONTEXT_WORDS words either side on the same
    line, never past QUOTE_CHARS; always a slice of text. Short on purpose: a
    quote re-found on another reading of the page (a second OCR, a different
    column layout) must not depend on how far apart the page's columns sit."""
    ls = text.rfind('\n', 0, start) + 1
    le = text.find('\n', end)
    le = len(text) if le < 0 else le
    a, b = start, end
    for _ in range(CONTEXT_WORDS):
        k = text.rfind(' ', ls, max(ls, a - 1))
        na = ls if k < 0 else k + 1
        if na >= a or end - na > QUOTE_CHARS:
            break
        a = na
    for _ in range(CONTEXT_WORDS):
        k = text.find(' ', min(le, b + 1), le)
        nb = le if k < 0 else k
        if nb <= b or nb - a > QUOTE_CHARS:
            break
        b = nb
    return text[a:b].strip()


def _order(n_pages: int, hint: int | None) -> list[int]:
    """The cited page first, then its neighbours outward, then the rest."""
    if not hint or not 1 <= hint <= n_pages:
        return list(range(1, n_pages + 1))
    out, d = [hint], 1
    while len(out) < n_pages:
        for p in (hint - d, hint + d):
            if 1 <= p <= n_pages:
                out.append(p)
        d += 1
    return out


def _search(pages: list, hint: int | None, finder) -> Cite:
    for p in _order(len(pages), hint):
        text = pages[p - 1] or ''
        span = finder(text)
        if span:
            return Cite(p, _quote_around(text, *span), 'printed' if p == hint else 'moved')
    return Cite(None, '', 'not_found')


def _num_finder(value: float):
    def find(text):
        for m in _NUM.finditer(text):
            v = _number(m.group(0))
            if v is not None and abs(v - value) <= 0.005 + 1e-9 * abs(value):
                return m.start(), m.end()
        return None
    return find


def cite_number(pages: list, value, page_hint: int | None = None, also=()) -> Cite:
    """Where `value` is printed. `also` = [(other_value, basis)]: values the
    number is computed from (an annual amount for a monthly one), tried only
    when the value itself is printed nowhere; the Cite then carries that basis."""
    if value is None:
        return Cite(None, '', 'not_found')
    try:
        v = float(value)
    except (TypeError, ValueError):
        return Cite(None, '', 'not_found')
    c = _search(pages, page_hint, _num_finder(v))
    if c.page is not None:
        return c
    for other, basis in also:
        if other is None:
            continue
        c = _search(pages, page_hint, _num_finder(float(other)))
        if c.page is not None:
            c.basis = f'computed: {basis}'
            return c
    return Cite(None, '', 'not_found')


def _date_patterns(d: date) -> list:
    mon = _MONTHS[d.month - 1]
    mon_rx = f'(?:{mon}|{mon[:3]}\\.?{"|sept\\.?" if d.month == 9 else ""})'
    day = f'0?{d.day}(?:st|nd|rd|th)?'
    yr = f'{d.year}'
    yy = f'{d.year % 100:02d}'
    out = [
        rf'\b{mon_rx}\s+{day}\s*,?\s*{yr}\b',                                   # March 1, 2014
        rf'\b{day}\s+(?:day\s+of\s+)?{mon_rx}\s*,?\s*{yr}\b',                    # 1st day of March, 2014
        rf'(?<!\d)0?{d.month}\s*[/.\-]\s*0?{d.day}\s*[/.\-]\s*(?:{yr}|{yy})(?!\d)',  # 3/1/14, 03/01/2014
        rf'\b{yr}-{d.month:02d}-{d.day:02d}\b',
    ]
    if d.day in _ORD_WORDS:
        out.append(rf'\b{_ORD_WORDS[d.day]}\s+(?:\(\s*{d.day}(?:st|nd|rd|th)?\s*\)\s+)?day\s+of\s+{mon_rx}\s*,?\s*{yr}\b')
    return [re.compile(p, re.I) for p in out]


def cite_date(pages: list, iso, page_hint: int | None = None) -> Cite:
    """Where the date is printed (written or numeric forms)."""
    if not iso:
        return Cite(None, '', 'not_found')
    try:
        d = iso if isinstance(iso, date) else date.fromisoformat(str(iso)[:10])
    except ValueError:
        return Cite(None, '', 'not_found')
    pats = _date_patterns(d)

    def find(text):
        for rx in pats:
            m = rx.search(text)
            if m:
                return m.start(), m.end()
        return None
    return _search(pages, page_hint, find)


def cite_text(pages: list, words: str, page_hint: int | None = None) -> Cite:
    """Where a short phrase (a clause heading) is printed, word for word up to
    punctuation, spacing and case."""
    toks = re.findall(r'[A-Za-z0-9]+', words or '')[:12]
    if not toks:
        return Cite(None, '', 'not_found')
    rx = re.compile(r'\b' + r'[\W_]+'.join(map(re.escape, toks)) + r'\b', re.I)

    def find(text):
        m = rx.search(text)
        return (m.start(), m.end()) if m else None
    return _search(pages, page_hint, find)
