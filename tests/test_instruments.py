"""
Instruments (extractors/instruments.py): a packet split into its
instruments, each typed, numbered and dated by its OWN date; a recited date
is kept apart and never used; a signing date counts only beside signature
words; an undated instrument says so. Fictional packet.

    venv/Scripts/python tests/test_instruments.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.instruments import classify, recited_dates, split_instruments  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


PACKET = [
    "SHOPPING CENTER LEASE\nTHIS LEASE is made and entered into as of the 14th day of March, 2016, by and between "
    "Willow Holdings LLC and Pine Street Bakery, Inc.",
    "ARTICLE 3. The term shall end March 31, 2026.",
    "SECOND AMENDMENT TO LEASE\nThis Second Amendment is made as of June 1, 2021 and amends the Lease dated "
    "March 14, 2016 between Willow Holdings LLC and Pine Street Bakery, Inc.",
    "ASSIGNMENT AND ASSUMPTION OF LEASE\nPine Street Bakery, Inc. assigns to Rye Bakery LLC the Lease dated "
    "March 14, 2016. The term expires March 31, 2026.\nASSIGNOR: By: ________ Its: President\nDate: 9/15/2022",
    "GUARANTY\nFor value received the undersigned guarantees the obligations of Tenant under the Lease dated "
    "March 14, 2016, which term ends March 31, 2026.",
]

ins = split_instruments(PACKET)
types = [i.event_type for i in ins]
test('the packet splits into its four instruments, typed',
     types == ['original lease', 'amendment', 'assignment / assumption', 'guaranty'])
test('each instrument spans its pages', [(i.page_from, i.page_to) for i in ins] == [(1, 2), (3, 3), (4, 4), (5, 5)])
test("the lease is dated by its own opening ('the 14th day of March, 2016')",
     ins[0].date == '2016-03-14' and ins[0].date_basis == 'opening' and '14th day of March, 2016' in ins[0].date_quote)
test('an amendment is numbered and dated by ITS date, not the lease date it recites',
     ins[1].ordinal == 2 and ins[1].date == '2021-06-01'
     and any(d == '2016-03-14' for d, _ in ins[1].recited))
test("a signing date beside 'Date:' dates an instrument with no opening date",
     ins[2].date == '2022-09-15' and ins[2].date_basis == 'signature block')
test('a term date is never taken as a signing date; an undated instrument says so',
     ins[3].date is None and ins[3].date_null_reason)
test("a notary's 'this 9th day of April, 2012' is a signing date",
     split_instruments(["GUARANTY\nThe undersigned guarantees.\nSubscribed and sworn before me this 9th day of "
                        "April, 2012."])[0].date == '2012-04-09')
test('a title must sit at the top of a page', classify('Recitals\nline two\nline three\nline four\nline five\nline six\n'
                                                       'line seven\nFIRST AMENDMENT TO LEASE')[0] is None)
test('estoppel, SNDA and commencement letters are their own types',
     classify('TENANT ESTOPPEL CERTIFICATE')[0] == 'estoppel'
     and classify('SUBORDINATION, NON-DISTURBANCE AND ATTORNMENT AGREEMENT')[0] == 'SNDA'
     and classify('COMMENCEMENT DATE AGREEMENT')[0] == 'commencement')
test('a file with no title is one unclassified instrument',
     [i.event_type for i in split_instruments(['some text about the premises'])] == ['lease document (unclassified)'])

PKT = ["FIRST AMENDMENT TO LEASE\nThis First Amendment is made as of the ___ day of ______, 2019 by Elm Partners LLC "
       "and Bean Coffee Inc.",
       "GUARANTY\nThe undersigned guarantees the obligations of Bean Coffee Inc under the Lease.",
       "SECOND AMENDMENT TO LEASE\nThis Second Amendment is effective as of March 1, 2022. RECITALS: Landlord and "
       "Tenant are parties to the Lease dated May 6, 2015, as amended by the First Amendment to Lease dated June 3, "
       "2019, and guaranteed pursuant to the Guaranty dated May 6, 2015.",
       "THIRD AMENDMENT TO LEASE\nThis Third Amendment is effective as of April 1, 2024. RECITALS: the Lease, as "
       "amended by the First Amendment to Lease dated June 3, 2091 (an OCR slip for 2019)."]
pk = split_instruments(PKT)
rd = recited_dates([(7, pk)])
test('an undated amendment takes the date a later instrument in the same file recites for it',
     rd.get((7, 0), ('',))[0] == '2019-06-03' and rd[(7, 0)][3] == 7)
test("the file's one undated guaranty takes its recited date", rd.get((7, 1), ('',))[0] == '2015-05-06')
test('a dated instrument is never re-dated', (7, 2) not in rd and (7, 3) not in rd)
two = split_instruments(PKT[:2] + ["GUARANTY\nThe second guarantor guarantees the Lease."] + PKT[2:])
test('two undated guaranties stay undated (the recital cannot say which)',
     not any(two[k].event_type == 'guaranty' for (_, k) in recited_dates([(7, two)])))

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
