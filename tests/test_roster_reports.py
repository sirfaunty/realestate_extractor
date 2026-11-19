"""
The two roster reports: the Portfolio Rent Roll workbook and the Property
One-Pager. A rent is a figure on the face of either only when it is
high-confidence; everything else goes to Review / Open items. Fictional
properties and tenants; the real app (no dev bypass) on a throwaway org.

    venv/Scripts/python tests/test_roster_reports.py
"""

import io
import os
import sys
import tempfile
from datetime import date

TMP = tempfile.mkdtemp(prefix='cap_reports_')
os.environ['CAPACTIVE_DEV_MODE'] = '0'
os.environ['CAPACTIVE_DATA_DIR'] = TMP
os.environ['CAPACTIVE_CONFIG_DB'] = os.path.join(TMP, 'config.db')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from realestate_extractor.lease_roster import RosterRow, summarize  # noqa: E402
from realestate_extractor import roster_reports as rr  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


AS_OF = date(2026, 6, 30)


def row(tenant, sf, rent, conf, exp, needs=''):
    return RosterRow(tenant=tenant, filenames=[f'Elm Court - {tenant} Lease.pdf'], square_feet=sf,
                     commencement=date(2022, 1, 1), commencement_source='lease', expiration=exp,
                     expiration_source='lease', monthly_rent=rent, rent_method='schedule',
                     rent_confidence=conf, rent_detail='5-row schedule, row year 5', needs=needs,
                     grouped_by='filename', key=f'fn:{tenant.lower()}')


ROWS = [row('Bean Coffee', 2000, 5000.0, 'high', date(2027, 3, 31)),
        row('Pet Barn', 4000, 9000.0, 'high', date(2030, 12, 31)),
        row('Fern Dental', 1500, 3100.0, 'medium', date(2026, 12, 31), needs='confirm the commencement date'),
        row('Quick Print', None, None, 'low', None, needs='upload the lease')]


def prop(pid, name, rows, total_sf):
    s = summarize(rows, AS_OF)
    ten = rr._tenancies(rows)
    return {'id': pid, 'name': name, 'address': '100 Main St', 'city': 'Elmwood', 'state': 'MN',
            'property_type': 'retail', 'total_sqft': total_sf, 'rows': rows, 'tenancies': ten, 'summary': s,
            'annual_rent_shown': round(s['monthly_rent_confirmed'] * 12, 2), 'avg_psf': 28.0,
            'occupancy': s['leased_sf'] / total_sf, 'expiring_12mo': rr._expiring_within(rows, AS_OF, 365),
            'expiring_24mo': rr._expiring_within(rows, AS_OF, 730),
            'in_review': [r for r in rows if r.needs], 'rollover': rr._rollover(rows, AS_OF)}


DATA = {'as_of': AS_OF, 'properties': [prop(1, 'Elm Court', ROWS, 10000)]}

print('\nPortfolio Rent Roll workbook')
from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(io.BytesIO(rr.build_rent_roll_workbook(DATA, prepared_for='Cedar Test Partners')))
test('four sheets in order', wb.sheetnames == ['Summary', 'Rent Roll', 'Rollover', 'Review'])
ws = wb['Summary']
vals = [c.value for c in ws[6]]
test('the summary row carries the property and only the shown rent',
     vals[0] == 'Elm Court' and vals[7] == 14000.0 and vals[8] == 168000.0 and vals[9] == 2)
test('a total row follows', ws.cell(row=7, column=1).value == 'Total' and ws.cell(row=7, column=8).value == 14000.0)
roll = {r[1]: r for r in wb['Rent Roll'].iter_rows(min_row=2, values_only=True)}
test('a high-confidence rent is a figure with rent per SF',
     roll['Bean Coffee'][7] == 5000.0 and roll['Bean Coffee'][9] == 30.0 and roll['Bean Coffee'][10] == 'Shown')
test('a medium-confidence rent is blank on the rent roll',
     roll['Fern Dental'][7] is None and roll['Fern Dental'][8] is None and roll['Fern Dental'][10] == 'In review')
test('every row names its source documents', all('Lease.pdf' in (r[13] or '') for r in roll.values()))
rev = {r[1]: r for r in wb['Review'].iter_rows(min_row=2, values_only=True)}
test('the review sheet lists what is held back with the step that resolves it',
     set(rev) == {'Fern Dental', 'Quick Print'} and rev['Fern Dental'][3] == 'confirm the commencement date'
     and rev['Fern Dental'][4] == 3100.0 and rev['Fern Dental'][5] == 'medium')
ro = list(wb['Rollover'].iter_rows(min_row=2, values_only=True))
by = {r[1]: r for r in ro if r[0] == 'All properties'}
test('rollover counts leases by year, with rent only where shown',
     by['2026'][2] == 1 and by['2026'][4] == 0 and by['2027'][2] == 1 and by['2027'][4] == 5000.0
     and by['Expiration not on file'][2] == 1)
empty = load_workbook(io.BytesIO(rr.build_rent_roll_workbook({'as_of': AS_OF, 'properties': []})))
test('an org with no leases still gets a workbook that says so',
     empty['Summary'].cell(row=6, column=1).value == 'No leases on file yet.')

print('\nProperty One-Pager')
from docx import Document  # noqa: E402
doc = Document(io.BytesIO(rr.build_one_pager(DATA['properties'][0], AS_OF, prepared_for='Cedar Test Partners')))
text = '\n'.join(p.text for p in doc.paragraphs)
cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
test('it names the property, place and date', 'Elm Court' in text and 'Elmwood' in text and '2026-06-30' in text)
test('shown rents are figures', '$5,000.00' in cells and '$9,000.00' in cells)
test('a rent in review is never printed', 'in review' in cells and not any('3,100' in c for c in cells)
     and '3,100' not in text)
test('open items say what resolves each', 'Fern Dental: confirm the commencement date' in text)
test('the headline says how much of the roster is shown', 'Rent is shown for 2 of 4 tenancies' in text)

print('\nExpired leases and held-back figures')
ROWS2 = [row('Bean Coffee', 2000, 5000.0, 'high', date(2027, 3, 31)),
         row('Old Mill Cafe', 1000, 2000.0, 'high', date(2026, 3, 31)),
         row('Fern Dental', 1500, 3100.0, 'medium', date(2028, 12, 31),
             needs='reconcile: lease says $3,100.00/mo, rent roll says $3,400.00/mo'),
         row('Pet Barn', 4000, 9000.0, 'medium', date(2030, 12, 31),
             needs='rent roll shows $9,450.00/mo — another step of the lease schedule; check which applies today'),
         row('Snow Globe Pop-Up', 3000, None, 'low', date(2026, 12, 31),
             needs='seasonal lease: $12,000.00 total for 2026-10-01..2026-12-31 — not a monthly rent')]
P2 = prop(2, 'Oak Plaza', ROWS2, 20000)
wb = load_workbook(io.BytesIO(rr.build_rent_roll_workbook({'as_of': AS_OF, 'properties': [P2]})))
ro = {r[1]: r for r in wb['Rollover'].iter_rows(min_row=2, values_only=True) if r[0] == 'Oak Plaza'}
test('a lease past its expiration has its own rollover line, not this year',
     ro['Expired before 2026-06-30'][2] == 1 and ro['Expired before 2026-06-30'][4] == 2000.0
     and ro['2026'][2] == 1 and ro['2026'][3] == 3000)
roll = {r[1]: r for r in wb['Rent Roll'].iter_rows(min_row=2, values_only=True)}
test('the rent roll marks it Expired and the rest Current',
     roll['Old Mill Cafe'][11] == 'Expired' and roll['Bean Coffee'][11] == 'Current'
     and roll['Old Mill Cafe'][7] == 2000.0)
vals = [c.value for c in wb['Summary'][6]]
test('the summary counts expired leases and the rent they carry', vals[15] == 1 and vals[16] == 2000.0)
rev = {r[1]: r for r in wb['Review'].iter_rows(min_row=2, values_only=True)}
test('the review sheet keeps the figures', '$3,400.00' in rev['Fern Dental'][3] and rev['Fern Dental'][4] == 3100.0)
doc = Document(io.BytesIO(rr.build_one_pager(P2, AS_OF)))
text = '\n'.join(p.text for p in doc.paragraphs)
cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
items = text[text.index('Open items'):]
test('the one-pager marks the expired lease', '2026-03-31 (expired)' in cells
     and '1 lease is past expiration and still counted, with $2,000 of the monthly rent' in text)
test('open items keep the step and drop the dollars', '$' not in items
     and 'Fern Dental: reconcile the lease rent with the rent roll' in items
     and 'Pet Barn: rent roll shows a different rent — another step of the lease schedule' in items
     and 'Snow Globe Pop-Up: seasonal lease: a total for 2026-10-01..2026-12-31 — not a monthly rent' in items)

print('\nIn the app')
from werkzeug.security import generate_password_hash  # noqa: E402
from realestate_extractor.config import ConfigStore  # noqa: E402
from realestate_extractor.licensing import generate_org_key  # noqa: E402
from realestate_extractor.permissions import PermissionStore  # noqa: E402
from realestate_extractor.webapp import app, get_org_db  # noqa: E402

store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
for org, who in (('cedar', 'avery'), ('birch', 'blake')):
    store.create_org(org, f'{org.title()} Test Partners', generate_org_key(org, 'standard'))
    store.create_user(org, who, f'{who}@{org}.test', who.title(), role='admin',
                      password_hash=generate_password_hash('right-pass-1'))
store.close()
perms = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
perms.connect()
perms.init_user_permissions('avery', 'cedar', role='admin')
perms.init_user_permissions('blake', 'birch', role='admin')
perms.close()

app.config['TESTING'] = True
with app.test_request_context():
    db = get_org_db('cedar')
    db.conn.execute("INSERT INTO properties (id, name, city, state, property_type, total_sqft) "
                    "VALUES (1, 'Elm Court', 'Elmwood', 'MN', 'retail', 10000)")
    db.conn.execute("INSERT INTO documents (id, filename, filepath, document_type, property_id) "
                    "VALUES (1, 'Elm Court - Bean Coffee Lease.pdf', 'x', 'lease', 1)")
    db.conn.execute("INSERT INTO financial_terms (document_id, term_type, value_raw, expiration_date) "
                    "VALUES (1, 'governing_expiration', '2029-12-31', '2029-12-31')")
    db.conn.execute("INSERT INTO financial_terms (document_id, term_type, value_raw) "
                    "VALUES (1, 'tenant_identity', 'Bean Coffee LLC')")
    db.conn.execute("UPDATE documents SET analysis_status = 'analyzed'")
    db.conn.commit()
    db.close()

anon = app.test_client()
test('signed out, neither report downloads',
     anon.get('/reports/rent-roll.xlsx').status_code == 302
     and anon.get('/property/1/one-pager.docx').status_code == 302)
c = app.test_client()
c.post('/login', data={'email': 'avery@cedar.test', 'password': 'right-pass-1'})
r = c.get('/reports/rent-roll.xlsx')
wb = load_workbook(io.BytesIO(r.data)) if r.status_code == 200 else None
test('the workbook downloads with the org name in its file name', wb is not None
     and 'Cedar_Test_Partners_Portfolio_Rent_Roll_' in r.headers.get('Content-Disposition', ''))
test('it holds the org tenancy', wb is not None
     and any('Bean Coffee' in str(x[1]) for x in wb['Rent Roll'].iter_rows(min_row=2, values_only=True)))
r = c.get('/property/1/one-pager.docx')
test('the one-pager downloads', r.status_code == 200
     and 'Elm_Court_One_Pager_' in r.headers.get('Content-Disposition', '')
     and 'Elm Court' in '\n'.join(p.text for p in Document(io.BytesIO(r.data)).paragraphs))
test('a property that does not exist is refused', c.get('/property/99/one-pager.docx').status_code == 302)
page = c.get('/property/1/roster').get_data(as_text=True)
test('the roster page links both reports', '/property/1/one-pager.docx' in page and '/reports/rent-roll.xlsx' in page)
test('the dashboard and the properties page link the portfolio rent roll',
     '/reports/rent-roll.xlsx' in c.get('/').get_data(as_text=True)
     and '/reports/rent-roll.xlsx' in c.get('/properties').get_data(as_text=True))
test('the download is in the Audit Trail', 'report downloaded' in c.get('/admin/audit').get_data(as_text=True))
other = app.test_client()
other.post('/login', data={'email': 'blake@birch.test', 'password': 'right-pass-1'})
wb = load_workbook(io.BytesIO(other.get('/reports/rent-roll.xlsx').data))
test('another org sees none of it', not any('Bean Coffee' in str(x) for x in
                                            wb['Rent Roll'].iter_rows(min_row=2, values_only=True))
     and other.get('/property/1/one-pager.docx').status_code == 302)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
