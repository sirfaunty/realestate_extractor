"""
Tests for lease_roster grouping rules. Fictional file names and tenants that
copy the real patterns found on the pilot properties (2026-09-29).

    venv/Scripts/python tests/test_lease_roster.py
"""

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.lease_roster import (  # noqa: E402
    RosterRow, _check_rent_roll, _clean_identity, _common_filename_words, _filename_stem,
    _file_trade_name, _kind, _needs, _restated_sf, summarize)

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


def test_generic_folder_never_becomes_the_tenancy():
    # imported files keep their folder path ('__'); a numbered staging folder
    # names no tenant, a folder named for the tenant does
    files = ['05_source_documents__Willow Plaza - Rosa Tacos Scanned Lease.pdf',
             '05_source_documents__Willow Plaza - Fern Dental Scanned Lease.pdf',
             'Scanned Leases__Willow Plaza - Juniper Florist Lease.pdf',
             'LeaseDocuments__QuinceBakery__Checklist.pdf']
    common = _common_filename_words(files[:3])
    stems = [_filename_stem(f, common) for f in files]
    assert stems[0] != stems[1] and 'rosa' in stems[0] and 'fern' in stems[1] and 'juniper' in stems[2], stems
    assert 'quincebakery' in stems[3], stems


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
    # an upload keeps underscores in its file name
    tower = RosterRow(tenant='', filenames=['Elm_Court_-_Tower_Scanned_License_Agreement.pdf'])
    assert _kind(tower, 'LEASE DOCUMENT CHECKLIST ...', as_of) == 'other_agreement'
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


def test_relocation_delivery_and_later_recital_restate_the_premises():
    # a relocation amendment hands over a smaller suite; the roster must read it
    # off the text the way the lease reader does (the stored term may predate it)
    t = """LEASE. Landlord leases to Tenant approximately 2,000 square feet known as
    Suite 4 of the Pinecrest Commons. ... Tenant shall pay $1.00 per 2,000 square feet ...
    RELOCATION AMENDMENT. Landlord shall deliver to Tenant (including keys to all
    doors) possession of that certain space containing approximately 1,350 square
    feet having a street address of 88 Pinecrest Way, Suite #12 (the "New Premises").
    ... SECOND MODIFICATION ... (collectively the "Lease"), for certain real property
    containing approximately 1,350 square feet located at 88 Pinecrest Way, Suite #12."""
    assert _restated_sf(t) == 1350



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
    c.execute("UPDATE documents SET analysis_status = 'analyzed'")
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



def test_extension_amendment_sets_the_expiration():
    # the amendment only STATES the new date (lease_expiration); the lease
    # carries the governing one -> the later instrument's date wins
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import build_roster
    db = Database(os.path.join(tempfile.mkdtemp(), 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    for i, fn in ((1, 'Elm Court - Bean Coffee Lease.pdf'),
                  (2, 'Elm Court - Bean Coffee First Amendment.pdf'),
                  (3, 'Elm Court - Pet Barn Lease.pdf')):
        c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
                  "VALUES (?, ?, 'x', 'lease', 1)", (i, fn))
    c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw, expiration_date) "
              "VALUES (1, 'governing_expiration', '2026-08-31', '2026-08-31')")
    c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw) "
              "VALUES (2, 'lease_expiration', '06/30/2029')")
    c.execute("UPDATE documents SET analysis_status = 'analyzed'")
    c.commit()
    r = [x for x in build_roster(c, 1, as_of=date(2026, 9, 30)) if x.label == 'bean coffee'][0]
    db.close()
    assert r.expiration == date(2029, 6, 30) and r.kind == 'tenancy', r
    assert 'past expiration' not in r.needs



def _expansion_roster(amendment_text):
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import build_roster
    db = Database(os.path.join(tempfile.mkdtemp(), 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    for i, fn in ((1, 'Elm Court - Bean Coffee Lease.pdf'),
                  (2, 'Elm Court - Bean Coffee First Amendment.pdf'),
                  (3, 'Elm Court - Pet Barn Lease.pdf')):
        c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
                  "VALUES (?, ?, 'x', 'lease', 1)", (i, fn))
    for ty, v in (('square_footage', '2,000'), ('base_rent', '$4,000.00'), ('escalation_rate', '3%')):
        c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw) VALUES (1, ?, ?)", (ty, v))
    c.execute("INSERT INTO document_fulltext (document_id, page_number, content) VALUES ('1', '1', ?)",
              ('Landlord leases to Tenant approximately 2,000 rentable square feet.',))
    c.execute("INSERT INTO document_fulltext (document_id, page_number, content) VALUES ('2', '1', ?)",
              (amendment_text,))
    c.execute("UPDATE documents SET analysis_status = 'analyzed'")
    c.commit()
    rows = build_roster(c, 1, as_of=date(2026, 9, 30),
                        confirmed_commencements={'fn:bean coffee': '2022-10-01'})
    db.close()
    return [x for x in rows if x.label == 'bean coffee'][0]


def test_expansion_holds_back_a_year1_rolled_rent():
    # confirmed date + stated Y1 + stated % would be high — but an amendment
    # enlarged the premises, so the original Year-1 rent no longer applies
    r = _expansion_roster('2. Expansion of Premises. The Premises are expanded such that '
                          'the Premises shall consist of a combined total of approximately '
                          '2,900 rentable square feet. 3. Base Rent shall be adjusted.')
    assert r.square_feet == 2900 and not r.show_rent, r
    assert 'premises_changed_since_year1' in r.rent_flags and 'confirm the current rent' in r.needs


def test_unanalyzed_document_says_run_analyze_and_holds_back_rent():
    # uploaded, never run through Analyze: the one action is Analyze, not
    # "add the rent exhibit" (staging 2026-10-01, Lantern Bay)
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import build_roster
    db = Database(os.path.join(tempfile.mkdtemp(), 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    for i, fn in ((1, 'Elm Court - Bean Coffee Lease.pdf'),
                  (2, 'Elm Court - Bean Coffee First Amendment.pdf'),
                  (3, 'Elm Court - Pet Barn Lease.pdf')):
        c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id, "
                  "analysis_status) VALUES (?, ?, 'x', 'lease', 1, ?)",
                  (i, fn, 'ingested' if i == 2 else 'analyzed'))
    for ty, v in (('square_footage', '2,000'), ('base_rent', '$4,000.00'), ('escalation_rate', '3%')):
        c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw) VALUES (1, ?, ?)", (ty, v))
    c.commit()
    rows = build_roster(c, 1, as_of=date(2026, 9, 30),
                        confirmed_commencements={'fn:bean coffee': '2022-10-01'})
    db.close()
    bean = [x for x in rows if x.label == 'bean coffee'][0]
    pet = [x for x in rows if x.label == 'pet barn'][0]
    assert not bean.show_rent and bean.needs.startswith('not analyzed yet'), bean
    assert 'not_analyzed' not in pet.rent_flags, pet


def test_confirmed_commencement_shows_escalated_rent():
    # same lease, amendment that doesn't touch the premises -> rent counts
    r = _expansion_roster('2. Notices. Notices to Tenant shall be sent to the Premises.')
    assert r.square_feet == 2000 and r.show_rent and r.monthly_rent == 4370.91, r



def test_past_stated_date_does_not_expire_a_tenancy():
    # a 2nd amendment states 10/31/2014, a later 3rd amendment states no
    # date we could read: the stale date must not mark the tenancy expired
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import build_roster
    db = Database(os.path.join(tempfile.mkdtemp(), 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    for i, fn in ((1, 'Elm Court - Bean Coffee Lease.pdf'),
                  (2, 'Elm Court - Bean Coffee 2nd Amendment.pdf'),
                  (3, 'Elm Court - Bean Coffee 3rd Amendment.pdf'),
                  (4, 'Elm Court - Pet Barn Lease.pdf')):
        c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
                  "VALUES (?, ?, 'x', 'lease', 1)", (i, fn))
    c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw) "
              "VALUES (2, 'lease_expiration', '10/31/2014')")
    c.execute("UPDATE documents SET analysis_status = 'analyzed'")
    c.commit()
    r = [x for x in build_roster(c, 1, as_of=date(2026, 9, 30)) if x.label == 'bean coffee'][0]
    db.close()
    assert r.expiration is None and r.kind == 'tenancy', r


def test_unassigned_upload_does_not_recount_the_portfolio():
    # one lease not yet matched to a property must add one tenancy, not
    # re-count every lease in the org (staging 2026-10-01: 12 -> 25)
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import portfolio_summary
    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    for i, (fn, pid) in enumerate([('Elm Court - Bean Coffee Lease.pdf', 1),
                                   ('Elm Court - Pet Barn Lease.pdf', 1),
                                   ('Elm Court - Lamp Shop Lease.pdf', None)], 1):
        c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
                  "VALUES (?, ?, 'x', 'lease', ?)", (i, fn, pid))
    c.execute("UPDATE documents SET analysis_status = 'analyzed'")
    c.commit()
    s = portfolio_summary(db, as_of=date(2026, 10, 1))
    db.close()
    assert s['tenancies'] == 3, s
    by_pid = {p['property_id']: p['tenancies'] for p in s['properties']}
    assert by_pid == {1: 2, None: 1}, by_pid


def test_confirmed_commencement_finishes_a_full_months_term():
    # "ending on the last day of the 63rd full calendar month after the
    # Commencement Date", commencement set later: confirming it gives the date
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import build_roster
    db = Database(os.path.join(tempfile.mkdtemp(), 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    for i, fn in ((1, 'Elm Court - Bean Coffee Lease.pdf'), (2, 'Elm Court - Pet Barn Lease.pdf')):
        c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id, "
                  "analysis_status) VALUES (?, ?, 'x', 'lease', 1, 'analyzed')", (i, fn))
        c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw) "
                  "VALUES (?, 'term_full_months', ?)", (i, '63' if i == 1 else '36'))
    c.commit()
    as_of = date(2026, 10, 1)
    keys = {r.label: r.key for r in build_roster(c, 1, as_of=as_of)}
    assert all(r.expiration is None for r in build_roster(c, 1, as_of=as_of))
    rows = {r.label: r for r in build_roster(c, 1, as_of=as_of, confirmed_commencements={
        keys['bean coffee']: '2022-01-04',      # 63 full months from Feb 2022
        keys['pet barn']: '2020-03-01'})}       # 36 months: ended 2023 -> left out
    db.close()
    assert rows['bean coffee'].expiration == date(2027, 4, 30), rows['bean coffee'].expiration
    assert rows['bean coffee'].expiration_source == 'computed'
    assert rows['pet barn'].expiration is None and rows['pet barn'].kind == 'tenancy'


def test_store_name_from_the_file_name():
    common = {'elm', 'court', 'ecp'}
    assert _file_trade_name("Elm Court - Rosa's Tacos Scanned Lease.pdf", 'rosa tacos', common) == "Rosa's Tacos"
    assert _file_trade_name('Elm Court Plaza I Z-Golf Scanned Lease.pdf', 'golf', common | {'plaza'}) == 'Z-Golf'
    assert _file_trade_name('Elm Court - QR Nails Scanned Lease.pdf', 'nails', common) == 'QR Nails'
    assert _file_trade_name('ECP Pine Pets - ECP-Pine Pets 4th Lease Amendment.pdf', 'pine pets', common) == 'Pine Pets'
    assert _file_trade_name('Elm Court - Birch Goods FKA Oak Health Scanned.pdf', 'birch goods', common) == 'Birch Goods'
    assert _file_trade_name('Elm Court - Lark Smiles (aka Lark Dental) Lease.pdf', 'lark smiles', common) == 'Lark Smiles'
    assert _file_trade_name('Elm_Court_-_Rosas_Tacos_Scanned_Lease.pdf', 'rosas tacos', common) == 'Rosas Tacos'
    assert _file_trade_name('Elm Court - Lamp Shop Lease.pdf', 'rosa tacos', common) is None


def test_roster_shows_the_store_name_beside_a_personal_legal_name():
    import tempfile
    from realestate_extractor.database import Database
    from realestate_extractor.lease_roster import build_roster
    db = Database(os.path.join(tempfile.mkdtemp(), 'org_test.db'))
    db.connect()
    c = db.conn
    c.execute("INSERT INTO properties (id, name) VALUES (1, 'Elm Court')")
    for i, fn, who in ((1, "Elm Court - Rosa's Tacos Scanned Lease.pdf", 'MARIA LOPEZ MARIN'),
                       (2, 'Elm Court - Bean Coffee Lease.pdf', 'Bean Coffee, Inc.'),
                       (3, 'Elm Court - Pet Barn Lease.pdf', 'Pet Barn Holdings')):
        c.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id, "
                  "analysis_status) VALUES (?, ?, 'x', 'lease', 1, 'analyzed')", (i, fn))
        c.execute("INSERT INTO financial_terms (document_id, term_type, value_raw) "
                  "VALUES (?, 'tenant_identity', ?)", (i, who))
    c.commit()
    names = {r.tenant for r in build_roster(c, 1, as_of=date(2026, 10, 1))}
    db.close()
    # the store name is added only where the legal name shares no word with it
    assert names == {'Bean Coffee, Inc.', 'Pet Barn Holdings', "MARIA LOPEZ MARIN / Rosa's Tacos"}, names


def test_need_attention_counts_tenancies_not_expired_leases():
    # "9 of 17 confirmed" beside "9 need attention" read as flipped when the
    # expired lease was counted in the second number
    rows = [RosterRow(tenant='Bean Coffee', monthly_rent=2000.0, rent_confidence='high'),
            RosterRow(tenant='Pet Barn', needs='confirm the commencement date'),
            RosterRow(tenant='Old Anchor Inc.', kind='expired', needs='confirm it has ended')]
    s = summarize(rows, as_of=date(2026, 10, 1))
    assert s['tenancies'] == 2 and s['rent_confirmed_for'] == 1, s
    assert s['needs_review'] == [('Pet Barn', 'confirm the commencement date')], s['needs_review']
    assert s['expired_on_paper'] == 1, s



def test_one_tenancy_filed_under_label_variants_is_one_row():
    from realestate_extractor.lease_roster import _merge_label_variants, _same_label
    # spacing, filing words, a glued filing word, a typo, a repeated word
    assert _same_label('maplecrest', 'maple crest')
    assert _same_label('leasedocuments harborviewdance', 'harborviewdance scannedlease')
    assert _same_label('pinewoodbakery', 'pinewoodbakery fid')
    assert _same_label('ostrander chiropractic', 'ostradner chiropractic')
    assert _same_label('fenwick', 'fenwick fenwick')
    # the start of a longer label counts only when both name the same tenant
    assert _same_label('red kite', 'redkitepizzascannedlease', same_tenant=True)
    assert not _same_label('red kite', 'redkitepizzascannedlease')
    # different names stay apart
    assert not _same_label('salon one', 'salon two', same_tenant=True)
    assert not _same_label('oak grill', 'oak grove', same_tenant=True)
    g = {'fn:maplecrest': RosterRow(tenant='', label='maplecrest', doc_ids=[1, 2], filenames=['a', 'b'],
                                    aliases=['Maplecrest Dental PA']),
         'fn:maple crest': RosterRow(tenant='', label='maple crest', doc_ids=[3], filenames=['c'],
                                     aliases=['Maplecrest Dental PA']),
         'fn:fenwick': RosterRow(tenant='', label='fenwick', doc_ids=[4], filenames=['d'], aliases=['Fenwick LLC']),
         'fn:fenwick fenwick': RosterRow(tenant='', label='fenwick fenwick', doc_ids=[5], filenames=['e'],
                                         aliases=['Birch Optical Inc'])}
    _merge_label_variants(g)
    assert sorted(g['fn:maplecrest'].doc_ids) == [1, 2, 3] and 'fn:maple crest' not in g, g.keys()
    # the same label but two different legal tenants: two leases, kept apart
    assert 'fn:fenwick' in g and 'fn:fenwick fenwick' in g, g.keys()


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
