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

from ..extractors.document_family import archived_hint, assemble_rent_chain, rent_chain_family
from ..extractors.rent_derivation import (
    _anchor, _instrument_start, _is_option_table, parse_dated_rows, parse_rent_schedule)
from ..lease_roster import _pages, build_roster

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
}


def _slug(name: str) -> str:
    words = [re.sub(r'[^a-z]', '', w.lower()) for w in re.split(r'[^A-Za-z]+', name or '')]
    words = [w for w in words if len(w) >= 3 and w not in GENERIC]
    return '_'.join(words[:2]) or re.sub(r'[^a-z]', '', (name or '').lower()) or 'unknown'


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


def build_door_tables(db_path: str, property_id=None, as_of: date | None = None) -> dict:
    """Read a property's extraction DB and return {table: (columns, rows)}."""
    as_of = as_of or date.today()
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    docs = _property_docs(conn, property_id)
    pages_by_doc = {d['id']: _pages(conn, d['id']) for d in docs}

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
            t_page.append((d['id'], n, method, len(p or ''), p or ''))

    # the roster = the lease rows
    rows = build_roster(conn, property_id, as_of=as_of,
                        confirmed_commencements=_confirmations(conn, property_id))
    t_lease, t_chain, t_step, t_opt = [], [], [], []
    step_id = opt_id = 0
    for row in rows:
        lease_id = row.key or _slug(row.tenant)
        tenant_key = _slug(row.tenant.split(' / ')[0])
        doc_list = [(i, pages_by_doc.get(i, [])) for i in row.doc_ids]
        sel = assemble_rent_chain([(fn, pg) for (i, pg), fn in zip(doc_list, row.filenames)])
        kept_docs = [doc_list[i] for i in sel.kept]
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
                        ';'.join(sel.flags)))
        # rent steps: the same chain and parsers the derivation used
        if kept_docs:
            cite = _Cite(kept_docs)
            text = ' '.join(_flat('\n'.join(pg)).strip() for _, pg in kept_docs)
            rel = parse_rent_schedule(text)
            bounds = [(a, b) for a, b, _, _ in cite.spans]
            _anchor(rel, row.commencement, row.expiration_lease, text, bounds)
            dated = parse_dated_rows(text)
            for x in dated:
                step_id += 1
                d, p = cite.at(x.pos)
                t_step.append((step_id, lease_id, x.start.isoformat(), x.end.isoformat(), x.monthly,
                               x.annual, 'dated', 0, d, p))
            for x in rel:
                opt = int(_is_option_table(text, x.pos))
                d, p = cite.at(x.pos)
                if x.start and x.end:
                    step_id += 1
                    t_step.append((step_id, lease_id, x.start.isoformat(), x.end.isoformat(), x.monthly,
                                   x.annual, x.label, opt, d, p))
                elif opt:
                    opt_id += 1
                    t_opt.append((opt_id, lease_id, 'option_rent_table', x.label, x.monthly, x.annual,
                                  None, d, p))

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
            t_prov.append((prov_id, doc_lease[r[0]], r[0], 'term', r[1], r[2], r[3], r[4], r[5],
                           r[6], r[7], r[8], r[9], r[10]))
            if r[1] in ('renewal_options', 'renewal_option'):
                opt_id += 1
                t_opt.append((opt_id, doc_lease[r[0]], 'renewal_option_statement', r[3], None, None,
                              r[9], r[0], r[9]))
    except sqlite3.Error:
        pass
    try:
        for r in conn.execute(
                'SELECT document_id, clause_type, section_ref, clause_title, summary, page_number, confidence '
                'FROM clauses ORDER BY document_id, id'):
            if r[0] not in lease_doc_ids:
                continue
            prov_id += 1
            t_prov.append((prov_id, doc_lease[r[0]], r[0], 'clause', r[1], r[3], r[4], None, None,
                           None, None, r[2], r[5], r[6]))
    except sqlite3.Error:
        pass
    conn.close()

    return {
        'cap_document': (['doc_id', 'filename', 'sha256', 'pages', 'document_type', 'method', 'ocr_confidence',
                          'family', 'archived_hint', 'review_status', 'processed_at'], t_doc),
        'cap_page_text': (['doc_id', 'page_no', 'method', 'chars', 'text'], t_page),
        'cap_lease_lease': (['lease_id', 'tenant_key', 'trade_name', 'legal_names', 'status', 'sf',
                             'commencement', 'commencement_source', 'expiration', 'expiration_source',
                             'expiration_paper', 'base_rent_monthly', 'rent_method', 'rent_confidence',
                             'rent_flags', 'rent_detail', 'needs', 'show_unreviewed', 'n_documents',
                             'source_doc_ids', 'chain_flags'], t_lease),
        'cap_lease_chain': (['lease_id', 'seq', 'doc_id', 'filename', 'role', 'archived_hint',
                             'instrument_start'], t_chain),
        'cap_lease_rent_step': (['step_id', 'lease_id', 'period_from', 'period_to', 'monthly_amount',
                                 'annual_amount', 'basis', 'is_option_period', 'source_doc_id',
                                 'source_page'], t_step),
        'cap_lease_provision': (['prov_id', 'lease_id', 'doc_id', 'kind', 'category', 'label', 'value_raw',
                                 'value_numeric', 'unit', 'effective_date', 'expiration_date',
                                 'section_ref', 'source_page', 'confidence'], t_prov),
        'cap_lease_option': (['opt_id', 'lease_id', 'kind', 'detail', 'monthly_amount', 'annual_amount',
                              'source_page_hint', 'source_doc_id', 'source_page'], t_opt),
    }


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
                          property_label: str = '') -> dict:
    """Write the package into out_dir: the generation database, HANDOFF.txt,
    SOURCE_PATH.txt, BOUNDARY_CONTRACT.md, door/_cap_door.py and, LAST,
    PUBLISH_MANIFEST.json. Returns the manifest."""
    as_of = as_of or date.today()
    os.makedirs(os.path.join(out_dir, 'door'), exist_ok=True)
    man_path = os.path.join(out_dir, 'PUBLISH_MANIFEST.json')
    if os.path.exists(man_path):
        os.remove(man_path)                      # consumers now see a publish under way
    tables = build_door_tables(db_path, property_id, as_of)
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
    return manifest
