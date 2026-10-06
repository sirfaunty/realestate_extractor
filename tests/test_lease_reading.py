"""
Regression tests for the deterministic lease reader (extractors/lease_segmenter.py):
parties, Year-1 rent, security deposit, premises SF and expiration.

Every fixture is FICTIONAL (invented parties, places, dollars) but copies the
LAYOUT of a real lease passage that once broke the reader — found by the
2026-10 tie-outs on publicly filed leases and the pilot properties. Client
text never goes in git; the shapes do.

    venv/Scripts/python tests/test_lease_reading.py      (no pytest needed)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.lease_segmenter import (  # noqa: E402
    RE_EXPIRE_STMT, _party, _scan_expiration, _security_deposit, _sf_scan, _to_mdy,
    _year1_monthly_rent)


def flat(s):
    return ' '.join(s.split())


def best_sf(text, base=0):
    c = []
    _sf_scan(text, base, 1, c)
    return max(c, key=lambda x: x[0])[1] if c else None


def stated(text):
    m = RE_EXPIRE_STMT.search(text)
    return _to_mdy(next(g for g in m.groups() if g)) if m else None


def scan(text):
    return _scan_expiration([{'kind': 'lease', 'pages': [(1, text)], 'page_start': 1}])[0]


# ─── parties ─────────────────────────────────────────────────────────

def test_air_form_lessor_lessee():
    t = flat('This Lease is made by and between Harlan Ridge Partners, LLC ("Lessor") '
             'and Bluefin Robotics, Inc. ("Lessee"), (collectively the "Parties").')
    assert _party(t, 'Landlord') == 'Harlan Ridge Partners, LLC', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Bluefin Robotics, Inc', _party(t, 'Tenant')


def test_herein_called_and_uppercase_connective():
    t = flat('by and between ELM COURT HOLDINGS (herein called “Landlord”) AND '
             'NORTHFIELD GROCERS, INC., a Delaware corporation (herein called “Tenant”).')
    assert _party(t, 'Landlord') == 'ELM COURT HOLDINGS'
    assert _party(t, 'Tenant') == 'NORTHFIELD GROCERS, INC'


def test_as_quoted_role_and_by_and_between():
    t = flat('BY AND BETWEEN BIRCH OFFICE, LLC AS "LANDLORD" and PINE AIR, LLC AS "TENANT" '
             'DATED AS OF MAY 1, 2017')
    assert _party(t, 'Landlord') == 'BIRCH OFFICE, LLC', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'PINE AIR, LLC'


def test_summary_labels():
    t = flat('1.1 Date: March 3, 2019 1.2 Landlord: Oak Square Fund II, L.P., a Delaware '
             'limited partnership 1.3 Tenant: GLOWWORM LABS COMPANY a Nevada corporation '
             '1.4 Premises address: 12 Example Road')
    assert _party(t, 'Landlord') == 'Oak Square Fund II, L.P', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'GLOWWORM LABS COMPANY', _party(t, 'Tenant')


def test_hereinafter_referred_to_without_parens():
    t = flat('made by and between Cedar Station, L.L.C., a New Jersey limited liability company, '
             'with a principal office at 10 Mill Road, Warren, New Jersey 07000, hereinafter '
             'referred to as “LANDLORD”, AND Maple Tack Retail, Inc., a corporation, '
             'hereinafter referred to as “TENANT”.')
    assert _party(t, 'Landlord') == 'Cedar Station, L.L.C', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Maple Tack Retail, Inc', _party(t, 'Tenant')


def test_address_is_not_a_tenant():
    t = flat('AND QUARTZ APPAREL, INC., a Nevada corporation, whose address is: 5800 East '
             'Example Avenue, Commerce, CA 90040 ("Tenant").')
    assert _party(t, 'Tenant') == 'QUARTZ APPAREL, INC', _party(t, 'Tenant')


def test_owner_role_and_party_of_the_first_part():
    t = flat('Agreement of Lease, made as of this ___ day of August 2010, between JANE EXAMPLE, '
             'having an office at 100 Field Street, party of the first part, hereinafter '
             'referred to as OWNER, and PEAK BEVERAGE INC., party of the second part, '
             'hereinafter referred to as TENANT, witnesseth')
    assert _party(t, 'Landlord') == 'JANE EXAMPLE', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'PEAK BEVERAGE INC', _party(t, 'Tenant')


def test_hereinafter_called_owner_lowercase_entity():
    t = flat('by and between Oaks Plaza Venture, LP a Texas limited partnership, hereinafter '
             'called Owner, and Example Bank, SSB, hereinafter called Tenant.')
    assert _party(t, 'Landlord') == 'Oaks Plaza Venture, LP', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Example Bank, SSB'


def test_quoted_summary_labels():
    t = flat('(a) Date of Lease: March 28, 2005 (b) "Landlord": B. K. Example, Inc. Address: '
             '402 Bayard Road (c) "Tenant": First Example Bank Address: 9 Main')
    assert _party(t, 'Landlord') == 'B. K. Example, Inc', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'First Example Bank', _party(t, 'Tenant')


# ─── Year-1 monthly rent ─────────────────────────────────────────────

def test_rent_per_year_with_monthly_in_parens():
    t = flat('Rental Commencement Date - 11/30/2014 $51,000.00 per year ($4,250.00 per month)')
    assert _year1_monthly_rent(t, t)[0] == 4250.00


def test_rent_regular_installment_after_first_month():
    t = flat('the Rent shall be $150,000.00, payable $40,000.00 for the first month and '
             '$10,000.00 for each of the second through twelfth months.')
    assert _year1_monthly_rent(t, t)[0] == 10000.00


def test_rent_stated_per_month():
    t = flat('1.5 Base Rent: $ 2,250.00 per month ("Base Rent"), payable on the first day')
    assert _year1_monthly_rent(t, t)[0] == 2250.00


def test_rent_table_header_annual_first_is_not_monthly():
    # "ANNUAL RENT MONTHLY RENT" header: the first amount after "monthly" is ANNUAL
    t = flat('fixed annual Minimum Rent: PERIOD ANNUAL RENT MONTHLY RENT Lease Year 1 '
             '$88,380.00 $7,365.00 Lease Year 2 $88,380.00 $7,365.00')
    assert _year1_monthly_rent(t, t)[0] == 7365.00


def test_pair_monthly_then_annual():
    t = flat('Base Rent schedule: 4/1/2015 3/31/2016 $0.7159 $16,003.95 $192,047.32')
    assert _year1_monthly_rent(t, t)[0] == 16003.95


def test_option_period_rent_is_not_year1():
    t = flat('FOR OPTION PERIODS: During the option periods of this Lease, Tenant shall pay '
             'rent as follows: Lease Years 16-20 Monthly Rent $26,666.74')
    assert _year1_monthly_rent(t, t) is None


def test_rate_times_sf_monthly():
    t = flat('The initial Base Rent shall be $1.91 per Rentable sq. ft., per month (NNN). '
             'Lease Year Rate Monthly Installments of Base Rent 1 $ 1.91 $ 110,002.63')
    assert _year1_monthly_rent(t, t, sf=57593)[0] == 110002.63


def test_annual_rent_divided_by_twelve():
    t = flat('the Minimum Annual Rent shall be: Eighty-four Thousand Eight Hundred Three and '
             '60/100 Dollars ($84,803.60) per annum; and commencing on the first day')
    assert _year1_monthly_rent(t, t)[0] == round(84803.60 / 12, 2)


# ─── security deposit ────────────────────────────────────────────────

def test_deposit_none():
    assert _security_deposit(flat('Security Deposit: None. (Section 5.3)')) is None


def test_deposit_label_not_neighbouring_cam():
    t = flat('(a) Base Rent: $3,229.15 for the period. (b) Common Area Operating Expenses: '
             '$1,367.64 for the period. (c) Security Deposit: $3,229.15 ("Security Deposit").')
    assert _security_deposit(t)[0] == 3229.15


def test_deposit_total_beats_parts_and_tax():
    t = flat('(c) Rental Tax (2.3%): $368.09 (d) Security Deposit: Tenant to pay an additional '
             'security deposit of $13,000.00. Landlord currently holding a security deposit '
             'of $3,135.60. Total security deposit to be $16,135.60.')
    assert _security_deposit(t)[0] == 16135.60


def test_deposit_installment_is_not_the_deposit():
    t = flat('($90,000.00) in the form of a Security Deposit. The first installment of the '
             'Security Deposit in the amount of $7,500.00 shall be paid concurrently')
    assert _security_deposit(t)[0] == 90000.00


def test_deposit_with_landlord_as_security():
    t = flat('Upon execution hereof Tenant shall deposit with Landlord the sum of $9,800.00 as '
             'security for the faithful performance of Tenant\'s obligations.')
    assert _security_deposit(t)[0] == 9800.00


def test_deposit_not_the_combined_first_payment():
    t = flat('shall represent the first one month rental and two month(s) security deposit in '
             'the amount of $9,000.00. Lessee has deposited with Lessor the sum of Six Thousand '
             'Dollars and 00/100 ($6,000.00) as security for the full performance')
    assert _security_deposit(t)[0] == 6000.00


def test_deposit_label_with_alternative_wording():
    assert _security_deposit(flat("Security Deposit: Two month's base rent, or $15,000.00"))[0] \
        == 15000.00
    assert _security_deposit(flat("Tenant's Security Deposit shall be $24,000.00 and"))[0] == 24000.00


# ─── premises SF ─────────────────────────────────────────────────────

def test_premises_beats_development_recital():
    t = ('Landlord owns a mixed-use development consisting of (i) one two-story building '
         'containing approximately 21,700 square feet of office space on the second floor and '
         '(ii) a free standing restaurant buildings totaling approximately 1,600 square feet. '
         'Landlord hereby leases to Tenant the Leased Premises containing approximately '
         '4,200 square feet in Cedar Station.')
    assert best_sf(t) == 4200, best_sf(t)


def test_cam_example_is_not_the_premises():
    t = ('By way of example, if an invoice for $100.00 is for a service that relates only '
         'to 15,000 square feet of buildings, the denominator of which shall be 15,000 square feet.')
    c = []
    _sf_scan(t, 0, 1, c)
    assert c and max(x[0] for x in c) < 0, c


def test_restated_premises_beats_increment_and_prior_size():
    t = ('A. The size of the Demised Premises shall increase by 420 square feet '
         '(from 3,071 square feet to 3,491 square feet).')
    assert best_sf(t) == 3491, best_sf(t)


def test_total_rentable_beats_component():
    t = ('Suite 100 consists of approximately 3,800 total rentable square feet, with '
         'approximately 1,600 rentable square feet of warehouse space.')
    assert best_sf(t) == 3800, best_sf(t)


def test_substitution_clause_is_not_a_restatement():
    # landlord's relocation right: "New Premises" + a size THRESHOLD in words
    t = ('Landlord may substitute other space for the Leased Premises, provided: (a) Landlord '
         'gives Tenant written notice of the date Tenant is required to substitute the New '
         'Premises; (b) the Rentable Area in the Leased Premises is less than five thousand '
         '(5,000) square feet.')
    c = []
    _sf_scan(t, 2, 1, c, restate_only=True)
    assert not c, c


def test_new_premises_restatement_found_anywhere():
    t = ('The parties acknowledge and agree that the total rentable square feet of the New '
         'Premises is approximately 5,100 square feet. 3. Extended Term.')
    c = []
    _sf_scan(t, 2, 40, c, restate_only=True)
    assert c and c[0][1] == 5100, c


def test_cap_is_not_the_premises():
    t = ('which building shall contain approximately 1,600 rentable square feet (the "Leased '
         'Premises"). In no event shall the floor area be deemed to be more than 1,625 square feet.')
    assert best_sf(t) == 1600, best_sf(t)


# ─── expiration ──────────────────────────────────────────────────────

def test_summary_expiration_label():
    assert stated('1.7 EXPIRATION DATE: July 31, 2018. (Section 3.1)') == '7/31/2018'


def test_commencing_and_ending():
    assert stated('commencing February 1, 2018 and ending January 31, 2021') == '1/31/2021'
    assert stated('The current term began on December 1, 2023 and ends on November 30, 2028') \
        == '11/30/2028'


def test_through_expiration_date_amendment():
    assert stated('for the period from September 1, 2023 through August 31, 2030 '
                  '(the “Expiration Date”), unless sooner terminated') == '8/31/2030'


def test_day_of_form():
    assert stated('continuing thereafter to and including the 31st day of August, 2029') \
        == '8/31/2029'


def test_lease_year_ending_is_not_the_term():
    assert stated('Commencement Date is September 28, 2006, the 5th full lease year will be '
                  'the lease year ending January 31, 2012') is None


def test_option_period_statement_is_not_the_term():
    t = ('The term is extended starting on June 1, 2022 and ending on May 31, 2027. '
         'This option period shall commence April 1, 2024 and expire on March 31, 2029.')
    assert scan(t) == '5/31/2027', scan(t)


def test_option_schedule_row_is_not_the_term():
    t = ('Expiration Date: June 30, 2013. Fixed Minimum Rent: 7/1/2008 - 6/30/2010: $59,000.00 '
         '7/1/2010 - 6/30/2013: $65,000.00 Option Period: 7/1/2013 - 6/30/2018: $81,000.00')
    assert scan(t) == '6/30/2013', scan(t)


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
