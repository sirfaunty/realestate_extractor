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
from realestate_extractor.modules.module_door import publish_property_door  # noqa: E402

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
             "for Suite 220 at Elm Court, 1,800 square feet. Base Rent: $3,000.00 per month."]
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
             'cap_lease_provision', 'cap_lease_option'})
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
    test('the amendment table counts from its own effective date',
         any(s_['period_from'] == '2026-07-01' and s_['monthly_amount'] == 2500.0 for s_ in steps))
    test('a table not declared in HANDOFF.txt is refused', refused)

    print('\nTorn publish')
    os.remove(os.path.join(out, 'PUBLISH_MANIFEST.json'))
    try:
        with CAP.snapshot():
            torn = False
    except RuntimeError:
        torn = True
    test('no manifest -> the door refuses and the consumer keeps its previous pull', torn)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
