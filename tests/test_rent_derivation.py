"""
Regression tests for extractors/rent_derivation.py — current rent from paper.

Every fixture is FICTIONAL (invented tenants, dates, dollars) but copies the
exact LAYOUT of a real lease table that once broke the parser, found by the
2026-09-29 rent tie-out against a landlord's rent roll. Client text never goes in
git; the shapes do.

    venv/Scripts/python tests/test_rent_derivation.py      (no pytest needed)
    venv/Scripts/python -m pytest tests/test_rent_derivation.py
"""

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.rent_derivation import (  # noqa: E402
    derive_current_rent, parse_dated_rows, parse_rent_schedule)

AS_OF = date(2026, 6, 30)


def rent(text, **kw):
    kw.setdefault('as_of', AS_OF)
    return derive_current_rent(schedule_text=text, **kw)


# ─── dated rows: layouts ─────────────────────────────────────────────

def test_monthly_only_rows_amounts_after():
    # amendment table, "Period / Monthly Rent", one amount per row
    t = """3. Base Rent. Rent payable during the Extension Term shall be paid in
    monthly installments as follows:
    Period Monthly Rent
    November 1, 2024 through October 31, 2025 $3,120.00
    November 1, 2025 through October 31, 2026 $3,213.60
    November 1, 2026 through October 31, 2027 $3,310.01
    4. Brokers. Tenant warrants that it has had no dealings with any broker."""
    r = rent(t)
    assert (r.method, r.monthly, r.confidence) == ('schedule', 3213.60, 'high'), r


def test_short_dates_psf_then_monthly():
    # "Period  Rent PSF  Monthly Rent" with M/D/YY-M/D/YY ranges
    t = """2. ANNUAL MINIMUM RENT: During the term of the lease extension the annual
    Minimum Rent shall be as follows:
    Period Rent PSF Monthly Rent
    10/1/23-9/30/25 $19.00 $4,750.00
    10/1/25-9/30/27 $20.00 $5,000.00
    3. TENANT IMPROVEMENTS: Landlord shall complete the work."""
    assert rent(t).monthly == 5000.00


def test_amounts_before_dates_in_brackets():
    # "Year n $annual $monthly (start to end)" — dates trail the amounts
    t = """3. Minimum Rent. During the First Extended Term, Tenant shall pay annual
    Minimum Rent as follows: FIRST EXTENDED TERM ANNUAL RENT MONTHLY RENT
    Year 1 $36,000.00 $3,000.00 (8/1/2025 to 7/31/2026)
    Year 2 $36,900.00 $3,075.00 (8/1/2026 to 7/31/2027)
    Year 3 $37,822.56 $3,151.88 (8/1/2027 to 7/31/2028)
    Monthly installments of annual Minimum Rent shall be paid monthly."""
    assert rent(t).monthly == 3000.00          # NOT 3,075 (next row)


def test_labels_then_dates_then_amounts():
    # "Months 13-24 (start to end) $annual $monthly" — dates lead the amounts
    t = """3. Minimum Rent. During the Extended Term, Tenant shall pay annual
    Minimum Rent as follows: EXTENDED TERM ANNUAL RENT MONTHLY RENT
    Months 1-12 (5/1/2025 to 4/30/2026) $24,000.00 $2,000.00
    Months 13-24 (5/1/2026 to 4/30/2027) $24,720.00 $2,060.00
    Months 25-36 (5/1/2027 to 4/30/2028) $25,461.60 $2,121.80
    Month 37 $0.00 $0.00 (5/1/2028 to 5/31/2028)
    Monthly installments of annual Minimum Rent shall be paid monthly."""
    assert rent(t).monthly == 2060.00          # NOT 2,000 (previous row)


def test_mixed_layout_within_one_table():
    # free-rent row dates-first, then amounts-first, then an OCR-scrambled row
    t = """3. Minimum Rent. During the Extended Term, Tenant shall pay annual
    Minimum Rent as follows: EXTENDED TERM ANNUAL RENT MONTHLY RENT
    Months 1 - 2 (5/1/2025 to 6/30/2025) $0.00 $0.00
    Months 3 - 14 $48,000.00 $4,000.00 (7/1/2025 to 6/30/2026)
    (7/1/ M 2 o 0 n 2 t 6 h s t o 1 6 5 / 3 - 0 / 2 2 6 027) $49,440.00 $4,120.00
    Monthly installments of annual Minimum Rent shall be paid monthly."""
    assert rent(t).monthly == 4000.00


def test_psf_column_and_option_label_between_rows():
    # "Time Period / Annual / Monthly / PSF" + "(2nd Option)" after the range
    t = """The Fixed Base Rent payable by Tenant during the Option Terms shall be:
    Time Period Annual Rent Monthly Amount PSF
    3/01/2020 - 2/28/2025 (1st Option) $96,000.00 $8,000.00 $9.60
    3/01/2025 - 2/28/2030 (2nd Option) $102,000.00 $8,500.00 $10.20
    The Landlord and Tenant agree to the foregoing."""
    assert rent(t).monthly == 8500.00          # NOT 8,000 (psf-adjacent row)


def test_prose_period_after_amounts():
    t = """1. RENT. Minimum Annual Rent shall be $30,000.00, to be paid in equal
    monthly installments of $2,500.00 for the period beginning July 1, 2025 and
    ending June 30, 2027. 2. Except as modified herein, the Lease remains."""
    assert rent(t).monthly == 2500.00


def test_per_month_words_after_amount():
    t = """Notwithstanding anything to the contrary, the Minimum Annual Rent during
    the Extension Period shall be as follows:
    December 1, 2023 through November 30, 2025 $1,900.00 per month
    December 1, 2025 through November 30, 2028 $2,050.00 per month
    B. No Options."""
    assert rent(t).monthly == 2050.00


def test_ocr_to_as_10_and_dot_thousands():
    # scanned amendment: "to" read as "10", "," read as "."
    t = """3. Minimum Rent. During the Extended Term, Tenant shall pay to Landlord annual
    Minimum Rent as follows: EXTENDED TERM ANNUAL RENT MONTHLY RENT
    Year 1 $48,000.00 $4,000.00 (1/1/20251012/31/2025)
    Year 2 $49,440.00 $4.120.00 (1/1/20261012/31/2026)
    Year 3 $50,923.20 $4,243.60 (1/1/20271012/31/2027)"""
    assert rent(t).monthly == 4120.00


# ─── the lease chain ─────────────────────────────────────────────────

def test_newest_instrument_wins_latest_start():
    # original extension row still "covers" the date, but a later relocation
    # amendment starts later -> it governs
    t = """3. Gross Rent. During the Extended Term Tenant shall pay annual Gross Rent:
    EXTENDED TERM ANNUAL RENT MONTHLY RENT
    Year 3 (8/1/2025 to 7/31/2026) $42,000.00 $3,500.00
    ... Relocation Amendment ...
    b. New Premises. Tenant shall pay Landlord Gross Rent for the New Premises:
    TERM ANNUAL RENT MONTHLY RENT
    Year 1 (1/1/2026 to 12/31/2026) $30,000.00 $2,500.00
    Year 2 (1/1/2027 to 12/31/2027) $30,900.00 $2,575.00"""
    assert rent(t).monthly == 2500.00


def test_ocr_dollar_read_as_digit_in_annual_column():
    # '$' of the annual column OCR'd as a leading '3' ("$27,600.00" ->
    # "327,600.00"): the pair still verifies once that digit is dropped
    t = """b. Notwithstanding anything to the contrary, Tenant shall pay the
    reduced annual Minimum Rent as follows:
    TERM ANNUAL RENT MONTHLY RENT RENT PER SQ. FT.
    1/1/2024 to 7/31/2025 $26,400.00 $2,200.00 $8.00
    8/1/2025 to 7/31/2026 327,600.00 $2,300.00 $8.36
    8/1/2026 to 7/31/2027 $28,800.00 $2,400.00 $8.73"""
    r = rent(t)
    assert (r.method, r.monthly, r.confidence) == ('schedule', 2300.00, 'high'), r


def test_undatable_later_table_caps_confidence():
    # original lease's Lease Year table + a relocation amendment counted from
    # an EVENT ("Relocation Commencement Date"). With a confirmed original
    # commencement the old table places, but the newer one may supersede it
    # -> never high
    t = """2. Minimum Rent. Tenant shall pay Landlord annual Minimum Rent:
    PERIOD ANNUAL RENT MONTHLY RENT
    Lease Year 1 $18,000.00 $1,500.00
    Lease Year 2 $18,600.00 $1,550.00
    Lease Year 3 $19,200.00 $1,600.00
    Lease Year 4 $19,800.00 $1,650.00
    """ + 'x ' * 300 + """
    b. New Premises. Beginning on the Relocation Commencement Date, Tenant
    shall pay Landlord Minimum Rent for the New Premises as follows:
    TERM ANNUAL RENT MONTHLY RENT
    Year 1 $36,000.00 $3,000.00
    Year 2 $37,080.00 $3,090.00"""
    r = rent(t, commencement='2023-08-01')
    assert r.monthly == 1600.00 and r.confidence == 'medium', r
    assert 'later_schedule_unplaced' in r.flags, r


def test_expansion_amendment_after_dated_row_caps_confidence():
    # dated option-term schedule for the ORIGINAL premises, then a later
    # expansion amendment whose table counts from the "Expansion Date" (an
    # event) — it says "Extended Term" but it EXECUTED the extension, so it
    # may supersede the dated row
    t = """The Fixed Base Rent due during the Renewal Term and Option Terms:
    Time Period Annual Rent Monthly Amount PSF
    3/01/2020 - 2/28/2025 (1st Option) $60,000.00 $5,000.00 $10.00
    3/01/2025 - 2/28/2030 (2nd Option) $63,000.00 $5,250.00 $10.50
    """ + 'x ' * 300 + """
    FOURTH AMENDMENT. Landlord hereby also leases to Tenant the Expansion
    Space. 3. Extended Term: The term of the Lease is hereby extended for ten
    years starting on the Expansion Date. 4. Base Rent: Beginning on the
    Expansion Date and continuing through the Extended Term, Tenant shall pay
    Landlord annual Fixed Base Rent for the Leased Premises as follows:
    Months Monthly Annually 1-4 $6,000.00 $72,000.00 5-64 $6,200.00 $74,400.00
    65-124 $6,500.00 $78,000.00"""
    r = rent(t)
    assert r.monthly == 5250.00 and r.confidence == 'medium', r
    assert 'later_schedule_unplaced' in r.flags, r


def test_later_sublease_table_does_not_cap_confidence():
    # a sublease later in the file (Sublandlord / Subtenant) is not the
    # tenant's own rent — it must not demote the lease's dated row
    t = """Minimum Rent shall be as follows:
    Period Annual Rent Monthly Rent
    2/1/2023 to 1/31/2028 $60,000.00 $5,000.00
    """ + 'x ' * 300 + """
    6) RENT. A. Subtenant covenants and agrees to pay to Sublandlord, in
    lawful money of the United States, Gross Rent ("Gross Rent") at the
    following rates: Annual Rent Monthly Rent Years 1-5 $90,000.00 $7,500.00
    Years 6- January 31, 2033 $96,000.00 $8,000.00"""
    r = rent(t)
    assert (r.monthly, r.confidence, r.flags) == (5000.00, 'high', []), r


def test_dates_after_amounts_row_is_not_unplaced():
    # "Year 3 $.. $.. (1/1/2028 to 12/31/2028)": the dated parser places it,
    # so the relative parser's undated copy must not trigger the cap
    t = """b. New Premises. Tenant shall pay Landlord Gross Rent as follows:
    TERM ANNUAL RENT MONTHLY RENT
    Year 1 (1/1/2026 to 12/31/2026) $24,000.00 $2,000.00
    Year 2 (1/1/2027 to 12/31/2027) $24,720.00 $2,060.00
    Year 3 $25,461.60 $2,121.80 (1/1/2028 to 12/31/2028)"""
    r = rent(t)
    assert (r.monthly, r.confidence, r.flags) == (2000.00, 'high', []), r


TWO_SPACES = """3. Term. The Initial Term of this Lease shall run and extend for ten (10)
years from and after the Commencement Date (the "Initial Term"). Landlord grants
Tenant two (2) additional term of five (5) years each (each an "Extended Term").
4.1.1 Minimum Rental for the Existing Space. An annual Minimum Rental for the
Existing Space as follows: Initial Term: One hundred twenty thousand dollars
($120,000.00) per Lease Year, payable at the rate of Ten thousand dollars and no
cents ($10,000.00) per month; (based upon six dollars ($6.00) per square foot).
First Extended Term: One hundred thirty-two thousand dollars ($132,000.00) per
Lease Year, payable at the rate of Eleven thousand dollars ($11,000.00) per month.
Second Extended Term: One hundred forty-four thousand dollars 004.507 ($144,000.00)
per Lease Year, payable at the rate of Twelve thousand dollars ($12,000.00) per month.
4.1.2 Minimum Rental for the New Space. An annual Minimum Rental for the New Space
as follows: Initial Term: Twenty-four thousand dollars ($24,000.00) per Lease
Year, payable at the rate of Two thousand dollars ($2,000.00) per month. First
Extended Term: Twenty-six thousand four hundred dollars ($26,400.00) per Lease
Year, payable at the rate of Two thousand two hundred dollars ($2,200.00) per month.
Second Extended Term: Twenty-eight thousand eight hundred dollars ($28,800.00) per
Lease Year, payable at the rate of Two thousand four hundred dollars 003.50
($2,400.00) per month."""


def test_rent_in_words_named_terms_two_spaces_are_summed():
    # anchor-style lease: amounts spelled out between annual and monthly,
    # "Initial Term / First Extended Term" rows, one table per space;
    # commenced 9/15/2008 -> Initial to 9/14/2018, First Extended to
    # 9/14/2023, Second Extended covers 2026 -> 12,000 + 2,400
    r = rent(TWO_SPACES, commencement='2008-09-15')
    assert r.monthly == 14400.00 and r.method == 'schedule', r
    assert 'multi_space_sum' in r.flags and 'extension_term_assumed' in r.flags, r
    assert r.confidence == 'medium', r          # an extension may not have been exercised
    # in the Initial Term the sum is high confidence
    r0 = rent(TWO_SPACES, commencement='2020-01-01')
    assert (r0.monthly, r0.confidence) == (12000.00, 'high'), r0


def test_prose_term_mention_is_not_a_term_row():
    # "the initial term" in running prose is not a row label
    t = """3. OPTION TO RENEW. Landlord grants Tenant the option to extend this
    Lease for five (5) years under the terms in effect at the expiration of the
    initial term except that the Minimum Annual Rent during the option term shall
    be $24,000.00, payable in equal monthly installments of $2,000.00."""
    assert all(not r.label.startswith('term') for r in parse_rent_schedule(t))


def test_option_term_table_does_not_cap_confidence():
    # a later Extended Term table can't be dated either, but it only starts
    # after the initial term ends -> the initial-term row stays high
    t = """2. Minimum Rent. Tenant shall pay Landlord annual Minimum Rent:
    PERIOD ANNUAL RENT MONTHLY RENT
    Lease Year 1 $18,000.00 $1,500.00
    Lease Year 2 $18,600.00 $1,550.00
    Lease Year 3 $19,200.00 $1,600.00
    Lease Year 4 $19,800.00 $1,650.00
    """ + 'x ' * 300 + """
    3. Option to Extend: Landlord grants Tenant one option to extend for five
    years, except that Minimum Rent for the Extended Term shall be as follows:
    PERIOD ANNUAL RENT MONTHLY RENT
    Extended Term Year 1 $20,400.00 $1,700.00
    Extended Term Year 2 $21,000.00 $1,750.00"""
    r = rent(t, commencement='2023-08-01')
    assert (r.monthly, r.confidence, r.flags) == (1600.00, 'high', []), r


def test_schedule_ends_before_date_is_flagged_not_guessed():
    t = """Minimum Rent shall be as follows:
    Period Monthly Rent
    January 1, 2020 through December 31, 2022 $1,800.00
    January 1, 2023 through December 31, 2025 $1,900.00"""
    r = rent(t)
    assert r.method != 'schedule' and 'schedule_ends_before_date' in r.flags, r
    assert r.confidence == 'low'


# ─── guards ──────────────────────────────────────────────────────────

def test_deposit_and_cam_rows_ignored():
    t = """Tenant shall pay a security deposit of $5,000.00 on execution.
    Estimated operating costs for the period January 1, 2026 through
    December 31, 2026 are $1,200.00 per month."""
    assert parse_dated_rows(t) == []


def test_sanity_cap_rejects_non_rent_sums():
    t = """Base Rent schedule for the Term:
    Period Monthly Rent
    August 1, 2025 through July 31, 2026 $1,250,000.00"""
    assert parse_dated_rows(t) == []


def test_free_rent_zero_row_is_not_a_rent():
    t = """Minimum Rent shall be as follows: Period Monthly Rent
    May 1, 2026 through June 30, 2026 $0.00
    July 1, 2026 through June 30, 2027 $2,400.00"""
    assert all(r.monthly >= 50 for r in parse_dated_rows(t))


# ─── relative schedules ──────────────────────────────────────────────

def test_lease_years_from_commencement_is_high():
    t = """4. Annual Gross Rent. Tenant shall pay Landlord Gross Rent as follows:
    PERIOD ANNUAL RENT MONTHLY RENT
    Lease Years 1 - 5 $24,000.00 $2,000.00
    Lease Years 6 - 10 $26,400.00 $2,200.00"""
    r = rent(t, commencement='4/1/2022')
    assert (r.monthly, r.confidence) == (2000.00, 'high'), r
    r = rent(t, commencement='4/1/2020')
    assert r.monthly == 2200.00


def test_lease_years_from_expiration_is_medium():
    t = """4. Annual Gross Rent. Tenant shall pay Landlord Gross Rent as follows:
    PERIOD ANNUAL RENT MONTHLY RENT
    Lease Years 1 - 5 $24,000.00 $2,000.00
    Lease Years 6 - 10 $26,400.00 $2,200.00"""
    r = rent(t, expiration='3/31/2032')         # term 4/1/2022 .. 3/31/2032
    assert (r.monthly, r.confidence) == (2000.00, 'medium'), r


def test_lease_year_1_end_on_anchors_amendment_table():
    # unlabelled OCR'd rows; the table's start comes from its Year-1 definition
    t = """3. Minimum Rent: Beginning on the Expansion Date and continuing through the
    Extended Term, Tenant shall pay Landlord annual Minimum Rent as follows:
    Lease Year Monthly Annually
    ad $3,000.00 $36,000.00
    2. $3,090.00 $37,080.00
    3 $3,182.70 $38,192.40
    $3,278.18 $39,338.16
    $3,376.53 $40,518.36
    For purposes of this First Amendment, Lease Year | shall commence on the Expansion
    Date and end on August 31, 2022. Each subsequent Lease Year shall consist of twelve
    consecutive full calendar months."""
    # LY1 = 9/1/2021..8/31/2022 -> 6/30/2026 is LY5 (9/1/2025..8/31/2026)
    r = rent(t)
    assert (r.method, r.monthly) == ('schedule', 3376.53), r


def test_unrelated_date_after_table_is_not_an_anchor():
    # an early-termination clause after the schedule must NOT date it
    t = """2. Minimum Rent. Tenant shall pay Landlord fixed annual minimum rent in the
    following amounts for the following periods:
    PERIOD ANNUAL RENT MONTHLY RENT
    Months 1-3 $0.00 $0.00
    Months 4-64 $120,000.00 $10,000.00
    Months 65-126 $132,000.00 $11,000.00
    3. Early Termination: Beginning on June 1, 2021 and on each June 1 thereafter,
    either party may terminate this Lease on sixty days notice."""
    r = rent(t)                                  # no commencement known
    assert r.method != 'schedule', r             # route to review, don't guess
    r = rent(t, commencement='12/1/2020')        # with the one missing date
    assert (r.monthly, r.confidence) == (11000.00, 'high'), r


def test_dated_rows_not_double_counted_as_relative():
    t = """Rent schedule: Months Annualized Monthly Rate/sf
    11/1/2025 through 10/31/2026 $24,000.00 $2,000.00 $20.00
    11/1/2026 through 10/31/2027 $24,600.00 $2,050.00 $20.50"""
    assert parse_rent_schedule(t) == []


# ─── fallbacks ───────────────────────────────────────────────────────

def test_escalation_roll_forward_is_medium():
    r = rent('', year1_monthly='$2,000.00', escalation_pct='3%',
             commencement='7/1/2023')
    assert (r.method, r.monthly, r.confidence) == ('escalated', 2121.8, 'medium'), r


def test_escalation_with_confirmed_commencement_is_high():
    # stated Y1 + stated fixed % + a commencement the user (or rent roll)
    # confirmed: nothing left to guess
    r = rent('', year1_monthly='$2,000.00', escalation_pct='3%',
             commencement='7/1/2023', commencement_confirmed=True)
    assert (r.method, r.monthly, r.confidence) == ('escalated', 2121.8, 'high'), r
    # ...but a schedule that doesn't cover the date still caps it
    r2 = rent('Rent: Lease Year 1 $24,000.00 $2,000.00', year1_monthly='$2,000.00',
              escalation_pct='3%', commencement='7/1/2010', commencement_confirmed=True)
    assert r2.confidence != 'high', r2


def test_near_step_boundary_is_not_high():
    # relative schedule dated from a confirmed commencement: as-of a week after
    # a step -> which row applies depends on the exact day -> not shown unreviewed
    t = """Tenant shall pay Landlord Minimum Rent as follows:
    PERIOD ANNUAL RENT MONTHLY RENT
    Months 1-12 $24,000.00 $2,000.00
    Months 13-24 $25,200.00 $2,100.00
    Months 25-36 $26,400.00 $2,200.00"""
    near = rent(t, commencement=date(2024, 6, 23), commencement_confirmed=True)
    mid = rent(t, commencement=date(2024, 1, 1), commencement_confirmed=True)
    assert near.confidence != 'high' and 'near_step_boundary' in near.flags, near
    assert mid.confidence == 'high' and mid.monthly == 2200.00, mid


def test_single_amount_table_read_when_no_pairs():
    # one amount per row (monthly header), typographic dashes from OCR
    t = ("Rent: Years Monthly Rent 1 ‐ 3 $1,850.00 ($20.00 psf annually) "
         "4 ‐ 7 $2,035.00 ($22.00 psf annually) Tenant's share 4.10%")
    r = rent(t, commencement=date(2021, 9, 1), commencement_confirmed=True)
    assert (r.monthly, r.confidence) == (2035.00, 'high'), r
    # annual header -> /12; a range that doesn't continue ends the table
    t2 = "Lease Years Annual Base Rent 1-5 $30,000.00 6-10 $33,000.00 15-20 $99,000.00"
    r2 = rent(t2, commencement=date(2021, 1, 1), commencement_confirmed=True)
    assert r2.monthly == 2750.00, r2


def test_defined_extension_term_with_prose_rent():
    # an amendment defines the period once and states its rent in prose
    t = ("2. Minimum Rent. The current term of the Lease began on March 1, 2024 and ends on "
         "February 28, 2029 (the “Extended Term”). Landlord and Tenant hereby agree that during "
         "the Extended Term, Tenant shall pay to Landlord annual Minimum Rent in the amount of "
         "Thirty Thousand and no/100 dollars ($30,000.00) in monthly installments of Two "
         "Thousand Five Hundred and no/100 dollars ($2,500.00). Monthly installments ...")
    r = rent(t, commencement=date(2010, 3, 1), commencement_confirmed=True)
    assert (r.monthly, r.confidence) == (2500.00, 'high'), r
    # "... to commence on X, and to expire on Y (the "Fourth Extension Term")"
    t2 = ('1. Term. The Term is hereby extended, to commence on May 1, 2025, and to expire on '
          'April 30, 2030 (the "Fourth Extension Term"). 2. Rent. For and during the Fourth '
          'Extension Term, Tenant shall pay fixed annual gross rent of $96,000.00, payable in '
          'monthly installments of $8,000.00. 3. Other.')
    assert rent(t2).monthly == 8000.00, rent(t2)


def test_bare_year_ranges_tagged_with_term():
    t = ("3.3 Base Rent. Tenant shall pay Base Rent in the amounts set forth below: "
         "1-5 (Initial Term) | $60,000.00 | $5,000.00 6-10 (Initial Term) | $66,000.00 | "
         "$5,500.00 11-15 (First Extended Term) | $72,600.00 | $6,050.00")
    r = rent(t, commencement=date(2023, 1, 1), commencement_confirmed=True)
    assert (r.monthly, r.confidence) == (5000.00, 'high'), r


def test_column_scrambled_dated_table():
    # extraction emitted the date column, then the amount column
    t = ("3. Minimum Rent. Monthly installments of Minimum Rent shall be as follows: "
         "January 1, 2025 through December 31, 2025 January 1, 2026 through December 31, 2026 "
         "January 1, 2027 through December 31, 2027 Period $3,000.00 $36,000.00 $3,090.00 "
         "$37,080.00 $3,182.70 $38,192.40")
    r = rent(t)
    assert (r.monthly, r.confidence) == (3090.00, 'high'), r


def test_flat_year1_is_low():
    r = rent('', year1_monthly='$2,000.00')
    assert (r.method, r.confidence) == ('flat', 'low')


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
