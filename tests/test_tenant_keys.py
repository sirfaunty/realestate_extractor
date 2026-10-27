"""
Tenant keys (modules/tenant_keys.py): each tenancy tied to the owner's
rent-roll key by alias, exact name, a roll name cut short, or the same
distinctive words; one roll row per tenancy; no match keeps our key.
Fictional names.

    venv/Scripts/python tests/test_tenant_keys.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.modules.tenant_keys import match_tenant_keys, norm  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


ROLL = [
    {'suite': '0110', 'occupant': "O'Malley Hardware", 'tenant_key': 'omalley_hardware'},
    {'suite': '0120', 'occupant': 'Northstar Pediatric Dentistr', 'tenant_key': 'northstar_pediatric_dentistry'},
    {'suite': '0130', 'occupant': 'Lark Florist & Gifts', 'tenant_key': 'lark_florist_and_gifts'},
    {'suite': '0140', 'occupant': 'Copper Kettle', 'tenant_key': 'copper_kettle'},
    {'suite': '0150', 'occupant': 'Vacant', 'tenant_key': ''},
    {'suite': '0160', 'occupant': 'Blue Fern Nails', 'tenant_key': 'blue_fern_nails'},
]
ALIAS = {'kettle bistro group': 'copper_kettle'}
TEN = [
    ('fn:omalley', ['OMALLEY HARDWARE, INC.']),
    ('fn:northstar', ['Northstar Pediatric Dentistry, P.A.']),
    ('fn:lark', ['Lark Florist and Gifts LLC']),
    ('fn:kettle', ['Kettle Bistro Group']),
    ('fn:fern', ['Blue Fern Nails Studio']),
    ('fn:fern_old', ['Blue Fern Nails']),
    ('fn:nobody', ['Granite Tax Advisors']),
]
m = match_tenant_keys(TEN, ROLL, ALIAS)

test("names normalise: case, '&', apostrophes, punctuation",
     norm("O'Malley Hardware, Inc.") == 'omalley hardware inc' and norm('Lark Florist & Gifts') == 'lark florist and gifts')
test('a legal name ties to the roll occupant (suffixes aside)', m['fn:omalley'].tenant_key == 'omalley_hardware')
test('a roll name cut short ties by prefix', m['fn:northstar'].tenant_key == 'northstar_pediatric_dentistry'
     and m['fn:northstar'].basis == 'prefix')
test("'&' on the roll and 'and' on the paper tie", m['fn:lark'].tenant_key == 'lark_florist_and_gifts')
test('the alias table ties a name the roll does not print', m['fn:kettle'].tenant_key == 'copper_kettle'
     and m['fn:kettle'].basis == 'alias')
test('one roll row goes to one tenancy: the exact name wins it',
     m['fn:fern_old'].tenant_key == 'blue_fern_nails' and m['fn:fern'].tenant_key is None)
test('a tenancy with no roll row keeps no roll key and says so',
     m['fn:nobody'].tenant_key is None and m['fn:nobody'].basis == 'not on roll')
test('a vacant row is never matched', all(x.suite != '0150' for x in m.values()))
test('one shared word is not enough ("Dental" vs "Dentistry" names differ)',
     match_tenant_keys([('x', ['Riverside Dental'])], [{'suite': '1', 'occupant': 'Lakeside Dental',
                                                          'tenant_key': 'lakeside_dental'}])['x'].tenant_key is None)

test("store numbers and codes on the roll are set aside ('#1134', 'MNST006878', '(2025)')",
     match_tenant_keys([('a', ['Granite Pizza'])], [{'suite': '1', 'occupant': 'Granite Pizza #1134',
                                                    'tenant_key': 'granite_pizza_1134'}])['a'].tenant_key
     == 'granite_pizza_1134')
test("spacing in a name does not matter (\"O' Malley\" vs \"O'Malley\")",
     match_tenant_keys([('a', ["O'Malley Hardware"])], [{'suite': '1', 'occupant': "O' Malley Hardware",
                                                        'tenant_key': 'o_malley_hardware'}])['a'].tenant_key
     == 'o_malley_hardware')

files = match_tenant_keys([('f1', ['LeaseDocuments BirchBakery']), ('f2', ['2010-MarigoldTaloringScannedLease']),
                           ('f3', ['Contact List'])],
                          [{'suite': '1', 'occupant': 'Birch Bakery', 'tenant_key': 'birch_bakery'},
                           {'suite': '2', 'occupant': 'Marigold Tailoring', 'tenant_key': 'marigold_tailoring'},
                           {'suite': '3', 'occupant': 'Fennel Deli', 'tenant_key': 'fennel_deli'}])
test('a roll name run together inside a file-name tenancy matches (within)',
     files['f1'].tenant_key == 'birch_bakery' and files['f1'].basis == 'within')
test('a file name that misspells the roll name by a letter still matches (near)',
     files['f2'].tenant_key == 'marigold_tailoring' and files['f2'].basis == 'near')
test('a file name about something else never matches', files['f3'].tenant_key is None)

abbr = match_tenant_keys([('a1', ['Quillon Claims Management Services, Inc.']), ('a2', ['Juniper Lamps'])],
                         [{'suite': '1', 'occupant': 'Quillon Claims Mgt Svc Inc.', 'tenant_key': 'quillon'},
                          {'suite': '2', 'occupant': 'Juniper Lamp MN', 'tenant_key': 'juniper'}])
test("a roll's abbreviations (Mgt, Svc) are the long words", abbr['a1'].tenant_key == 'quillon')
test('a plural and its singular are one word', abbr['a2'].tenant_key == 'juniper')

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
