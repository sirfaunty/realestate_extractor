"""Publish a property's extraction as a MODULE DOOR: a self-contained SQLite
package another system reads through a manifest, in the shape a portfolio
warehouse merges (registry -> page text -> lease rows -> rent steps ->
provisions), every row cited to a document and page.

Why a door and not an export: the consumer reads the manifest, then the
declared tables, then the manifest again; a missing manifest or a database
whose hash differs means a publish is under way or torn, and the consumer
keeps its previous pull. The manifest is therefore removed first and written
LAST. Nothing in the package is ever edited in place; a new publish is a new
generation file.

Tables (prefix `cap_`):
  cap_document        one row per document: sha256, pages, family, archived hint
  cap_page_text       every page verbatim, with the method that read it
  cap_lease_lease     one row per tenancy (the roster): SF, dates, current rent
                      with method / confidence / flags, the one action it needs
  cap_lease_chain     the tenancy's documents in chain order, with their role
  cap_lease_rent_step every placed rent row on paper: period, amount, basis
  cap_lease_provision every extracted term and clause, with its page
  cap_lease_option    renewal-option statements and option rent tables
  cap_lease_fact      each tenancy value (SF, commencement, expiration, rent) with its words

Every fact row carries the citation columns (CITE_COLS): the document's
sha256, the file's page, a VERBATIM quote of that page around the value
(extractors/citation.py), the method that read the page, the basis of the
value ('printed' or 'computed: <inputs>') and, when the paper prints nothing
for it, null_reason instead of a quote.

    from realestate_extractor.modules.module_door import publish_property_door
    publish_property_door('org.db', property_id=1, out_dir='doors/plaza', package='PLAZA')
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import date, datetime

from ..extractors.document_family import archived_hint, assemble_rent_chain, rent_chain_family, worksheet_page
from ..extractors.rent_derivation import (
    _anchor, _instrument_start, _is_option_table, parse_dated_rows, parse_rent_schedule)
from ..extractors.articles import split_articles, sweep
from ..extractors.citation import Cite, cite_date, cite_number, cite_text
from ..extractors.exhibits import find_exhibits
from ..extractors.instrument_terms import instrument_term
from ..extractors.instruments import recited_dates, split_instruments
from ..extractors.ner_sheet import parse_ner
from ..extractors.options import find_options
from ..extractors.second_read import in_read, load_reads
from ..lease_roster import _pages, build_roster
from .tenant_keys import match_tenant_keys

PREFIX = 'cap'
PUBLISHER = 'Capactive lease extraction'
GENERIC = {'the', 'and', 'inc', 'llc', 'corp', 'company', 'store', 'stores', 'shop',
           'center', 'centre', 'dba', 'of', 'group', 'services', 'service', 'ltd'}

TABLES = {
    # table: (key columns, what it holds)
    'cap_document': ('doc_id', 'one row per document: sha256, pages, how it was read, its family and folder hints'),
    'cap_page_text': ('doc_id, page_no', 'every page of every document verbatim, with the method that read it'),
    'cap_lease_lease': ('lease_id', 'one row per tenancy: SF, commencement, expiration, current base rent with method, confidence and flags, the action it needs'),
    'cap_lease_chain': ('lease_id, seq', "the tenancy's documents in chain order with their role (instrument, worksheet, summary, archived)"),
    'cap_lease_rent_step': ('step_id', 'every rent row placed in time from the instruments: period, monthly, annual, basis, option flag; cited'),
    'cap_lease_provision': ('prov_id', 'every extracted term and segmented clause with its page and confidence'),
    'cap_lease_option': ('opt_id', 'renewal-option statements and option rent tables, cited'),
    'cap_lease_event': ('event_id', "every instrument of every tenancy (lease, amendment, assignment, guaranty, estoppel, ...), typed, dated by its own date with the words it was read from, its recited dates kept apart; a date a LATER instrument recites for it in recited_by_later_* (never as its own)"),
    'cap_lease_exhibit': ('exhibit_id', 'every exhibit, rider or addendum attached to an instrument: label, title as printed, pages, the instrument it is filed in'),
    'cap_lease_term': ('term_id', 'one row per term-setting instrument: start, expiry and years as it prints them (else computed from its own rent rows), SF, starting and ending rent PSF and average escalation from its rent rows, with the nearest deal sheet economics'),
    'cap_option_register': ('optreg_id', 'every renewal / extension option granted or deleted in a tenancy instrument: count, years each, notice window, rent basis, cited'),
    'cap_ner_deal': ('ner_id', 'every net effective rent deal sheet: its printed fields, the printed total landlord expense and whether the cost lines tie to it (TIE / BREAK)'),
    'cap_roll_reconciliation': ('recon_id', "every rent-roll tenancy given, field by field (SF, base rent, expiration): the roll's value, the paper's value with the words it is printed in, TIE / DIFFERS / NOT_ON_PAPER / NO_LEASE, and what explains a difference"),
    'cap_ner_cost_line': ('ner_id, seq', "every cost line printed on a deal sheet, proposed and budgeted, cited to its line; memo says why a printed line is not counted in the sum"),
    'cap_lease_fact': ('fact_id', "each tenancy value (SF, commencement, expiration, current rent) with the document, page and verbatim words it came from"),
}

CITE_COLS = ['source_sha256', 'quote', 'quote_page_method', 'value_basis', 'period_basis', 'null_reason']


def _cite_cols(c: Cite, doc_id, doc_sha: dict, doc_method: dict, period_basis, what: str,
               missing_value: bool = False) -> tuple:
    """The citation columns for one row."""
    if missing_value:
        return (None, None, None, None, period_basis, 'the paper states no value for this field')
    if c.page is None:
        return (doc_sha.get(doc_id), None, None, None, period_basis,
                f'{what} not found printed on any page of the document')
    method = doc_method(doc_id, c.page, c.quote) if callable(doc_method) else doc_method.get(doc_id)
    return (doc_sha.get(doc_id), c.quote, method, c.basis, period_basis, None)


def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', s or '').strip().lower()


def _loose(s: str) -> str:
    return re.sub(r'[^0-9a-z]', '', (s or '').lower())


def verify_quotes(tables: dict, consumer_text=None) -> dict:
    """The pre-publish gate: every quote must be found again, word for word
    (up to spacing and case; or letters and digits alone), on the page it
    cites -- in our text of that page or its second read. A quote that is
    not is withdrawn and the row says so: no row leaves with a quote its own
    page does not print.

    consumer_text(sha256, page) -> the consumer's own text of that page ('' =
    a page it cannot read; None = a page it does not hold). An OCR quote our
    second read does not corroborate stands only on a second read that
    agrees: the consumer's (R2), never on our one read alone (RV5)."""
    pages = {}
    for r in tables['cap_page_text'][1]:
        pages[(r[0], r[1])] = (r[4] or '') + ' ' + (r[5] or '')
    sha_docs = {}
    for r in tables['cap_document'][1]:
        sha_docs.setdefault(r[2], set()).add(r[0])

    def printed(q, t):
        return _norm(q) in _norm(t) or (len(_loose(q)) >= 4 and _loose(q) in _loose(t))
    for name, (cols, rows) in tables.items():
        if not set(CITE_COLS) <= set(cols) or 'source_page' not in cols:
            continue
        ix = {c: i for i, c in enumerate(cols)}
        for k, r in enumerate(rows):
            q, page = r[ix['quote']], r[ix['source_page']]
            if not q:
                continue
            # the cited document: the one the row names, or any copy with the cited sha256
            docs = set(sha_docs.get(r[ix['source_sha256']], ()))
            for c in ('source_doc_id', 'doc_id'):
                if c in ix and r[ix[c]] is not None:
                    docs.add(r[ix[c]])
            if not any(printed(q, pages.get((d, page), '')) for d in docs):
                why = 'quote not found again on its cited page; withheld for review'
            elif consumer_text and r[ix['quote_page_method']] in ('ocr_uncorroborated', 'ocr'):
                theirs = consumer_text((r[ix['source_sha256']] or '').lower(), page)
                if theirs is None or (theirs.strip() and printed(q, theirs)):
                    continue
                why = ('an OCR quote only our first read prints: our second read does not agree and '
                       + ('your text of this page is empty' if not theirs.strip() else 'your text of this page prints otherwise')
                       + '; withheld (RV5)')
            else:
                continue
            r = list(r)
            r[ix['quote']] = r[ix['quote_page_method']] = None
            r[ix['null_reason']] = why
            rows[k] = tuple(r)
    return tables


def _cite_amount(pages: list, monthly, annual, page_hint) -> Cite:
    """A rent amount: the monthly figure where printed, else the annual one
    it is computed from (or the reverse)."""
    if monthly is not None:
        return cite_number(pages, monthly, page_hint,
                           also=[(annual, 'annual / 12') if annual is not None else (None, '')]
                           + ([(round(monthly * 12, 2), 'monthly x 12 printed as annual')] if annual is None else []))
    return cite_number(pages, annual, page_hint)


def _cite_heading(pages: list, label: str, section_ref: str, page_hint) -> Cite:
    """A segmented clause: the label is '<Section N>: <Heading>' as the
    segmenter names it; the page prints the heading (and maybe its number).
    A preamble has no heading: its opening line is the quote."""
    # 'Article 13, Section 5: <heading>', 'sublease: Section 10.1: <heading>'
    head = re.sub(r'^\s*(?:[a-z][a-z /]{0,30}:\s*)?(?:(?:article|section)\s+[\w.]+\s*,?\s*)+:\s*', '', label, flags=re.I)
    if head == label:
        head = label.split(':', 1)[1].strip() if ':' in label else label
    if head.startswith('(') or not head:
        hint = page_hint if page_hint and 1 <= page_hint <= len(pages) else 1
        for p in [hint] + [i for i in range(1, len(pages) + 1) if i != hint]:
            line = next((ln.strip() for ln in (pages[p - 1] or '').splitlines() if len(ln.strip()) >= 4), '')
            if line:
                return Cite(p, line[:140], 'printed' if p == page_hint else 'moved')
        return Cite(None, '', 'not_found')
    c = cite_text(pages, head, page_hint)
    # a clause named by its opening words, cut mid-word ("If TENANT fails to
    # comply with the r"): the words before the cut, then the first six
    # (four at least, so a stray phrase never matches)
    toks = re.findall(r'[A-Za-z0-9]+', head)[:12]
    for k in (len(toks) - 1, 6):
        if c.page is not None or k < 4 or k >= len(toks):
            continue
        c = cite_text(pages, ' '.join(toks[:k]), page_hint)
    if c.page is None and section_ref and not section_ref.lower().startswith(('section', 'preamble')):
        c = cite_text(pages, section_ref, page_hint)
    return c


_MDY = re.compile(r'^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})\s*$')


def _iso(raw) -> str | None:
    """'10/01/2025' / '2025-10-01' -> '2025-10-01'; anything else None."""
    s = str(raw or '')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s[:10]):
        return s[:10]
    m = _MDY.match(s)
    if m:
        y = int(m.group(3))
        y = y + (2000 if y < 50 else 1900) if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def _cite_term(pages: list, raw, numeric, eff_date, page_hint) -> Cite:
    """An extracted term: its number, its date, else its raw words."""
    if numeric is not None:
        c = cite_number(pages, numeric, page_hint)
        if c.page is not None:
            return c
    if eff_date:
        c = cite_date(pages, eff_date, page_hint)
        if c.page is not None:
            return c
    if raw:
        iso = _iso(raw)
        c = cite_date(pages, iso, page_hint) if iso else Cite(None, '', 'not_found')
        if c.page is None:
            c = cite_text(pages, str(raw)[:120], page_hint)
        return c
    return Cite(None, '', 'not_found')


def _cite_in_docs(docs: list, value, kind: str):
    """(doc_id, Cite) for a tenancy value: the latest instrument that prints it."""
    if value is None:
        return None, Cite(None, '', 'not_found')
    for doc_id, pages in docs:
        if kind == 'date':
            c = cite_date(pages, value)
        elif kind == 'amount':
            c = cite_number(pages, value, also=[(round(float(value) * 12, 2), 'annual / 12')])
        else:
            c = cite_number(pages, value)
        if c.page is not None:
            return doc_id, c
    return (docs[0][0] if docs else None), Cite(None, '', 'not_found')


_GENERIC_LEGAL = {'tobedetermined', 'statementsnotices', 'international', 'corporation', 'incorporated',
                  'limitedliabilitycompany', 'minnesotacorporation', 'specific', 'tenantname', 'landlord', 'tenant'}


def _slug(name: str) -> str:
    words = [re.sub(r'[^a-z]', '', w.lower()) for w in re.split(r'[^A-Za-z]+', name or '')]
    words = [w for w in words if len(w) >= 3 and w not in GENERIC]
    return '_'.join(words[:2]) or re.sub(r'[^a-z]', '', (name or '').lower()) or 'unknown'


def _num_or_none(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _roll_reconciliation(roll: list, keyed: dict, rows: list, t_fact: list, docs: list,
                         pages_by_doc: dict) -> list:
    """One row per rent-roll tenancy and field (sf / base rent / expiration):
    the roll's value, the paper's value with the words it is printed in,
    whether they tie (SF exact, rent within 2%, expiration the same day) and,
    when not, what explains it. A roll tenancy no lease row matched gets one
    'lease' row saying whether any document names it at all.

    roll rows: {'suite', 'occupant', 'tenant_key', 'sf', 'base_rent_monthly',
    'expiration'} (the last three optional)."""
    if not roll:
        return []
    by_key = {m.tenant_key: lid for lid, m in keyed.items() if m.tenant_key}
    row_of = {(r.key or _slug(r.tenant)): r for r in rows}
    facts = {(f[1], f[2]): f for f in t_fact}
    out = []
    for rr in roll:
        lid = by_key.get(rr.get('tenant_key'))
        base = (rr.get('suite'), rr.get('occupant'), rr.get('tenant_key'))
        if not lid:
            words = [w for w in re.findall(r'[a-z]{4,}', (rr.get('occupant') or '').lower()) if w not in GENERIC]
            hits = [d['filename'] for d in docs if words and (
                all(w in (d.get('filename') or '').lower() for w in words[:2]) or
                all(w in ' '.join(pages_by_doc.get(d['id'], [])[:3]).lower() for w in words[:2]))]
            why = (f'no lease row: {len(hits)} document(s) name this occupant but do not form a tenancy '
                   f'({"; ".join(h[-60:] for h in hits[:3])})' if hits else
                   'no lease row: no document in the files received names this occupant')
            out.append(base + ('lease', None, None, None, 'NO_LEASE', why, None, None, None))
            continue
        row = row_of.get(lid)
        for field, roll_v, ours in (
                ('sf', _num_or_none(rr.get('sf')), row.square_feet if row else None),
                ('base_rent_monthly', _num_or_none(rr.get('base_rent_monthly')), row.monthly_rent if row else None),
                ('expiration', rr.get('expiration') or None,
                 row.expiration_lease.isoformat() if row and row.expiration_lease else None)):
            f = facts.get((lid, field))
            doc, page, quote = (f[5], f[6], f[8]) if f else (None, None, None)
            if roll_v is None:
                state, why = 'NO_ROLL_VALUE', 'the roll prints no value for this field'
            elif ours is None:
                state = 'NOT_ON_PAPER'
                why = {'sf': 'the paper in the files states no square footage for this tenancy',
                       'base_rent_monthly': 'no current base rent read from the instruments' + (
                           f' ({row.rent_detail})' if row and row.rent_detail else ''),
                       'expiration': 'the paper in the files prints no expiration for this tenancy'}[field]
            else:
                if field == 'sf':
                    tie = abs(float(ours) - roll_v) <= 0.5
                elif field == 'base_rent_monthly':
                    tie = roll_v > 0 and abs(float(ours) - roll_v) / roll_v <= 0.02
                else:
                    tie = str(ours) == str(roll_v)[:10]
                state = 'TIE' if tie else 'DIFFERS'
                where = f' (document {doc} p{page}: "{(quote or "")[:80]}")' if page else ''
                if tie:
                    why = 'ties'
                elif field == 'expiration' and str(ours) < str(roll_v)[:10]:
                    why = (f'the paper ends the term {ours}{where}; the roll runs to {roll_v}: a renewal or '
                           f'extension not in the files would explain it')
                elif field == 'base_rent_monthly':
                    why = (f'the instruments give {ours:,.2f} a month ({row.rent_method}, {row.rent_confidence}'
                           f'{"; " + ";".join(row.rent_flags) if row.rent_flags else ""}); the roll says {roll_v:,.2f}')
                else:
                    why = f'the paper prints {ours}{where}; the roll says {roll_v}'
            out.append(base + (field, roll_v, ours, lid, state, why, doc, page, quote))
    return [(i,) + r for i, r in enumerate(out, 1)]


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _flat(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub('[‐‑‒–—−]', '-', s or ''))


class _Cite:
    """Map a position in a tenancy's flat chain text back to (doc_id, page)."""

    def __init__(self, docs):
        # docs: [(doc_id, pages)] in chain order; the chain is built exactly as
        # derive_current_rent builds it from per-document texts
        self.spans = []           # (start, end, doc_id, [(page_start, page_no)])
        at = 0
        for doc_id, pages in docs:
            flat_pages = [_flat(p).strip() for p in pages]
            start = at
            page_marks, cur = [], at
            for n, fp in enumerate(flat_pages, 1):
                if not fp:        # a blank page adds nothing to the flat text
                    continue
                page_marks.append((cur, n))
                cur += len(fp) + 1
            end = at + len(_flat('\n'.join(pages)).strip())
            self.spans.append((start, end, doc_id, page_marks))
            at = end + 1

    def at(self, pos: int):
        for a, b, doc_id, marks in self.spans:
            if a <= pos <= b:
                page = 1
                for s, n in marks:
                    if s <= pos:
                        page = n
                return doc_id, page
        return None, None


def _file_pages(doc: dict, pages: list) -> list:
    """Pad a document's pages out to the file's own page count when the file
    is on hand (older ingests recorded only the pages that had text)."""
    path = doc.get('filepath') or ''
    if path.lower().endswith('.pdf') and os.path.exists(path):
        try:
            import fitz
            with fitz.open(path) as f:
                n = f.page_count
            if n > len(pages):
                return pages + [''] * (n - len(pages))
        except Exception:
            pass
    return pages


def _opening(pages: list, a) -> str:
    """An article's quote: its heading line as it stands on its first page
    (a short slice of that page, up to 80 characters, never across a page):
    short so another reading of the page still prints it."""
    if not a.page_from or a.page_from > len(pages):
        return ''
    page = pages[a.page_from - 1] or ''
    head = a.body[:60]
    i = page.find(head)
    if i < 0:
        i = page.find(a.body[:20]) if len(a.body) >= 20 else -1
    if i < 0:
        return ''
    end = page.find(chr(10), i)
    end = len(page) if end < 0 else end
    if end - i < 12:                       # a bare "ARTICLE 3": take its title line too
        nxt = page.find(chr(10), end + 1)
        end = len(page) if nxt < 0 else nxt
    q = page[i:min(end, i + 80)]
    return (q.rsplit(' ', 1)[0] if end > i + 80 and ' ' in q else q).strip()


def _read_agreement(conn, reads_db) -> dict:
    """{(doc_id, page): agreement of the two reads}."""
    out = {}
    for src in (conn, reads_db):
        if src is None:
            continue
        try:
            c = src if isinstance(src, sqlite3.Connection) else sqlite3.connect(f'file:{src}?mode=ro', uri=True)
            for d, p, a in c.execute("SELECT document_id, page_number, agreement FROM page_reads "
                                     "ORDER BY method = 'ocr_second_read'"):
                out[(int(d), int(p))] = a
        except sqlite3.Error:
            pass
    return out


def _property_docs(conn, property_id):
    where, args = '', []
    if property_id is not None:
        where, args = 'WHERE property_id = ?', [property_id]
    cols = [r[1] for r in conn.execute('PRAGMA table_info(documents)')]
    rows = conn.execute(f'SELECT * FROM documents {where} ORDER BY id', args).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _confirmations(conn, property_id):
    try:
        return {r[0]: r[1] for r in conn.execute(
            'SELECT tenancy_key, commencement FROM lease_confirmations WHERE property_id = ?',
            (property_id,))}
    except sqlite3.Error:
        return {}


def build_door_tables(db_path: str, property_id=None, as_of: date | None = None,
                      reads_db: str | None = None, tenant_keys: tuple | None = None,
                      consumer_text=None) -> dict:
    """Read a property's extraction DB and return {table: (columns, rows)}.
    Second OCR reads (extractors/second_read.py) come from the DB's own
    page_reads table and, when given, reads_db. tenant_keys = (roll rows,
    alias table) maps each tenancy to the owner's rent-roll key
    (modules/tenant_keys.py); the engine's own key stays in source_tenant_key."""
    as_of = as_of or date.today()
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    docs = _property_docs(conn, property_id)
    pages_by_doc = {d['id']: _file_pages(d, _pages(conn, d['id'])) for d in docs}
    second = load_reads(conn)
    if reads_db:
        second.update(load_reads(reads_db))
    second_agree = _read_agreement(conn, reads_db)

    # registry + page text
    t_doc, t_page = [], []
    fam_by_doc = {}
    for d in docs:
        pages = pages_by_doc[d['id']]
        fam = rent_chain_family('\n'.join(pages), len(pages))
        fam_by_doc[d['id']] = fam
        method = 'ocr' if d.get('is_scanned') else 'text_layer'
        t_doc.append((d['id'], d.get('filename'), d.get('file_hash'), len(pages),
                      d.get('document_type'), method, d.get('ocr_confidence'), fam,
                      int(archived_hint(d.get('filename') or '')), d.get('review_status'),
                      d.get('processed_at')))
        for n, p in enumerate(pages, 1):
            # every page of the file has a row; a page nothing could read says so.
            # An OCR page carries its second read and how far the two agree.
            s2 = second.get((d['id'], n))
            m = method if (p or '').strip() else ('ocr_second_read' if (s2 or '').strip() else 'none')
            t_page.append((d['id'], n, m, len(p or ''), p or '', s2, second_agree.get((d['id'], n))))

    # the roster = the lease rows
    rows = build_roster(conn, property_id, as_of=as_of,
                        confirmed_commencements=_confirmations(conn, property_id))
    t_lease, t_chain, t_step, t_opt, t_fact = [], [], [], [], []
    step_id = opt_id = fact_id = 0
    doc_sha = {d['id']: d.get('file_hash') for d in docs}
    scanned = {d['id']: bool(d.get('is_scanned')) for d in docs}

    def doc_method(doc_id, page, quote):
        """How the quoted page was read: its text layer, one OCR read, or two
        OCR reads that both print the quote (corroborated) or do not."""
        s2 = second.get((doc_id, page))
        if s2 is not None:
            first = pages_by_doc.get(doc_id, [])
            p1 = first[page - 1] if page and page <= len(first) else ''
            in1, in2 = in_read(quote, p1), in_read(quote, s2)
            if in1 and in2:
                return 'ocr_corroborated'
            return 'ocr_second_read_only' if in2 else 'ocr_uncorroborated'
        return 'ocr' if scanned.get(doc_id) else 'text'

    second_pages = {}
    for (sd, sp), st in second.items():
        second_pages.setdefault(sd, {})[sp] = st

    def cite_reads(doc_id, fn, *args):
        """Cite from the first read; when it prints nothing, from the second read."""
        first = pages_by_doc.get(doc_id, [])
        c = fn(first, *args)
        if c.page is None and doc_id in second_pages:
            alt = [second_pages[doc_id].get(i, '') for i in range(1, max(len(first), max(second_pages[doc_id])) + 1)]
            c2 = fn(alt, *args)
            if c2.page is not None:
                return c2
        return c
    # the owner's rent-roll keys, when given: (roll rows, alias table)
    keyed = {}
    if tenant_keys:
        keyed = match_tenant_keys(
            [(row.key or _slug(row.tenant), [n for n in row.tenant.split(' / ')] + list(row.aliases)
              + ([row.label] if row.label else []))
             for row in rows], tenant_keys[0], tenant_keys[1] if len(tenant_keys) > 1 else None)
    for row in rows:
        lease_id = row.key or _slug(row.tenant)
        source_key = _slug(row.tenant.split(' / ')[0])
        km = keyed.get(lease_id)
        tenant_key = (km.tenant_key if km and km.tenant_key else source_key)
        doc_list = [(i, pages_by_doc.get(i, [])) for i in row.doc_ids]
        sel = assemble_rent_chain([(fn, pg) for (i, pg), fn in zip(doc_list, row.filenames)])
        # the derivation's own text: worksheet / summary pages stapled into an instrument
        # are left out (blanked here, so page numbers stay the file's)
        kept_docs = [(doc_list[i][0], ['' if worksheet_page(x) else x for x in doc_list[i][1]])
                     for i in sel.kept]
        # chain rows: kept instruments in chain order, then what was left out
        seq = 0
        for i in sel.kept:
            seq += 1
            doc_id, pg = doc_list[i]
            start = _instrument_start(_flat('\n'.join(pg))[:8000])
            t_chain.append((lease_id, seq, doc_id, row.filenames[i], 'instrument',
                            int(archived_hint(row.filenames[i])), start.isoformat() if start else None))
        for i, why in sel.dropped.items():
            seq += 1
            t_chain.append((lease_id, seq, doc_list[i][0], row.filenames[i], why, 0, None))
        t_lease.append((lease_id, tenant_key, row.tenant, '; '.join(row.aliases), row.kind,
                        row.square_feet,
                        row.commencement.isoformat() if row.commencement else None, row.commencement_source,
                        row.expiration.isoformat() if row.expiration else None, row.expiration_source,
                        row.expiration_lease.isoformat() if row.expiration_lease else None,
                        row.monthly_rent, row.rent_method, row.rent_confidence,
                        ';'.join(row.rent_flags), row.rent_detail, row.needs,
                        int(row.show_rent), len(row.doc_ids), ','.join(str(i) for i in row.doc_ids),
                        ';'.join(sel.flags), source_key,
                        km.suite if km else None, km.occupant if km else None,
                        km.basis if km else 'no rent roll given'))
        # rent steps: the same chain and parsers the derivation used
        if kept_docs:
            cite = _Cite(kept_docs)
            text = ' '.join(_flat('\n'.join(pg)).strip() for _, pg in kept_docs)
            rel = parse_rent_schedule(text)
            bounds = [(a, b) for a, b, _, _ in cite.spans]
            # placed exactly as the rent derivation places them: a rent roll's
            # start is the CURRENT term's start, not the original lease's
            _anchor(rel, row.commencement, row.expiration_lease, text, bounds,
                    row.commencement_source == 'rent_roll',
                    row.expiration if row.expiration_source == 'rent_roll' else None, as_of)
            dated = parse_dated_rows(text)
            comm_txt = (f'commencement {row.commencement.isoformat()} '
                        f'({row.commencement_source or "source not recorded"})') if row.commencement else \
                'a commencement the paper does not date'

            def anchor_of(label, row=row, comm_txt=comm_txt):
                """What a relative row was counted from, by the anchor the derivation used."""
                if '[stated]' in label:
                    return "counted from the table's own stated start"
                if '[instrument]' in label:
                    return "counted from its instrument's own stated start"
                if '[expiration]' in label:
                    return 'back-dated from the expiration ' + (
                        row.expiration_lease.isoformat() if row.expiration_lease else '(undated)')
                if '[term start' in label:
                    return "counted from the rent roll's current-term start " + (
                        row.commencement.isoformat() if row.commencement else '')
                return 'counted from ' + comm_txt
            # one row per (period, amount): a table read twice is one schedule, cited where
            # the chain reads it first (an archived copy goes first; measured 2026-10-27,
            # citing the current file instead verified fewer quotes on the KA text of record)
            seen_steps = set()

            def add_step(sk, rest):
                nonlocal step_id
                if sk not in seen_steps:
                    seen_steps.add(sk)
                    step_id += 1
                    t_step.append((step_id,) + rest)

            for x in dated:
                sk = (x.start, x.end, round(x.monthly or 0, 2))
                d, p = cite.at(x.pos)
                c = cite_reads(d, _cite_amount, x.monthly, x.annual, p)
                add_step(sk, (lease_id, x.start.isoformat(), x.end.isoformat(), x.monthly,
                              x.annual, 'dated', 0, d, c.page or p)
                         + _cite_cols(c, d, doc_sha, doc_method, 'printed', 'amount'))
            for x in rel:
                opt = int(_is_option_table(text, x.pos))
                d, p = cite.at(x.pos)
                c = cite_reads(d, _cite_amount, x.monthly, x.annual, p)
                if x.start and x.end:
                    sk = (x.start, x.end, round(x.monthly or 0, 2))
                    if not opt and row.expiration_lease and x.start > row.expiration_lease:
                        # counted past the lease's own expiration: the anchor is wrong, so
                        # the period is left blank and says why (flag, don't fabricate)
                        add_step(sk, (lease_id, None, None, x.monthly, x.annual, x.label, opt, d, c.page or p)
                                 + _cite_cols(c, d, doc_sha, doc_method,
                                              f'unplaced: {x.label} {anchor_of(x.label)} would start '
                                              f'{x.start.isoformat()}, after the lease expires '
                                              f'{row.expiration_lease.isoformat()}', 'amount'))
                        continue
                    add_step(sk, (lease_id, x.start.isoformat(), x.end.isoformat(), x.monthly,
                                  x.annual, x.label, opt, d, c.page or p)
                             + _cite_cols(c, d, doc_sha, doc_method, f'computed: {x.label} {anchor_of(x.label)}',
                                          'amount'))
                elif opt:
                    opt_id += 1
                    t_opt.append((opt_id, lease_id, 'option_rent_table', x.label, x.monthly, x.annual,
                                  None, d, c.page or p)
                                 + _cite_cols(c, d, doc_sha, doc_method, None, 'amount'))
                else:
                    # printed but not placed in time (a superseded instrument's table, or one
                    # whose start the paper never dates): the amount stands, the period is blank
                    add_step((x.label, round(x.monthly or 0, 2)),
                             (lease_id, None, None, x.monthly, x.annual, x.label, opt, d, c.page or p)
                             + _cite_cols(c, d, doc_sha, doc_method,
                                          f'unplaced: {x.label}, no start date the paper or the roll gives '
                                          f'for this table', 'amount'))
        # tenancy facts: each value the lease row states, with the words it came from
        search_docs = list(reversed([doc_list[i] for i in sel.kept])) or list(reversed(doc_list))
        for field, value, kind in (('sf', row.square_feet, 'number'),
                                   ('commencement', row.commencement, 'date'),
                                   ('expiration', row.expiration_lease, 'date'),
                                   ('base_rent_monthly', row.monthly_rent, 'amount')):
            fact_id += 1
            d, c = _cite_in_docs(search_docs, value, kind)
            note = {'sf': None, 'commencement': row.commencement_source,
                    'expiration': None, 'base_rent_monthly': row.rent_method}[field]
            t_fact.append((fact_id, lease_id, field, value.isoformat() if hasattr(value, 'isoformat') else value,
                           note, d, c.page)
                          + _cite_cols(c, d, doc_sha, doc_method, None, field, missing_value=value is None))

    t_recon = _roll_reconciliation(tenant_keys[0] if tenant_keys else [], keyed, rows, t_fact, docs, pages_by_doc)

    # provisions: extracted terms + segmented clauses of the roster's documents
    t_prov = []
    prov_id = 0
    lease_doc_ids = {i for row in rows for i in row.doc_ids}
    doc_lease = {i: (row.key or _slug(row.tenant)) for row in rows for i in row.doc_ids}
    try:
        for r in conn.execute(
                'SELECT document_id, term_type, term_label, value_raw, value_numeric, value_unit, '
                'effective_date, expiration_date, section_ref, page_number, confidence '
                'FROM financial_terms ORDER BY document_id, id'):
            if r[0] not in lease_doc_ids:
                continue
            prov_id += 1
            c = cite_reads(r[0], _cite_term, r[3], r[4], r[6], r[9])
            t_prov.append((prov_id, doc_lease[r[0]], r[0], 'term', r[1], r[2], r[3], r[4], r[5],
                           r[6], r[7], r[8], c.page or r[9], r[10], None)
                          + _cite_cols(c, r[0], doc_sha, doc_method, None, 'value'))
            if r[1] in ('renewal_options', 'renewal_option'):
                opt_id += 1
                t_opt.append((opt_id, doc_lease[r[0]], 'renewal_option_statement', r[3], None, None,
                              r[9], r[0], c.page or r[9])
                             + _cite_cols(c, r[0], doc_sha, doc_method, None, 'value'))
    except sqlite3.Error:
        pass
    try:
        for r in conn.execute(
                'SELECT document_id, clause_type, section_ref, clause_title, summary, page_number, confidence '
                'FROM clauses ORDER BY document_id, id'):
            if r[0] not in lease_doc_ids:
                continue
            prov_id += 1
            pg = pages_by_doc.get(r[0], [])
            c = _cite_heading(pg, r[3] or '', r[2] or '', r[5])
            t_prov.append((prov_id, doc_lease[r[0]], r[0], 'clause', r[1], r[3], r[4], None, None,
                           None, None, r[2], c.page or r[5], r[6], None)
                          + _cite_cols(c, r[0], doc_sha, doc_method, None, 'heading'))
    except sqlite3.Error:
        pass
    conn.close()

    # events: every instrument of every tenancy document, typed, dated by its
    # OWN date (never a date it recites), cited to the words it was read from
    t_event, t_exhibit = [], []
    event_id = exhibit_id = 0
    for row in rows:
        lid = row.key or _slug(row.tenant)
        seq = 0
        ins_by_doc = {i: split_instruments(pages_by_doc.get(i, [])) for i in row.doc_ids}
        # what the tenancy's instruments recite about each other: an instrument
        # that prints no date of its own takes the date a LATER instrument
        # recites for it ("as amended by the Third Amendment dated ..."), and
        # says so; only when every recital of it agrees
        recited = recited_dates(list(ins_by_doc.items()))
        for i in row.doc_ids:
            doc_ins = ins_by_doc[i]
            base = seq
            for ex in find_exhibits(pages_by_doc.get(i, []), doc_ins):
                exhibit_id += 1
                c = Cite(ex.page_from, ex.heading, 'printed')
                t_exhibit.append((exhibit_id, lid, i, ex.label, ex.title or None, ex.page_from, ex.page_to,
                                  (base + ex.parent_seq) if ex.parent_seq else None)
                                 + _cite_cols(c, i, doc_sha, doc_method, None, 'exhibit'))
            for k, ins in enumerate(doc_ins):
                seq += 1
                event_id += 1
                # event_date is the instrument's OWN date only (ELP LD rules); a date a
                # later instrument recites for it is kept apart, with its own citation
                ev_date, ev_page = ins.date, ins.date_page
                c = Cite(ins.date_page, ins.date_quote, 'printed') if ins.date else Cite(None, '', 'not_found')
                cols = _cite_cols(c, i, doc_sha, doc_method, ins.date_basis or None, 'instrument date')
                later = recited.get((i, k)) or (None, None, None, None)
                if not ev_date:
                    why = ins.date_null_reason
                    if later[0]:
                        why += f'; a later instrument recites it as {later[0]}'
                    cols = cols[:5] + (why,)
                t_event.append((event_id, lid, seq, i, ins.event_type, ins.ordinal, ins.title or None,
                                ins.page_from, ins.page_to, ev_date, ev_page,
                                '; '.join(f'{d} ({q[:80]})' for d, q in ins.recited) or None) + cols
                               + (later[0], later[3], later[2], later[1]))

    # possible duplicates: two tenancies whose documents name the same legal
    # tenant under different file names. FLAGGED for review, never merged:
    # one tenant can hold two leases (two suites on the rent roll = two leases)
    legal = {}
    for row in rows:
        lid = row.key or _slug(row.tenant)
        for a in row.aliases:
            k = re.sub(r'[^a-z]', '', (a or '').lower())
            if len(k) >= 8 and k not in _GENERIC_LEGAL:
                legal.setdefault(k, set()).add(lid)
    suite_of = {r[0]: r[22] for r in t_lease}
    dup = {}
    for k, lids in legal.items():
        lids = sorted(lids)
        for a in lids:
            for b in lids:
                if a == b or (suite_of.get(a) and suite_of.get(b) and suite_of[a] != suite_of[b]):
                    continue
                dup.setdefault(a, set()).add(b)
    t_lease = [r + ('; '.join(sorted(dup[r[0]])) if r[0] in dup else None,) for r in t_lease]

    # option register: every renewal / extension option granted (or deleted)
    # in a tenancy's instruments, with count, length, notice window, rent basis
    t_optreg = []
    optreg_id = 0
    for row in rows:
        lid = row.key or _slug(row.tenant)
        for i in row.doc_ids:
            if fam_by_doc.get(i) != 'instrument':
                continue
            for o in find_options(pages_by_doc.get(i, [])):
                optreg_id += 1
                c = Cite(o.page, o.quote, 'printed')
                t_optreg.append((optreg_id, lid, i, o.page, o.status, o.n_options, o.option_years, o.notice_window,
                                 o.notice_min_days, o.notice_max_days, o.rent_basis, o.rent_quote or None)
                                + _cite_cols(c, i, doc_sha, doc_method, None, 'option'))

    # NER deal sheets: every printed field with the line it is on, every cost
    # line, and the tie of the cost lines to the sheet's printed total
    t_ner, t_ner_cost = [], []
    ner_id = 0
    for d in docs:
        for pno, page in enumerate(pages_by_doc.get(d['id'], []), 1):
            ns = parse_ner(page)
            if not ns:
                continue
            ner_id += 1
            tot = ns.printed_total_landlord_expense
            dpages = pages_by_doc.get(d['id'], [])

            def on_line(value, line, pno=pno, dpages=dpages):
                # the printed amount itself (a sheet's columns OCR differently line to
                # line, so the bare value verifies where the whole line does not); the
                # line when the amount is not printed on this page
                if value is not None:
                    cv = cite_number(dpages, value, pno)
                    if cv.page == pno:
                        return Cite(pno, cv.quote, 'printed')
                return Cite(pno, line, 'printed') if line else Cite(None, '', 'not_found')
            c = on_line(tot.value, tot.line)
            t_ner.append((ner_id, doc_lease.get(d['id']), d['id'], pno, ns.version_date.value, ns.deal_type.value,
                          ns.tenant.value, ns.use.value, ns.space.value, ns.sqft.value, ns.term_years.value,
                          ns.total_contract_rent.value, ns.allowance_total.value, ns.leasing_fee.value,
                          ns.cobroker_fee.value, tot.value, ns.cost_sum, ns.tie, ns.tie_note,
                          ns.total_effective_contract_rent.value, ns.avg_annual_effective_rent.value,
                          ns.avg_psf_effective_rent.value,
                          '; '.join(f'{x[0]}: {x[2]}' for x in ns.ladder) or None)
                         + _cite_cols(c, d['id'], doc_sha, doc_method, None, 'total landlord expense',
                                      missing_value=tot.value is None and not tot.line))
            for seq, cl in enumerate(ns.cost_lines, 1):
                cc = on_line(cl.amount if cl.amount is not None else cl.budgeted, cl.line)
                t_ner_cost.append((ner_id, seq, cl.label, cl.amount, cl.budgeted, cl.memo or None, d['id'], pno)
                                  + _cite_cols(cc, d['id'], doc_sha, doc_method, None, 'cost line'))

    # lease terms: one row per term-setting instrument, its start / expiry /
    # length as it prints them, else computed from the rent rows cited to its
    # pages; the nearest deal sheet of the tenancy
    # (by version date) supplies allowance, commission and NER
    TERM_TYPES = ('original lease', 'amendment', 'extension / renewal exercised', 'expansion', 'license agreement',
                  'lease document (unclassified)')
    sf_of = {(row.key or _slug(row.tenant)): row.square_feet for row in rows}
    step_by_doc = {}
    for st in t_step:
        if st[2] and st[4]:                       # placed periods with an amount
            step_by_doc.setdefault(st[8], []).append(st)
    ner_by_lease = {}
    for nr in t_ner:
        if nr[1]:
            ner_by_lease.setdefault(nr[1], []).append(nr)
    t_term = []
    term_id = 0
    for ev in t_event:
        _, lid, seq, doc_id, etype, _, title, p_from, p_to, ev_date = ev[:10]
        if etype not in TERM_TYPES:
            continue
        steps = sorted((st for st in step_by_doc.get(doc_id, []) if p_from <= (st[9] or 0) <= p_to and not st[7]),
                       key=lambda st: st[2])
        # the term as the instrument prints it wins; its rent rows fill what it leaves out
        pt = instrument_term(pages_by_doc.get(doc_id, []), p_from, p_to)
        if not steps and not (pt.start or pt.end):
            continue
        term_id += 1
        start = pt.start or (steps[0][2] if steps else None)
        end = pt.end or (max(st[3] for st in steps) if steps else None)
        years = (round(pt.months / 12, 1) if pt.months else
                 round((date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25, 1) if start and end else None)
        sf = sf_of.get(lid)
        psf = (lambda m: round(m * 12 / sf, 2) if sf else None)
        p0, p1 = (psf(steps[0][4]), psf(steps[-1][4])) if steps else (None, None)
        esc = None
        if len(steps) > 1 and steps[0][4] and steps[-1][4] and (years or 0) >= 1:
            esc = round(((steps[-1][4] / steps[0][4]) ** (1 / max(1, len(steps) - 1)) - 1) * 100, 2)
        ner = None
        if ner_by_lease.get(lid):
            ref = ev_date or start
            def gap(nr, ref=ref):
                try:
                    return abs((date.fromisoformat(nr[4]) - date.fromisoformat(ref)).days)
                except (TypeError, ValueError):
                    return 10 ** 6
            ner = min(ner_by_lease[lid], key=gap)
        printed = [x for x in ((pt.start, pt.start_quote, pt.start_page), (pt.end, pt.end_quote, pt.end_page),
                               (pt.months, pt.months_quote, pt.months_page)) if x[0]]
        rows_txt = f'{len(steps)} rent rows of this instrument, pages {p_from}-{p_to}'
        if printed:
            quotes = list(dict.fromkeys(q for _, q, _ in printed))
            src_term = 'printed: ' + ' | '.join(quotes) + (
                f'; computed from {rows_txt}: ' + ', '.join(
                    w for w, have in (('start', pt.start), ('expiry', pt.end)) if not have)
                if steps and not (pt.start and pt.end) else '')
            c = Cite(printed[0][2], printed[0][1], 'printed')
            basis = 'printed by the instrument' if pt.start and pt.end else 'printed in part; the rest from its rent rows'
        else:
            src_term = f'computed: {rows_txt}'
            c = Cite(steps[0][9], steps[0][11], 'printed') if steps[0][11] else Cite(None, '', 'not_found')
            basis = 'computed: first and last placed rent rows'
        t_term.append((term_id, lid, seq, doc_id, etype, title, ev_date, start, end, years, sf, p0, p1, esc,
                       ner[12] if ner else None, ner[13] if ner else None, ner[14] if ner else None,
                       ner[21] if ner else None, ner[5] if ner else None, ner[0] if ner else None,
                       src_term,
                       ('computed: monthly x 12 / tenancy SF' if sf else 'SF not known') if steps else 'no rent rows',
                       c.page)
                      + _cite_cols(c, doc_id, doc_sha, doc_method, basis, 'term'))

    # depth: every article of every instrument verbatim (layer A) and the
    # category sweep (layer B), each cited to the file pages it is on
    for d in docs:
        if d['id'] not in lease_doc_ids or fam_by_doc.get(d['id']) != 'instrument':
            continue
        pg = pages_by_doc.get(d['id'], [])
        lid = doc_lease[d['id']]
        for a in split_articles(pg):
            prov_id += 1
            q = _opening(pg, a)
            c = Cite(a.page_from, q, 'printed') if q else Cite(None, '', 'not_found')
            t_prov.append((prov_id, lid, d['id'], 'article', f'article {a.ordinal}', a.title or None, a.body,
                           None, None, None, None, a.heading or f'(boundary at Section 1, {a.style})',
                           a.page_from, None, a.page_to)
                          + _cite_cols(c, d['id'], doc_sha, doc_method, None, 'article'))
        for h in sweep(pg):
            prov_id += 1
            c = Cite(h.page, h.quote or h.excerpt, 'printed')
            t_prov.append((prov_id, lid, d['id'], 'sweep', h.category, None, h.excerpt, None, None, None, None,
                           None, h.page, None, h.page)
                          + _cite_cols(c, d['id'], doc_sha, doc_method, None, 'excerpt'))

    tables = {
        'cap_document': (['doc_id', 'filename', 'sha256', 'pages', 'document_type', 'method', 'ocr_confidence',
                          'family', 'archived_hint', 'review_status', 'processed_at'], t_doc),
        'cap_page_text': (['doc_id', 'page_no', 'method', 'chars', 'text', 'second_read',
                           'read_agreement'], t_page),
        'cap_lease_lease': (['lease_id', 'tenant_key', 'trade_name', 'legal_names', 'status', 'sf',
                             'commencement', 'commencement_source', 'expiration', 'expiration_source',
                             'expiration_paper', 'base_rent_monthly', 'rent_method', 'rent_confidence',
                             'rent_flags', 'rent_detail', 'needs', 'show_unreviewed', 'n_documents',
                             'source_doc_ids', 'chain_flags', 'source_tenant_key', 'roll_suite',
                             'roll_occupant', 'key_basis', 'possible_same_lease_as'], t_lease),
        'cap_lease_chain': (['lease_id', 'seq', 'doc_id', 'filename', 'role', 'archived_hint',
                             'instrument_start'], t_chain),
        'cap_lease_rent_step': (['step_id', 'lease_id', 'period_from', 'period_to', 'monthly_amount',
                                 'annual_amount', 'basis', 'is_option_period', 'source_doc_id',
                                 'source_page'] + CITE_COLS, t_step),
        'cap_lease_provision': (['prov_id', 'lease_id', 'doc_id', 'kind', 'category', 'label', 'value_raw',
                                 'value_numeric', 'unit', 'effective_date', 'expiration_date',
                                 'section_ref', 'source_page', 'confidence', 'source_page_to'] + CITE_COLS,
                                t_prov),
        'cap_lease_option': (['opt_id', 'lease_id', 'kind', 'detail', 'monthly_amount', 'annual_amount',
                              'source_page_hint', 'source_doc_id', 'source_page'] + CITE_COLS, t_opt),
        'cap_lease_fact': (['fact_id', 'lease_id', 'field', 'value', 'value_source', 'source_doc_id',
                            'source_page'] + CITE_COLS, t_fact),
        'cap_lease_event': (['event_id', 'lease_id', 'seq', 'source_doc_id', 'event_type', 'ordinal', 'title',
                             'page_from', 'page_to', 'event_date', 'source_page', 'recited_dates'] + CITE_COLS
                            + ['recited_by_later_date', 'recited_by_later_doc_id', 'recited_by_later_page',
                               'recited_by_later_quote'], t_event),
        'cap_lease_exhibit': (['exhibit_id', 'lease_id', 'source_doc_id', 'exhibit', 'title_as_printed', 'page_from',
                               'page_to', 'parent_event_seq'] + CITE_COLS, t_exhibit),
        'cap_lease_term': (['term_id', 'lease_id', 'event_seq', 'source_doc_id', 'instrument_type', 'instrument_title',
                            'instrument_date', 'lease_start', 'lease_expiry', 'term_years', 'sf', 'start_rent_psf',
                            'end_rent_psf', 'avg_escalation_pct', 'ti_total', 'lc_total', 'cobroker_total', 'ner_psf',
                            'deal_type', 'ner_id', 'source_term', 'source_rent', 'source_page'] + CITE_COLS, t_term),
        'cap_option_register': (['optreg_id', 'lease_id', 'source_doc_id', 'source_page', 'status', 'n_options',
                                 'option_years', 'notice_window', 'notice_min_days', 'notice_max_days', 'rent_basis',
                                 'rent_basis_words'] + CITE_COLS, t_optreg),
        'cap_ner_deal': (['ner_id', 'lease_id', 'source_doc_id', 'source_page', 'version_date', 'deal_type', 'tenant',
                          'use', 'space', 'sqft', 'term_years', 'total_contract_rent', 'allowance_total',
                          'leasing_fee', 'cobroker_fee', 'printed_total_landlord_expense', 'cost_lines_sum',
                          'tie_status', 'tie_note', 'total_effective_contract_rent', 'avg_annual_effective_rent',
                          'avg_psf_effective_rent', 'rent_ladder'] + CITE_COLS, t_ner),
        'cap_roll_reconciliation': (['recon_id', 'roll_suite', 'roll_occupant', 'tenant_key', 'field', 'roll_value',
                                     'paper_value', 'lease_id', 'state', 'explanation', 'source_doc_id', 'source_page',
                                     'paper_quote'], t_recon),
        'cap_ner_cost_line': (['ner_id', 'seq', 'label', 'proposed', 'budgeted', 'memo', 'source_doc_id', 'source_page']
                              + CITE_COLS, t_ner_cost),
    }
    return verify_quotes(tables, consumer_text)


DOOR_READER = '''"""_cap_door.py -- the one door to a Capactive module package. Stdlib only.

Copy this file into your module. Point PACKAGE at the package folder (or
write it to a SOURCE_PATH.txt beside this file) and declare the tables you
read in a HANDOFF.txt beside it, one line per table:
    YYYY-MM-DD | table | what you take | selection | your record

    import _cap_door as CAP
    with CAP.snapshot() as s:
        cols, rows = s.rows('cap_lease_lease')
A missing manifest or a database whose sha256 differs from the manifest means
a publish is under way or torn: snapshot() raises and you keep your previous pull.
"""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = None


def _lines(name):
    p = os.path.join(HERE, name)
    return [x.strip() for x in open(p, encoding='utf-8') if x.strip() and not x.strip().startswith('#')]


def package():
    return PACKAGE or _lines('SOURCE_PATH.txt')[0]


def declared():
    t = {}
    for ln in _lines('HANDOFF.txt'):
        parts = [p.strip() for p in ln.split('|')]
        if len(parts) >= 5 and re.match(r'\\d{4}-\\d{2}-\\d{2}$', parts[0]):
            t[parts[1]] = parts[3]
    if not t:
        raise PermissionError('your HANDOFF.txt declares no table')
    return t


def manifest():
    p = os.path.join(package(), 'PUBLISH_MANIFEST.json')
    if not os.path.exists(p):
        raise RuntimeError('no manifest in %s: a publish is under way or torn; keep your previous pull' % package())
    return json.load(open(p, encoding='utf-8'))


def _sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


class Snapshot:
    def __init__(self, con, man):
        self.c, self.manifest, self.generation = con, man, man['generation_id']
        self.tables = declared()

    def _check(self, table):
        if table not in self.tables:
            raise PermissionError('%r is not declared in your HANDOFF.txt' % table)
        if table not in self.manifest['tables']:
            raise PermissionError('%r is not in the package (generation %s)' % (table, self.generation))

    def columns(self, table):
        self._check(table)
        return [r[1] for r in self.c.execute('pragma table_info("%s")' % table)]

    def rows(self, table, where=None, args=()):
        cols = self.columns(table)
        key = self.manifest['tables'][table]['key']
        cur = self.c.execute('select * from "%s"%s order by %s' % (
            table, ' where %s' % where if where else '', key), args)
        return cols, [dict(zip(cols, r)) for r in cur]


@contextmanager
def snapshot():
    man = manifest()
    p = os.path.join(package(), man['database'])
    if not os.path.exists(p) or _sha(p) != man['files'][man['database']]['sha256']:
        raise RuntimeError('the package database does not match its manifest: torn or changed; keep your previous pull')
    con = sqlite3.connect(Path(p).as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        yield Snapshot(con, man)
    finally:
        con.close()
'''


def publish_property_door(db_path: str, out_dir: str, package: str, property_id=None,
                          as_of: date | None = None, publisher: str = PUBLISHER,
                          property_label: str = '', reads_db: str | None = None,
                          tenant_keys: tuple | None = None, consumer_text=None) -> dict:
    """Write the package into out_dir: the generation database, HANDOFF.txt,
    SOURCE_PATH.txt, BOUNDARY_CONTRACT.md, door/_cap_door.py and, LAST,
    PUBLISH_MANIFEST.json. Returns the manifest."""
    as_of = as_of or date.today()
    os.makedirs(os.path.join(out_dir, 'door'), exist_ok=True)
    man_path = os.path.join(out_dir, 'PUBLISH_MANIFEST.json')
    if os.path.exists(man_path):
        os.remove(man_path)                      # consumers now see a publish under way
    tables = build_door_tables(db_path, property_id, as_of, reads_db=reads_db, tenant_keys=tenant_keys,
                               consumer_text=consumer_text)
    gen = uuid.uuid4().hex
    db_name = f'{PREFIX}_{package.lower()}_{gen}.db'
    db_out = os.path.join(out_dir, db_name)
    con = sqlite3.connect(db_out)
    man_tables = {}
    for name, (cols, rows) in tables.items():
        con.execute(f'CREATE TABLE "{name}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(cols))})', rows)
        key = TABLES[name][0]
        h = hashlib.sha256()
        for r in con.execute(f'SELECT * FROM "{name}" ORDER BY {key}'):
            h.update(repr(r).encode())
        man_tables[name] = {'key': key, 'rows': len(rows), 'content_sha256': h.hexdigest()}
    con.commit()
    con.close()

    stamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    today = date.today().isoformat()
    lines = ['# Capactive module package catalog. One dated line per declared table:',
             '#   declared on | table | key | what it holds | record',
             f'PACKAGE | {package} | PUBLISH_MANIFEST.json names the current generation db']
    for name, (key, what) in TABLES.items():
        lines.append(f'{today} | {name} | {key} | {what} | Capactive publish {gen[:8]}')
    with open(os.path.join(out_dir, 'HANDOFF.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    with open(os.path.join(out_dir, 'SOURCE_PATH.txt'), 'w', encoding='utf-8') as fh:
        fh.write(f'# this package\n{os.path.abspath(out_dir)}\n')
    with open(os.path.join(out_dir, 'door', '_cap_door.py'), 'w', encoding='utf-8') as fh:
        fh.write(DOOR_READER)
    n_lease = man_tables['cap_lease_lease']['rows']
    n_high = sum(1 for r in tables['cap_lease_lease'][1] if r[17])
    contract = f"""# BOUNDARY CONTRACT: {publisher} -> package {package}

_Generated {stamp} by modules/module_door.py. Do not edit; re-run the publish._
Generation: {gen}. As of: {as_of.isoformat()}.{(' Property: ' + property_label) if property_label else ''}

## What this package holds
The LEASE PAPER of one property as read by the extraction engine: every document registered by sha256 and read page by page,
the tenancies the documents form (the roster), the current base rent derived from the instruments with its method, confidence
and flags, every rent row placed in time, every extracted term and clause. {n_lease} tenancies, {n_high} with a rent the engine
would show unreviewed (confidence high).

## What a consumer may rely on
1. Names: the tables and columns keep their names; a column may be added, none renamed or dropped without a new line here.
2. Every structured row cites its document (doc_id -> cap_document.sha256) and page.
3. Rent evidence is instruments only: worksheets, management-system lease summaries and their pages are registered but left
   out of the rent chain (cap_lease_chain.role says which). A rent at confidence 'high' is the engine's unreviewed answer;
   'medium' / 'low' carry the flag that says why and the one action that would resolve it (cap_lease_lease.needs).
4. Whole or nothing: the manifest is removed first and written last, with the db sha256 and a content hash per table.
5. Nothing here is ever edited in place; a new publish is a new generation file and a new manifest.
"""
    with open(os.path.join(out_dir, 'BOUNDARY_CONTRACT.md'), 'w', encoding='utf-8') as fh:
        fh.write(contract)
    files = {}
    for rel in (db_name, 'HANDOFF.txt', 'SOURCE_PATH.txt', 'BOUNDARY_CONTRACT.md', 'door/_cap_door.py'):
        p = os.path.join(out_dir, rel)
        files[rel] = {'sha256': _sha(p), 'bytes': os.path.getsize(p)}
    manifest = {'publisher': publisher, 'package': package, 'generation_id': gen, 'database': db_name,
                'generated_at': stamp, 'as_of': as_of.isoformat(), 'property': property_label,
                'tables': man_tables, 'files': files, 'script': 'modules/module_door.py'}
    with open(man_path, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=1)
    # one current generation per folder: once the manifest names the new db,
    # an older generation left beside it goes (the consumer reads only what
    # the manifest names; a stray db would look like a second current one)
    for f in os.listdir(out_dir):
        if f != db_name and f.startswith(f'{PREFIX}_{package.lower()}_') and f.endswith('.db'):
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
    return manifest
