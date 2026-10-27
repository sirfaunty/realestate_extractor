"""
An instrument's own term as it prints it: when the term (or the extended
term) starts, when it ends, and how long it is, each with the words it came
from and the page they are on.

    instrument_term(pages, page_from, page_to) -> Term

    "extended for an additional period of five (5) years starting on April 1,
    2017 and ending on March 31, 2022"  -> start, end, 60 months
    "the Lease term shall expire on March 31, 2017"            -> end
    "ending on the last day of January, 2014"                  -> end 2014-01-31
    "for a term of ten (10) years"                             -> 120 months

Only the term itself: a rent row's "Beginning on September 1, 2005 and
continuing through August 31, 2006" is a rent period, and an option's
"option to extend the term for five (5) years" is not the term. Nothing is
computed here; a value the instrument does not print is None.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

from .instruments import DATE, _MON, _iso

_NUM = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
        'ten': 10, 'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20, 'twenty-five': 25, 'thirty': 30}
_N = r'([a-z\-]+)?\s*(?:\(\s*(\d{1,3})\s*\))?'

# start and end in one phrase, under term words
_RANGE = re.compile(r'(?i)(?:starting|commencing|beginning|effective|from)\s+(?:on\s+|as\s+of\s+)?(' + DATE + r')'
                    r'\s*,?\s*(?:and\s+)?(?:ending|expiring|terminating|continuing\s+through|through|to)\s+'
                    r'(?:on\s+)?(?:and\s+including\s+)?(' + DATE + r')')
_START = re.compile(r'(?i)(?:\bcommencement\s+date\b[^.$]{0,50}?(?:shall\s+be|is|means|:)\s*(?:the\s+)?'
                    r'|\bterm\b[^.$]{0,60}?(?:shall\s+)?(?:commence|begin)s?\s+on\s+(?:the\s+)?'
                    r'|\bfrom\s+and\s+after\s+)(' + DATE + r')')
_LAST_DAY = r'last\s+day\s+of\s+(' + _MON + r')\s*,?\s*(\d{4})'
_END = re.compile(r'(?i)(?:\bexpire[sd]?\s+on\s+(?:the\s+)?|\bexpiration\s+date\b[^.$]{0,60}?(?:shall\s+be|is|means|:)\s*(?:the\s+)?'
                  r'|\b(?:shall\s+)?(?:end|terminate)s?\s+on\s+(?:the\s+)?|\bending\s+on\s+(?:the\s+)?)'
                  r'(?:(' + DATE + r')|' + _LAST_DAY + r')')
_MONTHS = re.compile(r'(?i)\b(?:term|extended|extension|renewed)\b[^.$]{0,50}?\b(?:of|for)\s+'
                     r'(?:an?\s+(?:additional\s+|further\s+)?(?:period|term)\s+of\s+)?' + _N +
                     r'\s+(years?|months?)(?:\s*,?\s*(?:and\s+)?' + _N + r'\s+months?)?')
_TERM_CTX = re.compile(r'(?i)\bterm\b|\bextend|\bextension\b|\brenew')
_RENT_CTX = re.compile(r'(?i)\brent\b|\$\s?\d')
_OPTION_CTX = re.compile(r'(?i)\boptions?\b|\bright\s+to\s+(?:extend|renew)')


@dataclass
class Term:
    start: str | None = None
    end: str | None = None
    months: int | None = None
    start_quote: str = ''
    end_quote: str = ''
    months_quote: str = ''
    start_page: int | None = None
    end_page: int | None = None
    months_page: int | None = None


def _n(word, digits) -> int | None:
    if digits:
        return int(digits)
    return _NUM.get((word or '').lower())


def _month_end(mon: str, year: str) -> str | None:
    d = _iso(f'{mon} 1, {year}')
    if not d:
        return None
    y, m = int(d[:4]), int(d[5:7])
    return f'{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}'


def _is_term(flat: str, a: int, b: int) -> bool:
    """A term phrase: term words just before it, no rent amount or rent word
    closer than they are, and no option grant around it."""
    before = flat[max(0, a - 200):a]
    t = [m.end() for m in _TERM_CTX.finditer(before)]
    if not t:
        return False
    r = [m.end() for m in _RENT_CTX.finditer(before)]
    if r and max(r) > max(t):
        return False
    return not _OPTION_CTX.search(flat[max(0, a - 120):b + 40])


def instrument_term(pages: list, page_from: int, page_to: int) -> Term:
    out = Term()
    for n in range(page_from, page_to + 1):
        page = pages[n - 1] if 0 < n <= len(pages) else ''
        flat = re.sub(r'\s+', ' ', page or '')
        if not flat:
            continue
        if out.start is None or out.end is None:
            for m in _RANGE.finditer(flat):
                if not _is_term(flat, m.start(), m.end()):
                    continue
                s, e = _iso(m.group(1)), _iso(m.group(2))
                if s and e and s < e:
                    q = m.group(0).strip()
                    if out.start is None:
                        out.start, out.start_quote, out.start_page = s, q, n
                    if out.end is None:
                        out.end, out.end_quote, out.end_page = e, q, n
                    break
        if out.start is None:
            for m in _START.finditer(flat):
                if _OPTION_CTX.search(flat[max(0, m.start() - 120):m.end()]):
                    continue
                s = _iso(m.group(1))
                if s:
                    out.start, out.start_quote, out.start_page = s, m.group(0).strip(), n
                    break
        if out.end is None:
            for m in _END.finditer(flat):
                if not _is_term(flat, m.start(), m.end()):
                    continue
                e = _iso(m.group(1)) if m.group(1) else _month_end(m.group(2), m.group(3))
                if e:
                    out.end, out.end_quote, out.end_page = e, m.group(0).strip(), n
                    break
        if out.months is None:
            for m in _MONTHS.finditer(flat):
                if _OPTION_CTX.search(flat[max(0, m.start() - 120):m.end()]):
                    continue
                k = _n(m.group(1), m.group(2))
                if not k:
                    continue
                total = k * 12 if m.group(3).lower().startswith('year') else k
                extra = _n(m.group(4), m.group(5)) if (m.group(4) or m.group(5)) else 0
                total += extra or 0
                if 1 <= total <= 600:
                    out.months, out.months_quote, out.months_page = total, m.group(0).strip(), n
                    break
        if out.start and out.end and out.months:
            break
    if out.start and out.end and out.end < out.start:
        out.end = out.end_quote = None
        out.end_page = None
    return out
