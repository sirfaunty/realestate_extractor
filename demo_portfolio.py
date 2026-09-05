"""
Fictional demo portfolio generator.

Produces a believable small commercial portfolio — every name, address,
entity, dollar and date invented — as real PDFs the extraction engine can
chew on, plus a manifest.json of the ground-truth terms (so extraction can
be tied out against it, exactly like the KA campaign harness did against
Riley's masters). Deterministic: same --seed, same portfolio.

Uses: sales demos with zero client data, landing-page screenshots, a rich
fixture for exercising every module on the staging instance, and a
regression corpus for the segmenter (leases follow the ARTICLE/Section
form family the engine knows).

    venv/Scripts/python demo_portfolio.py                       # PDFs + manifest
    venv/Scripts/python demo_portfolio.py --ingest --db data/org_demo.db
    venv/Scripts/python demo_portfolio.py --ingest --approve --db data/org_demo.db
        (--approve finalizes every doc so `sync_client.py --db data/org_demo.db
         --push` can populate a demo org on the staging instance)

Output: data/demo_portfolio/<Property>/<files>.pdf + manifest.json
"""

import argparse
import json
import os
import random
import sys
from datetime import date

import fitz  # PyMuPDF

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'demo_portfolio')

# ─── Fictional universe ──────────────────────────────────────────────

PROPERTIES = [
    dict(key='MRSC', name='Maple Ridge Shopping Center',
         address='4210 Maple Ridge Parkway, Northfield Lake, Minnesota 55099',
         owner='Maple Ridge Retail Partners LLC', state='Minnesota',
         gla=68_400, cam_psf=6.85, lender='First Prairie Bank, N.A.',
         loan_amount=7_850_000, rate=5.625, closing=date(2022, 6, 15),
         maturity=date(2029, 7, 1), amort=25),
    dict(key='HPP', name='Harbor Point Plaza',
         address='1550 Harbor Point Drive, Bayview Falls, Wisconsin 54999',
         owner='Harbor Point Holdings LLC', state='Wisconsin',
         gla=41_200, cam_psf=5.90, lender='Lakeshore Mutual Insurance Company',
         loan_amount=4_200_000, rate=4.95, closing=date(2019, 11, 1),
         maturity=date(2026, 12, 1), amort=30),
    dict(key='CCR', name='Cedar Crossing Retail',
         address='880 Cedar Crossing Boulevard, Elk Hollow, Minnesota 55098',
         owner='Cedar Crossing Investors LLC', state='Minnesota',
         gla=53_750, cam_psf=7.40, lender=None),
]

TENANTS = [
    # (property key, tenant, entity suffix, use, sf, rent psf, guarantor?)
    ('MRSC', 'Northwind Coffee Roasters', 'LLC', 'retail coffee shop and roastery', 2_400, 24.50, True),
    ('MRSC', 'Blue Heron Dental', 'P.A.', 'general dentistry practice', 3_150, 21.00, False),
    ('MRSC', 'Prairie Fitness Co.', 'Inc.', 'fitness center and group exercise studio', 12_800, 14.75, True),
    ('MRSC', 'Gopher State Insurance Agency', 'Inc.', 'insurance agency office', 1_600, 19.50, False),
    ('MRSC', 'Iron Skillet Diner', 'LLC', 'full-service restaurant', 4_200, 22.00, True),
    ('HPP', 'Lakeside Nails & Spa', 'LLC', 'nail salon and day spa', 1_450, 23.00, True),
    ('HPP', 'Summit Wireless', 'Inc.', 'retail sale of wireless devices and service plans', 1_800, 26.00, False),
    ('HPP', 'Bright Path Learning Center', 'LLC', 'tutoring and educational services', 2_900, 17.25, True),
    ('HPP', 'Riverbend Books', 'LLC', 'retail bookstore and cafe', 3_600, 16.50, False),
    ('CCR', 'Cobalt Yoga Studio', 'LLC', 'yoga and wellness studio', 2_200, 20.00, True),
    ('CCR', 'Pine & Post Mercantile', 'Inc.', 'retail home goods and gifts', 5_400, 18.00, False),
    ('CCR', 'Sunrise Vision Care', 'P.A.', 'optometry clinic and eyewear retail', 2_750, 22.50, False),
]

GUARANTORS = ['Jordan A. Whitfield', 'Priya N. Raman', 'Marcus T. Delgado',
              'Elena K. Sorensen', 'Theo J. Abernathy', 'Nadia F. Okafor']

FOOTER = '*** FICTIONAL DOCUMENT GENERATED FOR SOFTWARE DEMONSTRATION — NOT A REAL INSTRUMENT ***'


def money(x):
    return f'${x:,.2f}'


def longdate(d):
    return d.strftime('%B %-d, %Y') if os.name != 'nt' else d.strftime('%B %d, %Y').replace(' 0', ' ')


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def end_of_term(start, months):
    nxt = add_months(start, months)
    return date(nxt.year, nxt.month, 1) - (date(nxt.year, nxt.month, 2) - date(nxt.year, nxt.month, 1))


# ─── PDF writer ──────────────────────────────────────────────────────

def write_pdf(path, pages, title):
    doc = fitz.open()
    for i, text in enumerate(pages, 1):
        p = doc.new_page(width=612, height=792)
        p.insert_text((64, 60), text, fontsize=10.2, fontname='helv',
                      lineheight=1.28)
        p.insert_text((64, 752), f'{title} — Page {i} of {len(pages)}',
                      fontsize=8, fontname='helv', color=(0.4, 0.4, 0.4))
        p.insert_text((64, 766), FOOTER, fontsize=7, fontname='helv',
                      color=(0.55, 0.1, 0.1))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc.save(path)
    doc.close()


# ─── Lease ───────────────────────────────────────────────────────────

def lease_pages(prop, t, rng):
    pkey, name, suffix, use, sf, psf, has_guar = t
    entity = f'{name.upper()}, {suffix}'
    suite = f'{rng.randint(101, 140)}'
    start = date(rng.choice([2019, 2020, 2021, 2022, 2023]),
                 rng.choice([1, 3, 4, 6, 8, 9, 11]), 1)
    months = rng.choice([60, 60, 84, 120])
    exp = end_of_term(start, months)
    signed = date(start.year, start.month, 1) - (date(start.year, start.month, 15) - date(start.year, start.month, 1))
    esc = rng.choice([2.5, 3.0, 3.0, 3.5])
    monthly = sf * psf / 12
    deposit = monthly * 2
    share = round(sf / prop['gla'] * 100, 2)
    opt_terms = rng.choice([1, 1, 2])
    opt_len = rng.choice([3, 5, 5])
    notice = rng.choice([180, 180, 270])
    guarantor = rng.choice(GUARANTORS) if has_guar else None
    steps = []
    for yr in range(1, min(months // 12, 5) + 1):
        r = psf * (1 + esc / 100) ** (yr - 1)
        steps.append(f'   Lease Year {yr}:   {money(r):>8} per sq. ft.   {money(sf * r / 12):>12} per month')

    p1 = f"""SHOPPING CENTER LEASE

THIS LEASE (the "Lease") is made and entered into as of {longdate(signed)},
by and between {prop['owner'].upper()}, a {prop['state']} limited liability
company ("Landlord"), and {entity}, a {prop['state']}
{'professional association' if suffix == 'P.A.' else 'corporation' if suffix == 'Inc.' else 'limited liability company'} ("Tenant").

ARTICLE 1. PREMISES
Section 1.1. Premises. Landlord hereby leases to Tenant, and Tenant hereby
leases from Landlord, approximately {sf:,} rentable square feet of space
known as Suite {suite} (the "Premises") in the shopping center commonly
known as {prop['name']}, located at {prop['address']}
(the "Shopping Center").
Section 1.2. Common Areas. Tenant shall have the non-exclusive right to use
the parking areas, sidewalks and other common areas of the Shopping Center.

ARTICLE 2. TERM
Section 2.1. Initial Term. The initial term of this Lease shall be
{months} months, commencing on {longdate(start)} (the "Commencement Date")
and expiring on {longdate(exp)} (the "Expiration Date"), unless sooner
terminated or extended as provided herein.
Section 2.2. Delivery. Landlord shall deliver the Premises to Tenant in
broom-clean condition with all building systems in good working order.

ARTICLE 3. RENT
Section 3.1. Base Rent. Tenant shall pay to Landlord Base Rent in equal
monthly installments, in advance, on the first day of each calendar month,
in accordance with the following schedule:
{chr(10).join(steps)}
Section 3.2. Escalation. Commencing on the first anniversary of the
Commencement Date and on each anniversary thereafter, Base Rent shall
increase by {esc:g} percent ({esc:g}%) over the Base Rent for the preceding
Lease Year.
Section 3.3. Security Deposit. Upon execution hereof Tenant shall deposit
with Landlord the sum of {money(deposit)} as security for the faithful
performance of Tenant's obligations hereunder.
"""
    p2 = f"""ARTICLE 4. ADDITIONAL RENT
Section 4.1. Proportionate Share. Tenant's Proportionate Share is {share}%,
being the ratio of the rentable area of the Premises ({sf:,} sq. ft.) to the
gross leasable area of the Shopping Center ({prop['gla']:,} sq. ft.).
Section 4.2. Operating Expenses. Tenant shall pay its Proportionate Share
of Common Area Maintenance costs, Real Estate Taxes and Insurance, currently
estimated at {money(prop['cam_psf'])} per rentable square foot per annum,
payable monthly with Base Rent and reconciled annually.
Section 4.3. Utilities. Tenant shall pay directly for all utilities
separately metered to the Premises.

ARTICLE 5. USE
Section 5.1. Permitted Use. The Premises shall be used solely for the
operation of a {use} and for no other purpose without Landlord's prior
written consent.
Section 5.2. Operating Covenant. Tenant shall open for business within
ninety (90) days after the Commencement Date and shall thereafter operate
continuously during customary Shopping Center hours.

ARTICLE 6. ASSIGNMENT AND SUBLETTING
Section 6.1. Consent Required. Tenant shall not assign this Lease or sublet
all or any portion of the Premises without the prior written consent of
Landlord, which consent shall not be unreasonably withheld, conditioned or
delayed.

ARTICLE 7. RENEWAL OPTION
Section 7.1. Option to Extend. Provided Tenant is not then in default,
Tenant shall have {opt_terms} option{'s' if opt_terms > 1 else ''} to extend the
Term for {'an additional period' if opt_terms == 1 else 'successive additional periods'}
of {opt_len} years each, exercisable by written notice to Landlord not
less than {notice} days prior to the expiration of the then-current Term.
Section 7.2. Renewal Rent. Base Rent for each option period shall continue
to escalate at {esc:g}% per annum from the Base Rent in effect at the end of
the preceding Term.

ARTICLE 8. SUBORDINATION
Section 8.1. This Lease is subject and subordinate to any mortgage now or
hereafter placed upon the Shopping Center, provided the holder thereof
agrees not to disturb Tenant's possession so long as Tenant is not in
default.
"""
    p3 = f"""ARTICLE 9. DEFAULT AND REMEDIES
Section 9.1. Events of Default. Failure to pay Rent within ten (10) days
after written notice, or failure to perform any other covenant within
thirty (30) days after written notice, shall constitute an Event of Default.
Section 9.2. Remedies. Upon an Event of Default Landlord may terminate this
Lease, re-enter the Premises, and recover all damages permitted by law.

ARTICLE 10. GUARANTY
{'Section 10.1. The obligations of Tenant hereunder are unconditionally guaranteed by ' + guarantor + ' pursuant to a Guaranty of Lease of even date herewith.' if guarantor else 'Section 10.1. Intentionally omitted.'}

ARTICLE 11. MISCELLANEOUS
Section 11.1. Notices. All notices shall be in writing and delivered to
the addresses set forth below or such other address as a party may designate.
Section 11.2. Governing Law. This Lease shall be governed by the laws of
the State of {prop['state']}.
Section 11.3. Entire Agreement. This Lease constitutes the entire agreement
between the parties and supersedes all prior negotiations.

IN WITNESS WHEREOF, the parties have executed this Lease as of the date
first written above.

LANDLORD:                                  TENANT:
{prop['owner'].upper()}
{'':43}{entity}

By: /s/ Authorized Signatory               By: /s/ Authorized Officer
Its: Manager                               Its: {'President' if suffix != 'LLC' else 'Managing Member'}
"""
    truth = dict(property=prop['name'], tenant=name, entity=entity, suite=suite,
                 square_feet=sf, commencement=start.isoformat(),
                 expiration=exp.isoformat(), term_months=months,
                 base_rent_psf_year1=psf, base_rent_monthly_year1=round(monthly, 2),
                 escalation_pct=esc, security_deposit=round(deposit, 2),
                 proportionate_share_pct=share, permitted_use=use,
                 renewal_options=opt_terms, renewal_option_years=opt_len,
                 renewal_notice_days=notice, guarantor=guarantor)
    return [p1, p2, p3], truth


def amendment_pages(prop, truth, rng):
    """First Amendment: expansion or extension."""
    kind = rng.choice(['extension', 'expansion'])
    amend_date = date(int(truth['commencement'][:4]) + 2, rng.choice([2, 5, 9]), 1)
    if kind == 'extension':
        new_exp = end_of_term(date.fromisoformat(truth['expiration']) , 0)
        new_exp = add_months(new_exp, 36)
        new_exp = date(new_exp.year, new_exp.month, 1) - (date(new_exp.year, new_exp.month, 2) - date(new_exp.year, new_exp.month, 1))
        body = f"""2. Extension of Term. The Term of the Lease is hereby extended for a
period of thirty-six (36) months, such that the Expiration Date shall be
{longdate(new_exp)}. All references in the Lease to the Expiration Date
shall mean {longdate(new_exp)}.
3. Base Rent. Base Rent during the extension period shall continue to
escalate at {truth['escalation_pct']:g}% per annum in accordance with Section 3.2 of the Lease."""
        change = dict(type='extension', new_expiration=new_exp.isoformat())
    else:
        add_sf = rng.choice([600, 850, 1200])
        new_sf = truth['square_feet'] + add_sf
        body = f"""2. Expansion of Premises. Effective {longdate(amend_date)}, the Premises
are expanded to include approximately {add_sf:,} rentable square feet
adjacent to Suite {truth['suite']}, such that the Premises shall consist of a
combined total of approximately {new_sf:,} rentable square feet.
3. Base Rent. Base Rent shall be adjusted to reflect the expanded Premises
at the per-square-foot rate then in effect under the Lease. Tenant's
Proportionate Share is adjusted to {round(new_sf / prop['gla'] * 100, 2)}%."""
        change = dict(type='expansion', new_square_feet=new_sf)
    page = f"""FIRST AMENDMENT TO SHOPPING CENTER LEASE

THIS FIRST AMENDMENT TO LEASE (this "Amendment") is made as of
{longdate(amend_date)}, by and between {prop['owner'].upper()} ("Landlord")
and {truth['entity']} ("Tenant").

RECITALS
A. Landlord and Tenant are parties to that certain Shopping Center Lease
dated on or about {longdate(date.fromisoformat(truth['commencement']))} (the "Lease") for
Suite {truth['suite']} at {prop['name']}.
B. The parties desire to amend the Lease as set forth herein.

AGREEMENT
1. Defined Terms. Capitalized terms not defined herein have the meanings
given in the Lease.
{body}
4. Ratification. Except as modified hereby, the Lease remains in full
force and effect and is hereby ratified and confirmed.

LANDLORD: {prop['owner'].upper()}          TENANT: {truth['entity']}
By: /s/ Authorized Signatory                By: /s/ Authorized Officer
"""
    return [page], dict(amendment_date=amend_date.isoformat(), **change)


def loan_pages(prop):
    p = prop
    monthly_rate = p['rate'] / 100 / 12
    n = p['amort'] * 12
    pmt = p['loan_amount'] * monthly_rate / (1 - (1 + monthly_rate) ** -n)
    p1 = f"""LOAN AGREEMENT

THIS LOAN AGREEMENT (this "Agreement") is entered into as of
{longdate(p['closing'])}, between {p['owner'].upper()}, a {p['state']}
limited liability company ("Borrower"), and {p['lender'].upper()} ("Lender").

ARTICLE 1. THE LOAN
Section 1.1. Loan Amount. Lender agrees to lend to Borrower the principal
sum of {money(p['loan_amount'])} (the "Loan"), evidenced by a Promissory Note of
even date herewith (the "Note") and secured by a first Mortgage on the
real property commonly known as {p['name']}, {p['address']}
(the "Property").
Section 1.2. Interest Rate. The outstanding principal balance shall bear
interest at a fixed rate of {p['rate']:.3f}% per annum, computed on the basis
of a 360-day year of twelve 30-day months.
Section 1.3. Payments. Borrower shall pay monthly installments of principal
and interest of {money(pmt)} on the first day of each month, based on a
{p['amort']}-year amortization schedule.
Section 1.4. Maturity Date. All outstanding principal, accrued interest and
other sums shall be due and payable in full on {longdate(p['maturity'])}
(the "Maturity Date").

ARTICLE 2. PREPAYMENT
Section 2.1. Borrower may prepay the Loan in whole upon thirty (30) days
notice, subject to a prepayment premium equal to the greater of one
percent (1%) of the amount prepaid or a yield maintenance amount, except
that no premium applies during the final ninety (90) days before the
Maturity Date.
"""
    p2 = f"""ARTICLE 3. FINANCIAL COVENANTS
Section 3.1. Debt Service Coverage. Borrower shall maintain a Debt Service
Coverage Ratio of not less than 1.25 to 1.00, tested annually as of
December 31 based on the trailing twelve months' Net Operating Income.
Section 3.2. Reporting. Borrower shall deliver annual operating statements
and a current certified rent roll within one hundred twenty (120) days
after each fiscal year end, and quarterly rent rolls within forty-five (45)
days after each quarter.
Section 3.3. Reserves. Borrower shall fund a replacement reserve of $0.20
per square foot of gross leasable area per annum.

ARTICLE 4. LEASING COVENANTS
Section 4.1. Lender's consent shall be required for any new lease or
amendment covering more than 10,000 square feet or with a term exceeding
ten (10) years.
Section 4.2. Borrower shall deliver subordination, non-disturbance and
attornment agreements from each tenant occupying 5,000 square feet or more.

ARTICLE 5. EVENTS OF DEFAULT
Section 5.1. Failure to pay any installment within ten (10) days of its
due date, or breach of any covenant not cured within thirty (30) days after
notice, shall constitute an Event of Default, whereupon Lender may
accelerate the Loan and exercise all remedies under the Loan Documents.

IN WITNESS WHEREOF, the parties have executed this Agreement as of the
date first written above.

BORROWER: {p['owner'].upper()}             LENDER: {p['lender'].upper()}
By: /s/ Authorized Signatory               By: /s/ Vice President
"""
    truth = dict(property=p['name'], lender=p['lender'], principal=p['loan_amount'],
                 rate_pct=p['rate'], monthly_payment=round(pmt, 2),
                 amortization_years=p['amort'], closing=p['closing'].isoformat(),
                 maturity=p['maturity'].isoformat(), dscr_min=1.25)
    return [p1, p2], truth


# ─── Generate ────────────────────────────────────────────────────────

def generate(seed):
    rng = random.Random(seed)
    manifest = dict(seed=seed, generated=date.today().isoformat(),
                    disclaimer='Entirely fictional portfolio for software demonstration.',
                    properties=[], leases=[], amendments=[], loans=[])
    files = []
    for prop in PROPERTIES:
        pdir = os.path.join(OUT, prop['name'].replace(' ', '_'))
        manifest['properties'].append({k: (v.isoformat() if isinstance(v, date) else v)
                                       for k, v in prop.items()})
        if prop['lender']:
            pages, truth = loan_pages(prop)
            path = os.path.join(pdir, f"{prop['name']} - Loan Agreement.pdf")
            write_pdf(path, pages, 'Loan Agreement')
            truth['file'] = os.path.relpath(path, HERE)
            manifest['loans'].append(truth)
            files.append((path, 'loan', prop['name']))
    for t in TENANTS:
        prop = next(p for p in PROPERTIES if p['key'] == t[0])
        pdir = os.path.join(OUT, prop['name'].replace(' ', '_'))
        pages, truth = lease_pages(prop, t, rng)
        path = os.path.join(pdir, f"{prop['name']} - {t[1]} Lease.pdf")
        write_pdf(path, pages, 'Shopping Center Lease')
        truth['file'] = os.path.relpath(path, HERE)
        manifest['leases'].append(truth)
        files.append((path, 'lease', prop['name']))
        if rng.random() < 0.4:
            apages, atruth = amendment_pages(prop, truth, rng)
            apath = os.path.join(pdir, f"{prop['name']} - {t[1]} First Amendment.pdf")
            write_pdf(apath, apages, 'First Amendment to Lease')
            atruth.update(tenant=t[1], property=prop['name'],
                          file=os.path.relpath(apath, HERE))
            manifest['amendments'].append(atruth)
            files.append((apath, 'lease', prop['name']))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
    return files, manifest


def ingest(files, db_path, approve):
    sys.path.insert(0, os.path.dirname(HERE))
    from realestate_extractor.database import Database
    from realestate_extractor.batch_processor import BatchProcessor
    db = Database(db_path)
    db.connect()
    proc = BatchProcessor(db)          # ingest only — no LLM calls here
    ok = 0
    for path, dtype, prop_name in files:
        r = proc.process_single(path, document_type=dtype,
                                property_name=prop_name)
        if r.success:
            ok += 1
            if approve:
                doc = db.get_document(r.document_id)
                if doc and doc.get('property_id'):
                    db.approve_document_match(r.document_id, doc['property_id'])
            print(f'  ingested {os.path.basename(path)} (doc {r.document_id})')
        else:
            print(f'  FAILED {os.path.basename(path)}: {r.error}')
    print(f'\n{ok}/{len(files)} ingested into {db_path}'
          + (' and approved (sync-eligible)' if approve else ''))
    if approve:
        print('Next: run the property-level Analyze in the app for extracted '
              'terms, then\n  sync_client.py --db ' + db_path +
              ' --url <instance> --token <token> --push')


def main():
    ap = argparse.ArgumentParser(description='Generate a fictional demo portfolio.')
    ap.add_argument('--seed', type=int, default=2026)
    ap.add_argument('--ingest', action='store_true',
                    help='also ingest the PDFs into --db with property links')
    ap.add_argument('--approve', action='store_true',
                    help='with --ingest: approve/finalize every doc')
    ap.add_argument('--db', default=os.path.join(HERE, 'data', 'org_demo.db'))
    args = ap.parse_args()

    files, manifest = generate(args.seed)
    print(f"Generated {len(manifest['leases'])} leases, "
          f"{len(manifest['amendments'])} amendments, "
          f"{len(manifest['loans'])} loan agreements across "
          f"{len(manifest['properties'])} properties → {OUT}")
    print(f"Ground truth: {os.path.join(OUT, 'manifest.json')}")
    if args.ingest:
        print(f'\nIngesting into {args.db} …')
        ingest(files, args.db, args.approve)


if __name__ == '__main__':
    main()
