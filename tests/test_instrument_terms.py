"""
Instrument terms (extractors/instrument_terms.py): an instrument's own
start, end and length as it prints them, each quoted with its page; a rent
period and an option's term are not the term. Fictional text.

    venv/Scripts/python tests/test_instrument_terms.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.instrument_terms import instrument_term  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


ext = instrument_term(["SECOND AMENDMENT\nRecitals.",
                       "2. Extended Term: The term of the Lease is hereby extended for an additional period of "
                       "three (3) years and two (2) months starting on May 1, 2024 and ending on June 30, 2027."], 1, 2)
test('an extension prints start and end in one phrase, on its page',
     ext.start == '2024-05-01' and ext.end == '2027-06-30' and ext.start_page == 2 and ext.end_page == 2
     and ext.start_quote == 'starting on May 1, 2024 and ending on June 30, 2027')
test('years and months add up', ext.months == 38)

sep = instrument_term(["1. Term. The Lease term is extended for a period of four (4) years from and after "
                       "March 1, 2019, and the Lease term shall expire on February 29, 2024."], 1, 1)
test('start and end in separate phrases', sep.start == '2019-03-01' and sep.end == '2024-02-29'
     and sep.end_quote == 'expire on February 29, 2024' and sep.months == 48)

last = instrument_term(["TERM. This Lease shall be for a term of seven (7) years commencing on the Commencement "
                        "Date and ending on the last day of October, 2031."], 1, 1)
test("'the last day of October, 2031' is that month's last day; the start is not printed",
     last.end == '2031-10-31' and last.start is None and last.months == 84)

rent = instrument_term(["Minimum Rent: Beginning on June 1, 2020 and continuing through May 31, 2021, "
                        "$1,500.00 per month."], 1, 1)
test('a rent period is not the term', rent.start is None and rent.end is None)

opt = instrument_term(["Tenant shall have one (1) option to extend the Term for five (5) years, commencing on "
                       "July 1, 2029 and ending on June 30, 2034."], 1, 1)
test("an option's period is not the term", opt.start is None and opt.end is None and opt.months is None)

out = instrument_term(["FIRST AMENDMENT\nterm extended starting on May 1, 2024 and ending on June 30, 2027.",
                       "THIRD PARTY CONSENT"], 2, 2)
test('only the pages of the instrument are read', out.start is None and out.end is None)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
