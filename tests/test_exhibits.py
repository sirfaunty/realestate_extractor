"""
Exhibits (extractors/exhibits.py): an exhibit starts at its heading on top
of a page, runs to the next exhibit or its instrument's end, keeps its
printed title, and is parented to its instrument; a reference in running
text is not an exhibit; an attached form does not start an instrument.
Fictional packet.

    venv/Scripts/python tests/test_exhibits.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.exhibits import find_exhibits  # noqa: E402
from realestate_extractor.extractors.instruments import split_instruments  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


PAGES = [
    "SHOPPING CENTER LEASE\nTHIS LEASE is made as of May 1, 2020. The Premises are shown on Exhibit A.",
    "ARTICLE 3. RENT.",
    "EXHIBIT A\nSITE PLAN\n[drawing]",
    "EXHIBIT B - LANDLORD'S WORK\nLandlord shall deliver the Premises in vanilla shell condition.",
    "EXHIBIT B - LANDLORD'S WORK (continued)\nHVAC units in working order.",
    "EXHIBIT C\nCOMMENCEMENT DATE CERTIFICATE\nThe Commencement Date is ____________.",
    "FIRST AMENDMENT TO LEASE\nThis First Amendment is made as of June 1, 2023.",
    "RIDER NO. 1\nAdditional provisions.",
]
ins = split_instruments(PAGES)
ex = find_exhibits(PAGES, ins)
test('the attached commencement certificate does not start an instrument',
     [i.event_type for i in ins] == ['original lease', 'amendment'])
test('each exhibit with its label and printed title', [(e.label, e.title) for e in ex][:3]
     == [('Exhibit A', 'SITE PLAN'), ('Exhibit B', "LANDLORD'S WORK"), ('Exhibit C', 'COMMENCEMENT DATE CERTIFICATE')])
test('an exhibit runs across its continued page', ex[1].page_from == 4 and ex[1].page_to == 5)
test('an exhibit ends where the next instrument starts', ex[2].page_to == 6)
test('exhibits are parented to the instrument they are filed in',
     [e.parent_seq for e in ex] == [1, 1, 1, 2])
test('a rider counts as an attachment', ex[3].label == 'Rider 1')
test('"shown on Exhibit A" in running text is a reference, not an exhibit', not any(e.page_from == 1 for e in ex))

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
