"""
Net effective rent deal sheets (extractors/ner_sheet.py), both forms: every
printed field with its line, every cost line, the cost lines tied to the
printed total (TIE), a sheet whose lines do not add up listed as a BREAK.
Fictional sheets in the real layouts.

    venv/Scripts/python tests/test_ner_sheet.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.ner_sheet import canonical, parse_ner  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


TYPED = """3/14/2012
NET EFFECTIVE RENT SHEET- 5 YEAR
XXX NEW LEASE
RENEWAL
TENANT ENTITY: Juniper Tea House LLC EXPANSION
USE: Tea room
PROPERTY: Willow Commons
BAY: 7
SQ. FT.: 1,600
YEAR P.S.F. MONTHLY ANNUALLY
1 $ 18.00 $ 2,400.00 $ 28,800.00
2 $ 18.54 $ 2,472.00 $ 29,664.00
TOTAL CONTRACT RENT: $ 58,464.00
SPACE PLANS $ -
LEASEHOLD IMPROVEMENT - ALLOWANCE $ 10.00 psf. $ 16,000.00
LHI - AMORTIZED IN BASE RENT - ANNUAL PSF $ - psf. ann. $ - TERM
LEASING FEE @ $ 3.00 psf. $ 4,800.00
CO-BROKER FEE @ $ 2.00 psf. $ 3,200.00
RENT CONCESSION: - MONTHS $ -
MISCELLANEOUS LL COSTS: $ -
TOTAL LANDLORD EXPENSES: $ 24,000.00
TOTAL EFFECTIVE CONTRACT RENT: $ 34,464.00
AVG. ANNUAL EFFECTIVE RENT: (5 YEARS) $ 6,892.80
AVG. PER SQUARE FOOT EFFECTIVE RENT: $ 4.31
"""
COLUMNS = """NET EFFECTIVE RENT SHEET
PROPERTY: VERSION & DATE: 6/2/2023
NAME: Aspen Yard OWNERSHIP: Aspen Yard LLC MOS VACANT: 0.00
SPACE: 104 SF: 1,200 CARRY COST: $0.00
NAME/DBA: Lark Florist TENANT LEGAL ENTITY: Lark Florist Inc
NEW 0 $0.00 $0.00 $0.00 $0.00 $5.00 $0.00 $5.00 $500.00 $0.00
RENEW x 0 $0.00 $0.00 $0.00 $0.00 $5.00 $0.00 $5.00 $500.00 $0.00
TERM 5.0 60 $120,000.00 $30,000.00 $150,000.00
EXPENSES: PROPOSED BUDGETED
PSF TOTAL PSF TOTAL
ALLOWANCE $5.00 $6,000.00 $0.00 $0.00
LANDLORD WORK ● Vanilla Shell Cost $0.00 $0.00 $0.00 $0.00
● Tenant Specific Cost $2.00 $2,400.00 $0.00 $0.00
LEASING FEES ● Agency $4.00 $4,800.00 $0.00 $0.00
● Co-Broker Pine Realty $2.00 $2,400.00 $0.00 $0.00
MISC LANDLORD EXPENSES $0.00 $0.00 $0.00 $0.00
TOTAL LANDLORD EXPENSES $13.00 $15,600.00 $0.00 $0.00
TOTAL EFFECTIVE NET RENT: $104,400.00 $0.00
AVG. ANNUAL NET EFFECTIVE RENT PSF: $17.40 $0.00
"""

print('\nTyped form')
s = parse_ner(TYPED)
test('a typed sheet parses', s is not None)
test('header fields with their lines', s.version_date.value == '2012-03-14' and s.deal_type.value == 'new'
     and s.tenant.value == 'Juniper Tea House LLC' and s.sqft.value == 1600 and s.term_years.value == 5)
test('the rent ladder', [x[2] for x in s.ladder] == [2400.0, 2472.0])
test('every cost line, a "$ -" line as no amount', len(s.cost_lines) == 7
     and [c.amount for c in s.cost_lines if c.amount] == [16000.0, 4800.0, 3200.0])
test('a per-square-foot rate is never a line total', s.leasing_fee.value == 4800.0 and s.cobroker_fee.value == 3200.0)
test('the cost lines tie to the printed total', s.tie == 'TIE' and s.printed_total_landlord_expense.value == 24000.0)
test('the effective totals', s.avg_psf_effective_rent.value == 4.31 and s.total_effective_contract_rent.value == 34464.0)
test('each value carries its verbatim line', s.printed_total_landlord_expense.line == 'TOTAL LANDLORD EXPENSES: $ 24,000.00')
b = parse_ner(TYPED.replace('$ 24,000.00', '$ 25,000.00'))
test('lines that do not add up are a BREAK, with the sum and the printed total', b.tie == 'BREAK'
     and '24,000.00' in b.tie_note and '25,000.00' in b.tie_note)
test('an amortization line is kept as a memo line, out of the sum, and says why',
     [x.memo for x in s.cost_lines if 'AMORTIZED' in x.line][0].startswith('amortized')
     and all(not x.memo for x in s.cost_lines if 'AMORTIZED' not in x.line))
blank = parse_ner(TYPED.replace('TOTAL LANDLORD EXPENSES: $ 24,000.00', 'TOTAL LANDLORD EXPENSES: $'))
test('a total printed blank is NO_PRINTED_TOTAL, never a BREAK', blank.tie == 'NO_PRINTED_TOTAL')

print('\nSpreadsheet form')
c = parse_ner(COLUMNS)
test('header fields', c.version_date.value == '2023-06-02' and c.tenant.value == 'Lark Florist' and c.sqft.value == 1200
     and c.deal_type.value == 'renewal' and c.term_years.value == 5.0)
test('cost rows read their proposed TOTAL, not the PSF', [x.amount for x in c.cost_lines if x.amount]
     == [6000.0, 2400.0, 4800.0, 2400.0])
test('a sub-row is labelled with its group', any(x.label == 'LEASING FEES: Co-Broker Pine Realty' for x in c.cost_lines))
test('the cost rows tie to the printed proposed total', c.tie == 'TIE' and c.printed_total_landlord_expense.value == 15600.0)
test("OCR '$' read as 'S' and a space after a comma", parse_ner(
    COLUMNS.replace('TOTAL LANDLORD EXPENSES $13.00 $15,600.00 $0.00 $0.00',
                    'TOTAL LANDLORD EXPENSES S13.00 $15, 600.00 S0.00 S0.00')).tie == 'TIE')
test('a page that is not a deal sheet is None', parse_ner('LEASE AGREEMENT between ...') is None)

print('\nThe method: fixed lines with a kind, rate and total apart, printed means printed')
test('each line maps to its canonical line and kind; the template is named',
     s.template == 'legacy' and c.template == 'modern'
     and [(x.cost_line, x.kind) for x in c.cost_lines] == [('ALLOWANCE', 'allowance'), ('Vanilla Shell Cost', 'landlord_work'),
                                                            ('Tenant Specific Cost', 'landlord_work'),
                                                            ('Landlord Leasing Fee', 'leasing_fee'), ('Co-Broker', 'leasing_fee'),
                                                            ('MISC LANDLORD EXPENSES', 'misc')])
test('a legacy line keeps its rate beside its total', [(x.proposed_psf, x.amount) for x in s.cost_lines
                                                        if x.cost_line == 'ALLOWANCE'] == [(10.0, 16000.0)])
test('the modern form keeps proposed and budgeted PSF and totals apart',
     [(x.proposed_psf, x.amount, x.budgeted_psf, x.budgeted) for x in c.cost_lines if x.cost_line == 'ALLOWANCE']
     == [(5.0, 6000.0, 0.0, 0.0)])
no_fig = parse_ner(TYPED.replace('SPACE PLANS $ -', 'SPACE PLANS'))
test('the form\'s "$ -" is 0, a line printed with no figure is None and says so',
     [x.amount for x in s.cost_lines if x.cost_line == 'Space Plans'] == [0.0]
     and no_fig.cost_lines[0].value_status == 'line_on_form_no_figure' and no_fig.cost_lines[0].amount is None)
rate_only = parse_ner(TYPED.replace('LEASING FEE @ $ 3.00 psf. $ 4,800.00', 'LEASING FEE @ $ 3.00 psf.'))
lf = [x for x in rate_only.cost_lines if x.cost_line == 'Landlord Leasing Fee'][0]
test('a line printing only a rate gets its total as rate x SF, marked derived, and the sheet still ties',
     (lf.proposed_psf, lf.amount, lf.value_status) == (3.0, 4800.0, 'total derived = psf x SF (not printed)')
     and rate_only.tie == 'TIE' and 'derived' in rate_only.tie_note)
test('the control is the printed total: the difference is kept on a BREAK',
     b.control_delta == -1000.0 and s.control_delta == 0.0)
test('the budgeted column ties on its own where the form prints it',
     c.printed_total_budgeted.value == 0.0 and c.budget_tie == 'TIE' and c.budget_sum == 0.0
     and parse_ner(COLUMNS.replace('MISC LANDLORD EXPENSES $0.00 $0.00 $0.00 $0.00',
                                   'MISC LANDLORD EXPENSES $0.00 $0.00 $1.00 $1,200.00')).budget_tie == 'BREAK')
test('OCR forms of the labels still map', [canonical(x)[0] for x in ('K1A LEASING FEE @', 'SACIWAC (estimate)',
                                                                      'LEASEHOLD IMPROVEMENTS {LHI}', 'e Construction Docs',
                                                                      '5% Const Mgt Fee', 'CM Fee', 'LANDLORD WORK: Vanilla Shelt Cast')]
     == ['Landlord Leasing Fee', 'SAC/WAC', 'Leasehold Improvements (LHI)', 'Construction Docs',
         'Construction Management Fee', 'Construction Management Fee', 'Vanilla Shell Cost'])
test('a label that is none of the fixed lines is kept as kind other',
     canonical('Signage reimbursement') == (None, 'other'))
credit = parse_ner(COLUMNS.replace('MISC LANDLORD EXPENSES $0.00 $0.00 $0.00 $0.00',
                                   'MISC LANDLORD EXPENSES ($2.00) ($2,400.00) $0.00 $0.00')
                   .replace('TOTAL LANDLORD EXPENSES $13.00 $15,600.00', 'TOTAL LANDLORD EXPENSES $11.00 $13,200.00'))
test('a figure in parentheses is a credit that takes the total down, and the sheet ties',
     [x.amount for x in credit.cost_lines if x.cost_line == 'MISC LANDLORD EXPENSES'] == [-2400.0] and credit.tie == 'TIE')
hdr = parse_ner(TYPED.replace('TOTAL CONTRACT RENT: $ 58,464.00', 'TOTAL CONTRACT RENT: $ 58,464.00\nTerm: 5.00 Years PERIOD\n2 N/A N/A $2,425.50 $2,425.50'))
test('the term header and a ladder row between the lines are not cost lines',
     hdr.tie == 'TIE' and all(x.cost_line for x in hdr.cost_lines))
ocr = parse_ner(TYPED.replace('SPACE PLANS $ -', 'SPACE PLANS $ ?')
                .replace('RENT CONCESSION: - MONTHS $ -', 'RENT CONCESSION: - MONTHS Ss -')
                .replace('MISCELLANEOUS LL COSTS: $ -', 'MISCELLANEOUS LL COSTS: #REF!')
                .replace('LHI - AMORTIZED IN BASE RENT - ANNUAL PSF $ - psf. ann. $ - TERM',
                         'LHI - AMORTIZED IN BASE RENT - ANNUAL PSF - psf. ann. $ : TERM'))
test('the OCR forms of a dash cell ("$ ?", "Ss -", "$ :", "- psf", a #REF! cell) read as the zero the form prints',
     [x.amount for x in ocr.cost_lines if x.cost_line in ('Space Plans', 'RENT CONCESSION', 'MISC LANDLORD EXPENSES',
                                                           'LHI Amortized in Base Rent')] == [0.0, 0.0, 0.0, 0.0]
     and [x.proposed_psf for x in ocr.cost_lines if x.cost_line == 'LHI Amortized in Base Rent'] == [0.0] and ocr.tie == 'TIE')
from realestate_extractor.extractors.ner_sheet import retie  # noqa: E402
filled = parse_ner(TYPED.replace('SPACE PLANS $ -', 'SPACE PLANS'))
filled.cost_lines[0].amount, filled.cost_lines[0].value_status = 0.0, 'from the second OCR read of the page (printed)'
test('a line filled from another read re-sums and re-judges the tie', retie(filled).tie == 'TIE' and filled.cost_sum == 24000.0)

print('\nThe label as OCR prints it, and a read that lost every figure')
lost = parse_ner('\n'.join(re.sub(r'\$.*$', '', ln) if 'RENT SHEET' not in ln else ln for ln in TYPED.splitlines()))
test('a read with no cost figure and no total is NO_PRINTED_TOTAL, never a zero-for-zero TIE',
     lost.tie == 'NO_PRINTED_TOTAL' and 'no cost figure' in lost.tie_note)
HDR = 'TENANT ENTITY: Juniper Tea House LLC EXPANSION'
test('the entity label as OCR prints it', parse_ner(TYPED.replace('TENANT ENTITY:', 'TENANTY ENTITY:')).tenant.value == 'Juniper Tea House LLC'
     and parse_ner(TYPED.replace('TENANT ENTITY:', 'TENANTENTITY:')).tenant.value == 'Juniper Tea House LLC')
test('an entity line printed bare leaves the tenant to the name / dba line',
     parse_ner(TYPED.replace(HDR, 'TENANT ENTITY:\nTENANT NAMErdba: Juniper Tea Room EXPANSION')).tenant.value == 'Juniper Tea Room')
test('the older form prints TENANT: alone; a bare label names nobody',
     parse_ner(TYPED.replace(HDR, 'TENANT: Juniper Tea House LLC EXPANSION')).tenant.value == 'Juniper Tea House LLC'
     and parse_ner(TYPED.replace(HDR, 'TENANT ENTITY:')).tenant.value is None)
test('the spreadsheet form: NAME/DBA as OCR prints it, else the legal entity',
     parse_ner(COLUMNS.replace('NAME/DBA:', 'NAMEIDBA:')).tenant.value == 'Lark Florist'
     and parse_ner(COLUMNS.replace('NAME/DBA:', 'NAME/OBA:')).tenant.value == 'Lark Florist'
     and parse_ner(COLUMNS.replace('NAME/DBA: Lark Florist TENANT LEGAL ENTITY: Lark Florist Inc',
                                   'NAME/DBA:\nTENANT LEGAL ENTITY: Lark Florist Inc')).tenant.value == 'Lark Florist Inc')

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
