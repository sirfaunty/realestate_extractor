"""
Current-rent derivation from a lease's own paper.

Given what the extractor already reads (Year-1 monthly rent, escalation %,
commencement, governing expiration) plus the lease text, answer: "what is
this tenant's monthly base rent on date X?" — with the method used and an
honest confidence, so the dashboard can show derived numbers and route only
the unresolved leases to review.

Methods, best first:
  schedule    a rent-schedule row covers the date. Dated rows first
              ("11/1/2025 through 10/31/2026 $2,590.91" — self-dating, read
              across the whole lease + amendment chain; where several cover
              the date the latest-STARTING row wins = the newest instrument),
              then relative rows ("Lease Years 1-5", "Months 13-24",
              sequential), found as self-verifying ANNUAL / MONTHLY pairs and
              dated from their table's stated start or the commencement
  escalated   no covering schedule row: Year-1 rent rolled forward by the
              stated annual % on each commencement anniversary
  flat        Year-1 rent, no escalation stated (low confidence)
  none        nothing to derive from

Pure functions, no I/O — measured by
the pilot rent tie-out harness against the landlord's rent roll.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

# thousands separator may be OCR'd as '.' ("$5.706.25", "$30.578.35")
_MONEY = r'\$?\s*(\d{1,3}(?:[,.]\d{3})+(?:\.\d{2})?|\d+\.\d{2})'
# sanity bound: no single retail/office tenancy in scope pays more than this
# a month (the largest anchor in the pilot set is ~$41k) — larger reads are a
# price, a loan amount or a sum, never a monthly rent
MAX_MONTHLY = 250_000.0
_DATE = r'(\d{1,2})/(\d{1,2})/(\d{2,4})'
_MONTHS = {m: i for i, m in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july',
     'august', 'september', 'october', 'november', 'december'], 1)}
_LONG = r'([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})'


@dataclass
class ScheduleRow:
    monthly: float
    annual: float
    start: Optional[date] = None      # explicit or computed period start
    end: Optional[date] = None
    label: str = ''                   # 'dates' | 'months a-b' | 'years a-b' | 'year n' | 'seq n'
    table: int = 0                    # which table in the text (relative rows)
    pos: int = 0                      # position in the text (later = newer instrument)
    anchor: Optional[date] = None     # the table's own stated start date, if any


@dataclass
class RentResult:
    monthly: Optional[float]
    method: str                       # schedule | escalated | flat | none
    confidence: str                   # high | medium | low
    flags: list = field(default_factory=list)
    detail: str = ''


# ─── helpers ──────────────────────────────────────────────────────

def _num(s) -> Optional[float]:
    if s is None:
        return None
    s = str(s).replace('$', '')
    # OCR'd thousands dot: "5.706.25" -> 5706.25 (only the LAST '.' is decimal)
    m = re.search(r'\d{1,3}(?:[.,]\d{3})+\.\d{2}', s)
    if m:
        t = m.group(0)
        return float(re.sub(r'[.,]', '', t[:-3]) + t[-3:])
    m = re.search(r'-?[\d,]+(?:\.\d+)?', s)
    try:
        return float(m.group(0).replace(',', '')) if m else None
    except ValueError:
        return None


def _is_pair(annual: float, monthly: float) -> bool:
    """annual = 12 x monthly, to the cent. Also accepts an annual whose '$'
    was OCR'd as a leading digit ("344,143.75" for "$44,143.75" next to
    $3,678.65) — the remaining digits must still match exactly, so a real
    six-figure annual can't pass by accident."""
    if annual < 1200 or not monthly:
        return False
    if abs(annual / 12 - monthly) <= 0.02:
        return True
    s = f'{annual:.2f}'
    if len(s) > 7:                         # 5+ integer digits: one can be the '$'
        rest = float(s[1:])
        return rest >= 1200 and abs(rest / 12 - monthly) <= 0.02
    return False


def parse_date(s) -> Optional[date]:
    if not s:
        return None
    s = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', str(s))   # "November 1st, 2025"
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(_DATE, s)
        if m:
            y = int(m.group(3))
            y += 2000 if y < 50 else (1900 if y < 100 else 0)
            return date(y, int(m.group(1)), int(m.group(2)))
        m = re.search(_LONG, s, re.I)
        if m and m.group(1).lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    except ValueError:
        return None
    return None


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def lease_years_elapsed(commencement: date, as_of: date) -> int:
    """Completed lease years at as_of (0 during Year 1)."""
    n = as_of.year - commencement.year
    if (as_of.month, as_of.day) < (commencement.month, commencement.day):
        n -= 1
    return max(n, 0)


# ─── schedule parsing ─────────────────────────────────────────────

def parse_rent_schedule(text: str) -> list[ScheduleRow]:
    """Rent-schedule rows = consecutive self-verifying annual/monthly pairs.

    The first pair must sit near the word 'rent' (and not in a sublease
    context); later pairs belong to the same table while they stay within
    ~400 chars of the previous row. Each row is labelled with whatever
    period marker precedes it (explicit date range, 'Months a-b',
    'Year n'); unlabelled rows are sequential lease years.
    """
    flat = re.sub(r'\s+', ' ', text or '')
    # every money token, then test ADJACENT tokens as a pair — overlapping,
    # so a $/SF column between them ("2,400 $24.50 $58,800.00 $4,900.00")
    # can't swallow the real annual/monthly pair
    toks = [(m.start(), m.end(), _num(m.group(1)))
            for m in re.finditer(_MONEY, flat)]
    rows: list[tuple[int, ScheduleRow, str]] = []
    last_end = None
    i = 0
    while i < len(toks) - 1:
        s1, e1, a = toks[i]
        i += 1
        if not a:
            continue
        # partner = the next token, or the one after a stray small amount
        # (an OCR'd page-footer "003.50" or a $/SF figure). Rent written in
        # words puts the spelled-out amount between annual and monthly
        # ("($418,069.32) per Lease Year, payable at the rate of Thirty-four
        # thousand ... cents ($34,839.11) per month") -> up to 260 chars when
        # no other real amount sits between; the exact x12 check keeps it safe.
        found = None
        for j in (i, i + 1):
            if j >= len(toks):
                break
            s2, e2, b = toks[j]
            if j == i + 1 and (toks[i][2] or 0) >= 100:
                break
            gap = flat[e1:s2]
            if not b or s2 - e1 > (260 if _WORD_AMOUNT.search(gap) else 40):
                continue
            if _is_pair(a, b):
                found = (j, a, b)
            elif _is_pair(b, a):
                found = (j, b, a)
            if found:
                break
        if not found:
            continue
        j, annual, monthly = found
        s2, e2, _ = toks[j]
        if monthly > MAX_MONTHLY:
            continue
        ctx = flat[max(0, s1 - 220):s1]
        table = 0 if last_end is None else rows[-1][3]
        if last_end is None or s1 - last_end > 400:
            low = ctx.lower()
            if 'rent' not in low or re.search(r'sublease|subtenant|sublandlord', low):
                continue
            table += 1          # a new table (amendments carry their own)
        # period marker = text since the previous row (never that row's dates)
        lo = max(s1 - 160, last_end if last_end is not None else 0)
        rows.append((s1, ScheduleRow(monthly=monthly, annual=annual, pos=s1),
                     flat[lo:s1], table))
        last_end = e2
        i = j + 1               # both tokens consumed

    out = []
    seq = {}
    split, last_term = {}, {}
    for _, row, pre, table in rows:
        if _RANGE.search(pre):
            continue            # explicitly dated row — parse_dated_rows owns it
        mm = list(re.finditer(r'(?i)months?\s*(\d{1,3})\s*(?:-|–|to|through)\s*(\d{1,3})', pre))
        yr = list(re.finditer(r'(?i)(?:lease\s+)?years?\s*(\d{1,2})\s*(?:-|–|to|through)\s*(\d{1,2})\b', pre))
        yy = list(re.finditer(r'(?i)(?:lease\s+)?year\s*(\d{1,2})\b', pre))
        tm = list(_TERM_LABEL.finditer(pre))
        if tm and not (mm or yr or yy):
            k = _term_index(tm[-1])
            # the term sequence restarting ("Initial Term" again) = a
            # PARALLEL table for another space of the same lease
            if k is not None and k <= last_term.get(table, -1):
                split[table] = split.get(table, 0) + 1
                last_term[table] = -1
            if k is not None:
                last_term[table] = k
        key = (table, split.get(table, 0))
        seq[key] = seq.get(key, 0) + 1
        if mm:
            row.label = f'months {mm[-1].group(1)}-{mm[-1].group(2)}'
        elif yr:
            row.label = f'years {yr[-1].group(1)}-{yr[-1].group(2)}'
        elif yy:
            row.label = f'year {yy[-1].group(1)}'
        elif tm and _term_index(tm[-1]) is not None:
            row.label = f'term {_term_index(tm[-1])}'
        else:
            row.label = f'seq {seq[key]}'
        row.table = key
        out.append(row)
    # renumber (table, split) keys 1..n in text order
    ids = {}
    for r in out:
        r.table = ids.setdefault(r.table, len(ids) + 1)

    # each table's own start date, if the text states one ("... during the
    # Extended Term ... beginning on November 1, 2017") — amendment tables
    # count their years from THAT, not from the original commencement
    for t in {r.table for r in out}:
        trs = [r for r in out if r.table == t]
        a = _table_anchor(flat, trs[0].pos, trs[-1].pos)
        for r in trs:
            r.anchor = a
    return out


# Named-term rows: "Initial Term:" -> term 0, "First Extended Term:" /
# "2nd Renewal Term" / "Third Option Term" -> term n
_ORD = {'initial': 0, 'original': 0, 'first': 1, '1st': 1, 'second': 2, '2nd': 2,
        'third': 3, '3rd': 3, 'fourth': 4, '4th': 4, 'fifth': 5, '5th': 5}
_TERM_LABEL = re.compile(r'(?i)\b(initial|original|first|1st|second|2nd|third|3rd|fourth|4th|'
                         r'fifth|5th)\s+(?:(extended|extension|renewal|option)\s+)?term\s*[:\-–—]')
# a spelled-out amount between an annual and its monthly figure
_WORD_AMOUNT = re.compile(r'(?i)\b(?:thousand|hundred)\b[^$]{0,200}\bdollars\b')


def _term_index(m) -> Optional[int]:
    k = _ORD[m.group(1).lower()]
    if k == 0 and m.group(2):
        return None                 # "Original Extended Term" — not a clear index
    if k > 0 and not m.group(2):
        return None                 # "First Term" alone is ambiguous
    return k


def _term_lengths(flat: str) -> tuple[Optional[int], Optional[int]]:
    """(initial term, each extension) in months, from the Term article:
    'The Initial Term ... shall run ... for fifteen (15) years',
    'three (3) additional term of five (5) years each (each an "Extended
    Term")'."""
    def months(m):
        n = int(m.group(1))
        return n * 12 if m.group(2).lower().startswith('year') else n
    ini = re.search(r'(?i)initial\s+term[^.]{0,160}?\b(?:for|of)\s+(?:a\s+(?:period\s+|term\s+)?of\s+)?'
                    r'(?:[a-z\- ]{0,30}\(\s*)?(\d{1,3})\s*\)?\s*(?:full\s+)?(?:lease\s+|calendar\s+)?'
                    r'(years?|months?)', flat)
    ext = re.search(r'(?i)(?:additional|extended|extension|renewal|option)\s+terms?\s+of\s+'
                    r'(?:[a-z\- ]{0,30}\(\s*)?(\d{1,2})\s*\)?\s*(?:full\s+)?(?:lease\s+)?(years?|months?)',
                    flat)
    return (months(ini) if ini else None), (months(ext) if ext else None)


# "Year 1" incl. OCR'd 1s ("Year |", "Year I", "Year l")
_Y1 = r'(?:lease\s+)?year\s*(?:1|one|i|l|\|)(?![A-Za-z0-9])\W{0,3}'


def _table_anchor(flat: str, first: int, last: int) -> Optional[date]:
    """A relative table's own start date, if the text states it.

    1. the Lease-Year-1 definition next to the table (usually right AFTER
       it): '"Lease Year 1" shall commence on January 1, 2020', or
       'Lease Year 1 shall commence on the Expansion Date and end on
       August 31, 2017' (-> start = that end + 1 day - 12 months)
    2. an explicit start just BEFORE the table: 'Beginning on January 1,
       2020 ... Tenant shall pay ... as follows'.
    Never a date merely near the table: the text after a schedule is often
    an unrelated clause ('Beginning on June 1, 2021 ... either party may
    terminate') — that picked the wrong anchor for a real lease."""
    after = flat[max(0, first - 300):last + 600]
    m = re.search(r'(?i)' + _Y1 + r'["”\']?\s*shall\s+(?:commence|begin)\s+on\s+(' + _D + ')', after)
    if m:
        return parse_date(m.group(1))
    m = re.search(r'(?i)' + _Y1 + r'[^.]{0,140}?\bend(?:s|ing)?\s+on\s+(' + _D + ')', after)
    if m:
        e = parse_date(m.group(1))
        if e:
            return add_months(e + timedelta(days=1), -12)
    # the payment sentence that closes an amendment table: "Monthly
    # installments ... during the Extended Term, beginning on November 1, 2017"
    m = re.search(r'(?i)installments[^.]{0,220}?\b(?:beginning|commencing|starting)'
                  r'\s+(?:on\s+)?(' + _D + ')', flat[last:last + 600])
    if m:
        return parse_date(m.group(1))
    before = flat[max(0, first - 700):first]
    ms = list(re.finditer(r'(?i)(?:beginning|commencing|commence|starting|effective)'
                          r'\s+(?:on\s+)?(' + _D + ')', before))
    return parse_date(ms[-1].group(1)) if ms else None


# ─── dated rows (self-dating; no commencement needed) ─────────────

_D = (r'(?:(?:January|February|March|April|May|June|July|August|September|'
      r'October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}'
      r'|\d{1,2}/\d{1,2}/\d{2,4})')
_RANGE = re.compile(
    r'(' + _D + r')\s*\)?\s*(?:through|thru|to|until|-|–|—|'
    r'and\s+(?:ending|expiring|terminating)(?:\s+on)?|'
    r'10(?=\d{1,2}/))'                       # OCR: "to" read as "10" ("1/1/20261012/31/2026")
    r'\s*(' + _D + r')', re.I)
_OPTION_CTX = re.compile(r'\b(?:extended\s+term|extension\s+(?:term|option|period)|'
                         r'option\s+(?:to\s+)?(?:extend|renew)|renewal\s+(?:term|option|period))\b',
                         re.I)
# an amendment that actually changed the term / premises (not an unexercised
# option): its table can supersede, even if it also says "Extended Term"
_EXECUTED_CTX = re.compile(r'\b(?:hereby\s+extended|is\s+extended|expansion|relocat\w*)\b', re.I)


def _is_option_table(flat: str, pos: int) -> bool:
    """A renewal-option rent table: begins only after the current term ends,
    so it can't supersede a row inside that term."""
    return bool(_OPTION_CTX.search(flat[max(0, pos - 300):pos])
                and not _EXECUTED_CTX.search(flat[max(0, pos - 1500):pos]))


def _later_unplaced(rows: list, flat: str, after_pos: int, dated: list = ()) -> bool:
    """A relative rent table later in the chain than after_pos that could
    not be dated and is not a renewal option. A row the dated parser already
    placed ("Year 3 $27,233.30 $2,269.44 (1/1/2028 to 12/31/2028)") is not
    unplaced."""
    placed = {round(d.monthly, 2) for d in dated}
    return any(x.start is None and x.pos > after_pos and round(x.monthly, 2) not in placed
               and not _is_option_table(flat, x.pos)
               for x in rows)


_BAD_CTX = re.compile(r'\b(?:security\s+deposit|deposit|operating\s+costs?|cam|'
                      r'common\s+area|estimated?|sublease|subtenant|sublandlord|deferred)\b')


def _amounts(seg: str, keep_zero: bool = False):
    """Dollar amounts in seg. $0.00 rows (free-rent months) count for
    position (keep_zero) but never as a rent value."""
    out = []
    for m in re.finditer(_MONEY, seg):
        v = _num(m.group(1))
        if v is None or (not v and not keep_zero):
            continue
        out.append((m.start(), m.end(), v))
    return out


def _monthly_from(seg: str, header: str, nearest_last: bool = False) -> Optional[float]:
    """Monthly rent from the dollar amounts of one row.

    An annual/monthly pair decides itself. Otherwise the single row amount
    >= $100 (smaller ones are $/SF) is read by the words next to it, then by
    the column header: 'per month'/'monthly' -> monthly, 'annual'/'per
    year' -> /12. No unit signal -> None (flag, don't guess)."""
    am = _amounts(seg)
    pairs = list(zip(am, am[1:]))
    if nearest_last:            # amounts BEFORE the range: the closest row wins
        pairs.reverse()
    for (s1, e1, a), (s2, e2, b) in pairs:
        if _is_pair(a, b):
            return b
        if _is_pair(b, a):
            return a
    big = [(s, e, v) for s, e, v in am if v >= 100]
    if not big or (len(big) > 1 and not nearest_last):
        return None
    s, e, v = big[-1] if nearest_last else big[0]
    after = seg[e:e + 40].lower()
    if re.search(r'per\s+month|/\s*mo|monthly', after):
        return v
    if re.search(r'per\s+(?:year|annum)|annual', after):
        return round(v / 12, 2)
    h = header.lower()
    if 'month' in h:
        return v
    if 'annual' in h or 'per year' in h or 'per annum' in h:
        return round(v / 12, 2)
    return None


def parse_dated_rows(text: str) -> list[ScheduleRow]:
    """Rows whose period is an explicit date range, in any of the forms
    real leases use: table rows ('11/1/2013 through 10/31/2014 $21,645.00
    $1,803.75 $18.50', '10/1/25-9/30/27 $22.50 $6,545.63'), monthly-only
    rows ('November 1, 2025 through October 31, 2026 $2,590.91'), dates
    in brackets after the amounts ('Year 2 $39,057.60 $3,254.80 (8/1/2024
    to 7/31/2025)'), and prose ('... $1,383.67 for the period beginning
    October 1, 2014 and ending September 30, 2015').

    Amounts are read AFTER the range (up to the next range); if there are
    none there, BEFORE it (back to the previous range)."""
    flat = re.sub(r'\s+', ' ', text or '')
    ranges = list(_RANGE.finditer(flat))

    # Which side of the date range holds THIS row's amounts? Real tables mix
    # layouts row by row ("Months 1-12 (5/1/25 to 4/30/26) $18,600 $1,550"
    # next to "Year 1 $44,906.40 $3,742.20 (8/1/25 to 7/31/26)"), and OCR
    # scrambles some rows. Reading the wrong side takes the neighbouring
    # row's rent — exactly one escalation step off. So each row reads from
    # the side whose nearest amount is CLOSEST, measured in letters/digits in
    # between (row labels like "Months 13-24" count; "(", ")", "|" don't).
    # Tie -> after. Validated on six real pilot lease tables (2026-09-29).
    def _gap(s):
        # SHORT bracketed row labels ("(2nd Option)") and $/SF columns
        # ("$10.75") say nothing about which row an amount belongs to — don't
        # count them. Long bracketed runs are OCR-scrambled rows; they DO
        # count (they separate a row from the next row's amounts).
        s = re.sub(r'\([^()]{0,25}\)', '', s)
        s = re.sub(_MONEY, '', s)
        return len(re.sub(r'[^A-Za-z0-9]', '', s))

    out = []
    for i, m in enumerate(ranges):
        start, end = parse_date(m.group(1)), parse_date(m.group(2))
        if not start or not end or end <= start or (end - start).days > 366 * 25:
            continue
        nxt = ranges[i + 1].start() if i + 1 < len(ranges) else len(flat)
        prv = ranges[i - 1].end() if i else 0
        seg_after = flat[m.end():min(nxt, m.end() + 160)]
        seg_before = flat[max(prv, m.start() - 200):m.start()]
        am_a = _amounts(seg_after, keep_zero=True)
        am_b = _amounts(seg_before, keep_zero=True)
        gap_a = _gap(seg_after[:am_a[0][0]]) if am_a else 10 ** 6
        gap_b = _gap(seg_before[am_b[-1][1]:]) if am_b else 10 ** 6
        if gap_a == gap_b == 10 ** 6:
            continue
        before = gap_b < gap_a
        seg = seg_before if before else seg_after
        ctx = flat[max(0, m.start() - 600):m.start()].lower()
        near = (flat[max(0, m.start() - 120):m.start()] + flat[m.end():m.end() + 80]).lower()
        if 'rent' not in ctx or _BAD_CTX.search(near):
            continue
        monthly = _monthly_from(seg, flat[max(0, m.start() - 500):m.start()],
                                nearest_last=before)
        if not monthly or not (50 <= monthly <= MAX_MONTHLY):
            continue
        r = ScheduleRow(monthly=monthly, annual=round(monthly * 12, 2),
                        start=start, end=end, label='dates')
        r.pos = m.start()
        out.append(r)
    return out


def _offsets(r: ScheduleRow) -> tuple[int, int]:
    """(start, end) of a relative row in months from its table's anchor."""
    kind, val = r.label.split(' ', 1)
    if kind in ('months', 'years'):
        a, b = map(int, val.split('-'))
        return (a - 1, b) if kind == 'months' else (12 * (a - 1), 12 * b)
    n = int(val)
    return 12 * (n - 1), 12 * n


def _anchor(rows: list[ScheduleRow], commencement: Optional[date],
            expiration: Optional[date], flat: str = '') -> set:
    """Date the relative rows. Each table counts from its own stated start
    if it has one; the first table (the original lease) from the
    commencement; failing both, the LAST table is back-dated from the
    expiration (its final row ends the term). Returns the anchoring methods
    used, for confidence."""
    used = set()
    tables = sorted({r.table for r in rows})
    ini, ext = _term_lengths(flat) if flat else (None, None)
    for t in tables:
        trs = [r for r in rows if r.table == t]
        if all(r.label.startswith('term ') for r in trs):
            # named terms are lease-level: Initial Term from the
            # commencement, each Extended Term right after the previous one
            if not commencement or not ini:
                continue
            for r in trs:
                k = int(r.label.split()[1])
                if k and not ext:
                    continue
                s = 0 if k == 0 else ini + (k - 1) * ext
                e = ini if k == 0 else ini + k * ext
                r.start = add_months(commencement, s)
                r.end = add_months(commencement, e) - timedelta(days=1)
                r.label += ' [commencement]'
            used.add('commencement')
            continue
        a = trs[0].anchor or (commencement if t == tables[0] else None)
        how = 'stated' if trs[0].anchor else 'commencement'
        if not a and expiration and t == tables[-1]:
            span = max(_offsets(r)[1] for r in trs)
            a, how = add_months(expiration + timedelta(days=1), -span), 'expiration'
        if not a:
            continue
        used.add(how)
        for r in trs:
            s, e = _offsets(r)
            r.start = add_months(a, s)
            r.end = add_months(a, e) - timedelta(days=1)
            r.label += f' [{how}]'
    return used


# ─── the derivation ───────────────────────────────────────────────

def derive_current_rent(*, as_of: date,
                        year1_monthly=None, escalation_pct=None,
                        commencement=None, expiration=None,
                        schedule_text: str = '',
                        commencement_confirmed: bool = False) -> RentResult:
    """Monthly base rent in effect on `as_of`, with method + confidence.

    commencement_confirmed: the commencement came from a user confirmation
    or the rent roll, not from reading the lease — a stated Year-1 rent rolled
    forward by a stated fixed escalation is then fully determined (high)."""
    comm = commencement if isinstance(commencement, date) else parse_date(commencement)
    exp = expiration if isinstance(expiration, date) else parse_date(expiration)
    y1 = _num(year1_monthly)
    esc = _num(escalation_pct)
    flags = []
    if exp and as_of > exp:
        flags.append('past_expiration')     # holdover / renewal rent not on this paper
    if comm and as_of < comm:
        flags.append('not_commenced')

    # 1. explicitly dated rows — self-dating, strongest evidence. Where
    #    several cover the date (lease + later amendment), the row that
    #    STARTS latest belongs to the newer instrument; ties -> later in text.
    #    Relative tables are parsed + dated up front: one that sits LATER in
    #    the chain than the answer but can't be dated (an expansion,
    #    relocation or rent-reduction amendment counted from an event) may
    #    supersede it -> never high (flag later_schedule_unplaced).
    flat = re.sub(r'\s+', ' ', schedule_text or '')
    rows = parse_rent_schedule(schedule_text) if schedule_text else []
    how = _anchor(rows, comm, exp, flat)

    dated = parse_dated_rows(schedule_text) if schedule_text else []
    cov = [r for r in dated if r.start <= as_of <= r.end]
    if cov:
        r = max(cov, key=lambda x: (x.start, x.pos))
        if _later_unplaced(rows, flat, r.pos, dated):
            flags.append('later_schedule_unplaced')
        return RentResult(r.monthly, 'schedule', 'high' if not flags else 'medium',
                          flags, f'dated row {r.start}..{r.end} '
                          f'({len(dated)} dated rows found)')

    # 2. relative rows (Months 1-12 / Lease Year 3 / sequential) dated from
    #    each table's anchor
    covering = [r for r in rows if r.start and r.end and r.start <= as_of <= r.end]
    if covering:
        r = max(covering, key=lambda x: (x.start, x.pos))
        monthly = r.monthly
        # parallel named-term tables for the lease's separate spaces
        # ("Minimum Rental for the Existing Space ... for the New Space",
        # same term labels, same instrument) -> the tenancy pays their SUM
        if r.label.startswith('term '):
            def labels(t):
                return [x.label for x in rows if x.table == t]
            first = {t: min(x.pos for x in rows if x.table == t) for t in {x.table for x in rows}}
            par = [x for x in covering if x.table != r.table and x.label == r.label
                   and labels(x.table) == labels(r.table)
                   and abs(first[x.table] - first[r.table]) < 8000]
            if par:
                monthly = round(monthly + sum(x.monthly for x in par), 2)
                flags.append('multi_space_sum')
            if not r.label.startswith('term 0'):
                flags.append('extension_term_assumed')   # an option may not have been exercised
        # high only for explicitly labelled rows (Months / Lease Year) dated
        # from a stated start or the commencement; sequential guesses and
        # expiration back-dating are medium
        if _later_unplaced(rows, flat, r.pos, dated):
            flags.append('later_schedule_unplaced')
        conf = ('high' if not (set(flags) - {'multi_space_sum'})
                and not r.label.startswith('seq')
                and r.label.endswith('[commencement]') else 'medium')
        return RentResult(monthly, 'schedule', conf, flags,
                          f'{len(rows)}-row schedule, row {r.label} '
                          f'({r.start}..{r.end})'
                          + (f' + {len(par)} parallel space(s)' if 'multi_space_sum' in flags else ''))
    if dated and max(r.end for r in dated) < as_of:
        flags.append('schedule_ends_before_date')   # a later amendment is missing

    y1_stated = y1 is not None
    if rows and not y1:
        y1 = rows[0].monthly
    if y1 and comm and esc and 0 < esc < 15:
        n = lease_years_elapsed(comm, as_of)
        v = round(y1 * (1 + esc / 100.0) ** n, 2)
        if rows:
            flags.append('schedule_does_not_cover_date')
        if flags:
            conf = 'low'
        elif commencement_confirmed and y1_stated:
            conf = 'high'       # every input known: stated Y1, stated %, confirmed date
        else:
            conf = 'medium'
        return RentResult(v, 'escalated', conf, flags,
                          f'Y1 {y1:,.2f} x (1+{esc:g}%)^{n}')
    if y1:
        if not comm:
            flags.append('no_commencement')
        if not esc:
            flags.append('no_escalation_stated')
        return RentResult(y1, 'flat', 'low', flags, 'Year-1 rent, not rolled forward')
    return RentResult(None, 'none', 'low', flags + ['no_rent_found'], '')
