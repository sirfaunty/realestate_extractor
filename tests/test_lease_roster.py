"""
Tests for lease_roster grouping rules. Fictional file names and tenants that
copy the real patterns found on the Sponsor pilot properties (2026-09-29).

    venv/Scripts/python tests/test_lease_roster.py
"""

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.lease_roster import (  # noqa: E402
    RosterRow, _check_rent_roll, _clean_identity, _common_filename_words, _filename_stem,
    _kind, _needs, _restated_sf)

FILES = [
    'OKP Harbor Hardware - OK-HH 1st Lease Amendment 05 20 1992.pdf',
    'OKP Harbor Hardware - OK-HH 7th Lease Amendment 04 28 2017.pdf',
    'OKP Harbor Hardware - Assignment & Assumption of Lease 05 01 2001.pdf',
    'OKP Harbor Hardware - Lease Agreement 02 11 1992.pdf',
    'OKP Blue Lotus Spa - Lease Agreement 06 28 2010.pdf',
    'OKP Blue Lotus Spa - Second Amendment to Lease 03 25 2015.pdf',
    'OKP Quick Print-Copy Hub - First Amendment to Lease 2021.pdf',
    'OKP Quick Print-Copy Hub - Lease 06 14 2016 (Signed).pdf',
    'OKP Northside Bakery - Lease.pdf',
    'OKP Fern Dental - Lease.pdf',
]


def test_property_prefix_is_common():
    common = _common_filename_words(FILES)
    assert 'okp' in common and 'harbor' not in common, common


def test_stems_follow_the_tenancy_not_the_legal_name():
    common = _common_filename_words(FILES)
    stems = {_filename_stem(f, common) for f in FILES[:4]}
    assert stems == {'harbor hardware'}, stems        # 4 files, one tenancy
    assert _filename_stem(FILES[4], common) == 'blue lotus'
    assert _filename_stem(FILES[6], common) == _filename_stem(FILES[7], common) == 'quick print'


def test_tenant_word_in_every_name_is_not_the_prefix():
    # small property: "Bright" is in all 5 names, but leads only 4 tenant parts
    files = ['Elm Court II Bright Market Scanned Lease.pdf',
             'Elm Court II Bright Market Estoppel.pdf',
             'Elm Court II Bright Market Checklists and Notes.pdf',
             'Elm Court II Bright Market Lease Correspondence.pdf',
             'Elm Court II Corner Wine Bright Scanned Lease.pdf']
    common = _common_filename_words(files)
    assert common == {'elm', 'court'}, common
    assert {_filename_stem(f, common) for f in files[:4]} == {'bright market'}
    assert _filename_stem(files[4], common) == 'corner wine'


def test_center_words_never_become_tenant_words():
    files = ['Oakdale - Pet Barn Scanned Lease.pdf',
             'Oakdale - Nail Studio Scanned Lease.pdf',
             'Oakdale - Taco Stop Scanned Lease.pdf',
             'Oakdale Shopping Center - Frame Works Scanned Lease.pdf',
             'Oakdale Shopping Center - Bean Coffee Scanned Lease.pdf']
    common = _common_filename_words(files)
    assert _filename_stem(files[3], common) == 'frame works'
    assert _filename_stem(files[4], common) == 'bean coffee'


def test_single_file_property_has_no_common_words():
    assert _common_filename_words(['Only Plaza - Anchor Market Lease.pdf']) == set()


def test_identity_rejects_placeholders_and_landlord_names():
    common = {'harbor', 'point', 'plaza'}
    assert _clean_identity('the d/b/a or store brand if st', common) is None
    assert _clean_identity('HARBOR POINT PLAZA', common) is None       # the center itself
    assert _clean_identity('SHOPPING CENTER', common) is None
    assert _clean_identity('Licensee and its affiliates', common) is None
    assert _clean_identity('ASSOCIATES, LLC', common) is None
    assert _clean_identity('LLC', common) is None
    assert _clean_identity('Blue Lotus Spa, LLC', common) == 'Blue Lotus Spa, LLC'


def test_kind_other_agreement_and_expired():
    as_of = date(2026, 6, 30)
    tel = RosterRow(tenant='Comcast Cable Communications Management, LLC')
    assert _kind(tel, 'RIGHT OF ENTRY AGREEMENT ...', as_of) == 'other_agreement'
    sign = RosterRow(tenant='Metro Signs', filenames=['Plaza - Billboard License Agreement.pdf'])
    assert _kind(sign, '', as_of) == 'other_agreement'
    gone = RosterRow(tenant='Old Anchor Inc.', expiration=date(2018, 2, 1), rent_method='flat')
    assert _kind(gone, 'LEASE AGREEMENT', as_of) == 'expired'
    # expiration on paper is old, but a schedule covers today -> still a tenancy
    renewed = RosterRow(tenant='Shoe Co', expiration=date(2001, 8, 31), rent_method='schedule')
    assert _kind(renewed, 'LEASE AGREEMENT', as_of) == 'tenancy'


# --- paper vs uploaded rent roll (date-free) ---------------------------

SCHEDULE = """Minimum Rent shall be as follows:
Period Annual Rent Monthly Rent
3/1/2020 to 2/28/2025 $60,000.00 $5,000.00
3/1/2025 to 2/28/2030 $63,000.00 $5,250.00"""


def _row(m=5250.00):
    return RosterRow(tenant='Shoe Co', monthly_rent=m, rent_method='schedule',
                     rent_confidence='high')


def test_rent_roll_agrees_keeps_high():
    r = _row()
    _check_rent_roll(r, 5250.00, SCHEDULE)
    assert r.show_rent and 'matches_rent_roll' in r.rent_flags and _needs(r) == ''


def test_rent_roll_on_another_step_is_a_timing_question():
    # an older snapshot still showing the previous step: not a conflict,
    # but not shown unreviewed either
    r = _row()
    _check_rent_roll(r, 5000.00, SCHEDULE)
    assert not r.show_rent and 'rent_roll_on_other_step' in r.rent_flags
    assert 'another step' in _needs(r)


def test_rent_roll_conflict_is_routed_to_reconcile():
    # two suites summed on the rent roll (4,100 + 1,900) match nothing on paper
    r = _row()
    _check_rent_roll(r, 6000.00, SCHEDULE)
    assert not r.show_rent and 'conflicts_with_rent_roll' in r.rent_flags
    assert _needs(r) == 'reconcile: lease says $5,250.00/mo, rent roll says $6,000.00/mo'


def test_exact_rent_roll_match_confirms_a_low_confidence_rent():
    # flat Year-1 rent, no escalation stated: paper alone can't prove it's
    # still in force; the rent roll billing the same amount does
    r = RosterRow(tenant='Pho Place', monthly_rent=1500.00, rent_method='flat',
                  rent_confidence='low', rent_flags=['no_escalation_stated'])
    _check_rent_roll(r, 1500.00, '')
    assert r.show_rent and 'confirmed_by_rent_roll' in r.rent_flags and _needs(r) == ''


def test_near_rent_roll_match_does_not_confirm():
    # within 2% but not the same number -> agreement noted, not proof
    r = RosterRow(tenant='Pho Place', monthly_rent=1500.00, rent_method='flat',
                  rent_confidence='low')
    _check_rent_roll(r, 1520.00, '')
    assert not r.show_rent and 'matches_rent_roll' in r.rent_flags
    assert 'confirmed_by_rent_roll' not in r.rent_flags


def test_confirmed_rent_keeps_the_expiration_question():
    # the rent is settled, the lease status is not
    r = RosterRow(tenant='Gift Co', monthly_rent=4000.00, rent_method='schedule',
                  rent_confidence='medium', rent_flags=['past_expiration'])
    _check_rent_roll(r, 4000.00, '')
    assert r.show_rent
    assert _needs(r) == 'lease past its expiration on paper — add the renewal / holdover terms'



# --- premises restated by later instruments ---------------------------

def test_expansion_restates_the_premises():
    t = """ARTICLE 1. Landlord leases to Tenant approximately 4,000 square feet.
    ... 1 ton of air conditioning for every 350 square feet of Premises ...
    FIRST AMENDMENT. Landlord and Tenant desire to expand the Original Premises,
    thereby enlarging the total square foot area of the premises from 4,000
    square feet to 5,500 square feet (the "Combined Premises")."""
    assert _restated_sf(t) == 5500


def test_relocation_and_recital_latest_wins():
    t = """RECITALS: A. Landlord is currently leasing to Tenant approximately
    2,200 square feet of space known as Suite 110. ... The parties agree that the
    total rentable square feet of the New Premises is approximately 3,100 square
    feet. 3. Extended Term."""
    assert _restated_sf(t) == 3100


def test_spelled_out_area_with_digits_in_brackets():
    t = """the Leased Premises shall thereafter contain approximately twelve
    thousand five hundred (12,500) square feet of floor area."""
    assert _restated_sf(t) == 12500


def test_no_restatement_means_none():
    t = """Landlord leases approximately 4,000 square feet. Signage criteria for
    tenants having 5,000 square feet or more; HVAC per 350 square feet."""
    assert _restated_sf(t) is None



# --- expiration: uploaded rent roll vs paper (build_roster, temp DB) -----

def _roster_with_rent_roll(lease_end_on_roll):
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import build_roster
    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
              "VALUES (1, 'Elm Court - Bean Coffee Lease.pdf', 'x', 'lease', 1)")
    c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw, expiration_date) "
              "VALUES (1, 'governing_expiration', '2024-12-31', '2024-12-31')")
    c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
              "VALUES (3, 'Elm Court - Pet Barn Lease.pdf', 'x', 'lease', 1)")
    c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
              "VALUES (2, 'Elm Court Rent Roll.pdf', 'x', 'rent_roll', 1)")
    c.execute("INSERT INTO rent_roll_entries (document_id, property_id, tenant_name, lease_start, "
              "lease_end, monthly_rent) VALUES (2, 1, 'Bean Coffee', '2020-01-01', ?, 2500)",
              (lease_end_on_roll,))
    c.commit()
    rows = [r for r in build_roster(c, 1, as_of=date(2026, 6, 30)) if r.label == 'bean coffee']
    db.close()
    return rows[0]


def test_rent_roll_expiration_wins_and_difference_is_flagged():
    # paper says the lease ended 2024; the rent roll shows a 2029 extension
    r = _roster_with_rent_roll('2029-12-31')
    assert r.expiration == date(2029, 12, 31) and r.expiration_source == 'rent_roll', r
    assert r.expiration_lease == date(2024, 12, 31)
    assert 'expiration_differs_from_lease' in r.flags
    assert r.kind == 'tenancy'            # not "expired" — the landlord still bills it


def test_paper_expiration_kept_without_rent_roll_end():
    r = _roster_with_rent_roll(None)
    assert r.expiration == date(2024, 12, 31) and r.expiration_source == 'lease', r
    assert 'expiration_differs_from_lease' not in r.flags



if __name__ == '__main__':
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_')]
    for name, fn in tests:
        try:
            fn()
            print(f'  ok    {name}')
        except AssertionError as e:
            fails += 1
            print(f'  FAIL  {name}: {e}')
        except Exception as e:           # noqa: BLE001
            fails += 1
            print(f'  ERROR {name}: {type(e).__name__}: {e}')
    print(f'\n{len(tests) - fails}/{len(tests)} passed')
    sys.exit(1 if fails else 0)
