"""
Which files in a tenancy's folder are rent evidence (extractors/document_family).
Fictional texts in the real layouts: a net-effective-rent worksheet, the
management system's lease summary report, a lease packet with a checklist
cover, a lease whose first page is a "Summary of Basic Lease Terms".

    venv/Scripts/python tests/test_document_family.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from realestate_extractor.extractors.document_family import (  # noqa: E402
    INSTRUMENT, LEASE_SUMMARY, NER_SHEET, archived_hint, assemble_rent_chain,
    rent_chain_family)

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


NER = ("Docusign Envelope ID: 1A2B NET EFFECTIVE RENT SHEET PROPERTY: VERSION & DATE: 2/11/2025 "
       "NAME: Elm Court OWNERSHIP: Elm Partners MOS VACANT: 0.00 SPACE: 216 SF: 2,400 "
       "PROPOSED TENANT: Bean Coffee PROPOSED RENT: NET RENT OPERATING COSTS GROSS RENT "
       "YRS MOS PSF MONTHLY ANNUALLY PERIOD CAM: $3.80 12 $13.12 $2,624.00 $31,488.00")
NER_OCR = ("NETE. :CTIVE ENT SHEET- 5 YEAR NEW LEASE RENEWAL TENANT ENTITY: Bean Coffee "
           "PROPERTY: Elm Court BAY: 212 SQ. FT.: 2300 YEAR P.S.F. MONTHLY ANNUALLY "
           "1 $ 10.00 $ 1,916.67 $ 23,000.00 TOTAL CONTRACT RENT: $ 57,883.33")
NER_APPROVAL = ("Docusign Envelope ID: 9F Approved 6/16/2025 Pls ensure we have our standard clause. "
                "Approved. " * 20 + NER)
SUMMARY = ("Bean Coffee DATE: 5/29/2026 MRI Lease ID: 000515 PREPARED BY: G. Smith LEASED BY: C. Jones "
           "SHOPPING CENTER - LEASE SUMMARY SHOPPING CENTER: Elm Court LESSEE: Bean Coffee Inc "
           "Commencement Date: 6/1/2020 Termination Date: N/A From Date To Date Annual Amount "
           "Monthly Amount Rent Per SF 1/1/2025 12/31/2025 $24,150.00 $2,012.50 $10.50")
PACKET = ("LEASE DOCUMENT CHECKLIST Document Originating From: Leasing / Property Management "
          "Property: Elm Court Tenant: Bean Coffee Net Effective Rent Sheet [x] Lease Summary [x] "
          "LEASE AGREEMENT This Lease is made between Elm Partners LLC, as Landlord, and Bean Coffee Inc, "
          "as Tenant. Base Rent: Months 1-12 $2,000.00 per month " + "term. " * 400)
BASIC_TERMS = ("OFFICE LEASE SUMMARY OF BASIC LEASE TERMS Landlord: Elm Partners LLC Tenant: Bean Coffee Inc "
               "Premises: Suite 216 Base Rent: $2,000.00 per month Term: 60 months " + "covenant. " * 400)
LEASE = ("LEASE AGREEMENT between Elm Partners LLC, as Landlord, and Bean Coffee Inc, as Tenant. "
         "Base Rent: Months 1-12 $2,000.00 per month " + "covenant. " * 400)


def main():
    print('\nFamily from the text')
    test('a net-effective-rent worksheet', rent_chain_family(NER, 1) == NER_SHEET)
    test('...with the OCR-garbled title', rent_chain_family(NER_OCR, 1) == NER_SHEET)
    test('...behind a page of approval comments', rent_chain_family(NER_APPROVAL, 3) == NER_SHEET)
    test('the management system lease summary report', rent_chain_family(SUMMARY, 4) == LEASE_SUMMARY)
    test('a scanned packet with a checklist cover is the lease',
         rent_chain_family(PACKET, 40) == INSTRUMENT)
    test('a lease opening with a Summary of Basic Lease Terms is the lease',
         rent_chain_family(BASIC_TERMS, 38) == INSTRUMENT)
    test('a plain lease', rent_chain_family(LEASE, 30) == INSTRUMENT)
    test('a long lease quoting worksheet column words is still the lease',
         rent_chain_family(LEASE + ' YEAR P.S.F. MONTHLY ANNUALLY', 30) == INSTRUMENT)
    test('page count unknown: the plain lease still reads as one',
         rent_chain_family(LEASE) == INSTRUMENT)

    print('\nFolder hints')
    test('ArchivedLeases in the path', archived_hint('2001-Leases__ArchivedLeases__EC-BeanCoffee.pdf'))
    test('Terminated folder', archived_hint('Lease Summaries__Terminated__Elm Court - Bean.pdf'))
    test('"Archive" alone', archived_hint('Archive__Bean Coffee lease.pdf'))
    test('not: a tenant called Archer', not archived_hint('EC-Archer Dental-Lease.pdf'))
    test('not: "terminate" inside a word (determination)',
         not archived_hint('EC-Bean-Determination Letter.pdf'))

    print('\nAssembling the rent chain')
    docs = [('EC-BeanCoffee-Lease.pdf', LEASE, 30),
            ('NERS__EC Bean Coffee NERS_2-11-2025.pdf', NER, 1),
            ('Lease Summaries__Elm Court - Bean Coffee (216).pdf', SUMMARY, 4),
            ('ArchivedLeases__EC-BeanCoffee-1998.pdf', LEASE.replace('2,000', '900'), 28),
            ('EC-BeanCoffee-1st Amendment.pdf', 'FIRST AMENDMENT TO LEASE ' + LEASE, 6)]
    sel = assemble_rent_chain(docs)
    test('keeps the archived lease first, then the lease and the amendment',
         sel.kept == [3, 0, 4])
    test('drops the worksheet and the summary',
         sel.dropped == {1: NER_SHEET, 2: LEASE_SUMMARY})
    test('chain text is the kept documents, archived paper before current',
         sel.text.index('900') < sel.text.index('2,000') and 'FIRST AMENDMENT' in sel.text
         and 'MRI Lease ID' not in sel.text and 'MOS VACANT' not in sel.text)
    test('flags say what was reordered and left out',
         sorted(sel.flags) == ['archived_first', 'summaries_ignored'])
    sel = assemble_rent_chain([docs[3]])
    test('only an archived lease: it stays, flagged archived_only',
         sel.kept == [0] and sel.flags == ['archived_only'])
    sel = assemble_rent_chain([docs[1], docs[2]])
    test('only worksheets: nothing kept, flagged summaries_only',
         sel.kept == [] and sel.text == '' and 'summaries_only' in sel.flags)
    sel = assemble_rent_chain([])
    test('no documents: empty, no flags', sel.kept == [] and sel.flags == [])

    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
