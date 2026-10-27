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
from realestate_extractor.modules.module_door import publish_property_door, verify_quotes  # noqa: E402

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
                                       ('lease_commencement', 'July 1, 2022', None, None, 1)):
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
             'cap_lease_provision', 'cap_lease_option', 'cap_lease_fact', 'cap_ner_deal', 'cap_ner_cost_line', 'cap_lease_event', 'cap_option_register', 'cap_lease_term', 'cap_lease_exhibit'})
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
         len(leases) == 2 and sorted(L['tenant_key'] for L in leases) == ['bean_coffee', 'riverdental']
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
         any(s_['period_from'] is None and s_['monthly_amount'] == 3090.0 and s_['lease_id'] == 'fn:riverdental'
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

    shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
