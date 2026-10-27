"""
Renewal / extension options: every option grant in an instrument, as an
option register row, each value with the words it came from.

    find_options(pages) -> [OptionGrant]

An OptionGrant is one printed grant ("Tenant shall have two (2) options to
extend the Term for five (5) years each"): how many options, how long each,
the notice window ("not more than nine (9) and not less than six (6) months
before the expiration"), the rent basis (fair market / a percent of fair
market / a stated schedule / CPI / not stated), and whether the text
deletes or voids an option rather than granting one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_NUMW = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
         'ten': 10, 'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20, 'thirty': 30, 'forty-five': 45,
         'sixty': 60, 'ninety': 90, 'a': 1, 'an': 1, 'one hundred twenty': 120, 'one hundred eighty': 180,
         'two hundred seventy': 270}
_N = r'(?:\d{1,3}|one hundred (?:twenty|eighty)|two hundred seventy|forty-five|[a-z]+)'
_PAREN = r'(?:\s*\(\s*\d{1,3}\s*\))?'

_GRANT = re.compile(
    r'(?:have|has|grant(?:s|ed)?|given|be\s+entitled\s+to)\s+(?:to\s+)?(?:(?:the\s+)?Tenant\s+)?(?:the\s+)?(' + _N + r')' + _PAREN +
    r'\s+(?:(?:successive|consecutive|additional|separate|further|renewal|extension)\s+)*'
    r'(?:options?|rights?|terms?)\b'
    r'(?:\s*\([^)]{0,40}\))?(?:\s+(?:each\s+)?(?:to|for|of))?[^.;]{0,160}?'
    r'(?:for|of)\s+(?:a\s+(?:period|term)\s+of\s+|an?\s+additional\s+)?(' + _N + r')' + _PAREN +
    r'\s*(?:additional\s+|consecutive\s+)?(years?|months?)', re.I)
# the count after the verb: "to renew this Lease for three (3) successive
# additional terms of five (5) years each", "for two (2) five (5) year terms"
_GRANT_FOR = re.compile(
    r'(?:option|right)s?\b[^.;]{0,60}?to\s+(?:renew|extend)[^.;]{0,80}?\bfor\s+(' + _N + r')' + _PAREN +
    r'\s+(?:(?:successive|consecutive|additional|separate|further|renewal|extension)\s+)*'
    r'(?:(?:terms?|periods?)\s+of\s+(' + _N + r')' + _PAREN + r'\s*(years?|months?)'
    r'|(' + _N + r')' + _PAREN + r'[\s-]*(years?|months?)\s+(?:terms?|periods?))', re.I)
_DELETED = re.compile(r'(?:renewal|extension)\s+options?\b[^.]{0,120}?(?:(?:is|are)\s+hereby\s+'
                      r'(?:deleted|terminated|void|null\s+and\s+void|of\s+no\s+(?:further\s+)?force)|'
                      r'(?:is|are)\s+deleted\s+from)', re.I)    # a conditional 'shall be void if ...' is no deletion
_NOTICE = re.compile(r'(?:not\s+(?:more|less|later|earlier)\s+than|at\s+least|no\s+(?:later|earlier|more|less)\s+than|'
                     r'within)\s+(' + _N + r')' + _PAREN + r'\s*(days?|months?)?'
                     r'(?:[^.;]{0,60}?(?:and|nor)\s+(?:not\s+)?(?:more|less|later|earlier)\s+than\s+(' + _N + r')' +
                     _PAREN + r'\s+(days?|months?))?[^.;]{0,80}?(?:prior\s+to|before|preceding)', re.I)


@dataclass
class OptionGrant:
    n_options: int | None
    option_years: float | None
    notice_window: str | None       # the printed window, verbatim
    notice_min_days: int | None
    notice_max_days: int | None
    rent_basis: str                  # fair market | percent of fair market | stated schedule | CPI | not stated
    status: str                      # granted | deleted
    page: int
    quote: str                       # the grant's words, verbatim from its page
    notice_quote: str = ''
    rent_quote: str = ''


def _n(tok: str) -> int | None:
    t = (tok or '').strip().lower()
    if t.isdigit():
        return int(t)
    return _NUMW.get(t)


def _days(n, unit) -> int | None:
    if n is None:
        return None
    return n * 30 if unit.lower().startswith('month') else n


def _rent_basis(text: str) -> tuple[str, str]:
    for basis, rx in (('percent of fair market', r'\d{2,3}\s*%\s*\)?\s*of\s+(?:the\s+)?(?:then\s+)?(?:prevailing\s+)?fair\s+market'),
                      ('fair market', r'fair\s+market\s+(?:rent|rental|value)'),
                      ('CPI', r'consumer\s+price\s+index|\bCPI\b'),
                      ('stated schedule', r'(?:minimum|base|annual)\s+rent[^.]{0,80}?(?:as\s+follows|shall\s+be\s+\$|'
                                          r'set\s+forth\s+(?:below|in))|\$\s?[\d,]+\.\d{2}\s+per\s+(?:month|year|square)')):
        m = re.search(rx, text, re.I)
        if m:
            return basis, text[max(0, m.start() - 20):m.end() + 20].strip()
    return 'not stated', ''


def find_options(pages: list) -> list[OptionGrant]:
    out, seen = [], set()
    for n, page in enumerate(pages, 1):
        if not page:
            continue
        flat = re.sub(r'\s+', ' ', page)
        grants = [(m, m.group(1), m.group(2), m.group(3)) for m in _GRANT.finditer(flat)]
        grants += [(m, m.group(1), m.group(2) or m.group(4), m.group(3) or m.group(5)) for m in _GRANT_FOR.finditer(flat)]
        for m, g_cnt, g_len, g_unit in grants:
            near = flat[max(0, m.start() - 160):m.end() + 40].lower()
            if not re.search(r'renew|extend|extension|option', near):
                continue
            cnt, length, unit = _n(g_cnt), _n(g_len), (g_unit or '').lower()
            if cnt is None or length is None or cnt > 10:
                continue
            years = length if unit.startswith('year') else round(length / 12, 2)
            key = (n, cnt, years)
            if key in seen:
                continue
            seen.add(key)
            after = flat[m.end():m.end() + 1200]
            nm = _NOTICE.search(after) or _NOTICE.search(flat[max(0, m.start() - 600):m.start()])
            nmin = nmax = None
            window = None
            if nm:
                unit1 = nm.group(2) or nm.group(4) or 'days'      # "nine (9) and not less than six (6) months"
                a = _days(_n(nm.group(1)), unit1)
                b = _days(_n(nm.group(3)), nm.group(4)) if nm.group(3) else None
                window = nm.group(0)
                lo_hi = sorted(x for x in (a, b) if x is not None)
                nmin = lo_hi[0] if lo_hi else None
                nmax = lo_hi[-1] if len(lo_hi) > 1 else (a if re.match(r'(?i)not\s+(?:more|earlier)|no\s+(?:more|earlier)', window) else None)
                if nmax is not None and nmin == nmax and len(lo_hi) == 1:
                    nmin = None
            basis, rq = _rent_basis(flat[m.start():m.start() + 1500])
            out.append(OptionGrant(cnt, years, window, nmin, nmax, basis, 'granted', n, m.group(0).strip(),
                                   window or '', rq))
        for m in _DELETED.finditer(flat):
            out.append(OptionGrant(None, None, None, None, None, 'not stated', 'deleted', n, m.group(0).strip()))
    return out
