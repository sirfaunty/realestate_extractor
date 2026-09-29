"""
Regression tests for extractors/rent_derivation.py — current rent from paper.

Every fixture is FICTIONAL (invented tenants, dates, dollars) but copies the
exact LAYOUT of a real lease table that once broke the parser, found by the
2026-09-29 rent tie-out against Landlord's rent roll. Client text never goes in
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
