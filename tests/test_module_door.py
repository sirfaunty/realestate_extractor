"""
The module-door publisher (modules/module_door.py) on a fictional property:
two lease documents and a rent worksheet in a throwaway org DB -> a package
with the registry, page text, one tenancy row, placed rent steps, provisions
and a manifest written last, readable through the shipped door.

    venv/Scripts/python tests/test_module_door.py
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.database import Database  # noqa: E402
from realestate_extractor.modules.module_door import build_door_tables, publish_property_door, verify_quotes  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


LEASE = ["""LEASE AGREEMENT between Elm Partners LLC, as Landlord, and Bean Coffee Inc, as
Tenant, for Suite 216 at Elm Court, containing 2,400 square feet. The Commencement
Date shall be July 1, 2022.""",
         """3. Minimum Rent. Tenant shall pay Landlord Minimum Rent as follows:
PERIOD ANNUAL RENT MONTHLY RENT
Lease Year 1 $24,000.00 $2,000.00
Lease Year 2 $24,720.00 $2,060.00
Lease Year 3 $25,461.60 $2,121.80
Lease Year 4 $26,225.45 $2,185.45
Lease Year 5 $27,012.21 $2,251.02"""]
AMENDMENT = ["""FIRST AMENDMENT TO LEASE between Elm Partners LLC and Bean Coffee Inc. The
Effective Date of this Amendment is July 1, 2026. """ + 'recital. ' * 60 + """
2. Minimum Rent. From the Effective Date Tenant shall pay:
TERM ANNUAL RENT MONTHLY RENT
Year 1 $30,000.00 $2,500.00
Year 2 $30,900.00 $2,575.00"""]
NER = ["""NET EFFECTIVE RENT SHEET PROPERTY: Elm Court MOS VACANT: 0.00 SPACE: 216 SF: 2,400
PROPOSED TENANT: Bean Coffee NET RENT OPERATING COSTS GROSS RENT YRS MOS PSF MONTHLY
ANNUALLY PERIOD CAM: $3.80 12 $12.50 $2,500.00 $30,000.00"""]


def main():
    tmp = tempfile.mkdtemp(prefix='cap_door_')
    db_path = os.path.join(tmp, 'org.db')
    db = Database(db_path)
    db.connect()
    pid = db.create_property('Elm Court', property_type='retail')
    docs = {}
    OTHER = ["LEASE AGREEMENT between Elm Partners LLC, as Landlord, and River Dental PA, as Tenant, "
             "for Suite 220 at Elm Court, 1,800 square feet. Base Rent: $3,000.00 per month.",
             "4. Base Rent. Tenant shall pay Base Rent as follows:\nPERIOD ANNUAL RENT MONTHLY RENT\n"
             "Months 1-12 $36,000.00 $3,000.00\nMonths 13-24 $37,080.00 $3,090.00"]
    for name, pages in (('EC-BeanCoffee-Lease.pdf', LEASE), ('EC-BeanCoffee-1st Amendment.pdf', AMENDMENT),
                        ('EC-BeanCoffee-NERS.pdf', NER), ('EC-RiverDental-Lease.pdf', OTHER)):
        did = db.insert_document(name, os.path.join(tmp, name), 'lease', auto_create_property=False)
        db.conn.execute("UPDATE documents SET property_id=?, file_hash=?, page_count=?, is_scanned=0, "
                        "analysis_status='analyzed' WHERE id=?", (pid, 'f' * 64, len(pages), did))
        for n, p in enumerate(pages, 1):
            db.conn.execute("INSERT INTO document_fulltext(document_id, page_number, content) VALUES (?,?,?)",
                            (did, n, p))
        docs[name] = did
    lease_id = docs['EC-BeanCoffee-Lease.pdf']
    for term, raw, num, unit, page in (('tenant_identity', 'Bean Coffee Inc', None, None, 1),
                                       ('square_footage', '2,400', 2400, 'sf', 1),
                                       ('base_rent', '$2,000.00', 2000.0, 'monthly', 2),
                                       ('lease_commencement', 'July 1, 2022', None, None, 1),
                                       ('landlord_name', 'Birchwood Realty LLC', None, None, 1)):
        db.conn.execute("INSERT INTO financial_terms(document_id, term_type, value_raw, value_numeric, value_unit, "
                        "page_number, confidence) VALUES (?,?,?,?,?,?,?)", (lease_id, term, raw, num, unit, page, 0.9))
    db.conn.commit()
    db.close()

    out = os.path.join(tmp, 'door')
    man = publish_property_door(db_path, out, 'ELMCOURT', property_id=pid, as_of=date(2026, 9, 1),
                                property_label='Elm Court')

    print('\nPackage')
    test('manifest names the generation db and every table',
         os.path.exists(os.path.join(out, man['database'])) and set(man['tables']) == {
             'cap_document', 'cap_page_text', 'cap_lease_lease', 'cap_lease_chain', 'cap_lease_rent_step',
             'cap_lease_provision', 'cap_lease_option', 'cap_lease_fact', 'cap_ner_deal', 'cap_ner_cost_line', 'cap_lease_event', 'cap_option_register', 'cap_lease_term', 'cap_lease_exhibit', 'cap_roll_reconciliation'})
    test('HANDOFF, SOURCE_PATH, BOUNDARY_CONTRACT and the door reader are in the package',
         all(os.path.exists(os.path.join(out, f)) for f in
             ('HANDOFF.txt', 'SOURCE_PATH.txt', 'BOUNDARY_CONTRACT.md', 'door/_cap_door.py')))
    test('file hashes in the manifest match the files',
         all(man['files'][f]['bytes'] == os.path.getsize(os.path.join(out, f)) for f in man['files']))

    print('\nThrough the door')
    sys.path.insert(0, os.path.join(out, 'door'))
    import _cap_door as CAP   # noqa: E402
    CAP.PACKAGE = out
    with open(os.path.join(out, 'door', 'HANDOFF.txt'), 'w', encoding='utf-8') as fh:
        fh.write('2026-09-01 | cap_lease_lease | all | all | test\n'
                 '2026-09-01 | cap_lease_rent_step | all | all | test\n'
                 '2026-09-01 | cap_document | all | all | test\n'
                 '2026-09-01 | cap_lease_chain | all | all | test\n')
    with CAP.snapshot() as s:
        _, leases = s.rows('cap_lease_lease')
        _, steps = s.rows('cap_lease_rent_step')
        _, regs = s.rows('cap_document')
        _, chain = s.rows('cap_lease_chain')
        try:
            s.rows('cap_lease_provision')
            refused = False
        except PermissionError:
            refused = True
    test('the four documents are registered with their sha256 and family',
         len(regs) == 4 and all(r['sha256'] == 'f' * 64 for r in regs)
         and {r['family'] for r in regs} == {'instrument', 'ner_sheet'})
    test('two tenancies: the coffee shop (lease, amendment and worksheet together) and the dentist',
         len(leases) == 2 and sorted(L['tenant_key'] for L in leases) == ['bean_coffee', 'river_dental']
         and max(L['n_documents'] for L in leases) == 3)
    row = next(L for L in leases if L['tenant_key'] == 'bean_coffee')
    test('its SF and commencement come from the lease', row['sf'] == 2400 and row['commencement'] == '2022-07-01')
    test('its current rent is the amendment Year 1 (effective 2026-07-01), cited and flagged medium',
         row['base_rent_monthly'] == 2500.0 and row['rent_confidence'] == 'medium')
    test('the worksheet is in the chain as a worksheet, not an instrument',
         any(c['role'] == 'ner_sheet' for c in chain) and
         [c['role'] for c in chain if c['filename'] == 'EC-BeanCoffee-Lease.pdf'] == ['instrument'])
    test('rent steps are placed in time and cite document + page',
         len(steps) >= 5 and all(s_['source_doc_id'] and s_['source_page'] for s_ in steps)
         and any(s_['period_from'] == '2022-07-01' and s_['monthly_amount'] == 2000.0 for s_ in steps))
    test('a table no date places is published with its printed amounts, period blank, and says why',
         any(s_['period_from'] is None and s_['monthly_amount'] == 3090.0 and s_['lease_id'].replace(' ', '_') == 'fn:river_dental'
             and (s_['period_basis'] or '').startswith('unplaced') and s_['source_page'] == 2 for s_ in steps))
    test('the amendment table counts from its own effective date',
         any(s_['period_from'] == '2026-07-01' and s_['monthly_amount'] == 2500.0 for s_ in steps))
    test('a table not declared in HANDOFF.txt is refused', refused)

    print('\nCitations')
    import sqlite3  # noqa: E402
    cc = sqlite3.connect(os.path.join(out, man['database']))
    cc.row_factory = sqlite3.Row
    page = {(r['doc_id'], r['page_no']): r['text'] for r in cc.execute('SELECT * FROM cap_page_text')}
    norm = lambda t: ' '.join((t or '').split()).lower()  # noqa: E731
    steps_c = cc.execute('SELECT * FROM cap_lease_rent_step').fetchall()
    test('every rent step quotes its page verbatim, and the quote prints its amount',
         steps_c and all(s_['quote'] and norm(s_['quote']) in norm(page[(s_['source_doc_id'], s_['source_page'])])
                         and f"{s_['monthly_amount']:,.2f}" in s_['quote'] for s_ in steps_c))
    test('every step carries the document sha256',
         all(s_['source_sha256'] == 'f' * 64 for s_ in steps_c))
    lease_year = [s_ for s_ in steps_c if s_['period_from'] == '2022-07-01']
    test('a Lease Year period is computed and names the commencement it was counted from',
         lease_year and lease_year[0]['period_basis'].startswith('computed:')
         and '2022-07-01' in lease_year[0]['period_basis'] and lease_year[0]['value_basis'] == 'printed')
    facts = {(f['lease_id'], f['field']): f for f in cc.execute('SELECT * FROM cap_lease_fact')}
    sf = next(f for (lid, fld), f in facts.items() if fld == 'sf' and f['value'] == 2400)
    test('the tenancy SF is quoted from the lease ("2,400" on page 1)',
         sf['quote'] == '2,400' and sf['source_page'] == 1)
    com = next(f for (lid, fld), f in facts.items() if fld == 'commencement' and f['value'] == '2022-07-01')
    test('the commencement is quoted where it is printed ("July 1, 2022")', 'July 1, 2022' in com['quote'])
    empty = [f for f in facts.values() if f['value'] is None]
    test('a field the paper does not state has no quote and says why',
         empty and all(f['quote'] is None and f['null_reason'] for f in empty))
    terms = cc.execute('SELECT * FROM cap_lease_term').fetchall()
    lease_term = [t for t in terms if t['instrument_type'] == 'original lease']
    test('a lease term: the start as printed, the expiry, PSF and escalation from its rent rows',
         lease_term and lease_term[0]['lease_start'] == '2022-07-01' and lease_term[0]['start_rent_psf'] == 10.0
         and lease_term[0]['avg_escalation_pct'] == 3.0 and lease_term[0]['lease_expiry'] == '2027-06-30'
         and lease_term[0]['source_term'].startswith('printed: Commencement Date shall be July 1, 2022')
         and 'computed from 5 rent rows' in lease_term[0]['source_term'])
    term = cc.execute("SELECT * FROM cap_lease_provision WHERE category='base_rent'").fetchone()
    test('an extracted term quotes the words its value came from', '2,000.00' in term['quote'])
    cc.close()

    gl = [L for L in leases if L.get('possible_same_lease_as')]
    test('two tenancies of one lease are flagged, never merged, when their names differ only by filing',
         all(L['tenant_key'] != 'bean_coffee' for L in gl))
    print('\nTorn publish')
    os.remove(os.path.join(out, 'PUBLISH_MANIFEST.json'))
    try:
        with CAP.snapshot():
            torn = False
    except RuntimeError:
        torn = True
    test('no manifest -> the door refuses and the consumer keeps its previous pull', torn)

    print("\nPage numbers are the file's pages")
    gap_db = os.path.join(tmp, 'gaps.db')
    g = Database(gap_db)
    g.connect()
    gpid = g.create_property('Birch Row', property_type='retail')
    gdid = g.insert_document('BR-Lease.pdf', os.path.join(tmp, 'BR-Lease.pdf'), 'lease',
                             auto_create_property=False)
    # a six-page scan: pages 3 and 6 are blank, so ingest stored no text for them
    g.conn.execute("UPDATE documents SET property_id=?, file_hash=?, page_count=6, is_scanned=1 WHERE id=?",
                   (gpid, 'e' * 64, gdid))
    for n, text in ((1, LEASE[0]), (2, 'recital. ' * 20), (4, LEASE[1]), (5, 'IN WITNESS WHEREOF')):
        g.conn.execute("INSERT INTO document_fulltext(document_id, page_number, content) VALUES (?,?,?)",
                       (gdid, n, text))
    g.conn.commit()
    from realestate_extractor.lease_roster import _pages  # noqa: E402
    pg = _pages(g.conn, gdid)
    g.close()
    test('a blank page keeps its place: six entries, pages 3 and 6 empty',
         len(pg) == 6 and pg[2] == '' and pg[5] == '' and pg[3] == LEASE[1])
    gout = os.path.join(tmp, 'gap_door')
    gman = publish_property_door(gap_db, gout, 'BIRCHROW', property_id=gpid, as_of=date(2026, 9, 1),
                                 property_label='Birch Row')
    import sqlite3  # noqa: E402
    gc = sqlite3.connect(os.path.join(gout, gman['database']))
    reg_pages = gc.execute('SELECT pages FROM cap_document').fetchone()[0]
    page_rows = gc.execute('SELECT page_no, method FROM cap_page_text ORDER BY page_no').fetchall()
    gc.close()
    test('the registry counts every page of the file and every page has a row',
         reg_pages == 6 and [r[0] for r in page_rows] == [1, 2, 3, 4, 5, 6])
    test('a page nothing could read is marked, not dropped',
         dict(page_rows)[3] == 'none' and dict(page_rows)[6] == 'none' and dict(page_rows)[4] == 'ocr')
    from realestate_extractor.modules.module_door import _Cite, _flat  # noqa: E402
    chain = _flat('\n'.join(pg)).strip()
    test('a cite after the blank page lands on the file page (4), not the 3rd readable page',
         _Cite([(gdid, pg)]).at(chain.index('Lease Year 3')) == (gdid, 4))
    again = publish_property_door(gap_db, gout, 'BIRCHROW', property_id=gpid, as_of=date(2026, 9, 1))
    test('a republish leaves exactly one generation db, the one the manifest names',
         [f for f in os.listdir(gout) if f.endswith('.db')] == [again['database']]
         and again['database'] != gman['database'])

    print('\nRent-roll reconciliation')
    roll = [{'suite': '216', 'occupant': 'Bean Coffee', 'tenant_key': 'bean_coffee', 'sf': '2400',
             'base_rent_monthly': '2500.00', 'expiration': ''},
            {'suite': '220', 'occupant': 'River Dental', 'tenant_key': 'river_dental', 'sf': '1950',
             'base_rent_monthly': '', 'expiration': ''},
            {'suite': '230', 'occupant': 'Quince Bakery', 'tenant_key': 'quince_bakery', 'sf': '900'},
            {'suite': '300', 'occupant': 'Birchwood Corporate Office', 'tenant_key': 'birchwood_corporate'},
            {'suite': '001', 'occupant': 'Leasing / Mgmt Office', 'tenant_key': 'leasing_office'}]
    rt = build_door_tables(db_path, pid, date(2026, 9, 1), tenant_keys=(roll, {}))
    rcols, rrows = rt['cap_roll_reconciliation']
    rec = {(r[rcols.index('tenant_key')], r[rcols.index('field')]): dict(zip(rcols, r)) for r in rrows}
    test('a roll tenancy whose SF and rent the paper prints ties, field by field, citing the page',
         rec[('bean_coffee', 'sf')]['state'] == 'TIE' and rec[('bean_coffee', 'base_rent_monthly')]['state'] == 'TIE'
         and rec[('bean_coffee', 'sf')]['source_page'] == 1)
    test('a field the paper does not state is NOT_ON_PAPER, and says so',
         rec[('river_dental', 'sf')]['state'] == 'NOT_ON_PAPER' and 'no square footage' in rec[('river_dental', 'sf')]['explanation'])
    roll[0]['base_rent_monthly'] = '2650.00'
    rt2 = build_door_tables(db_path, pid, date(2026, 9, 1), tenant_keys=(roll, {}))['cap_roll_reconciliation']
    diff = [dict(zip(rt2[0], r)) for r in rt2[1] if r[3] == 'bean_coffee' and r[4] == 'base_rent_monthly'][0]
    test('a rent more than 2% from the roll DIFFERS, and says what the instruments give',
         diff['state'] == 'DIFFERS' and '2,500.00' in diff['explanation'] and '2,650.00' in diff['explanation'])
    roll[1]['base_rent_monthly'] = '3090.00'
    rt3 = build_door_tables(db_path, pid, date(2026, 9, 1), tenant_keys=(roll, {}))['cap_roll_reconciliation']
    rd = [dict(zip(rt3[0], r)) for r in rt3[1] if r[3] == 'river_dental' and r[4] == 'base_rent_monthly'][0]
    test("a roll rent that is a step of our own schedule says so",
         rd['state'] == 'DIFFERS' and 'a step of the schedule we read' in rd['explanation'])
    test('a roll rent on no page says so', 'printed on no page' in diff['explanation'])
    split = [dict(r, suite='216', sf='1600', base_rent_monthly='1700.00') if r['tenant_key'] == 'bean_coffee' else r
             for r in roll[:1]] + [{'suite': '218', 'occupant': 'Bean Coffee', 'tenant_key': 'bean_coffee', 'sf': '800',
                                    'base_rent_monthly': '800.00'}] + roll[1:]
    rt4 = build_door_tables(db_path, pid, date(2026, 9, 1), tenant_keys=(split, {}))['cap_roll_reconciliation']
    bc = [dict(zip(rt4[0], r)) for r in rt4[1] if r[3] == 'bean_coffee' and r[4] in ('sf', 'base_rent_monthly')]
    test("one lease over two roll suites ties the suites' sum, and says so",
         len(bc) == 4 and all(x['state'] == 'TIE' and 'sum of the roll' in x['explanation'] for x in bc))
    test("the landlord's own company on the roll is OWNER_AFFILIATE, naming the landlord",
         rec[('birchwood_corporate', 'lease')]['state'] == 'OWNER_AFFILIATE'
         and 'Birchwood Realty' in rec[('birchwood_corporate', 'lease')]['explanation'])

    print('\nSuite history from the monthly rolls')
    runs = [{'hist_id': '7:220:1', 'suite_id': '220', 'seq': 1, 'status': 'former', 'occupant': 'Lark Florist',
             'tenant_key': 'lark_florist', 'sf': 1800.0, 'base_rent': 2500.0, 'first_roll': '2021-01', 'last_roll': '2023-06',
             'rolls': 30, 'lease_start': '1/1/2019', 'lease_end': '6/30/2023', 'prior_occupant': None,
             'next_occupant': 'River Dental PA', 'basis': 'monthly rent rolls 2021-01 to 2023-06', 'note': '30 rolls',
             'source_sha256': 'a' * 64, 'source_page': 3},
            {'hist_id': '7:220:2', 'suite_id': '220', 'seq': 2, 'status': 'current', 'occupant': 'River Dental PA',
             'tenant_key': 'river_dental', 'sf': 1800.0, 'base_rent': 3000.0, 'first_roll': '2023-07', 'last_roll': '2026-09',
             'rolls': 39, 'prior_occupant': 'Lark Florist', 'basis': 'monthly rent rolls 2023-07 to 2026-09'}]
    sh = build_door_tables(db_path, pid, date(2026, 9, 1), suite_history=runs)
    test('the runs the publisher hands in are published as cap_suite_history, one row per run, on the declared columns',
         'cap_suite_history' in sh and len(sh['cap_suite_history'][1]) == 2
         and sh['cap_suite_history'][0][:4] == ['hist_id', 'suite_id', 'seq', 'status']
         and dict(zip(*[sh['cap_suite_history'][0], sh['cap_suite_history'][1][1]]))['next_occupant'] is None)
    test('with no runs given there is no cap_suite_history table',
         'cap_suite_history' not in build_door_tables(db_path, pid, date(2026, 9, 1)))
    test('a management office on the roll is NOT_A_TENANCY', rec[('leasing_office', 'lease')]['state'] == 'NOT_A_TENANCY')
    test('a roll tenancy no document names is NO_LEASE, and says so',
         rec[('quince_bakery', 'lease')]['state'] == 'NO_LEASE'
         and 'no document' in rec[('quince_bakery', 'lease')]['explanation'])

    print('\nPre-publish quote gate')
    cols = ['row_id', 'source_doc_id', 'source_page', 'source_sha256', 'quote', 'quote_page_method',
            'value_basis', 'period_basis', 'null_reason']
    gated = verify_quotes({
        'cap_document': (['doc_id', 'filename', 'sha256'], [(1, 'a.pdf', 'a' * 64)]),
        'cap_page_text': (['doc_id', 'page_no', 'method', 'chars', 'text', 'second_read', 'read_agreement'],
                          [(1, 1, 'ocr', 40, 'Base Rent shall be\n$2,000.00 per month', 'Base Rent $2,O00.00', None)]),
        'cap_x': (cols, [(1, 1, 1, 'a' * 64, 'Base Rent shall be $2,000.00', 'ocr', 'printed', None, None),
                         (2, 1, 1, 'a' * 64, '$3,000.00 per month', 'ocr', 'printed', None, None)])})['cap_x'][1]
    test('a quote its page prints (across a line break) passes the gate',
         gated[0][4] == 'Base Rent shall be $2,000.00' and gated[0][8] is None)
    test('a quote its page does not print is withheld, and the row says why',
         gated[1][4] is None and gated[1][5] is None and 'withheld' in gated[1][8])
    rv5 = verify_quotes({
        'cap_document': (['doc_id', 'filename', 'sha256'], [(1, 'a.pdf', 'a' * 64)]),
        'cap_x': (cols, [(1, 1, 1, 'a' * 64, '$2,000.00', 'ocr_uncorroborated', 'printed', None, None),
                         (2, 1, 1, 'a' * 64, '$2,000.00', 'ocr_corroborated', 'printed', None, None),
                         (3, 1, 2, 'a' * 64, 'Base Rent', 'ocr_uncorroborated', 'printed', None, None),
                         (4, 1, 3, 'a' * 64, 'Base Rent', 'ocr_uncorroborated', 'printed', None, None)]),
        'cap_page_text': (['doc_id', 'page_no', 'method', 'chars', 'text', 'second_read', 'read_agreement'],
                          [(1, 1, 'ocr', 20, 'Base Rent $2,000.00', '', None),
                           (1, 2, 'ocr', 9, 'Base Rent', '', None), (1, 3, 'ocr', 9, 'Base Rent', '', None)])},
        consumer_text=lambda sha, page: {1: '', 2: 'Base Rent $2,000', 3: 'Basc Rcnt'}[page])['cap_x'][1]
    test("an uncorroborated OCR quote on a page the consumer cannot read is withheld; a corroborated one stands",
         rv5[0][4] is None and 'RV5' in rv5[0][8] and 'empty' in rv5[0][8] and rv5[1][4] == '$2,000.00')
    test("an uncorroborated OCR quote stands where the consumer's read prints it, and is withheld where it does not",
         rv5[2][4] == 'Base Rent' and rv5[3][4] is None and 'does not print this quote' in rv5[3][8]
         and "our read prints: 'Base Rent'" in rv5[3][8])
    # the consumer's own text of a page decides every quote on it, whatever read ours came from
    lcols = cols + ['kind', 'quote_label']
    theirs = ('NET EFFECTIVE RENT SHEET\nPROPERTY: Elm Court\nLEASED BY: D. Mossey\nDATE: 5/10/2022\n'
              'The Tenant shall pay Base Rent of $2,000.00 per month\nin advance on the first day of each month')
    anchored = verify_quotes({
        'cap_document': (['doc_id', 'filename', 'sha256'], [(1, 'a.pdf', 'a' * 64)]),
        'cap_page_text': (['doc_id', 'page_no', 'method', 'chars', 'text', 'second_read', 'read_agreement'],
                          [(1, 1, 'text', 40, 'PROPERTY: LEASED BY: DATE: Elm Court D. Mossey 5/10/2022 Tenant pay Base Rent', '', None)]),
        'cap_lease_provision': (lcols, [
            (1, 1, 1, 'a' * 64, 'Tenant shall pay Base Rent of $2,000.00 per month', 'text', 'printed', None, None, 'clause', None),
            (2, 1, 1, 'a' * 64, 'first day of each month in advance', 'text', 'printed', None, None, 'clause', None),
            (3, 1, 1, 'a' * 64, 'pay Base Rent of $2,000.00 per month on the first day', 'text', 'printed', None, None, 'term', None),
            (4, 1, 1, 'a' * 64, 'Tenant pays $2,000.00 monthly rent here', 'text', 'printed', None, None, 'term', None),
            (5, 1, 1, 'a' * 64, 'D. Mossey', 'text', 'printed', None, None, 'label', 'LEASED BY'),
            (6, 1, 1, 'a' * 64, '5/10/2022', 'text', 'printed', None, None, 'label', 'LEASE SUMMARY DATE'),
            (7, 1, 1, 'a' * 64, 'Elm Court', 'text', 'printed', None, None, 'label', 'PROPERTY')])},
        consumer_text=lambda sha, page: theirs)['cap_lease_provision'][1]
    test("a quote the consumer's text prints stands, read from our own page order or not",
         anchored[0][4] == 'Tenant shall pay Base Rent of $2,000.00 per month' and anchored[0][8] is None)
    test("a quote it prints only in part is cut to the longest run of words it prints contiguously",
         anchored[1][4] == 'in advance on the first day of each month'[-len('first day of each month'):] or anchored[1][4] == 'first day of each month')
    test('a figure stays inside the cut quote of a valued row',
         anchored[2][4] == 'pay Base Rent of $2,000.00 per month')
    test('a valued row whose figure the consumer prints apart is withdrawn, with our read in the reason',
         anchored[3][4] is None and "our read prints: 'Tenant pays $2,000.00 monthly rent here'" in anchored[3][8])
    test("a form label the consumer's text prints stands with its value",
         anchored[4][4] == 'D. Mossey' and anchored[4][10] == 'LEASED BY' and anchored[4][8] is None)
    test('a label it does not print is withdrawn on its own; the value stands and the row says why',
         anchored[5][4] == '5/10/2022' and anchored[5][10] is None and 'label' in anchored[5][8]
         and "'LEASE SUMMARY DATE'" in anchored[5][8])
    test('a page the consumer does not hold is checked on our own text as before',
         verify_quotes({'cap_document': (['doc_id', 'filename', 'sha256'], [(1, 'a.pdf', 'a' * 64)]),
                        'cap_page_text': (['doc_id', 'page_no', 'method', 'chars', 'text', 'second_read', 'read_agreement'],
                                          [(1, 1, 'text', 9, 'Base Rent', '', None)]),
                        'cap_x': (cols, [(1, 1, 1, 'a' * 64, 'Base Rent', 'text', 'printed', None, None)])},
                       consumer_text=lambda sha, page: None)['cap_x'][1][0][4] == 'Base Rent')

    print('\nA deal sheet read a third time')
    from realestate_extractor.extractors.second_read import LADDER, METHOD, store_read  # noqa: E402
    SHEET = """3/14/2012
NET EFFECTIVE RENT SHEET- 5 YEAR
XXX NEW LEASE
TENANT ENTITY: Hazel Yarn Co LLC
USE: Yarn shop
PROPERTY: Cedar Row
BAY: 4
SQ. FT.: 1,600
YEAR P.S.F. MONTHLY ANNUALLY
1 $ 18.00 $ 2,400.00 $ 28,800.00
2 $ 18.54 $ 2,472.00 $ 29,664.00
TOTAL CONTRACT RENT: $ 58,464.00
SPACE PLANS $ -
LEASEHOLD IMPROVEMENT - ALLOWANCE $ 10.00 psf. $ 16,000.00
LEASING FEE @ $ 3.00 psf. $ 4,800.00
CO-BROKER FEE @ $ 2.00 psf. $ 3,200.00
RENT CONCESSION: - MONTHS $ -
MISCELLANEOUS LL COSTS: $ -
TOTAL LANDLORD EXPENSES: $ 24,000.00
TOTAL EFFECTIVE CONTRACT RENT: $ 34,464.00
"""
    ladder_db = os.path.join(tmp, 'ladder.db')
    ld = Database(ladder_db)
    ld.connect()
    lpid = ld.create_property('Cedar Row', property_type='retail')
    # the first OCR read misreads the leasing fee, the second misreads it differently, the third
    # read (higher resolution) prints the sheet as it is; a second sheet breaks on all three reads
    reads = {'CR-HazelYarn-NERS.pdf': (SHEET.replace('$ 4,800.00', '$ 4,300.00'), SHEET.replace('$ 4,800.00', '$ 4,900.00'), SHEET),
             'CR-Quill-NERS.pdf': (SHEET.replace('$ 4,800.00', '$ 4,300.00'), SHEET.replace('$ 4,800.00', '$ 4,900.00'),
                                   SHEET.replace('$ 4,800.00', '$ 4,100.00')),
             # a first read that ties but printed the tenant label bare (OCR split the labels from
             # the values); the second read names the tenant
             'CR-Moss-NERS.pdf': (SHEET.replace('TENANT ENTITY: Hazel Yarn Co LLC', 'TENANT ENTITY:'),
                                  SHEET.replace('Hazel Yarn Co LLC', 'Moss Pottery LLC'), None)}
    ldid = {}
    for name, (first, second_, third_) in reads.items():
        did = ld.insert_document(name, os.path.join(tmp, name), 'lease', auto_create_property=False)
        ld.conn.execute("UPDATE documents SET property_id=?, file_hash=?, page_count=1, is_scanned=1, "
                        "analysis_status='analyzed' WHERE id=?", (lpid, 'd' * 64, did))
        ld.conn.execute("INSERT INTO document_fulltext(document_id, page_number, content) VALUES (?,?,?)", (did, 1, first))
        store_read(ld.conn, did, 1, second_, 0.8, 0.95, METHOD)
        if third_:
            store_read(ld.conn, did, 1, third_, 0.9, 0.95, LADDER)
        ldid[name] = did
    ld.conn.commit()
    ld.close()
    lt = build_door_tables(ladder_db, lpid, date(2026, 9, 1))
    dcols, drows = lt['cap_ner_deal']
    deal = {r[dcols.index('source_doc_id')]: dict(zip(dcols, r)) for r in drows}
    hazel, quill = deal.get(ldid['CR-HazelYarn-NERS.pdf']), deal.get(ldid['CR-Quill-NERS.pdf'])
    test('a sheet neither read ties is read a third time, and that read stands when it ties',
         hazel and hazel['tie_status'] == 'TIE' and hazel['printed_total_landlord_expense'] == 24000.0
         and hazel['leasing_fee'] == 4800.0 and 'third OCR read' in hazel['tie_note'])
    test('a sheet that breaks on all three reads stays a BREAK and says nothing of a third read',
         quill and quill['tie_status'] == 'BREAK' and 'third' not in quill['tie_note'])
    moss = deal.get(ldid['CR-Moss-NERS.pdf'])
    test('a header the read in hand printed bare takes the other read\'s value; the tying read still stands',
         moss and moss['tenant'] == 'Moss Pottery LLC' and moss['tie_status'] == 'TIE'
         and 'second OCR read' not in moss['tie_note'])
    ccols, crows = lt['cap_ner_cost_line']
    lines = [dict(zip(ccols, r)) for r in crows if r[ccols.index('ner_id')] == hazel['ner_id']]
    fee = next(l_ for l_ in lines if l_['cost_line'] and 'Leasing Fee' in l_['cost_line'])
    tot_method = hazel['quote_page_method']
    test('a figure only the third read prints says so; the total every read prints is corroborated',
         fee['quote_page_method'] == 'ocr_third_read_only' and '4,800.00' in fee['quote']
         and tot_method == 'ocr_corroborated')

    shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
