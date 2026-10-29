"""
Net effective rent (NER) deal sheets: the landlord's one-page approval form
for a lease deal. Read every printed field with the line it is on, list EVERY
cost line of the form (printed or blank) against a fixed list of lines with a
kind, keep a line's rate and its total apart, and tie the cost lines to the
sheet's printed total (a deal whose lines do not sum to its printed total
within $1 is a BREAK, listed, never forced to tie).

Two templates:
  legacy  "NET EFFECTIVE RENT SHEET - n YEAR": one figure column, each line
          "LABEL [@ n sf] $psf psf. $total"; the figure before "psf" is the
          RATE, the last figure the line TOTAL; a line with only a rate has
          no printed total (it is derived as rate x SF and says so).
  modern  "EXPENSES: PROPOSED BUDGETED": each line LABEL psf total psf total.

Printed means printed: a blank on the form is None with a reason, the form's
"$ -" is 0. The cost block ends at TOTAL LANDLORD EXPENSES; text below it is
comments. A line restating the improvement cost as rent ("amortized in base
rent") is a memo line, kept and left out of the sum.

    parse_ner(page_text) -> NerSheet | None      (None: not an NER sheet)

Each field is a Field(value, line) where `line` is the verbatim line the
value was read from ('' and value None when the sheet does not print it).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TIE_TOLERANCE = 1.0

# a dollar figure; OCR reads '$' as 'S' before a cents figure ("S0.00") and
# puts a space after a thousands comma ("$63, 102.00"); "$ -" is the form's zero
_MONEY = re.compile(r'(?:\$|(?<![A-Za-z])S(?=[0-9][0-9,]*\.\d{2}))\s*([0-9]{1,3}(?:\s?,\s?[0-9]{3})+(?:\s?\.\s?[0-9]{1,2})?'
                    r'|[0-9]+(?:\s?\.\s?[0-9]{1,2})?)'
                    r'|(?<![\w/$.,])([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2}|[0-9]+\.[0-9]{2})(?![\d/%])'   # a figure OCR left without its "$"
                    r'|(?:\$|(?<![A-Za-z])S)\s*(?:-|—|–|_|\*|\.|·|�)(?=\s|$)')                  # "$ -" and its OCR forms
_RATE_MARK = re.compile(r'\s*(?:psf|ps[a-z]|p\.s\.f|\$s\b|/\s*sf|%|\|\s*psf)', re.I)   # a figure followed by "psf" (or its OCR) is a rate
_DATE = re.compile(r'\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b')
_COST_START = re.compile(r'TOTAL\s+CONTRACT\s+RENT', re.I)
_COST_END = re.compile(r'TOTAL\s+LANDLORD\s+EXPENSE', re.I)
# the spreadsheet (modern) form, known by its header or by row names the typed form never prints
_COLUMN_FORM = re.compile(r'EXPENSES\s*:?\s*PROPOSED|LANDLORD\s+WORK|LEASING\s+FEES|MISC\s+LANDLORD\s+EXPENSES',
                          re.I)    # cost-row names (both forms print NAME/DBA in their header)

# the fixed list of cost lines: (canonical line, kind, label pattern). Every
# printed label maps to one of these; a label that maps to none is kept as
# kind 'other'. Patterns allow the OCR forms of the labels.
LINES = [
    ('ALLOWANCE', 'allowance', r'^\W*(leasehold improvement\s*-?\s*)?allowance\b|tenant improvement allowance'),
    ('Leasehold Improvements (LHI)', 'landlord_work', r'^\W*leasehold improvements?\s*[\({\[]?\s*lhi'),
    ('SAC/WAC', 'landlord_work', r'\bsac\s*.?\s*wac'),
    ('Space Plans', 'landlord_work', r'^\W*space plans?'),
    ('LANDLORD WORK', 'landlord_work', r'^\W*landlord work\b'),
    ('Vanilla Shell Cost', 'landlord_work', r'vanilla shel'),
    ('Tenant Specific Cost', 'landlord_work', r'tenant specific'),
    ('Construction Management Fee', 'landlord_work', r'const(ruction)?\.?\s*m(anagement|g(m)?t)\b|\bcm fee'),
    ('Construction Docs', 'landlord_work', r'construction doc'),
    ('Construction Costs', 'landlord_work', r'^\W*construction costs'),
    ('Tenant Improvements', 'landlord_work', r'^\W*tenant improvements\b'),
    ('LHI Amortized in Base Rent', 'memo', r'amortized in base rent'),
    ('LEASING FEES', 'leasing_fee', r'^\W*leasing fees\b'),
    ('Co-Broker', 'leasing_fee', r'co-?\s*broker( fee)?\b(?!\s*name)'),   # before the landlord's own fee: "Co-Broker Pine Realty"
    ('LL Rep', 'leasing_fee', r'\bll rep\b'),
    ('License %', 'leasing_fee', r'license fee\s*%|\blicense\b.*%'),
    ('Landlord Leasing Fee', 'leasing_fee', r'\brealty\b|\bleasing fee\b|\bagency\b'),
    ('CAM & CAP EXCLUSIONS', 'exclusion', r'cap(s)? & exclusions|cam & cap'),
    ('RENT CONCESSION', 'concession', r'rent concession'),
    ('MISC LANDLORD EXPENSES', 'misc', r'misc(ellaneous)?\.?\s*(ll|landlord)'),
]
_LINES = [(name, kind, re.compile(rx, re.I)) for name, kind, rx in LINES]


def canonical(label: str) -> tuple:
    """(canonical line, kind) for a printed label; (None, 'other') when it is
    none of the fixed lines. A sub-row carries its group in front
    ("LEASING FEES: Co-Broker"): the row's own words decide."""
    for part in ([label.split(':', 1)[1]] if ':' in label else []) + [label]:
        low = ' '.join(part.lower().split())
        for name, kind, rx in _LINES:
            if rx.search(low):
                return name, kind
    return None, 'other'


@dataclass
class Field:
    value: object = None
    line: str = ''


@dataclass
class CostLine:
    label: str                  # as printed (a sub-row with its group in front)
    amount: float | None        # the line's proposed dollar total; None when the form prints nothing
    line: str
    budgeted: float | None = None
    memo: str = ''              # why the line is printed but not a cost (left out of cost_sum); '' = counted
    cost_line: str | None = None    # the canonical line of the fixed list (None: not one of them)
    kind: str = 'other'
    proposed_psf: float | None = None
    budgeted_psf: float | None = None
    value_status: str = 'printed'   # printed | line_on_form_no_figure | total derived = psf x SF (not printed)


# a line the sheet prints beside the costs that is not itself a cost: an
# amortization restates the improvements as rent; an annual / PSF line is a rate
_MEMO = [(re.compile(r'AMORTIZED', re.I), 'amortized in base rent: restates the improvement cost as rent'),
         (re.compile(r'\bANNUAL\s+PSF\b|\bPSF\s*,?\s*ANN', re.I), 'an annual PSF rate, not a dollar cost')]


def _memo(line: str) -> str:
    return next((why for rx, why in _MEMO if rx.search(line)), '')


@dataclass
class NerSheet:
    template: str = 'legacy'                              # legacy | modern
    version_date: Field = field(default_factory=Field)
    deal_type: Field = field(default_factory=Field)       # new / renewal / expansion / license / not marked
    tenant: Field = field(default_factory=Field)
    use: Field = field(default_factory=Field)
    property: Field = field(default_factory=Field)
    space: Field = field(default_factory=Field)
    sqft: Field = field(default_factory=Field)
    term_years: Field = field(default_factory=Field)
    ladder: list = field(default_factory=list)            # [(year label, psf, monthly, annual, line)]
    total_contract_rent: Field = field(default_factory=Field)
    cost_lines: list = field(default_factory=list)        # [CostLine]
    printed_total_landlord_expense: Field = field(default_factory=Field)
    printed_total_budgeted: Field = field(default_factory=Field)
    leasing_fee: Field = field(default_factory=Field)
    cobroker_fee: Field = field(default_factory=Field)
    allowance_total: Field = field(default_factory=Field)
    total_effective_contract_rent: Field = field(default_factory=Field)
    avg_annual_effective_rent: Field = field(default_factory=Field)
    avg_psf_effective_rent: Field = field(default_factory=Field)
    cost_sum: float = 0.0
    budget_sum: float | None = None
    control_delta: float | None = None                     # cost_sum - printed total
    tie: str = ''                                          # TIE | BREAK | NO_TOTAL | NO_PRINTED_TOTAL
    budget_tie: str = ''                                   # TIE | BREAK | '' (no budgeted column printed)
    tie_note: str = ''


def _valid(y: int, mo: int, d: int) -> str | None:
    """An ISO date only when the calendar has that day (a sheet can print 4/31)."""
    from datetime import date as _date
    try:
        return _date(y, mo, d).isoformat()
    except ValueError:
        return None


def _num(s: str) -> float | None:
    s = re.sub(r'\s', '', s or '')
    if not s or not re.search(r'\d', s):
        return None
    try:
        return float(s.replace(',', ''))
    except ValueError:
        return None


def _figures(line: str) -> list:
    """Dollar figures on a line in order, each (value, is_rate): a printed
    "$ -" is 0.0; a figure followed by "psf" or "%" is a rate."""
    out = []
    for m in _MONEY.finditer(line):
        tail = line[m.end():m.end() + 8]
        rate = bool(_RATE_MARK.match(tail))
        fig = m.group(1) or m.group(2)
        out.append((_num(fig) if fig else 0.0, rate))
    return out


def _amounts(line: str) -> list:
    """Dollar figures on a line in order ("$ -" is 0.0)."""
    return [v for v, _ in _figures(line)]


def _last_amount(line: str) -> float | None:
    """The line's dollar total: its last figure that is not a per-square-
    foot rate ("@ $ 1.50 psf." is a rate, never the line's total)."""
    out = [v for v, rate in _figures(line) if not rate]
    return out[-1] if out else None


def _bare_amount(line: str) -> bool:
    """A line that is only an amount (a column split onto its own line)."""
    return bool(re.fullmatch(r'\s*[l|(]?\s*\$?\s*[0-9][0-9,]*(?:\.\d{2})?\s*[I|)]?\s*', line or ''))


def _find(lines: list, rx: str):
    r = re.compile(rx, re.I)
    for ln in lines:
        if r.search(ln):
            return ln
    return ''


def is_ner_sheet(text: str) -> bool:
    return bool(re.search(r'EFFECTIVE\s+RENT\s+SHEET', text or '', re.I)
                and _COST_END.search(text or ''))


def _cost_line(label: str, line: str, proposed: float | None, proposed_psf: float | None,
               budgeted: float | None, budgeted_psf: float | None, sqft) -> CostLine:
    """One cost line: canonical name and kind, rate and total apart, a total
    derived from the rate when the form prints only the rate."""
    name, kind = canonical(label)
    memo = _memo(line) or ('amortized in base rent: restates the improvement cost as rent' if kind == 'memo' else '')
    status = 'printed'
    if proposed is None and proposed_psf is not None and sqft:
        proposed = round(proposed_psf * sqft, 2)          # a "$ -" rate derives a 0 total
        status = 'total derived = psf x SF (not printed)'
    elif proposed is None and proposed_psf is None and budgeted is None and budgeted_psf is None:
        status = 'line_on_form_no_figure'
    return CostLine(label, proposed, line.strip(), budgeted, memo, name, kind if memo == '' or kind == 'memo' else kind,
                    proposed_psf, budgeted_psf, status)


def parse_ner(text: str) -> NerSheet | None:
    if not is_ner_sheet(text):
        return None
    lines = [ln.rstrip() for ln in (text or '').splitlines()]
    if _COLUMN_FORM.search(text):
        return _parse_columns(lines)
    s = NerSheet(template='legacy')

    head = lines[:15]
    for ln in head:
        m = _DATE.search(ln)
        if m:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = y + (2000 if y < 50 else 1900) if y < 100 else y
            iso = _valid(y, mo, d)
            if iso:
                s.version_date = Field(iso, ln.strip())
                break

    # a deal-type box is ticked when a mark sits right before its label
    mark = r'(?:^|\s)(?:x{1,3}|X{1,3}|[✓✔√]|x-)\s*[-—]?\s*'
    for kind, rx in (('license', r'licen[cs]e\s+agree'), ('new', r'new\s+lease'), ('renewal', r'renewal'),
                     ('expansion', r'expansion')):
        ln = _find(head, mark + rx)
        if ln:
            s.deal_type = Field(kind, ln.strip())
            break
    if s.deal_type.value is None:
        ln = _find(head, r'new\s+lease|renewal|expansion')
        s.deal_type = Field('not marked', ln.strip())

    def labelled(rx, stop=r'\s{2,}(?:EXPANSION|RENEWAL|REVISION)|$'):
        for ln in head + lines[15:25]:
            m = re.search(rx + r'\s*[:.;]?\s*[~\-]?\s*(.+?)(?:' + stop + ')', ln, re.I)
            if m and m.group(1).strip(' :~-'):
                return Field(m.group(1).strip(' :~-'), ln.strip())
        return Field()
    s.tenant = labelled(r'TENANT\s+ENTITY', r'\s+(?:EXPANSION|RENEWAL|NEW LEASE)\s*$|$')
    s.use = labelled(r'^\s*USE')
    s.property = labelled(r'PROPERTY')
    s.space = labelled(r'^\s*(?:BAY|SPACE|SUITE)')
    ln = _find(head + lines[15:25], r'SQ\.?\s*FT\.?|SF\s*:')
    m = re.search(r'(?:SQ\.?\s*FT\.?|SF)\s*[:.]?\s*([0-9][0-9,]{2,})', ln, re.I) if ln else None
    if m:
        s.sqft = Field(_num(m.group(1)), ln.strip())
    ln = _find(head, r'RENT\s+SHEET\s*[-–—]?\s*\d+\s*YEAR')
    m = re.search(r'(\d+)\s*YEAR', ln, re.I) if ln else None
    if m:
        s.term_years = Field(int(m.group(1)), ln.strip())

    # rent ladder: "1 $ 15.00 $ 6,307.50 $ 75,690.00"
    for ln in lines:
        m = re.match(r'^\s*(\d{1,2}(?:\s*-\s*\d{1,2})?)\s*[:.]?\s*[$S]\s*([0-9.,]+)\s+[$S]\s*([0-9.,]+)\s+[$S]\s*([0-9.,]+)',
                     ln)
        if m:
            s.ladder.append((m.group(1), _num(m.group(2)), _num(m.group(3)), _num(m.group(4)), ln.strip()))

    # cost lines: everything between TOTAL CONTRACT RENT and TOTAL LANDLORD EXPENSES
    start = next((i for i, ln in enumerate(lines) if _COST_START.search(ln) and 'EFFECTIVE' not in ln.upper()), None)
    end = next((i for i, ln in enumerate(lines) if _COST_END.search(ln)), None)

    def total_at(i):
        """A total on its own line, or on the next line when the column split."""
        v = _last_amount(lines[i])
        if v is None and i + 1 < len(lines) and _bare_amount(lines[i + 1]):
            return _num(re.sub(r'[^0-9.,]', '', lines[i + 1])), lines[i + 1].strip()
        return v, lines[i].strip()
    if start is not None:
        s.total_contract_rent = Field(*total_at(start))
    if end is not None:
        s.printed_total_landlord_expense = Field(*total_at(end))
    lo = (start + 1) if start is not None and (end is None or start < end) else 0
    for ln in lines[lo:end if end is not None else lo]:
        if not ln.strip() or not re.search(r'[A-Za-z]{3}', ln):
            continue
        label = re.split(r'[:@$]', ln, 1)[0].strip(' .-')
        name, kind = canonical(label)
        figs = _figures(ln)
        if name is None and (len(re.findall(r'[A-Za-z]', label)) < 3 or ('$' not in ln and not figs)):
            continue                          # a ladder row or prose between the lines, not a cost line
        psf = next((v for v, rate in figs if rate), None)
        total = _last_amount(ln)
        cl = _cost_line(label, ln, total, psf, None, None, s.sqft.value)
        s.cost_lines.append(cl)
        if cl.amount and not cl.memo:
            s.cost_sum += cl.amount
        if cl.cost_line == 'Co-Broker':
            s.cobroker_fee = Field(cl.amount, ln.strip())
        elif cl.cost_line == 'Landlord Leasing Fee':
            s.leasing_fee = Field(cl.amount, ln.strip())
        elif cl.cost_line == 'ALLOWANCE':
            s.allowance_total = Field(cl.amount, ln.strip())

    for attr, rx in (('total_effective_contract_rent', r'TOTAL\s+EFFECTIVE\s+CONTRACT\s+RENT'),
                     ('avg_annual_effective_rent', r'AVG\.?\s+ANNUAL\s+EFFECTIVE\s+RENT'),
                     ('avg_psf_effective_rent', r'(?:AVG\.?\s+)?PER\s+SQUARE\s+FOOT\s+EFFECTIVE\s+RENT')):
        ln = _find(lines, rx)
        if ln:
            setattr(s, attr, Field(_last_amount(ln), ln.strip()))
    _tie(s, end is not None)
    return s


def _tie(s: NerSheet, has_total: bool):
    """The control: the sheet's own printed total. The lines sum to it within
    $1 or the sheet is a BREAK with the difference; budgeted likewise where
    the form prints a budgeted column."""
    s.cost_sum = round(s.cost_sum, 2)
    total = s.printed_total_landlord_expense.value
    if not has_total:
        s.tie, s.tie_note = 'NO_TOTAL', 'the sheet prints no total landlord expense'
    elif total is None and s.cost_sum == 0:
        s.tie, s.tie_note = 'TIE', 'no costs printed and the total is printed blank'
    elif total is None:
        s.tie, s.tie_note = 'NO_PRINTED_TOTAL', f'cost lines sum {s.cost_sum:,.2f}; the total is printed blank or unreadable'
    elif abs(total - s.cost_sum) <= TIE_TOLERANCE:
        s.tie, s.tie_note = 'TIE', f'cost lines sum {s.cost_sum:,.2f} = printed {total:,.2f}'
    else:
        s.tie, s.tie_note = 'BREAK', f'cost lines sum {s.cost_sum:,.2f} vs printed {total:,.2f}'
    if total is not None:
        s.control_delta = round(s.cost_sum - total, 2)
    derived = [c for c in s.cost_lines if c.value_status.startswith('total derived')]
    if derived:
        s.tie_note += f'; {len(derived)} line total(s) derived as psf x SF'
    budget = s.printed_total_budgeted.value
    if budget is not None:
        s.budget_sum = round(sum(c.budgeted or 0.0 for c in s.cost_lines if not c.memo), 2)
        s.budget_tie = 'TIE' if abs(budget - s.budget_sum) <= TIE_TOLERANCE else 'BREAK'
        s.tie_note += f'; budgeted {s.budget_sum:,.2f} vs printed {budget:,.2f} ({s.budget_tie})'


def _parse_columns(lines: list) -> NerSheet:
    """The spreadsheet (modern) form: header fields as LABEL: value pairs, the
    deal box marked after its label ("NEW x"), and cost rows printing proposed
    PSF / TOTAL then budgeted PSF / TOTAL; sub-rows ("* Co-Broker ...")
    belong to the group named on the row that opens them ("LEASING FEES")."""
    s = NerSheet(template='modern')

    def after(rx, stop=r'\s+[A-Z][A-Z &/.]{2,}:|$'):
        for ln in lines[:30]:
            m = re.search(rx + r'\s*:?\s*(.+?)(?:' + stop + ')', ln)
            if m and m.group(1).strip():
                return Field(m.group(1).strip(), ln.strip())
        return Field()
    d = after(r'VERSION\s*&\s*DATE')
    m = _DATE.search(d.value or '')
    if m:
        mo, dd, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + (2000 if y < 50 else 1900) if y < 100 else y
        iso = _valid(y, mo, dd)
        if iso:
            s.version_date = Field(iso, d.line)
    s.tenant = after(r'NAME\s*/\s*DBA')
    s.use = after(r'DESCRIPTION')
    s.property = after(r'^\s*NAME')
    s.space = after(r'SPACE', r'\s+SF\s*:|$')
    ln = _find(lines[:30], r'\bSF\s*:')
    m = re.search(r'\bSF\s*:\s*([0-9][0-9,]*)', ln) if ln else None
    if m:
        s.sqft = Field(_num(m.group(1)), ln.strip())
    for kind, rx in (('new', r'NEW'), ('renewal', r'RENEW'), ('expansion', r'EXPAND'), ('license', r'LICENSE')):
        ln = _find(lines, r'^\s*' + rx + r'\s+[xX✓✔]\s')
        if ln:
            s.deal_type = Field(kind, ln.strip())
            break
    if s.deal_type.value is None:
        s.deal_type = Field('not marked', _find(lines, r'^\s*NEW\b').strip())
    ln = _find(lines, r'^\s*TERM\s+\d')
    m = re.match(r'\s*TERM\s+(\d+(?:\.\d+)?)', ln) if ln else None
    if m:
        s.term_years = Field(float(m.group(1)), ln.strip())

    # the cost block opens at its header; when OCR lost the header, at the
    # first cost row (ALLOWANCE / LANDLORD WORK) after the TERM row
    start = next((i for i, ln in enumerate(lines) if re.match(r'\s*EXPENSES\s*:?\s*PROPOSED', ln, re.I)), None)
    if start is None:
        t0 = next((i for i, ln in enumerate(lines) if re.match(r'\s*TERM\b', ln, re.I)), 0)
        first = next((i for i, ln in enumerate(lines[t0:], t0)
                      if re.match(r'\s*(?:ALLOWANCE|LANDLORD\s+WORK)', ln, re.I)), None)
        start = (first - 1) if first is not None else len(lines)
    end = next((i for i, ln in enumerate(lines) if _COST_END.search(ln)), None)
    if end is not None:
        am = _amounts(lines[end])
        if len(am) >= 4:
            tot, btot = am[1], am[3]
        elif len(am) >= 2 and am[0] is not None and am[1] is not None and am[0] < 100 <= am[1]:
            tot, btot = am[1], None
        elif len(am) >= 2:
            tot, btot = am[0], am[1]
        else:
            tot, btot = (am[0] if am else None), None
        s.printed_total_landlord_expense = Field(tot, lines[end].strip())
        s.printed_total_budgeted = Field(btot, lines[end].strip() if btot is not None else '')
    group = ''
    for ln in lines[start + 1:end if end is not None else start + 1]:
        if not re.search(r'[A-Za-z]{3}', ln):
            continue
        if re.match(r'\s*PSF\s+TOTAL', ln, re.I):
            continue                              # the column header under EXPENSES
        am = _amounts(ln)
        txt = re.split(r'\$', ln, 1)[0].strip()
        if not txt.startswith(('●', '•', '*')):
            parts = re.split(r'\s*[●•]\s*', txt, 1)
            group = parts[0].strip()
            label = txt if len(parts) == 1 else f'{group}: {parts[1].strip()}'
        else:
            label = f"{group}: {txt.lstrip('●•* ').strip()}" if group else txt.lstrip('●•* ').strip()
        name, _kind = canonical(label)
        if not am and name is None:
            continue                              # prose between the rows
        # four figures: proposed PSF, TOTAL, budgeted PSF, TOTAL; two: either a
        # PSF and its total (a rate is small beside its total) or proposed and
        # budgeted totals; one: the proposed total alone (a scanned sheet)
        ppsf = bpsf = proposed = budgeted = None
        if len(am) == 1:
            proposed = am[0]
        elif len(am) >= 4:
            ppsf, proposed, bpsf, budgeted = am[0], am[1], am[2], am[3]
        elif len(am) == 3:
            ppsf, proposed, budgeted = am[0], am[1], am[2]
        elif len(am) == 2 and am[0] < 100 <= am[1]:
            ppsf, proposed = am[0], am[1]
        elif len(am) == 2:
            proposed, budgeted = am[0], am[1]
        cl = _cost_line(label, ln, proposed, ppsf, budgeted, bpsf, s.sqft.value)
        s.cost_lines.append(cl)
        if cl.amount and not cl.memo:
            s.cost_sum += cl.amount
        if cl.cost_line == 'Co-Broker':
            s.cobroker_fee = Field(cl.amount, ln.strip())
        elif cl.kind == 'leasing_fee' and cl.cost_line != 'LEASING FEES' and s.leasing_fee.value is None:
            s.leasing_fee = Field(cl.amount, ln.strip())
        elif cl.cost_line == 'ALLOWANCE':
            s.allowance_total = Field(cl.amount, ln.strip())
    for attr, rx in (('total_effective_contract_rent', r'TOTAL\s+EFFECTIVE\s+NET\s+RENT'),
                     ('avg_psf_effective_rent', r'AVG\.?\s+ANNUAL\s+NET\s+EFFECTIVE\s+RENT\s+PSF')):
        ln = _find(lines, rx)
        if ln:
            am = _amounts(ln)
            setattr(s, attr, Field(am[0] if am else None, ln.strip()))
    _tie(s, end is not None)
    return s
