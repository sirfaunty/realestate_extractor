"""
Net effective rent (NER) deal sheets: the landlord's one-page approval form
for a lease deal. Read every printed field with the line it is on, list EVERY
cost line, and tie the cost lines to the sheet's printed total (a deal whose
lines do not sum to its printed total within $1 is a BREAK, listed, never
forced to tie).

The form: a header (date, NEW LEASE / RENEWAL / EXPANSION boxes, tenant,
use, property, bay, square feet), a rent ladder (YEAR / P.S.F. / MONTHLY /
ANNUALLY), TOTAL CONTRACT RENT, the cost lines down to TOTAL LANDLORD
EXPENSES, then the effective totals (total effective contract rent, average
annual and per-square-foot effective rent).

    parse_ner(page_text) -> NerSheet | None      (None: not an NER sheet)

Each field is a Field(value, line) where `line` is the verbatim line the
value was read from ('' and value None when the sheet does not print it).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TIE_TOLERANCE = 1.0

# a dollar figure; OCR reads '$' as 'S' before a cents figure ("S0.00") and
# puts a space after a thousands comma ("$63, 102.00")
_MONEY = re.compile(r'(?:\$|(?<![A-Za-z])S(?=[0-9][0-9,]*\.\d{2}))\s*([0-9]{1,3}(?:\s?,\s?[0-9]{3})+(?:\s?\.\s?[0-9]{1,2})?'
                    r'|[0-9]+(?:\s?\.\s?[0-9]{1,2})?)|\$\s*(-|—|–|\.)(?=\s|$)')
_DATE = re.compile(r'\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\b')
_COST_START = re.compile(r'TOTAL\s+CONTRACT\s+RENT', re.I)
_COST_END = re.compile(r'TOTAL\s+LANDLORD\s+EXPENSE', re.I)
# the spreadsheet form, known by its header or by row names the typed form never prints
_COLUMN_FORM = re.compile(r'EXPENSES\s*:?\s*PROPOSED|LANDLORD\s+WORK|LEASING\s+FEES|MISC\s+LANDLORD\s+EXPENSES',
                          re.I)    # cost-row names (both forms print NAME/DBA in their header)


@dataclass
class Field:
    value: object = None
    line: str = ''


@dataclass
class CostLine:
    label: str
    amount: float | None        # the line's printed (proposed) dollar total; None when printed '-' / blank
    line: str
    budgeted: float | None = None


@dataclass
class NerSheet:
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
    leasing_fee: Field = field(default_factory=Field)
    cobroker_fee: Field = field(default_factory=Field)
    allowance_total: Field = field(default_factory=Field)
    total_effective_contract_rent: Field = field(default_factory=Field)
    avg_annual_effective_rent: Field = field(default_factory=Field)
    avg_psf_effective_rent: Field = field(default_factory=Field)
    cost_sum: float = 0.0
    tie: str = ''                                          # TIE | BREAK | NO_TOTAL
    tie_note: str = ''


def _num(s: str) -> float | None:
    s = re.sub(r'\s', '', s or '')
    if not s or not re.search(r'\d', s):
        return None
    try:
        return float(s.replace(',', ''))
    except ValueError:
        return None


def _amounts(line: str) -> list:
    """Dollar figures on a line in order; a printed '$ -' is None."""
    out = []
    for m in _MONEY.finditer(line):
        out.append(_num(m.group(1)) if m.group(1) else None)
    return out


def _last_amount(line: str) -> float | None:
    """The line's dollar total: its last figure that is not a per-square-
    foot rate ("@ $ 1.50 psf." is a rate, never the line's total)."""
    out = []
    for m in _MONEY.finditer(line):
        if re.match(r'\s*psf', line[m.end():m.end() + 6], re.I):
            continue
        out.append(_num(m.group(1)) if m.group(1) else None)
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


def parse_ner(text: str) -> NerSheet | None:
    if not is_ner_sheet(text):
        return None
    lines = [ln.rstrip() for ln in (text or '').splitlines()]
    if _COLUMN_FORM.search(text):
        return _parse_columns(lines)
    s = NerSheet()

    head = lines[:15]
    for ln in head:
        m = _DATE.search(ln)
        if m:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = y + (2000 if y < 50 else 1900) if y < 100 else y
            if 1 <= mo <= 12 and 1 <= d <= 31:
                s.version_date = Field(f'{y:04d}-{mo:02d}-{d:02d}', ln.strip())
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
        if not ln.strip() or not re.search(r'[A-Za-z]{3}', ln) or '$' not in ln:
            continue
        label = re.split(r'[:@$]', ln, 1)[0].strip(' .-')
        amount = _last_amount(ln)
        if re.search(r'AMORTIZED', ln, re.I):
            amount_for_sum = None          # an amortization restates LHI as rent; it is not an extra cost
        else:
            amount_for_sum = amount
        s.cost_lines.append(CostLine(label, amount, ln.strip()))
        if amount_for_sum:
            s.cost_sum += amount_for_sum
        if re.search(r'CO-?\s*BROKER', ln, re.I):
            s.cobroker_fee = Field(amount, ln.strip())
        elif re.search(r'LEASING\s+FEE', ln, re.I):
            s.leasing_fee = Field(amount, ln.strip())
        elif re.search(r'ALLOWANCE', ln, re.I):
            s.allowance_total = Field(amount, ln.strip())

    for attr, rx in (('total_effective_contract_rent', r'TOTAL\s+EFFECTIVE\s+CONTRACT\s+RENT'),
                     ('avg_annual_effective_rent', r'AVG\.?\s+ANNUAL\s+EFFECTIVE\s+RENT'),
                     ('avg_psf_effective_rent', r'(?:AVG\.?\s+)?PER\s+SQUARE\s+FOOT\s+EFFECTIVE\s+RENT')):
        ln = _find(lines, rx)
        if ln:
            setattr(s, attr, Field(_last_amount(ln), ln.strip()))

    s.cost_sum = round(s.cost_sum, 2)
    total = s.printed_total_landlord_expense.value
    if end is None:
        s.tie, s.tie_note = 'NO_TOTAL', 'the sheet prints no total landlord expense'
    elif total is None and s.cost_sum == 0:
        s.tie, s.tie_note = 'TIE', 'no costs printed and the total is printed blank'
    elif total is None:
        s.tie, s.tie_note = 'BREAK', f'cost lines sum {s.cost_sum:,.2f}; the total is printed blank or unreadable'
    elif abs(total - s.cost_sum) <= TIE_TOLERANCE:
        s.tie, s.tie_note = 'TIE', f'cost lines sum {s.cost_sum:,.2f} = printed {total:,.2f}'
    else:
        s.tie, s.tie_note = 'BREAK', f'cost lines sum {s.cost_sum:,.2f} vs printed {total:,.2f}'
    return s


def _tie(s: NerSheet, has_total: bool):
    s.cost_sum = round(s.cost_sum, 2)
    total = s.printed_total_landlord_expense.value
    if not has_total:
        s.tie, s.tie_note = 'NO_TOTAL', 'the sheet prints no total landlord expense'
    elif total is None and s.cost_sum == 0:
        s.tie, s.tie_note = 'TIE', 'no costs printed and the total is printed blank'
    elif total is None:
        s.tie, s.tie_note = 'BREAK', f'cost lines sum {s.cost_sum:,.2f}; the total is printed blank or unreadable'
    elif abs(total - s.cost_sum) <= TIE_TOLERANCE:
        s.tie, s.tie_note = 'TIE', f'cost lines sum {s.cost_sum:,.2f} = printed {total:,.2f}'
    else:
        s.tie, s.tie_note = 'BREAK', f'cost lines sum {s.cost_sum:,.2f} vs printed {total:,.2f}'


def _parse_columns(lines: list) -> NerSheet:
    """The spreadsheet form: header fields as LABEL: value pairs, the deal
    box marked after its label ("NEW x"), and cost rows printing proposed
    PSF / TOTAL then budgeted PSF / TOTAL; sub-rows ("* Co-Broker ...")
    belong to the group named on the row that opens them ("LEASING FEES")."""
    s = NerSheet()

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
        s.version_date = Field(f'{y:04d}-{mo:02d}-{dd:02d}', d.line)
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
        if len(am) >= 4 or (len(am) >= 2 and am[0] is not None and am[1] is not None and am[0] < 100 <= am[1]):
            tot = am[1]
        else:
            tot = am[0] if am else None
        s.printed_total_landlord_expense = Field(tot, lines[end].strip())
    group = ''
    for ln in lines[start + 1:end if end is not None else start + 1]:
        am = _amounts(ln)
        if not am or not re.search(r'[A-Za-z]{3}', ln):
            continue
        txt = re.split(r'\$', ln, 1)[0].strip()
        if not txt.startswith(('●', '•', '*')):
            parts = re.split(r'\s*[●•]\s*', txt, 1)
            group = parts[0].strip()
            label = txt if len(parts) == 1 else f'{group}: {parts[1].strip()}'
        else:
            label = f"{group}: {txt.lstrip('●•* ').strip()}" if group else txt.lstrip('●•* ').strip()
        # four figures: proposed PSF, TOTAL, budgeted PSF, TOTAL; two: either a
        # PSF and its total (a rate is small beside its total) or proposed and
        # budgeted totals
        if len(am) == 1:                  # a scanned sheet often prints the proposed total alone
            proposed, budgeted = am[0], None
        elif len(am) >= 4:
            proposed, budgeted = am[1], am[3]
        elif am[0] is not None and am[1] is not None and am[0] < 100 <= am[1]:
            proposed, budgeted = am[1], None
        else:
            proposed, budgeted = am[0], am[1]
        s.cost_lines.append(CostLine(label, proposed, ln.strip(), budgeted))
        if proposed:
            s.cost_sum += proposed
        if re.search(r'CO-?\s*BROKER', ln, re.I):
            s.cobroker_fee = Field(proposed, ln.strip())
        elif re.search(r'LEASING\s+FEES', group, re.I) and s.leasing_fee.value is None:
            s.leasing_fee = Field(proposed, ln.strip())
        elif re.search(r'ALLOWANCE', ln, re.I):
            s.allowance_total = Field(proposed, ln.strip())
    for attr, rx in (('total_effective_contract_rent', r'TOTAL\s+EFFECTIVE\s+NET\s+RENT'),
                     ('avg_psf_effective_rent', r'AVG\.?\s+ANNUAL\s+NET\s+EFFECTIVE\s+RENT\s+PSF')):
        ln = _find(lines, rx)
        if ln:
            am = _amounts(ln)
            setattr(s, attr, Field(am[0] if am else None, ln.strip()))
    _tie(s, end is not None)
    return s
