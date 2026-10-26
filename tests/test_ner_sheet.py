"""
Net effective rent deal sheets (extractors/ner_sheet.py), both forms: every
printed field with its line, every cost line, the cost lines tied to the
printed total (TIE), a sheet whose lines do not add up listed as a BREAK.
Fictional sheets in the real layouts.

    venv/Scripts/python tests/test_ner_sheet.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.ner_sheet import parse_ner  # noqa: E402

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

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
