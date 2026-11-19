"""
Roster reports — two documents built from the org's own lease roster:

  * Portfolio Rent Roll (.xlsx): every tenancy across the org's properties,
    for a finance or data team to sort, filter and reconcile. Sheets:
    Summary, Rent Roll, Rollover, Review.
  * Property One-Pager (.docx): one property at a glance.

Both follow the roster's rule: a rent is a figure only when it is
high-confidence. Anything else is left blank on the face of the report and
listed, with the reading held back and the one action that resolves it, on
the Review sheet / under Open items. Read-only; nothing is stored.
"""

from __future__ import annotations

import io
import re
from datetime import date
from typing import Optional

from .lease_roster import build_roster, summarize

_CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
ROLLOVER_YEARS = 6          # this year + five, then "later"


def _clean(s):
    return _CTRL.sub('', s) if isinstance(s, str) else s


def _tenancies(rows):
    return [r for r in rows if r.grouped_by != 'unassigned' and r.kind == 'tenancy']


def _psf(r) -> Optional[float]:
    """Annual rent per square foot, when both are known and the rent shows."""
    if r.show_rent and r.square_feet:
        return round(r.monthly_rent * 12 / r.square_feet, 2)
    return None


def _expired(r, as_of: date) -> bool:
    """Past its expiration on paper and still on the roster (holdover, or a
    renewal not on file): never read as a current lease."""
    return bool(r.expiration and r.expiration < as_of)


def _lease_status(r, as_of: date) -> str:
    if not r.expiration:
        return 'No expiration on file'
    return 'Expired' if _expired(r, as_of) else 'Current'


_TOTAL_AMOUNT = re.compile(r'\$\s?[\d,]+(?:\.\d+)?\s+total')
_ROLL_SHOWS = re.compile(r'rent roll shows \$\s?[\d,]+(?:\.\d+)?(?:/mo)?')
_AMOUNT = re.compile(r'\$\s?[\d,]+(?:\.\d+)?(?:/mo)?')


def _action(needs: str) -> str:
    """A review step with its dollar figures left out, for a document that is
    passed around: the step stays, the readings held back stay in review."""
    s = needs or ''
    if s.startswith('reconcile:'):
        return 'reconcile the lease rent with the rent roll'
    s = _ROLL_SHOWS.sub('rent roll shows a different rent', s)
    s = _TOTAL_AMOUNT.sub('a total', s)
    return re.sub(r' {2,}', ' ', _AMOUNT.sub('an amount', s)).strip()


def _rollover(rows, as_of: date) -> list[dict]:
    """Expirations: already expired, then by calendar year (this year .. +5),
    'Later', 'Not on file'."""
    years = [as_of.year + i for i in range(ROLLOVER_YEARS)]
    buckets = {'expired': {'period': f'Expired before {as_of.isoformat()}', 'leases': 0, 'sf': 0.0,
                           'monthly_rent': 0.0}}
    buckets.update({y: {'period': str(y), 'leases': 0, 'sf': 0.0, 'monthly_rent': 0.0} for y in years})
    buckets['later'] = {'period': f'{years[-1] + 1} and later', 'leases': 0, 'sf': 0.0, 'monthly_rent': 0.0}
    buckets['none'] = {'period': 'Expiration not on file', 'leases': 0, 'sf': 0.0, 'monthly_rent': 0.0}
    for r in _tenancies(rows):
        if not r.expiration:
            k = 'none'
        elif _expired(r, as_of):
            k = 'expired'
        elif r.expiration.year > years[-1]:
            k = 'later'
        else:
            k = r.expiration.year
        b = buckets[k]
        b['leases'] += 1
        b['sf'] += r.square_feet or 0
        b['monthly_rent'] += r.monthly_rent if r.show_rent else 0
    return [b for b in buckets.values()]


def _expiring_within(rows, as_of: date, days: int) -> int:
    return sum(1 for r in _tenancies(rows) if r.expiration and 0 <= (r.expiration - as_of).days <= days)


def report_data(db, as_of: Optional[date] = None, property_id: Optional[int] = None) -> dict:
    """The roster of every property with leases (or one property), shaped
    for both reports. `db` is a database.Database."""
    as_of = as_of or date.today()
    props = {r[0]: dict(zip(('id', 'name', 'address', 'city', 'state', 'property_type', 'total_sqft'), r))
             for r in db.conn.execute(
                 "SELECT id, name, address, city, state, property_type, total_sqft FROM properties")}
    if property_id is not None:
        pids = [property_id] if property_id in props else []
    else:
        pids = sorted({r[0] for r in db.conn.execute(
            "SELECT DISTINCT property_id FROM documents "
            "WHERE document_type = 'lease' AND property_id IS NOT NULL")},
            key=lambda p: (props.get(p, {}).get('name') or '').lower())
    out = []
    for pid in pids:
        if pid not in props:
            continue
        rows = build_roster(db.conn, pid, as_of,
                            confirmed_commencements=db.get_lease_confirmations(pid))
        s = summarize(rows, as_of)
        ten = _tenancies(rows)
        shown_with_sf = [r for r in ten if _psf(r) is not None]
        total_sf = props[pid].get('total_sqft') or None
        out.append({
            **props[pid], 'rows': rows, 'tenancies': ten, 'summary': s,
            'annual_rent_shown': round(s['monthly_rent_confirmed'] * 12, 2),
            'avg_psf': (round(sum(r.monthly_rent * 12 for r in shown_with_sf)
                              / sum(r.square_feet for r in shown_with_sf), 2) if shown_with_sf else None),
            'occupancy': (min(1.0, s['leased_sf'] / total_sf) if total_sf and s['leased_sf'] else None),
            'expiring_12mo': _expiring_within(rows, as_of, 365),
            'expiring_24mo': _expiring_within(rows, as_of, 730),
            'in_review': [r for r in rows if r.needs and r.kind != 'expired'],
            'rollover': _rollover(rows, as_of),
        })
    return {'as_of': as_of, 'properties': out}


def _safe(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '_', name or 'Report').strip('_') or 'Report'


def rent_roll_filename(org_name: str, as_of: date) -> str:
    return f'{_safe(org_name)}_Portfolio_Rent_Roll_{as_of.isoformat()}.xlsx'


def one_pager_filename(property_name: str, as_of: date) -> str:
    return f'{_safe(property_name)}_One_Pager_{as_of.isoformat()}.docx'


# ─── Portfolio Rent Roll (.xlsx) ──────────────────────────────────

_NAVY, _CLOUD, _AMBER = '1B2A4A', 'EEF3F8', 'FFF4D6'
_MONEY, _INT, _PCT, _DATE, _PSF, _YRS = '$#,##0.00', '#,##0', '0%', 'yyyy-mm-dd', '$#,##0.00', '0.0'


def build_rent_roll_workbook(data: dict, prepared_for: str = '') -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    as_of = data['as_of']
    props = data['properties']
    wb = Workbook()
    head_font = Font(bold=True, color='FFFFFF', size=10)
    head_fill = PatternFill('solid', fgColor=_NAVY)
    total_fill = PatternFill('solid', fgColor=_CLOUD)
    review_fill = PatternFill('solid', fgColor=_AMBER)

    def header(ws, row, titles, widths):
        for i, (t, w) in enumerate(zip(titles, widths), start=1):
            c = ws.cell(row=row, column=i, value=t)
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(wrap_text=True, vertical='center')
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.row_dimensions[row].height = 30
        ws.freeze_panes = ws.cell(row=row + 1, column=1)

    def put(ws, row, values, formats=None, fill=None, bold=False):
        for i, v in enumerate(values, start=1):
            c = ws.cell(row=row, column=i, value=_clean(v))
            if formats and i <= len(formats) and formats[i - 1] and v is not None:
                c.number_format = formats[i - 1]
            if fill:
                c.fill = fill
            if bold:
                c.font = Font(bold=True)

    # Summary
    ws = wb.active
    ws.title = 'Summary'
    ws['A1'] = 'Portfolio Rent Roll'
    ws['A1'].font = Font(bold=True, size=14, color=_NAVY)
    ws['A2'] = (f'Prepared for {prepared_for} · ' if prepared_for else '') + f'As of {as_of.isoformat()}'
    ws['A3'] = ('Built from the leases on file. A rent is shown only where the lease’s own schedule places it '
                'with high confidence; every other tenancy is on the Review sheet with the step that resolves it.')
    ws['A3'].alignment = Alignment(wrap_text=True, vertical='top')
    ws.merge_cells('A3:N3')
    ws.row_dimensions[3].height = 32
    titles = ['Property', 'Type', 'Tenancies', 'Leased SF', 'Tenancies with SF', 'Building SF', 'Occupancy',
              'Monthly rent shown', 'Annual rent shown', 'Rents shown', 'Avg rent / SF / yr',
              'WALT (years)', 'Expiring ≤ 12 mo', 'Expiring ≤ 24 mo', 'In review',
              'Expired leases', 'Monthly rent shown on expired leases']
    fmts = [None, None, _INT, _INT, _INT, _INT, _PCT, _MONEY, _MONEY, _INT, _PSF, _YRS, _INT, _INT, _INT,
            _INT, _MONEY]
    header(ws, 5, titles, [34, 12, 10, 12, 11, 12, 11, 16, 16, 9, 12, 9, 10, 10, 9, 9, 16])
    past = lambda p: [r for r in p['tenancies'] if _expired(r, as_of)]                      # noqa: E731
    past_rent = lambda p: round(sum(r.monthly_rent for r in past(p) if r.show_rent), 2)     # noqa: E731
    row = 6
    for p in props:
        s = p['summary']
        put(ws, row, [p['name'], p.get('property_type'), s['tenancies'], s['leased_sf'], s['sf_known_for'],
                      p.get('total_sqft'), p['occupancy'], s['monthly_rent_confirmed'], p['annual_rent_shown'],
                      s['rent_confirmed_for'], p['avg_psf'], s['walt_years'], p['expiring_12mo'],
                      p['expiring_24mo'], len(p['in_review']), len(past(p)), past_rent(p)], fmts)
        row += 1
    if props:
        tot = lambda f: sum(f(p) or 0 for p in props)       # noqa: E731
        put(ws, row, ['Total', None, tot(lambda p: p['summary']['tenancies']),
                      tot(lambda p: p['summary']['leased_sf']), tot(lambda p: p['summary']['sf_known_for']),
                      tot(lambda p: p.get('total_sqft')) or None, None,
                      tot(lambda p: p['summary']['monthly_rent_confirmed']), tot(lambda p: p['annual_rent_shown']),
                      tot(lambda p: p['summary']['rent_confirmed_for']), None, None,
                      tot(lambda p: p['expiring_12mo']), tot(lambda p: p['expiring_24mo']),
                      tot(lambda p: len(p['in_review'])), tot(lambda p: len(past(p))), tot(past_rent)],
            fmts, fill=total_fill, bold=True)
    else:
        ws.cell(row=row, column=1, value='No leases on file yet.')

    # Rent Roll
    ws = wb.create_sheet('Rent Roll')
    titles = ['Property', 'Tenant', 'Square feet', 'Commencement', 'Commencement from', 'Expiration',
              'Expiration from', 'Monthly rent', 'Annual rent', 'Rent / SF / yr', 'Rent status',
              'Lease status', 'How the rent was read', 'Source documents']
    fmts = [None, None, _INT, _DATE, None, _DATE, None, _MONEY, _MONEY, _PSF, None, None, None, None]
    header(ws, 1, titles, [30, 34, 11, 13, 14, 13, 14, 14, 14, 11, 13, 14, 60, 70])
    row = 2
    for p in props:
        for r in sorted(p['tenancies'], key=lambda x: (x.tenant or '').lower()):
            shown = r.show_rent
            put(ws, row, [p['name'], r.tenant, r.square_feet, r.commencement, r.commencement_source or None,
                          r.expiration, r.expiration_source or None,
                          r.monthly_rent if shown else None,
                          round(r.monthly_rent * 12, 2) if shown else None, _psf(r),
                          'Shown' if shown else 'In review', _lease_status(r, as_of),
                          r.rent_detail if shown else 'See the Review sheet',
                          '; '.join(r.filenames)], fmts, fill=None if shown else review_fill)
            row += 1
    if row > 2:
        ws.auto_filter.ref = f'A1:{get_column_letter(len(titles))}{row - 1}'

    # Rollover
    ws = wb.create_sheet('Rollover')
    titles = ['Property', 'Expiring in', 'Leases', 'Square feet', 'Monthly rent shown']
    fmts = [None, None, _INT, _INT, _MONEY]
    header(ws, 1, titles, [34, 24, 9, 13, 18])
    row = 2
    if props:
        periods = [b['period'] for b in props[0]['rollover']]
        for i, period in enumerate(periods):
            bs = [p['rollover'][i] for p in props]
            put(ws, row, ['All properties', period, sum(b['leases'] for b in bs), round(sum(b['sf'] for b in bs)),
                          round(sum(b['monthly_rent'] for b in bs), 2)], fmts, fill=total_fill, bold=True)
            row += 1
        for p in props:
            for b in p['rollover']:
                if b['leases']:
                    put(ws, row, [p['name'], b['period'], b['leases'], round(b['sf']),
                                  round(b['monthly_rent'], 2)], fmts)
                    row += 1

    # Review
    ws = wb.create_sheet('Review')
    titles = ['Property', 'Tenant', 'Kind', 'What resolves it', 'Reading held back (monthly)', 'Confidence',
              'How it was read', 'Notes', 'Source documents']
    fmts = [None, None, None, None, _MONEY, None, None, None, None]
    header(ws, 1, titles, [30, 34, 16, 50, 16, 11, 55, 40, 70])
    row = 2
    for p in props:
        for r in p['in_review']:
            put(ws, row, [p['name'], r.tenant, r.kind.replace('_', ' '), r.needs,
                          None if r.show_rent else r.monthly_rent,
                          None if r.show_rent else r.rent_confidence,
                          None if r.show_rent else r.rent_detail,
                          '; '.join(r.flags) or None, '; '.join(r.filenames)], fmts)
            row += 1
    if row == 2:
        ws.cell(row=2, column=1, value='Nothing in review.')

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─── Property One-Pager (.docx) ───────────────────────────────────

def build_one_pager(prop: dict, as_of: date, prepared_for: str = '') -> bytes:
    """One property at a glance. `prop` is one entry of report_data()['properties']."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    navy, grey = RGBColor(0x1B, 0x2A, 0x4A), RGBColor(0x60, 0x6A, 0x78)
    s = prop['summary']
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Inches(0.6)
    sec.top_margin = sec.bottom_margin = Inches(0.5)
    doc.styles['Normal'].font.name = 'Calibri'
    doc.styles['Normal'].font.size = Pt(9)

    def shade(cell, fill):
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:fill'), fill)
        cell._tc.get_or_add_tcPr().append(shd)

    def line(text, size=9, bold=False, color=None, after=2, align=None):
        para = doc.add_paragraph()
        para.paragraph_format.space_after = Pt(after)
        para.paragraph_format.space_before = Pt(0)
        if align:
            para.alignment = align
        run = para.add_run(_clean(text))
        run.bold, run.font.size = bold, Pt(size)
        if color is not None:
            run.font.color.rgb = color
        return para

    def table(titles, rows, widths, right_from=1):
        t = doc.add_table(rows=1, cols=len(titles))
        t.style = 'Table Grid'
        t.autofit = False
        for i, (title, w) in enumerate(zip(titles, widths)):
            c = t.rows[0].cells[i]
            c.width = Inches(w)
            shade(c, _NAVY)
            run = c.paragraphs[0].add_run(title)
            run.bold, run.font.size = True, Pt(8)
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        for vals in rows:
            cells = t.add_row().cells
            for i, (v, w) in enumerate(zip(vals, widths)):
                cells[i].width = Inches(w)
                para = cells[i].paragraphs[0]
                if i >= right_from:
                    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                para.add_run('—' if v in (None, '') else _clean(str(v))).font.size = Pt(8.5)
        return t

    money = lambda v: f'${v:,.0f}' if v else None                     # noqa: E731
    place = ', '.join(x for x in (prop.get('address'), prop.get('city'), prop.get('state')) if x)
    line(prop['name'], size=18, bold=True, color=navy, after=0)
    line(' · '.join(x for x in (place, (prop.get('property_type') or '').title(),
                                f'As of {as_of.isoformat()}') if x), color=grey, after=1)
    if prepared_for:
        line(f'Prepared for {prepared_for}', color=grey, after=6)

    kpis = [('Tenancies', f"{s['tenancies']}"),
            ('Leased SF', f"{s['leased_sf']:,}" if s['leased_sf'] else None),
            ('Occupancy', f"{prop['occupancy']:.0%}" if prop['occupancy'] is not None else None),
            ('Monthly rent shown', money(s['monthly_rent_confirmed'])),
            ('Avg rent / SF / yr', f"${prop['avg_psf']:,.2f}" if prop['avg_psf'] else None),
            ('WALT', f"{s['walt_years']} yrs" if s['walt_years'] is not None else None)]
    t = doc.add_table(rows=2, cols=len(kpis))
    t.style = 'Table Grid'
    for i, (label, value) in enumerate(kpis):
        top, bottom = t.rows[0].cells[i], t.rows[1].cells[i]
        shade(top, _CLOUD)
        shade(bottom, _CLOUD)
        r1 = top.paragraphs[0].add_run(value or '—')
        r1.bold, r1.font.size = True, Pt(13)
        r1.font.color.rgb = navy
        r2 = bottom.paragraphs[0].add_run(label)
        r2.font.size = Pt(7.5)
        r2.font.color.rgb = grey
    ten = prop['tenancies']
    past = [r for r in ten if _expired(r, as_of)]
    past_rent = sum(r.monthly_rent for r in past if r.show_rent)
    note = ''
    if past:
        note = (f" {len(past)} lease{'s are' if len(past) != 1 else ' is'} past expiration and still counted"
                + (f', with ${past_rent:,.0f} of the monthly rent' if past_rent else '') + '.')
    line(f"Rent is shown for {s['rent_confirmed_for']} of {s['tenancies']} tenancies; square feet are on file "
         f"for {s['sf_known_for']}. Figures cover only what is shown.{note}", size=7.5, color=grey, after=6)

    top = sorted(ten, key=lambda r: (-(r.monthly_rent if r.show_rent else 0), -(r.square_feet or 0),
                                     (r.tenant or '').lower()))
    limit = 12
    line('Tenancies', size=11, bold=True, color=navy, after=2)
    rows = [[r.tenant, f'{r.square_feet:,.0f}' if r.square_feet else None,
             f'${r.monthly_rent:,.2f}' if r.show_rent else 'in review',
             f'${_psf(r):,.2f}' if _psf(r) is not None else None,
             (r.expiration.isoformat() + (' (expired)' if _expired(r, as_of) else ''))
             if r.expiration else None] for r in top[:limit]]
    rest = top[limit:]
    if rest:
        rows.append([f'{len(rest)} other tenancies',
                     f"{sum(r.square_feet or 0 for r in rest):,.0f}",
                     money(sum(r.monthly_rent for r in rest if r.show_rent)), None, None])
    if rows:
        table(['Tenant', 'Square feet', 'Monthly rent', 'Rent / SF / yr', 'Expiration'], rows,
              [2.9, 1.0, 1.2, 1.0, 1.2])
    else:
        line('No tenancies on file yet.', color=grey)

    line('', after=2)
    line('Lease rollover', size=11, bold=True, color=navy, after=2)
    roll = [[b['period'], b['leases'], f"{b['sf']:,.0f}" if b['sf'] else None, money(b['monthly_rent'])]
            for b in prop['rollover'] if b['leases']]
    if roll:
        table(['Expiring in', 'Leases', 'Square feet', 'Monthly rent shown'], roll, [2.4, 0.9, 1.3, 1.6])
    else:
        line('No expirations on file.', color=grey)

    review = prop['in_review']
    line('', after=2)
    line(f'Open items ({len(review)})', size=11, bold=True, color=navy, after=2)
    if review:
        for r in review[:8]:
            line(f'• {r.tenant}: {_action(r.needs)}', size=8.5, after=0)
        if len(review) > 8:
            line(f'… and {len(review) - 8} more on the lease roster.', size=8.5, color=grey, after=0)
    else:
        line('None. Every tenancy shows its rent.', color=grey)

    foot = sec.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = foot.add_run('CONFIDENTIAL · Built from the leases on file; a rent is shown only when it is '
                       'placed with high confidence.')
    run.font.size, run.font.color.rgb = Pt(7.5), grey

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
