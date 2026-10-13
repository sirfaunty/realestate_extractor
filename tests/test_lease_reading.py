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
    RE_EXPIRE_STMT, _TRADE_NAME_RX, _party, _scan_expiration, _security_deposit, _sf_pick, _sf_scan, _to_mdy,
    _year1_monthly_rent)


def flat(s):
    return ' '.join(s.split())


def best_sf(text, base=0):
    c = []
    _sf_scan(text, base, 1, c)
    return _sf_pick(c)[1] if c else None


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


def test_cover_title_names_tenant_and_center():
    # scanned shopping-center leases: the title page is the only place the
    # first pages name the tenant (the preamble is deeper in the package)
    t = flat('SHOPPING CENTER LEASE AGREEMENT FOR HARBOR LIGHT DENTAL, P.L.L.C. AT WILLOW BEND '
             'SHOPPING CENTER T:\\FILES\\WILLOW BEND\\LEASE.DOCX REPAIRS Section 1. Landlord shall '
             'keep the foundations in good repair.')
    assert _party(t, 'Tenant') == 'HARBOR LIGHT DENTAL, P.L.L.C', _party(t, 'Tenant')
    # body prose with "lease for ... at" is not a title
    t = flat('Tenant accepts this Lease for a term of five years at the rent stated in Article 3.')
    assert _party(t, 'Tenant') is None, _party(t, 'Tenant')


def test_basic_provisions_block_labels():
    t = flat("Section 1.1 Basic Lease Provisions DATE: January 8, 1999 LANDLORD: Willow Bend, Inc. "
             "ADDRESS OF LANDLORD: 111 Example Avenue, Suite 211 Springfield, ZZ 00000 LANDLORD'S FEDERAL "
             "ID NO: 00-0000000 TENANT: Hilltop Shoes, Inc. ADDRESS OF TENANT: 3520 Example Drive "
             "Centerville, ZZ 00001 TENANT'S TRADE NAME: Hilltop Shoes 230 8 LEASED PREMISES: Space No. 4")
    assert _party(t, 'Landlord') == 'Willow Bend, Inc', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Hilltop Shoes, Inc', _party(t, 'Tenant')
    assert _TRADE_NAME_RX.search(t).group(1).strip() == 'Hilltop Shoes'
    assert _TRADE_NAME_RX.search('Pine Air, LLC d/b/a Pine Air Fitness, a Minnesota').group(1) == 'Pine Air Fitness'


def test_cover_quoted_roles_without_parentheses_ocr():
    # cover page as a scanner reads it: stray punctuation, "Tenant" garbled
    t = flat('LEASE AGREEMENT Between. -Oakridge Holdings, Incorporated “Landlord” and, '
             'Maple Fabrics, LLC’ “Tenaot” Dated: July 22 ; 2016° measurement. Tenant and '
             'Landlord shall each be responsible for one-half of the fees.')
    assert _party(t, 'Landlord') == 'Oakridge Holdings, Incorporated', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Maple Fabrics, LLC', _party(t, 'Tenant')


def test_cover_title_mixed_case_dba_and_terminators():
    assert _party(flat('SHOPPING CENTER LEASE AGREEMENT FOR Willow Nails, LLC AT CEDAR ROW '
                       'SHOPPING CENTER Section 2. Notwithstanding'), 'Tenant') == 'Willow Nails, LLC'
    t = flat('LEASE AGREEMENT FOR P & Q EXAMPLE, LLC (dba Pupville) AT ELMWOOD SHOPPING CENTER')
    assert _party(t, 'Tenant') == 'P & Q EXAMPLE, LLC', _party(t, 'Tenant')
    assert _TRADE_NAME_RX.search(t).group(1) == 'Pupville'
    t = flat('RETAIL LEASE AGREEMENT Retail/Office Building FOR QRS EXAMPLE LLC Doing business as '
             'Noodle Grill AT RIVER ROW T:\\X\\LEASE.DOCX hired by Landlord')
    assert _party(t, 'Tenant') == 'QRS EXAMPLE LLC', _party(t, 'Tenant')
    assert _TRADE_NAME_RX.search(t).group(1) == 'Noodle Grill'
    t = flat('RETAIL LEASE AGREEMENT FOR ALEX EXAMPLE dba: BLUE LEAF SMOKES AT RIVER ROW by')
    assert _party(t, 'Tenant') == 'ALEX EXAMPLE', _party(t, 'Tenant')
    # ends at the file path, no "AT"
    t = flat('LEASE AGREEMENT FOR EVERGREEN CLINIC OF EXAMPLE, LLC T: X\\ELMWOOD\\LEASE.DOCX shall be construed')
    assert _party(t, 'Tenant') == 'EVERGREEN CLINIC OF EXAMPLE, LLC', _party(t, 'Tenant')
    # "AT ," as the scanner reads it
    t = flat('SHOPPING CENTER LEASE AGREEMENT FOR FAIRWAY EXAMPLE GOLF LLC AT , ELMWOOD SQUARE I '
             'SHOPPING CENTER T:\\X\\LEASE.DOCX any renewal')
    assert _party(t, 'Tenant') == 'FAIRWAY EXAMPLE GOLF LLC', _party(t, 'Tenant')
    # the center's own name is not a tenant
    t = flat('LEASE.AGREEMENT FOR ELMWOOD SQUARE SHOPPING CENTER T:\\X\\LEASE (4.10.17).DOCX prior notice')
    assert _party(t, 'Tenant') is None, _party(t, 'Tenant')
    # a title naming the BUILDING: the role clauses win, and the building's
    # name (part of the landlord's) is never the tenant
    t = flat('Exhibit 10.20 OFFICE LEASE AGREEMENT FOR GRANITE TOWER AT RIVERSIDE LANDING GRANITE TOWER '
             'OWNER LLC, a Delaware limited liability company as Landlord and THE EXAMPLE CORP., a '
             'Delaware corporation as Tenant Dated: May 17, 2019')
    assert _party(t, 'Tenant') == 'THE EXAMPLE CORP', _party(t, 'Tenant')
    assert _party(flat('OFFICE LEASE AGREEMENT FOR GRANITE TOWER AT RIVERSIDE LANDING GRANITE TOWER OWNER '
                       'LLC (“Landlord”) leases to the tenant named below'), 'Tenant') is None


def test_cover_bracketed_roles():
    t = flat('LEASE AGREEMENT BIRCHWOOD VENTURES, L.L.C. [Landlord] AND TIDEWATER BARBERS, INC. [Tenant] '
             'FOR 918 square feet of GREAT CUTS ELMWOOD, ZZ 00000 fees) incurred by Landlord')
    assert _party(t, 'Landlord') == 'BIRCHWOOD VENTURES, L.L.C', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'TIDEWATER BARBERS, INC', _party(t, 'Tenant')
    t = flat('kwiktag ~ 000 000 000 LEASE AGREEMENT BIRCHWOOD VENTURES, LLC [Landlord] AND LEE EXAMPLE dba '
             'RIVER LIQUOR DEPOT [Tenant] installed or located within the Premises.')
    assert _party(t, 'Tenant') == 'LEE EXAMPLE', _party(t, 'Tenant')
    assert _TRADE_NAME_RX.search(t).group(1) == 'RIVER LIQUOR DEPOT'


def test_cover_lessor_lessee_labels_and_role_after_name():
    t = flat('Lessor: Southgate Retail, LLC Lessee: Pat Example SHOPPING CENTER LEASE AGREEMENT '
             'SOUTHGATE Document1 term shall be equitably prorated')
    assert _party(t, 'Landlord') == 'Southgate Retail, LLC', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Pat Example', _party(t, 'Tenant')
    t = flat('Prairie Example Bank, a national banking association chartered under Federal law and '
             'authorized to do business in Minnesota TENANT and River Row Station, LLC LANDLORD '
             'RIVER ROW STATION RETAIL LEASE AGREEMENT Retail/Office Building')
    assert _party(t, 'Tenant') == 'Prairie Example Bank', _party(t, 'Tenant')
    assert _party(t, 'Landlord') == 'River Row Station, LLC', _party(t, 'Landlord')
    # mixed case; both roles are required — a provision title is not a cover
    t = flat('Elm Example Corporation Tenant and River Row Station, LLC Landlord RIVER ROW STATION '
             'OFFICE LEASE AGREEMENT . Retail/Office Building')
    assert _party(t, 'Tenant') == 'Elm Example Corporation', _party(t, 'Tenant')
    assert _party(flat('ALTERATIONS BY TENANT AND SIGNS Section 1. Tenant, at its own expense, shall '
                       'purchase, install and maintain its trade fixtures.'), 'Tenant') is None
    # side-heading lists are not labels
    t = flat('from Landlord to Tenant. ALTERATIONS, ADDITIONS, IMPROVEMENTS BY TENANT: ALTERATIONS BY '
             "LANDLORD: REPAIRS BY TENANT: MECHANIC'S LIENS: 10. Tenant agrees not to permit")
    assert _party(t, 'Tenant') is None, _party(t, 'Tenant')
    assert _party(t, 'Landlord') is None, _party(t, 'Landlord')


def test_term_arithmetic_corrects_a_misprinted_end_date():
    assert scan('THE LEASE TERM TO BE TEN YEARS SIX MONTHS COMMENCING OCTOBER 1, 1993 AND ENDING '
                'MARCH 31, 2084. ANNUAL MINIMUM RENT SCHEDULE') == '3/31/2004'
    assert scan('a term of 10 years commencing on the 1st day of October, 1991 and ending on the '
                '30th day of September, 2091.') == '9/30/2001'
    # agreement, or a small (option-length) disagreement: the typed date stands
    assert scan('The term shall be five (5) years commencing February 1, 2018 and ending '
                'January 31, 2023.') == '1/31/2023'
    assert scan('Tenant has one option of five years; the extended term commencing April 1, 2002 '
                'and ending March 31, 2012.') == '3/31/2012'


def test_amendment_roles_in_parentheses_without_quotes():
    t = flat('SECOND AMENDMENT TO LEASE This Second Amendment to Lease, dated as of 1998 (Second '
             'Amendment), between Ole Example, successor in interest to Elmwood Plaza Limited '
             'Partnership (Landlord) and Pawprint Pet Supply Outlet, Inc. (Tenant). WITNESSETH, '
             'that: WHEREAS, Landlord and Tenant have entered into a Lease dated February 11, 1990')
    assert _party(t, 'Landlord') == 'Ole Example', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Pawprint Pet Supply Outlet, Inc', _party(t, 'Tenant')
    # the role word itself is never a party
    assert _party(flat('agreed by Landlord and Tenant (Tenant) that the rent'), 'Tenant') is None


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


# ─── round 3 shapes (2026-10-06) ─────────────────────────────────────

def test_prose_as_landlord_is_not_a_party():
    t = flat('if any occupant holds over and Tenant agrees to accept possession at such time '
             'as Landlord is able to tender the same. A “ Landlord ”: MAPLE FORGE PARTNERSHIP, '
             'a Texas general partnership B. Address of Landlord: 1 Example Row')
    assert _party(t, 'Landlord') == 'MAPLE FORGE PARTNERSHIP', _party(t, 'Landlord')


def test_designated_as_and_address_after_name():
    t = flat('THIS LEASE made by and between J. Q. Example, c/o 100 W. Elm, Ste. 2, Anytown, '
             'Ohio, the Lessor, hereinafter designated as the Landlord, and Bluebird Radio, Inc., '
             '2900 W. Oak Road, Anytown, Oh, the Lessee, hereinafter designated as the Tenant.')
    assert _party(t, 'Landlord') == 'J. Q. Example', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Bluebird Radio, Inc', _party(t, 'Tenant')


def test_hereafter_called_with_lead_in_and_numeric_name():
    t = flat('by and between 4100 Harbor Partners LLC, a Florida limited liability company '
             '(and any successor or assign, hereafter called “Landlord”) and Kestrel Media, '
             'Inc., a Delaware corporation (hereafter called the “Tenant”).')
    assert _party(t, 'Landlord') == '4100 Harbor Partners LLC', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'Kestrel Media, Inc', _party(t, 'Tenant')


def test_caps_cover_page_with_agent_and_address():
    t = flat('LEASE AGREEMENT Between OAKVALE ASSOCIATES, LP OWNER OF OAKVALE PLAZA By OAKVALE '
             'MANAGEMENT, Inc. GENERAL PARTNER BY JANE ROE, PRESIDENT LANDLORD AND RIVERSIDE '
             'SAVINGS BANK 1250 Hwy 9 Anytown, NJ 00000 TENANT Dated: 1 March 2005')
    assert _party(t, 'Landlord') == 'OAKVALE ASSOCIATES, LP', _party(t, 'Landlord')
    assert _party(t, 'Tenant') == 'RIVERSIDE SAVINGS BANK', _party(t, 'Tenant')


def test_summary_label_runs_on_then_definition():
    t = flat('2. Tenant: Heron Therapy, LLC Tenant Contact Information: Heron Therapy, LLC '
             'C/O Parent Co. 1 Example Circle')
    assert _party(t, 'Tenant') == 'Heron Therapy, LLC', _party(t, 'Tenant')


def test_deposit_wordings_round3():
    cases = [
        ('The Landlord herewith acknowledges the receipt of One Thousand Two Hundred ($1,200.00 ) '
         'Dollars which he is to retain as security for the faithful performance', 1200.0),
        ('18. Security Deposit: One month’s Basic Rent, $ 9,100.50 , due at lease signing.', 9100.5),
        ('O. SECURITY DEPOSIT AMOUNT: $ 11,000.00 P. PARKING:', 11000.0),
        ('is paid herewith along with a security / damage deposit of $1,800.00. So long as', 1800.0),
        ('SECURITY DEPOSIT. Tenant has deposited with Landlord the sum of nine thousand dollars '
         '($9,000), (“Security Deposit”).', 9000.0),
    ]
    for t, want in cases:
        got = _security_deposit(flat(t))
        assert got and got[0] == want, (t[:40], got)


def test_round3_dates():
    assert stated('K. TERMINATION DATE: February 28, 2021 L. ANNUAL BASE RENT') == '2/28/2021'
    assert stated('3. The lease expiration date shall be SEPTEMBER 30, 2019.') == '9/30/2019'
    assert stated('“Lease Term”: The period beginning on the Commencement Date and ending '
                  'December 31, 2020.') == '12/31/2020'
    assert stated('2. Term The term of this Lease shall be from June 20, 2021 to June 30, 2024.') \
        == '6/30/2024'


def test_abatement_window_is_not_the_term():
    t = flat('Tenant shall pay no Rent during the first six (6) months of the Term, from '
             'March 1, 2015, through August 31, 2015 (the “Rent Abatement Period”).')
    assert stated(t) is None, stated(t)


def test_round3_rent_rows():
    for t, want in [('payable monthly as follows: Months 1 - 60 at $1,500.00 per month', 1500.0),
                    ('Initial Term: Years 1-10 $8,000.00 per month Years 11-15 $8,800.00 per month', 8000.0),
                    ('Rent per month shall be per the schedule below: Months 1-12 $1,750.00 '
                     'Months 13-24 $1,800.00', 1750.0)]:
        got = _year1_monthly_rent(flat(t), flat(t))
        assert got and got[0] == want, (t[:40], got)
    # a tenant allowance at $/SF x SF is not rent
    t = flat('Tenant Allowance: a $30.00 per square foot Tenant Allowance ($150,000.00). '
             'Basic Rent: Years 1 to 5: $18.00/sq. ft.')
    got = _year1_monthly_rent(t, t, sf=5000)
    assert not got or got[0] != 12500.0, got


def test_amendment_restates_rent_and_certified_sf():
    t = flat('FIRST AMENDMENT 2. Premises. The Premises contain 20,150 square feet of Rentable '
             'Area as certified in accordance with BOMA standards 4. Fixed Rent. Fixed Rent '
             'shall be amended as follows: Lease Yr. Monthly Annual Per Sq.Ft. 1-5 $25,187.50 '
             '$302,250 $15.00 LEASE ... the Premises containing approximately 20,000 square feet '
             '... Fixed Rent: 1-5 $25,000.00 $300,000.00')
    assert _year1_monthly_rent(t, t)[0] == 25187.50, _year1_monthly_rent(t, t)
    assert best_sf(t) == 20150, best_sf(t)


def test_square_footage_label_and_abbreviations():
    t = flat("Tenant's Store Number: 4 Square Footage: 1840 sq. feet TABLE OF CONTENTS "
             'Rental Space: Store #4 Square Footage: 1840 sf. (3.10 % of shopping center)')
    assert best_sf(t) == 1840, best_sf(t)


def test_building_definition_is_not_the_premises():
    t = flat('E. “Building”: The building on the land, containing approximately 40,000 rentable '
             'square feet. F. “Premises”: Suite 100 containing approximately 9,500 square feet '
             'of rentable area. G. “Rentable area in the Building” shall be 40,000 square feet.')
    assert best_sf(t) == 9500, best_sf(t)


def test_relocation_delivers_the_new_premises():
    # the lease: 1,760 SF; a later modification relocates the tenant to a
    # smaller suite it DELIVERS, and the next modification recites that space
    t = flat('Landlord leases to Tenant and Tenant rents from Landlord 1,760 square feet of floor '
             'space having a street address of 100 Example Drive, Suite 40. ... 5. Relocation: Landlord '
             'shall have the sole and exclusive right, upon sixty (60) days notice, to relocate the '
             'Premises to other premises similar in size to the existing Premises. ... Lease '
             'Modification Agreement No. 2. WHEREAS, Landlord and Tenant hereby agree to relocate the '
             'Premises. 1. Relocation and Delivery of the Premises: Landlord shall deliver to Tenant '
             '(including keys to all doors) possession of that certain space containing approximately '
             '1,120 square feet having a street address of 100 Example Drive, Suite #90 (hereinafter '
             'called the "Premises") on or before September 1, 2012.')
    assert best_sf(t) == 1120, best_sf(t)
    t = flat('Lease Modification Agreement No. 3. WHEREAS, Landlord and Tenant entered into that certain '
             'Lease fully executed on September 17, 2004, which has been modified from time to time '
             '(the "Lease"), for certain real property containing approximately 1,120 square feet '
             'located at 100 Example Drive, Suite #90 (the "Premises").')
    assert best_sf(t) == 1120, best_sf(t)
    # two modifications both restate the premises: the later one in the file wins
    t = flat('Lease Modification Agreement No. 1 ... that certain Lease (the "Lease") for certain real '
             'property containing approximately 1,760 square feet located at 100 Example Drive, Suite 40. '
             '... Lease Modification Agreement No. 2 ... Landlord shall deliver to Tenant possession of '
             'that certain space containing approximately 1,120 square feet having a street address of '
             '100 Example Drive, Suite #90 (hereinafter called the "Premises").')
    assert best_sf(t) == 1120, best_sf(t)
    # ordinary mentions: the operative clause stays ahead of the building total
    # that repeats later; "of the overall N" is the building
    t = flat('1. PREMISES. The Premises consists of approximately 5,471 square feet of space of the '
             'overall 10,553 square foot of building (51.8% of the Building). In the event the rentable '
             'area of the Building is increased above its present rentable area of 10,553 square feet, '
             'the Premises share shall be adjusted.')
    assert best_sf(t) == 5471, best_sf(t)


def test_premises_shown_on_site_plan_and_recital_consisting_of():
    # the operative clause points at the site plan AND states the area
    t = flat('Landlord leases to Tenant the space within the Example Shopping Center (the "Shopping '
             'Center") shown by cross-hatching on the Shopping Center site plan, attached to this Lease '
             'as Exhibit A containing approximately 2,804 square feet, referred to as Bay 7C, (the '
             '"Leased Premises"). The Shopping Center is situated in the City of Example.')
    assert best_sf(t) == 2804, best_sf(t)
    # a later amendment's recital of the premises as they stand
    t = flat('WHEREAS, Landlord and Tenant entered into a Lease Agreement dated November 17, 2010 and a '
             'Second Amendment dated April 22, 2014 (collectively the "Lease") for certain premises '
             'consisting of approximately 2,804 square feet of space in the Example Shopping Center.')
    assert best_sf(t) == 2804, best_sf(t)
    # an expansion recital names the ADDED space, not the premises
    t = flat('A. Landlord and Tenant entered into an Office Lease whereby Landlord leased to Tenant a '
             'total of 9,850 rentable square feet of space on the fourth floor (the "Existing Premises"). '
             'B. Tenant desires to expand the Existing Premises to include that certain space consisting '
             'of approximately 1,410 rentable square feet of space located on the fourth floor.')
    assert best_sf(t) == 9850, best_sf(t)


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
