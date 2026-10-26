"""
Citations (extractors/citation.py): a value is quoted from the page that
prints it, verbatim; a wrong page hint moves to the page that does; a value
printed nowhere is not_found; a computed value names what it was computed from.
Fictional page texts in real layouts.

    venv/Scripts/python tests/test_citation.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.citation import cite_date, cite_number, cite_text  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


def on_page(quote, page):
    n = lambda t: re.sub(r'\s+', ' ', t).strip().lower()  # noqa: E731
    return bool(quote) and n(quote) in n(page)


PAGES = [
    "THIS LEASE AGREEMENT is made as of the 14th day of March, 2019, by and between\n"
    "Willow Holdings LLC (\"Landlord\") and Pine Street Bakery, Inc. (\"Tenant\").",
    "ARTICLE 1. PREMISES. Landlord leases to Tenant approximately 2,350 rentable square feet\n"
    "known as Suite 104 of Willow Commons.",
    "ARTICLE 3. MINIMUM RENT.\nLease Year Annual Minimum Rent Monthly Installment\n"
    "1 $35,250.00 $2,937.50\n2 $36,307.50 $3,025.63\n"
    "The Commencement Date shall be 6/1/19 and the term ends May 31, 2029.",
    "",
    "Annual Base Rent for the extension term: $41,400 . 00 per year, payable monthly.",
]

print('\nNumbers')
c = cite_number(PAGES, 2937.50, page_hint=3)
test('a monthly amount is quoted from its own page, verbatim',
     c.page == 3 and c.state == 'printed' and c.quote == '2,937.50' and on_page(c.quote, PAGES[2]))
c = cite_number(PAGES, 2350, page_hint=1)
test('a wrong page hint moves to the page that prints the value',
     c.page == 2 and c.state == 'moved' and c.quote == '2,350')
c = cite_number(PAGES, 3450.0, page_hint=5, also=[(41400.0, 'annual / 12')])
test('a monthly figure printed only as an annual amount is computed, and says from what',
     c.page == 5 and c.basis == 'computed: annual / 12' and '41,400' in c.quote)
test('OCR spacing inside a number still matches ("$41,400 . 00")',
     cite_number(PAGES, 41400.0).page == 5)
c = cite_number(PAGES, 9999.99, page_hint=3)
test('a value printed nowhere is not_found with no quote', c.page is None and c.quote == '' and c.state == 'not_found')
test('a lease-year number is not mistaken for part of an amount',
     cite_number(['Year 12 $1,200.00'], 12.0).quote == '12' and cite_number(['Year 12 $1,200.00'], 1200.0).quote == '1,200.00')

print('\nDates')
c = cite_date(PAGES, '2019-03-14', page_hint=1)
test('a written date ("the 14th day of March, 2019")', c.page == 1 and '14th day of March, 2019' in c.quote)
c = cite_date(PAGES, '2019-06-01', page_hint=1)
test('a numeric date (6/1/19) found on another page', c.page == 3 and '6/1/19' in c.quote)
test('"May 31, 2029"', cite_date(PAGES, '2029-05-31').page == 3)
test('a date that is not printed is not_found', cite_date(PAGES, '2019-06-02').page is None)
test('a written ordinal day ("the first day of July, 2021")',
     cite_date(['commencing on the first day of July, 2021'], '2021-07-01').page == 1)

print('\nHeadings')
c = cite_text(PAGES, 'Minimum Rent', page_hint=2)
test('a clause heading is found on its page, case and punctuation aside',
     c.page == 3 and 'MINIMUM RENT' in c.quote)
test('a long line is trimmed around the value, never mid-word',
     len(cite_number(['word ' * 60 + '$5,000.00 ' + 'word ' * 60], 5000.0).quote) <= 140)
test('a blank page is skipped, not an error', cite_text(['', 'Signage'], 'Signage').page == 2)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
