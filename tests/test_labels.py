"""
Labels (extractors/labels.py): every "LABEL: value" a page prints, several
to a line on a form, the label as printed and the value up to the next
label; never a clock time, a URL or a sentence's colon. Fictional text.

    venv/Scripts/python tests/test_labels.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.labels import find_labels  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


PAGE = """NET EFFECTIVE RENT SHEET
PROPERTY: VERSION & DATE: 6/1/2026
NAME: Elm Court OWNERSHIP: Elm Partners MOS VACANT: 0.00
SPACE: 220 SF: 3,153 CARRY COST: $0.00
Lease Date: May 1, 2020 Term: 5 years
Tenant's Address: 12 Oak St
1/11/23, 9:35 AM https://forms.example.com/Pages
The Tenant agrees as follows: it will pay rent."""
got = [(x.label, x.value) for x in find_labels(PAGE)]

test('several pairs on one form line, each label with its own value',
     ('SPACE', '220') in got and ('SF', '3,153') in got and ('CARRY COST', '$0.00') in got)
test("a label is the upper-case run before the colon, the words before it the previous value",
     ('NAME', 'Elm Court') in got and ('OWNERSHIP', 'Elm Partners') in got and ('MOS VACANT', '0.00') in got)
test("'&' stays inside a label", ('VERSION & DATE', '6/1/2026') in got)
test('a label printed with nothing after it, before the next label, is not a pair',
     not any(lbl == 'PROPERTY' for lbl, _ in got))
test('title-case labels and an apostrophe', ('Lease Date', 'May 1, 2020') in got and ('Term', '5 years') in got
     and ("Tenant's Address", '12 Oak St') in got)
test('a clock time, a URL and a sentence colon are not labels',
     not any(lbl in ('9', 'https', 'The Tenant agrees as follows') for lbl, _ in got) and len(got) == 10)
form = [(x.label, x.value) for x in find_labels(
    "SECURITY DEPOSIT: $0.00 Received ADVANCE RENT PAID: $0.00\n"
    "SHOPPING CENTER: Elm Court SEND RENT STATEMENTS TO\n"
    "GUARANTOR: N/A    12 Elm Court Drive\n"
    "SECTION 9.01 - CONTINUANCE: ....................... 14")]
test("a value ends before the next column's upper-case label and at a wide gap",
     ('SECURITY DEPOSIT', '$0.00 Received') in form and ('SHOPPING CENTER', 'Elm Court') in form
     and ('GUARANTOR', 'N/A') in form)
test('a dot leader to a page number is no value', not any('CONTINUANCE' in lbl for lbl, _ in form))
test('the line number is the page line', [x.line_no for x in find_labels(PAGE) if x.label == 'SF'] == [4])
sep = [(x.label, x.value) for x in find_labels(
    "DATE: 7/12/2023 --- PENDING --- LEASED BY: D. Mossey\nRATE PSF 2 GUARANTOR: N/A\nTENANT ACCOUNT #: 19-05")]
test('a separator or a figure in front of a label belongs to the previous value, not the label',
     ('LEASED BY', 'D. Mossey') in sep and ('GUARANTOR', 'N/A') in sep and ('TENANT ACCOUNT #', '19-05') in sep
     and not any('---' in lbl or lbl.startswith('2 ') for lbl, _ in sep))
test('a date value is kept whole', ('DATE', '7/12/2023 --- PENDING ---') in sep or ('DATE', '7/12/2023') in sep)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
