"""
Renewal options (extractors/options.py): the count and length in both
wordings, the notice window as a range, the rent basis, a deletion told
apart from a condition. Fictional lease text.

    venv/Scripts/python tests/test_options.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.options import find_options  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


A = ("Tenant shall have two (2) options to extend the Term for five (5) years each, upon written notice given not more "
     "than nine (9) and not less than six (6) months before the expiration of the then current Term. Minimum Rent "
     "during each extension term shall be ninety-five percent (95%) of the then fair market rent.")
B = ("Landlord grants Tenant one (1) option (the 'Renewal Option') to renew the Term for 5 years, by notice at least "
     "one hundred eighty (180) days prior to the expiration. Minimum Rent for the renewal term shall be $2,450.00 per "
     "month.")
C = "Tenant shall have the option to renew this Lease for three (3) successive additional terms of five (5) years each."
D = "The Renewal Option is hereby deleted from the Lease as if it had never been a part thereof."
E = "The Renewal Options shall be void if Tenant is then in default beyond any cure period."
F = "Tenant shall have the right to use two (2) parking stalls for five (5) years."

a = find_options([A])[0]
test('count and length ("two (2) options ... for five (5) years each")', a.n_options == 2 and a.option_years == 5)
test('the notice window as a range: 6 to 9 months', a.notice_min_days == 180 and a.notice_max_days == 270
     and 'nine (9)' in a.notice_window)
test('rent at a percent of fair market', a.rent_basis == 'percent of fair market')
test('the grant quote is verbatim from the page', a.quote in A)
b = find_options([B])[0]
test('"grants Tenant one (1) option ... for 5 years", notice at least 180 days, stated rent',
     b.n_options == 1 and b.option_years == 5 and b.notice_min_days == 180 and b.rent_basis == 'stated schedule')
c = find_options([C])
test('the count after "for" ("for three (3) successive additional terms of five (5) years")',
     [(o.n_options, o.option_years) for o in c] == [(3, 5)])
test('a deletion is a deleted row', [o.status for o in find_options([D])] == ['deleted'])
test('a condition ("shall be void if ...") is not a deletion', find_options([E]) == [])
test('a right that is not to renew or extend is not an option', find_options([F]) == [])

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
