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
    _anchor, derive_current_rent, parse_dated_rows, parse_rent_schedule)

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
    # the last scheduled rent, kept low -- not a Year-1 rent rolled forward
    assert r.method == 'last_scheduled' and r.monthly == 1900.00, r


def test_ended_extension_table_beats_original_year1_escalation():
    t = '''Minimum Rent: $2,000.00 per month, increasing 10% each Lease Year.
    2. Extended Term: The term of the Lease is hereby extended for an additional period of
    five (5) years starting on March 1, 2019. 3. Minimum Rent: During the Extended Term,
    Tenant shall pay to Landlord annual Minimum Rent as follows: EXTENDED TERM ANNUAL RENT
    MONTHLY RENT Year 1 $36,000.00 $3,000.00 Year 2 $36,720.00 $3,060.00 Year 3 $37,440.00
    $3,120.00 Year 4 $38,160.00 $3,180.00 Year 5 $38,880.00 $3,240.00'''
    r = derive_current_rent(as_of=date(2026, 6, 30), year1_monthly='2000.00', escalation_pct='10',
                            commencement=date(1999, 3, 1), schedule_text=t)
    assert (r.method, r.monthly, r.confidence) == ('last_scheduled', 3240.00, 'low'), r


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


# ─── named-term tables, option letters, lump sums ───────────────────

NAMED_TERM_LEASE = """9. Base Rent ("Base Rent"). LEASE TERM | YEARS | PER SQ FT | MONTHLY
Original Lease Term | 1-5 $6.00 | $4,500.00 Second Renewal Term $5,250.00
Third Renewal Term | | First Renewal Term | 6-10 $6.50 | $5,700.00
10. Additional Rent. Tenant shall have the right to extend the term of this Lease for
three (3) additional periods of five (5) years each (each period referred to as
"Renewal Term")."""
LETTER_1 = """Please refer to the Lease dated October 14, 2011. We are exercising our renewal
option under this Lease for a period of five (5) years to expire on June 30, 2022. All other
terms and conditions of the Lease shall remain in full force and effect."""
LETTER_2 = """We are exercising our renewal option under this Lease for a period of five (5)
years to expire on June 30, 2027. All other terms and conditions of the Lease shall remain."""


def test_named_term_table_psf_verifies_and_option_letters_place_terms():
    # scanned national-tenant form: one amount per named term, a $/SF column,
    # rows shuffled by the scan; no commencement on paper (set by delivery)
    chain = '\n\n'.join([NAMED_TERM_LEASE, LETTER_1, LETTER_2])
    rows = parse_rent_schedule(chain, sf=9000)
    by = {r.label: r for r in rows}
    assert by['term 0'].monthly == 4500.00 and by['term 0'].verified and by['term 0'].years == 5
    # the shuffled column put the third term's figure on the first term's row: $/SF wins
    assert by['term 1'].monthly == 4875.00 and by['term 1'].verified
    assert by['term 2'].monthly == 5250.00 and not by['term 2'].verified
    r = rent(chain, square_feet=9000)
    assert r.monthly == 5250.00 and r.confidence == 'medium', r
    assert r.flags == ['unverified_amount'] and 'term 2 [option letter]' in r.detail, r
    # the same with the rent roll's commencement: dated from it, still not shown
    r = rent(chain, square_feet=9000, commencement=date(2012, 7, 1), commencement_confirmed=True)
    assert r.monthly == 5250.00 and 'term 2 [commencement]' in r.detail, r
    # a date inside an exercised renewal: no "option may not be exercised" doubt
    r = rent(chain, square_feet=9000, as_of=date(2020, 1, 1))
    assert r.monthly == 4875.00 and r.flags == [], r
    # without the SF nothing verifies; the lone figures are still read, flagged
    r = rent(chain)
    assert r.monthly == 5250.00 and 'unverified_amount' in r.flags, r


def test_month_range_rows_without_cents():
    t = """Rent: Months Monthly Rent 1-12 $2,292 13-24 $2,361 25-36 $2,432
    Estimated 2023 Operating Costs: $963 per month"""
    rows = parse_rent_schedule(t)
    assert [(r.label, r.monthly) for r in rows] == [('months 1-12', 2292.0), ('months 13-24', 2361.0),
                                                     ('months 25-36', 2432.0)], rows


def test_breakpoint_tables_and_repayment_schedules_are_not_rent_rows():
    # percentage-rent breakpoints and a deferred-rent repayment period carry
    # date ranges and dollars but are not rent; the real table still reads
    t = """ARTICLE 3, MINIMUM RENT: TENANT shall pay fixed annual Minimum Rent as follows:
    PERIOD MONTHLY RENT ANNUAL RENT Year 1 $4,100.00 $49,200.00 Year 2 $4,200.00 $50,400.00
    Percentage rent of four percent (4%) on all gross sales in excess of the Natural Breakpoint:
    Lease Year Natural Breakpoint 07/01/17-01/31/20 $1,485,900.00 02/01/20-01/31/21 $1,542,000.00
    b) The total amount of fixed Minimum Rent that has been deferred is $8,000.00 ("Deferred Rent"),
    and shall be paid to Landlord pursuant to the Promissory Note during the eighteen (18) month
    period from January 1, 2011 through June 1, 2012 (the "Repayment Period")."""
    assert parse_dated_rows(t) == [], parse_dated_rows(t)
    assert [r.monthly for r in parse_rent_schedule(t)] == [4100.0, 4200.0]


def test_annual_figure_with_monthly_installments_in_words():
    t = """2. FIXED ANNUAL MINIMUM RENT: For the period commencing on April 1, 2022 through
    March 31, 2027, the Fixed Annual Minimum Rent as provided for in the Lease shall be equal
    to $45,600 per year payable in equal monthly installments of Three Thousand Eight Hundred
    and 00/100 ($3,800.00)."""
    rows = parse_dated_rows(t)
    assert [(str(r.start), str(r.end), r.monthly) for r in rows] == [('2022-04-01', '2027-03-31', 3800.0)], rows
    # the same sentence without the spelled-out pair: the unit nearest the figure wins
    t2 = """FIXED ANNUAL MINIMUM RENT: For the period commencing on April 1, 2022 through
    March 31, 2027, the Fixed Annual Minimum Rent shall be equal to $45,600 per year payable in
    equal monthly installments."""
    assert [r.monthly for r in parse_dated_rows(t2)] == [3800.0], parse_dated_rows(t2)


def test_seasonal_lease_lump_sum_is_not_a_monthly_rent():
    t = """3. Rent. a. Storage Rent. Total rent for the Storage Season is $6,000.00 ("Storage Rent")
    due upon execution. b. Space Rent. Total rent ("Rent") for the Term is $22,500.00, due and
    payable to Landlord in two installments. 4. Term. The term of this Lease ("Term") shall
    commence on June 15, 2026 ("Commencement Date") and shall expire on November 23, 2026
    ("Expiration Date")."""
    r = rent(t)
    assert r.monthly is None and r.method == 'lump_sum' and 'lump_sum_term' in r.flags, r
    assert '$22,500.00' in r.detail and '2026-06-15..2026-11-23' in r.detail, r.detail


def test_parse_memo_returns_equal_independent_copies():
    from realestate_extractor.extractors import rent_derivation as rd
    rd.clear_parse_cache()
    t = NAMED_TERM_LEASE + '\n\n' + LETTER_1 + '\n\n' + LETTER_2
    a = parse_rent_schedule(t, sf=9000)
    b = parse_rent_schedule(t, sf=9000)         # served from the memo
    assert a == b and a is not b and a[0] is not b[0]
    # dating one view's rows in place must not reach the next view
    r = rent(t, square_feet=9000)
    assert r.monthly == 5250.00
    c = parse_rent_schedule(t, sf=9000)
    assert all(x.start is None and '[' not in x.label for x in c), [(x.label, x.start) for x in c]
    # a different SF is a different entry
    assert parse_rent_schedule(t, sf=4500)[0].monthly == 2250.00
    assert parse_dated_rows(t) == parse_dated_rows(t)


def test_later_instrument_table_counts_from_its_own_start():
    # the original lease's table counts from the commencement; the
    # extension amendment (its own document) states its Effective Date and
    # carries a Year 1 / Year 2 table with no date next to it -> with the
    # chain passed document by document, the amendment table counts from
    # the Effective Date and its row for today wins; one blob -> unplaced
    lease = """2. Minimum Rent. Tenant shall pay Landlord annual Minimum Rent:
    PERIOD ANNUAL RENT MONTHLY RENT
    Lease Year 1 $18,000.00 $1,500.00
    Lease Year 2 $18,600.00 $1,550.00
    Lease Year 3 $19,200.00 $1,600.00
    Lease Year 4 $19,800.00 $1,650.00"""
    amendment = """FIRST AMENDMENT TO LEASE. This First Amendment is made by Elm Partners
    LLC, as Landlord, and Bean Coffee Inc, as Tenant. The Effective Date of this
    Amendment is January 1, 2025. """ + 'x ' * 200 + """
    3. Minimum Rent. From the Effective Date, Tenant shall pay Landlord Minimum
    Rent for the Premises as follows:
    TERM ANNUAL RENT MONTHLY RENT
    Year 1 $36,000.00 $3,000.00
    Year 2 $37,080.00 $3,090.00
    Year 3 $38,192.40 $3,182.70"""
    r = derive_current_rent(as_of=AS_OF, commencement='2023-08-01',
                            schedule_docs=[lease, amendment])
    assert r.monthly == 3090.00, r                       # Year 2 of the amendment
    assert 'later_schedule_unplaced' not in r.flags, r
    assert '[instrument]' in r.detail and r.confidence == 'medium', r
    # one blob: the amendment's HEADING marks where its instrument begins,
    # so its table still counts from its Effective Date
    r = derive_current_rent(as_of=AS_OF, commencement='2023-08-01',
                            schedule_text=lease + '\n\n' + amendment)
    assert r.monthly == 3090.00 and '[instrument]' in r.detail, r
    # without a heading or a document boundary the table stays unplaced
    r = derive_current_rent(as_of=AS_OF, commencement='2023-08-01',
                            schedule_text=lease + '\n\n' + amendment.replace('FIRST AMENDMENT TO LEASE.', ''))
    assert r.monthly == 1600.00 and 'later_schedule_unplaced' in r.flags, r
    # a renewal OPTION table in a later document is still not placed
    option = """Option to Extend. Tenant shall have the option to extend the Term for
    one (1) Extended Term of five (5) years. The Effective Date of this Amendment
    is January 1, 2025. During the Extended Term Tenant shall pay Minimum Rent:
    Year 1 $40,000.00 $3,333.33
    Year 2 $41,200.00 $3,433.33"""
    r = derive_current_rent(as_of=AS_OF, commencement='2023-08-01',
                            schedule_docs=[lease, option])
    assert r.monthly == 1600.00, r


def test_option_period_rent_after_the_scheduled_term():
    # the scheduled term ended in 2025; the lease carries a renewal-option
    # rent table. Today the tenant is still billed -> the option table counts
    # from the day after the term ended: Year 2 of the option, medium, flagged
    t = """3. Minimum Rent. Tenant shall pay Landlord Minimum Rent as follows:
    PERIOD ANNUAL RENT MONTHLY RENT
    Lease Year 1 $24,000.00 $2,000.00
    Lease Year 2 $24,720.00 $2,060.00
    Lease Year 3 $25,461.60 $2,121.80
    """ + 'x ' * 200 + """
    28. Option to Renew. Tenant shall have one (1) option to renew the Term for
    five (5) years (the "Renewal Option"). Minimum Rent during the renewal option
    period shall be:
    RENEWAL OPTION RENT ANNUAL RENT MONTHLY RENT
    Year 1 $27,000.00 $2,250.00
    Year 2 $27,810.00 $2,317.50
    Year 3 $28,644.30 $2,387.03"""
    r = rent(t, commencement='2022-07-01')          # term ends 2025-06-30; option Year 1 runs to 2026-06-30
    assert r.monthly == 2250.00 and r.method == 'option_period', r
    r2 = derive_current_rent(as_of=date(2026, 9, 1), commencement='2022-07-01', schedule_text=t)
    assert r2.monthly == 2317.50 and r2.method == 'option_period', r2
    assert r.confidence == 'medium' and 'option_period_assumed' in r.flags, r
    # inside the scheduled term the option table is not consulted
    r = derive_current_rent(as_of=date(2024, 1, 15), commencement='2022-07-01', schedule_text=t)
    assert r.monthly == 2060.00 and r.confidence == 'high', r
    # no option table on paper: the last scheduled rent, low, as before
    r = rent(t.split('28. Option')[0], commencement='2022-07-01')
    assert r.method == 'last_scheduled' and r.monthly == 2121.80, r


def test_wrapped_period_column_national_tenant_form():
    # the period column wraps under the amounts: "Second Renewal Term
    # 10/01/2017- $10.00 $7,409.17 $88,910.00 09/30/2022" -- the end date
    # sits after the amounts. Each row is still a dated row.
    t = """Section A.9 of the Lease is hereby amended and restated as follows:
    | LEASE TERM | TIME PERIOD | PER SQ FT MONTHLY | ANNUALLY |
    Second Renewal Term 10/01/2017- $10.00 $7,409.17 $88,910.00 09/30/2022
    Interim Term 10/01/2022- $9.00 $6,668.25 $80,019.00 09/30/2025
    Third Renewal Term 10/01/2025- $10.50 $7,779.63 $93,355.50 09/30/2030
    Fourth Renewal Term 10/01/2030- $11.00 $8,150.08 $97,801.00 09/30/2035"""
    rows = parse_dated_rows(t)
    assert [(r.start.isoformat(), r.end.isoformat(), r.monthly) for r in rows] == [
        ('2017-10-01', '2022-09-30', 7409.17), ('2022-10-01', '2025-09-30', 6668.25),
        ('2025-10-01', '2030-09-30', 7779.63), ('2030-10-01', '2035-09-30', 8150.08)], rows
    r = rent(t)
    assert r.monthly == 7779.63 and r.confidence == 'high', r
    # the unwrapped form of the same table reads the same
    t2 = ("LEASE TERM | TIME PERIOD | PER SQ FT | MONTHLY | ANNUALLY "
          "Original Lease Term | 09/29/14-09/30/19 $6.50 $5,192.96 $62,315.50 "
          "Extension Term 10/01/19-09/30/20 $6.00 $4,793.50 $57,522.00")
    assert [(r.start.isoformat(), r.monthly) for r in parse_dated_rows(t2)] == [('2014-09-29', 5192.96), ('2019-10-01', 4793.5)]


def test_per_month_prose_rows_without_a_table_header():
    t = """3. Rent: Tenant promises to pay rent to Landlord from the Rent Commencement
    Date until the last day of the Primary Term according to the following schedule:
    Years 1 through 5 $4,500.00 per month Years 6 through 10 $4,950.00 per month
    Years 11 through 15 $5,445.00 per month Years 16 through 20 $5,989.50 per month"""
    rows = parse_rent_schedule(t)
    assert [(r.label, r.monthly) for r in rows] == [('years 1-5', 4500.0), ('years 6-10', 4950.0),
                                                     ('years 11-15', 5445.0), ('years 16-20', 5989.5)], rows
    r = rent(t, commencement='2012-03-01')          # 2026-06-30 is lease year 15
    assert r.monthly == 5445.00 and r.method == 'schedule' and r.confidence == 'high', r
    r = derive_current_rent(as_of=date(2018, 1, 15), commencement='2012-03-01', schedule_text=t)
    assert r.monthly == 4950.00, r


def test_option_rent_stated_per_renewal_term_spans_the_term():
    # two five-year renewal options, each with its own stated rent; the
    # scheduled term ended 2024-10-31, so today sits in the FIRST option term
    # (year 2 of it), not in a notional second year-row of the option table
    t = """Minimum Rent: PERIOD ANNUAL RENT MONTHLY RENT
    Lease Year 1 $30,000.00 $2,500.00
    Lease Year 2 $30,900.00 $2,575.00
    Lease Year 3 $31,827.00 $2,652.25
    """ + 'x ' * 200 + """
    30. Options. Tenant shall have two (2) options ("Renewal Options") to renew the
    Term for five (5) years each (each an "Additional Renewal Term"). The annual
    Minimum Rent for the first Additional Renewal Term shall be in the amount of
    Thirty-Five Thousand Two Hundred Ninety-Three and 50/100 dollars ($35,293.50) and
    shall be paid in monthly installments of Two Thousand Nine Hundred Forty-One and
    13/100 dollars ($2,941.13). The annual Minimum Rent for the second Additional
    Renewal Term shall be in the amount of Thirty-Eight Thousand Eight Hundred
    Twenty-Two and 88/100 dollars ($38,822.88) and shall be paid in monthly
    installments of Three Thousand Two Hundred Thirty-Five and 24/100 dollars ($3,235.24)."""
    r = rent(t, commencement='2021-11-01')          # term ends 2024-10-31
    assert r.monthly == 2941.13 and r.method == 'option_period', r


def test_rent_roll_start_dates_the_current_instrument_table_when_its_end_agrees():
    # the chain: the original lease (Lease Year table) and a renewal amendment
    # with its own Year 1 / Year 2 table and no start date. The rent roll says
    # the lease started 2025-02-10 and ends 2027-02-09: dated from that start
    # the amendment table ends exactly there -> it is the current instrument's
    # table (high); the lease table, so dated, would end 2028 -> not it
    lease = """2. Minimum Rent. Tenant shall pay Landlord annual Minimum Rent:
    PERIOD ANNUAL RENT MONTHLY RENT
    Lease Year 1 $18,000.00 $1,500.00
    Lease Year 2 $18,600.00 $1,550.00
    Lease Year 3 $19,200.00 $1,600.00"""
    amendment = """SECOND AMENDMENT TO LEASE. Landlord and Tenant agree the Term is
    extended for two (2) years. """ + 'x ' * 200 + """
    3. Minimum Rent. During the extended Term Tenant shall pay Minimum Rent:
    TERM ANNUAL RENT MONTHLY RENT
    Year 1 $36,000.00 $3,000.00
    Year 2 $37,080.00 $3,090.00"""
    r = derive_current_rent(as_of=AS_OF, commencement='2025-02-10', commencement_confirmed=True,
                            commencement_is_term_start=True, term_end='2027-02-09',
                            schedule_docs=[lease, amendment])
    assert r.monthly == 3090.00 and '[term start+]' in r.detail and r.confidence == 'medium', r
    # the roll's end does not agree with the amendment table (a 3-year end):
    # the start is not this instrument's -> the old rule: Lease Year 2
    r = derive_current_rent(as_of=AS_OF, commencement='2025-02-10', commencement_confirmed=True,
                            commencement_is_term_start=True, term_end='2028-02-09',
                            schedule_docs=[lease, amendment])
    assert r.monthly == 1550.00 and '[commencement]' in r.detail, r
    # no end known: coverage alone places it, at medium
    r = derive_current_rent(as_of=AS_OF, commencement='2025-02-10', commencement_confirmed=True,
                            commencement_is_term_start=True, schedule_docs=[lease, amendment])
    assert r.monthly == 3090.00 and r.confidence == 'medium' and '[term start]' in r.detail, r
    # the same date as a confirmed ORIGINAL commencement: the lease table
    r = derive_current_rent(as_of=AS_OF, commencement='2025-02-10', commencement_confirmed=True,
                            schedule_docs=[lease, amendment])
    assert r.monthly == 1550.00, r
    # a packet filed twice: the duplicate of the lease table is not "the
    # current instrument"; with no amendment the lease table counts from the date
    r = derive_current_rent(as_of=AS_OF, commencement='2025-02-10', commencement_confirmed=True,
                            commencement_is_term_start=True, term_end='2028-02-09',
                            schedule_docs=[lease, lease])
    assert r.monthly == 1550.00 and r.confidence == 'high', r


def test_prose_periods_with_written_dates_take_their_own_sentence_amounts():
    # "Beginning on <date> and continuing through <date>" and "During the
    # Second Lease Year period from <date> through <date>": each period's
    # amounts are in its own sentence; the previous sentence's "... per month;
    # and" amount must not be taken (that was one escalation step off)
    t1 = ("Minimum rent. Beginning on March 1, 2011 and continuing through February 28, 2013, the fixed annual "
          "minimum rent shall be twelve thousand and no/100 dollars ($12,000.00) per year, payable monthly at one "
          "thousand and no/100 dollars ($1,000.00) per month; and Beginning on March 1, 2013 and continuing "
          "through February 28, 2015, the fixed annual minimum rent shall be twelve thousand six hundred and "
          "no/100 dollars ($12,600.00) per year, payable monthly at one thousand fifty and no/100 dollars "
          "($1,050.00) per month; and")
    t2 = ("Rent. During the First Lease Year period from June 1, 2016 through May 31, 2017, Tenant shall be "
          "required to pay to the Landlord fixed annual minimum rent in the sum of Eighteen Thousand and no/100 "
          "dollars ($18,000.00), payable monthly at the rate of One Thousand Five Hundred and no/100 dollars "
          "($1,500.00) per month; and During the Second Lease Year period from June 1, 2017 through May 31, 2018, "
          "Tenant shall be required to pay to the Landlord fixed annual minimum rent in the sum of Eighteen "
          "Thousand Five Hundred Forty and no/100 dollars ($18,540.00), payable monthly at the rate of One "
          "Thousand Five Hundred Forty-five and no/100 dollars ($1,545.00) per month;")
    r1 = [(r.start, r.end, r.monthly) for r in parse_dated_rows(t1)]
    assert r1 == [(date(2011, 3, 1), date(2013, 2, 28), 1000.0), (date(2013, 3, 1), date(2015, 2, 28), 1050.0)], r1
    r2 = [(r.start, r.monthly) for r in parse_dated_rows(t2)]
    assert r2 == [(date(2016, 6, 1), 1500.0), (date(2017, 6, 1), 1545.0)], r2



def test_payment_start_after_free_months_dates_the_first_paid_row():
    # "Months 1-3 $0.00" is no rent row; payments begin with Months 4-15, so
    # the table's month 1 is three months before the payment start
    t = ("2. Extended Term: The term is hereby extended starting on May 1, 2023 and ending on July 31, 2027. "
         "3. Base Rent: During the Extended Term, Tenant shall pay annual Base Rent as follows: "
         "EXTENDED TERM ANNUAL MONTHLY Months 1-3 $0.00 $0.00 Months 4-15 $30,000.00 $2,500.00 "
         "Months 16-27 $30,900.00 $2,575.00 Monthly installments of Base Rent shall be paid on the first day "
         "of each month during the Extended Term, beginning on August 1, 2023.")
    rows = parse_rent_schedule(t)
    assert [r.monthly for r in rows] == [2500.0, 2575.0], rows
    assert all(r.anchor == date(2023, 5, 1) for r in rows), [r.anchor for r in rows]
    _anchor(rows, None, None, t)
    assert [(r.start, r.end) for r in rows] == [(date(2023, 8, 1), date(2024, 7, 31)),
                                                (date(2024, 8, 1), date(2025, 7, 31))], rows
    # drafted the other way: the payment sentence names the term's start, free months included
    t2 = t.replace('beginning on August 1, 2023', 'beginning on May 1, 2023')
    assert all(r.anchor == date(2023, 5, 1) for r in parse_rent_schedule(t2))



def test_table_ended_before_today_is_dated_back_from_the_roll_lease_end():
    # an amendment's lease-year table counted from the original 2010
    # commencement ends in 2015; the rent roll's lease runs to 2027-08-31, so
    # the table is the current term's: dated back, Year 3 covers mid-2026
    t = ("Minimum Rent. Tenant shall pay Minimum Rent as follows: PERIOD ANNUAL RENT MONTHLY RENT "
         "Lease Year 1 $24,000.00 $2,000.00 Lease Year 2 $24,720.00 $2,060.00 Lease Year 3 $25,461.60 $2,121.80 "
         "Lease Year 4 $26,225.45 $2,185.45 Lease Year 5 $27,012.21 $2,251.02")
    r = derive_current_rent(as_of=date(2026, 6, 30), commencement=date(2010, 9, 1), schedule_text=t,
                            term_end=date(2027, 8, 31))
    assert r.monthly == 2185.45 and 'dated_back_from_roll_end' in r.flags and r.confidence == 'medium', r
    # without a roll lease end the last scheduled rent stays the answer
    r2 = derive_current_rent(as_of=date(2026, 6, 30), commencement=date(2010, 9, 1), schedule_text=t)
    assert r2.method == 'last_scheduled', r2



def test_written_out_amounts_by_month_range():
    t = ("Base Rent. Tenant shall pay Base Rent at a monthly schedule of: Months 01 - 12 Two Thousand One Hundred "
         "and 00/100 Dollars ($2,100.00) Months 13 - 24 Two Thousand One Hundred Sixty-Three and 00/100 Dollars "
         "($2,163.00) Months 25 - 36 Two Thousand Two Hundred Twenty-Seven and 89/100 Dollars ($2,227.89)")
    rows = parse_rent_schedule(t)
    assert [(r.label, r.monthly) for r in rows] == [('months 1-12', 2100.0), ('months 13-24', 2163.0),
                                                    ('months 25-36', 2227.89)], rows


def test_option_rent_in_prose_follows_the_scheduled_term():
    base = ("Minimum Rent. Tenant shall pay Minimum Rent as follows: PERIOD ANNUAL RENT MONTHLY RENT "
            "Lease Year 1 $24,000.00 $2,000.00 Lease Year 2 $24,720.00 $2,060.00 Lease Year 3 $25,461.60 $2,121.80 "
            "Lease Year 4 $26,225.45 $2,185.45 Lease Year 5 $27,012.21 $2,251.02 ")
    opt = ("B. Option Rent: If this Lease is extended, Tenant promises to pay rent to Landlord according to the "
           "following schedule: 1) During the first five (5) year option period, Tenant shall pay $2,400.00 per "
           "month. 2) During the second five (5) year option period, Tenant shall pay $2,640.00 per month. "
           "Tenant shall have two (2) options to extend the Term for five (5) years each.")
    r = derive_current_rent(as_of=date(2026, 6, 30), commencement=date(2018, 7, 1), schedule_text=base + opt)
    # "If this Lease is extended" is the option's condition, not an extension made
    assert r.monthly == 2400.0 and r.method == 'option_period' and r.confidence == 'medium', r



def test_later_amendment_table_with_its_own_start_beats_an_older_dated_row():
    lease = ("Base Rent. 04/01/2020 - 03/31/2027 $36,000.00 $3,000.00 per year and month. ")
    amend = ("FIRST AMENDMENT TO LEASE. The Effective Date of this Amendment is April 1, 2026. " + 'recital. ' * 30 +
             "2. Base Rent. Commencing on April 1, 2026 Tenant shall pay Base Rent as follows: PERIOD ANNUAL RENT "
             "MONTHLY RENT Year 1 $39,600.00 $3,300.00 Year 2 $40,788.00 $3,399.00")
    r = derive_current_rent(as_of=date(2026, 6, 30), schedule_docs=[lease, amend])
    assert r.monthly == 3300.0 and 'later_stated_table' in r.flags and r.confidence == 'medium', r


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
