"""
Regression tests for the rent-roll text parser (extractors/extraction_engine.py).

Every fixture is FICTIONAL (invented tenants, suites, dollars) but copies the
LAYOUT of a real rent roll that once broke the parser. Client text never goes
in git; the shapes do.

    venv/Scripts/python tests/test_rent_roll_parsing.py      (no pytest needed)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.extraction_engine import ExtractionEngine  # noqa: E402
from realestate_extractor.extractors.llm_client import LocalLLMClient  # noqa: E402
from realestate_extractor.pdf_ingestion import DocumentContent, PageContent  # noqa: E402


class _Off(LocalLLMClient):
    def is_available(self):
        return False


def doc(*pages):
    return DocumentContent(filepath='x.pdf', filename='x.pdf', page_count=len(pages),
                           pages=[PageContent(page_number=i + 1, text=t) for i, t in enumerate(pages)])


def parse(*pages):
    return ExtractionEngine(_Off())._extract_rent_roll_from_text(doc(*pages))


# One tenant per line, section headings, future steps on the tail and on
# continuation lines, an alphanumeric suite, a zero-SF land lease, a vacant
# suite, a two-page report with repeated headers and a Totals block.
MRI_PAGE_1 = """Example Plaza (Building Id: 2001)
Bldg Status: Active only
Rent Roll
Report Date: 6/30/2026
. . . . . TSF Monthly Annual Monthly Cost Expense Monthl-y--------------- Future Rent Increases ----------------
Bldg Id Suite Id Occupant Name Rent Start Expiration Sqft Base Rent Rate PSF Recovery Stop Other Income Cat Date Monthly Amt PSF
New Leases
2001 200 Future Hobby Shop 9/29/2026 9/30/2031 11,000
Vacant Suites
2001 200 Vacant 11,000
Occupied Suites
2001 100 Lotus Nail Spa 6/1/2017 10/31/2032 1,600 1,866.67 14.00 665.38 RNT 11/1/2027 1,933.33 14.50
2001 10A Harvest Grocers 9/15/2005 9/14/2030 90,000 40,000.00 5.33 14,000.00 PTX 8/1/2026 95,000.00 12.29
RNT 9/15/2030 41,500.00 5.53
2001 110 Pizza Partners, LLC 1/1/2009 12/31/2030 1,440 2,346.00 19.55 822.38 12.00
2001 40 Walkwell Shoes 2/9/2020 6/30/2030 2,746 2,517.17 11.00 1,141.96
2001 50A Walkwell Shoes 2/9/2020 6/30/2030 12,280 11,256.66 11.00 5,106.80
Report Id MRIX_CMROLL Database EXAMPLE Reported by A. Person 7/1/2026 3:56 PM Page 5"""

MRI_PAGE_2 = """Example Plaza (Building Id: 2001)
Bldg Status: Active only
Rent Roll
Report Date: 6/30/2026
. . . . . TSF Monthly Annual Monthly Cost Expense Monthl-y--------------- Future Rent Increases ----------------
Bldg Id Suite Id Occupant Name Rent Start Expiration Sqft Base Rent Rate PSF Recovery Stop Other Income Cat Date Monthly Amt PSF
2001 90 Tax Prep Co 8/12/2012 4/30/2029 1,120 1,600.00 17.14 530.39 9.33 CON 4/1/2029 -1,700.00 -18.21
RNT 5/1/2027 1,650.00 17.68
RNT 5/1/2028 1,700.00 18.21
2001 LAND Outdoor Outfitters 4/1/2024 3/31/2034 0 3,035.69 RNT 4/1/2029 3,339.26 0.00
Totals: Occupied Sqft: 92.66% 7 Units 109,186 62,622.19 21,000.00 59.37
Vacant Sqft: 7.34% 1 Units 11,000
Total Sqft: 8 Units 120,186 62,622.19
Report Id MRIX_CMROLL Database EXAMPLE Reported by A. Person 7/1/2026 3:56 PM Page 6"""


def test_one_line_rows_read_in_column_order():
    rows = parse(MRI_PAGE_1, MRI_PAGE_2)
    by = {(r['unit_number'], r.get('tenant_name')): r for r in rows}
    pizza = by[('110', 'Pizza Partners, LLC')]
    assert (pizza['lease_start'], pizza['lease_end']) == ('1/1/2009', '12/31/2030'), pizza
    assert (pizza['square_footage'], pizza['monthly_rent'], pizza['rent_psf']) == (1440.0, 2346.00, 19.55), pizza
    assert pizza['annual_rent'] == 28152.00 and pizza['status'] == 'occupied'
    assert pizza['metadata']['psf_check'] == 'ok'
    # the monthly column, not the recovery or expense-stop columns
    assert by[('100', 'Lotus Nail Spa')]['monthly_rent'] == 1866.67
    # one lease on two suites stays two rows (the roster sums them)
    assert by[('40', 'Walkwell Shoes')]['monthly_rent'] == 2517.17
    assert by[('50A', 'Walkwell Shoes')]['monthly_rent'] == 11256.66


def test_steps_vacant_land_and_sections():
    rows = parse(MRI_PAGE_1, MRI_PAGE_2)
    by = {(r['unit_number'], r.get('tenant_name')): r for r in rows}
    # tail step + continuation line both attach to the grocer; PTX is not rent
    g = by[('10A', 'Harvest Grocers')]
    assert g['monthly_rent'] == 40000.00, g
    assert [s['monthly'] for s in g['metadata']['future_rent']] == [41500.00], g['metadata']
    assert g['metadata']['other_charges'][0]['cat'] == 'PTX'
    # two continuation lines; the CON (concession) line is not a rent step
    t = by[('90', 'Tax Prep Co')]
    assert [s['monthly'] for s in t['metadata']['future_rent']] == [1650.00, 1700.00], t['metadata']
    # land lease: 0 SF, rent still read; alphanumeric suite
    land = by[('LAND', 'Outdoor Outfitters')]
    assert (land['square_footage'], land['monthly_rent']) == (0.0, 3035.69), land
    # sections: the new lease has no rent yet; the vacant suite has no tenant
    assert by[('200', 'Future Hobby Shop')]['status'] == 'future'
    assert by[('200', 'Future Hobby Shop')]['monthly_rent'] is None
    assert by[('200', None)]['status'] == 'vacant'
    # nothing from the Totals block or the report footer became a row
    assert not any('Total' in (r.get('tenant_name') or '') for r in rows)
    assert len(rows) == 9, [(r['unit_number'], r.get('tenant_name')) for r in rows]


def test_other_layouts_untouched():
    # a Format-A style roll (unit + type + sqft, tenant, deposit, rent, other)
    # must not be caught by the one-line reader
    a = "\n".join(f"{u} One BR - 724\nTenant {u}\n0\n{r:,.2f}\n0" for u, r in
                  ((100, 1200.0), (101, 1250.0), (102, 1300.0)))
    rows = parse(a)
    assert len(rows) == 3 and rows[0]['tenant_name'] == 'Tenant 100', rows


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
