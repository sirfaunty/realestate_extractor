"""
Second reads (extractors/second_read.py): two reads of a page agree or not;
a quote is corroborated only when a read prints it; a broken text layer is
not a read; reads round-trip through page_reads; the door marks each OCR
quote corroborated / uncorroborated and carries the second read per page.
Fictional texts.

    venv/Scripts/python tests/test_second_read.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.database import Database  # noqa: E402
from realestate_extractor.extractors.second_read import (  # noqa: E402
    agreement, clean_text, in_read, load_reads, store_read)
from realestate_extractor.modules.module_door import build_door_tables  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


FIRST = "3. Minimum Rent. Tenant shall pay:\nLease Year 1 $18,000.00 $1,500.00\nLease Year 2 $18,540.00 $1,545.00"
SECOND = "3. Minimum Rent. Tenant shall pay:\nLease Year 1 $18,000.00 $1,500.00\nLease Year 2 $18,540.00 $1,545 .00"
GARBLED = "3. Minimum Rent. Tenant shall pay:\nLease Year 1 $18,000.00 $1,500.00\nLease Year 2 $18,540.00 $1,845.00"

print('\nReads')
test('two reads of the same page agree', agreement(FIRST, SECOND) > 0.9)
test('a read of a different page does not', agreement(FIRST, 'IN WITNESS WHEREOF the parties have signed') < 0.2)
test('a quote is in a read with different OCR spacing ("$1,545 .00")', in_read('1,545.00', SECOND))
test('a quote the read prints differently is not ("1,845.00" vs "1,545.00")', not in_read('1,545.00', GARBLED))
test('an empty read corroborates nothing', not in_read('1,500.00', ''))
test('a text layer of real words is a read', clean_text(FIRST + ' The Landlord and the Tenant agree as follows.'))
test('a broken font encoding is not a read',
     not clean_text('۹ˊƬ۹ไ\nŞ\x02\x13\t5Ņ[\tไS^\xad^O\x9aไ\xad\x01X' * 5))
test("a '(cid:' glyph-code layer is not a read", not clean_text('(cid:12)(cid:44) Lease ' * 10))

print('\nThrough the door')
tmp = tempfile.mkdtemp(prefix='cap_reads_')
db_path = os.path.join(tmp, 'org.db')
db = Database(db_path)
db.connect()
pid = db.create_property('Aspen Yard', property_type='retail')
did = db.insert_document('AY-Lease.pdf', os.path.join(tmp, 'AY-Lease.pdf'), 'lease', auto_create_property=False)
db.conn.execute("UPDATE documents SET property_id=?, file_hash=?, page_count=2, is_scanned=1 WHERE id=?",
                (pid, 'a' * 64, did))
for n, t in ((1, 'LEASE between Aspen Yard LLC, Landlord, and Lark Florist Inc, Tenant, for 1,200 square feet. '
                 'The Commencement Date is May 1, 2023.'), (2, FIRST)):
    db.conn.execute("INSERT INTO document_fulltext(document_id, page_number, content) VALUES (?,?,?)", (did, n, t))
for term, raw, num, unit, page in (('tenant_identity', 'Lark Florist Inc', None, None, 1),
                                   ('square_footage', '1,200', 1200, 'sf', 1),
                                   ('base_rent', '$1,500.00', 1500.0, 'monthly', 2),
                                   ('lease_commencement', 'May 1, 2023', None, None, 1)):
    db.conn.execute("INSERT INTO financial_terms(document_id, term_type, value_raw, value_numeric, value_unit, "
                    "page_number, confidence) VALUES (?,?,?,?,?,?,?)", (did, term, raw, num, unit, page, 0.9))
db.conn.execute("UPDATE documents SET analysis_status='analyzed' WHERE id=?", (did,))
db.conn.commit()
db.close()
side = os.path.join(tmp, 'reads.db')
sc = sqlite3.connect(side)
store_read(sc, did, 2, GARBLED, 88.0, agreement(FIRST, GARBLED))
sc.commit()
test('a stored read loads back by (document, page)', load_reads(side) == {(did, 2): GARBLED})
sc.close()
t = build_door_tables(db_path, pid, date(2026, 9, 1), reads_db=side)
cols, rows = t['cap_page_text']
ix = {c: i for i, c in enumerate(cols)}
p2 = next(r for r in rows if r[ix['page_no']] == 2)
test('the page row carries the second read and how far the reads agree',
     p2[ix['second_read']] == GARBLED and p2[ix['read_agreement']] > 0.8)
cols, rows = t['cap_lease_rent_step']
ix = {c: i for i, c in enumerate(cols)}
by_amount = {r[ix['monthly_amount']]: r[ix['quote_page_method']] for r in rows}
test('a step both reads print is corroborated', by_amount.get(1500.0) == 'ocr_corroborated')
test('a step the second read prints differently is uncorroborated', by_amount.get(1545.0) == 'ocr_uncorroborated')
cols, rows = t['cap_lease_fact']
ix = {c: i for i, c in enumerate(cols)}
sf = next(r for r in rows if r[ix['field']] == 'sf')
test('a page with no second read is a single OCR read', sf[ix['quote_page_method']] == 'ocr')
shutil.rmtree(tmp, ignore_errors=True)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
