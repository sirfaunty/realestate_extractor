"""
Lease roster — one row per TENANCY for a property, built from what the app
has already extracted. The data layer behind lease-derived dashboard KPIs
(leased SF, expirations / WALT, current rent) for clients who upload leases
rather than a rent roll.

Each row says what it knows and how sure it is:
  tenant, documents (lease + amendments), square feet, commencement,
  expiration, current monthly rent + method + confidence, and — when not
  high-confidence — the ONE thing that would resolve it (usually "confirm
  the commencement date": the rent schedule is found but the lease defines
  its start by an event, e.g. opening day).

Grouping (no master to lean on): documents carrying a tenant identity
anchor a tenancy; amendments / letters without one attach by a distinctive
name token in their filename or opening text. Anything unattached is its
own row, flagged — never silently merged.

Read-only. Rent method + accuracy measured by the pilot rent tie-out
harness (2026-09-29: schedule-derived high-confidence answers 23/24 within
2% of the landlord's rent roll).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .extractors.document_family import assemble_rent_chain
from .extractors.instrument_terms import instrument_term
from .extractors.instruments import split_instruments
from .extractors.lease_segmenter import _full_months_end, _sf_pick, _sf_scan
from .extractors.rent_derivation import (
    derive_current_rent, parse_date, parse_dated_rows, parse_rent_schedule, _num, _text_memo)

_ENTITY_WORDS = {'llc', 'inc', 'incorporated', 'corp', 'corporation', 'company',
                 'co', 'lp', 'llp', 'ltd', 'pa', 'pllc', 'dba', 'the', 'of', 'and'}
_GENERIC = {'store', 'stores', 'shop', 'center', 'health', 'club', 'lease',
            'amendment', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth',
            'agreement', 'scanned', 'signed', 'executed', 'extension', 'renewal',
            'minnesota', 'wisconsin', 'holdings', 'group', 'services', 'retail',
            'restaurant', 'market', 'enterprises', 'partners', 'properties'}


@dataclass
class RosterRow:
    tenant: str
    doc_ids: list = field(default_factory=list)
    filenames: list = field(default_factory=list)
    square_feet: Optional[float] = None
    commencement: Optional[date] = None
    expiration: Optional[date] = None
    monthly_rent: Optional[float] = None
    rent_method: str = 'none'
    rent_confidence: str = 'low'
    rent_flags: list = field(default_factory=list)
    rent_detail: str = ''
    needs: str = ''                 # the one action that would resolve it
    grouped_by: str = 'identity'    # filename | identity | unassigned
    label: str = ''                 # the file-name tenancy words ("bean coffee")
    aliases: list = field(default_factory=list)   # legal names across assignments
    kind: str = 'tenancy'           # tenancy | expired | other_agreement | other_document
    flags: list = field(default_factory=list)     # data-quality notes (SF rejected, ...)
    key: str = ''                   # stable tenancy id for confirmations ("fn:bean coffee")
    commencement_source: str = ''   # confirmed | rent_roll | lease | '' (unknown)
    commencement_guess: Optional[date] = None     # best guess to pre-fill a confirmation
    rent_roll_monthly: Optional[float] = None     # uploaded rent roll, summed across suites
    expiration_source: str = ''     # rent_roll | lease | computed | '' (unknown)
    expiration_lease: Optional[date] = None       # what the paper says, kept when the rent roll wins

    @property
    def show_rent(self) -> bool:
        """Dashboard shows the rent without review only when high confidence."""
        return self.monthly_rent is not None and self.rent_confidence == 'high'


def _alpha(s):
    return re.sub(r'[^a-z]', '', (s or '').lower())


def _tokens(name):
    return [t for t in (w.lower() for w in re.split(r'[^A-Za-z]+', name or ''))
            if len(t) >= 4 and t not in _ENTITY_WORDS and t not in _GENERIC]


def _key(name):
    return ' '.join(_tokens(name)[:3]) or _alpha(name)


def _terms(conn, doc_ids):
    """{doc_id: {term_type: row}} — current extraction run, user edits win."""
    if not doc_ids:
        return {}
    table = 'cur_financial_terms'
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
    except sqlite3.Error:
        table = 'financial_terms'
    q = ','.join('?' * len(doc_ids))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(financial_terms)")]
    has_user = 'user_value_raw' in cols
    sel = ("document_id, term_type, value_raw, value_numeric, expiration_date"
           + (", user_value_raw" if has_user else ""))
    out = {}
    for r in conn.execute(f"SELECT {sel} FROM {table} WHERE document_id IN ({q}) "
                          f"ORDER BY id", doc_ids):
        d = out.setdefault(r[0], {})
        if r[1] in d:
            continue
        raw = (r[5] if has_user and r[5] else r[2])
        d[r[1]] = {'raw': raw, 'num': r[3], 'exp': r[4]}
    return out


def _text(conn, doc_id, limit=None):
    t = '\n'.join(_pages(conn, doc_id))
    return t[:limit] if limit else t


_FILING_WORDS = ('fullyexecutedleaseagreement', 'leasedocuments', 'archivedscans', 'scannedlease', 'old',
                 'scanned', 'executed', 'lease', 'fid', 'copy')
# words that name no tenant in particular (legal forms, filing and document words)
_SF_NAME_NOISE = {'inc', 'llc', 'corp', 'corporation', 'company', 'limited', 'incorporated', 'partnership',
                  'lease', 'leases', 'leasedocuments', 'scanned', 'scannedlease', 'archived', 'archivedscans',
                  'documents', 'agreement', 'amendment', 'unassigned', 'known', 'store', 'stores', 'minnesota',
                  'group', 'services', 'center', 'shop', 'enterprises', 'holdings', 'properties'}


def _label_core(label: str) -> str:
    """A file-name label without spacing or filing words ("leasedocuments
    harborviewdance" and "harborviewdance scannedlease" ->
    "harborviewdance")."""
    out = []
    for tok in (label or '').lower().split():
        if tok in _FILING_WORDS:
            continue
        for w in _FILING_WORDS:          # a filing word glued to the name ("redkitepizzascannedlease")
            if tok != w and tok.endswith(w) and len(tok) - len(w) >= 4:
                tok = tok[:-len(w)]
            if tok != w and tok.startswith(w) and len(tok) - len(w) >= 4:
                tok = tok[len(w):]
        if tok not in _FILING_WORDS and tok not in out:      # "fraser fraser" -> "fraser"
            out.append(tok)
    return ''.join(out)


def _edits(a: str, b: str, cap: int) -> int:
    """Levenshtein distance, giving up past `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _same_label(a: str, b: str, same_tenant: bool = False) -> bool:
    """One name filed two ways. With same_tenant (both files name the same
    legal tenant) a label that is the start of the other also counts
    ("red kite" / "redkitepizza")."""
    ca, cb = _label_core(a), _label_core(b)
    if len(ca) < 5 or len(cb) < 5:
        return False
    if ca == cb:
        return True
    short, long_ = sorted((ca, cb), key=len)
    if same_tenant and len(short) >= 5 and long_.startswith(short):
        return True
    return len(short) >= 8 and _edits(ca, cb, 2) <= (1 if len(short) < 12 else 2)


def _merge_label_variants(groups: dict):
    """Merge 'fn:' groups whose labels are one name (see _same_label) and
    whose legal names do not conflict (they share one, or one has none)."""
    keys = sorted((k for k in groups if k.startswith('fn:')), key=lambda k: -len(groups[k].doc_ids))
    for i, k in enumerate(keys):
        if k not in groups:
            continue
        for k2 in keys[i + 1:]:
            if k2 not in groups:
                continue
            a, b = groups[k], groups[k2]
            ka, kb = {_key(x) for x in a.aliases}, {_key(x) for x in b.aliases}
            if not _same_label(a.label, b.label, same_tenant=bool(ka & kb)):
                continue
            if ka and kb and not ka & kb:
                continue                 # two legal tenants: two leases under one name
            a.doc_ids.extend(b.doc_ids)
            a.filenames.extend(b.filenames)
            a.aliases.extend(x for x in b.aliases if x not in a.aliases)
            del groups[k2]


def _pages(conn, doc_id) -> list:
    """A document's page texts, one entry per page of the FILE: entry n-1 is
    page n. Pages with no stored text (blank, or OCR found nothing) are ''
    so a page cite is the file's page, not a count of the readable ones."""
    try:
        rows = conn.execute(
            "SELECT CAST(page_number AS INTEGER), content FROM document_fulltext "
            "WHERE CAST(document_id AS INTEGER)=? "
            "ORDER BY CAST(page_number AS INTEGER)", (doc_id,)).fetchall()
    except sqlite3.Error:
        return []
    try:
        total = conn.execute('SELECT page_count FROM documents WHERE id = ?',
                             (doc_id,)).fetchone()
        total = int(total[0] or 0) if total else 0
    except (sqlite3.Error, TypeError, ValueError):
        total = 0
    by_no = {}
    for n, text in rows:
        if n is not None and n >= 1:
            by_no[n] = '\n'.join(filter(None, (by_no.get(n), text or '')))
    if not by_no:
        return [r[1] or '' for r in rows]
    return [by_no.get(n, '') for n in range(1, max(total, max(by_no)) + 1)]


def build_roster(conn: sqlite3.Connection, property_id: Optional[int] = None,
                 as_of: Optional[date] = None,
                 confirmed_commencements: Optional[dict] = None,
                 use_rent_roll: bool = True,
                 unassigned_only: bool = False,
                 rent_roll_terms: Optional[dict] = None) -> list[RosterRow]:
    """Tenancies for one property (or the whole DB when property_id is None;
    unassigned_only=True limits it to documents with no property).

    Commencement, the one input that places a relative rent schedule in
    time, comes from (best first): a user confirmation
    (confirmed_commencements = {RosterRow.key: date or ISO string}, see
    Database.get_lease_confirmations), an uploaded rent roll's lease start
    for that tenant, then the lease text itself.

    rent_roll_terms = {RosterRow.key: {'start': date, 'end': date}}: a rent
    roll's lease start / end matched to a tenancy outside this database (a
    roll the owner sent, keyed by the module door); used as an uploaded rent
    roll's would be, over it."""
    as_of = as_of or date.today()
    confirmed = {k: (v if isinstance(v, date) else parse_date(v))
                 for k, v in (confirmed_commencements or {}).items()}
    confirmed = {k: v for k, v in confirmed.items() if v}
    where, args = "WHERE document_type = 'lease'", []
    if unassigned_only:
        where += " AND property_id IS NULL"
    elif property_id is not None:
        where += " AND property_id = ?"
        args.append(property_id)
    docs = [dict(id=r[0], filename=r[1] or '') for r in conn.execute(
        f"SELECT id, filename FROM documents {where} ORDER BY id", args)]
    # uploaded but not yet run through Analyze: no terms extracted yet
    try:
        not_analyzed = {r[0] for r in conn.execute(
            f"SELECT id FROM documents {where} AND COALESCE(analysis_status, 'ingested') != 'analyzed'",
            args)}
    except sqlite3.Error:            # older DB without the column
        not_analyzed = set()
    terms = _terms(conn, [d['id'] for d in docs])

    # Words every file in this property shares ("EC", "Elm Court",
    # "Scanned") say nothing about WHICH tenancy a file belongs to.
    common = _common_filename_words([d['filename'] for d in docs])

    # 1. group by the tenancy words in the FILE NAME. Landlords file a
    #    tenancy's lease, amendments and assignments under one name ("EC
    #    Bean Coffee - 3rd Lease Amendment") even as the tenant's legal
    #    name changes through assignments (Bean Co LLC -> Roast Holdings ->
    #    Bean Coffee Inc) — so the file name follows the tenancy; the
    #    extracted tenant name does not. Validated on 9 pilot properties.
    # the property's own name ("Elm Court Plaza") — an extracted "tenant"
    # sharing 2+ of its words is the landlord / center, not a tenant
    prop_words = set()
    try:
        pq = "SELECT DISTINCT property_name FROM documents" + (
            " WHERE property_id IS NULL" if unassigned_only else
            " WHERE property_id = ?" if property_id is not None else "")
        for (pn,) in conn.execute(pq, [property_id] if property_id is not None
                                  and not unassigned_only else []):
            prop_words |= set(_tokens(pn))
    except sqlite3.Error:
        pass
    common = common | prop_words        # the property's own name is never tenancy words

    groups: dict[str, RosterRow] = {}
    loose = []
    alias_count: dict[str, int] = {}
    for d in docs:
        t = terms.get(d['id'], {})
        name = _clean_identity((t.get('tenant_identity') or t.get('tenant_name') or {}).get('raw'),
                               common, prop_words)
        if name:
            alias_count[name] = alias_count.get(name, 0) + 1
        stem = _filename_stem(d['filename'], common)
        if stem:
            k, how = 'fn:' + stem, 'filename'
        elif name:
            k, how = 'id:' + _key(name), 'identity'
        else:
            loose.append(d)
            continue
        row = groups.setdefault(k, RosterRow(tenant='', grouped_by=how, label=stem or ''))
        row.doc_ids.append(d['id'])
        row.filenames.append(d['filename'])
        if name and name not in row.aliases:
            row.aliases.append(name)

    # 2. files with neither: attach by a tenant name in the opening text —
    #    only when exactly ONE tenancy matches; otherwise their own row
    for d in loose:
        head = _alpha(_text(conn, d['id'], limit=3000))
        cands = [k for k, row in groups.items()
                 if any(_tokens(a) and all(tok in head for tok in _tokens(a)[:2])
                        for a in row.aliases)]
        if len(cands) == 1:
            groups[cands[0]].doc_ids.append(d['id'])
            groups[cands[0]].filenames.append(d['filename'])
        else:
            groups[f'unassigned:{d["id"]}'] = RosterRow(
                tenant=f'(unassigned) {d["filename"]}', doc_ids=[d['id']],
                filenames=[d['filename']], grouped_by='unassigned')

    # 2b. a file with no usable name in its filename but a legal name in its
    #     text (an assignment, an amendment scanned under a code) joins the
    #     tenancy whose documents carry that same legal name — only when
    #     exactly one does; otherwise it stays its own row
    for k in [k for k in groups if k.startswith('id:')]:
        row = groups[k]
        nt = _alpha(' '.join(row.aliases))
        cands = [k2 for k2, r2 in groups.items() if k2.startswith('fn:') and (
            any(_key(a) == k[3:] for a in r2.aliases)
            or (r2.label and all(w in nt for w in r2.label.split())))]
        if len(cands) == 1:
            tgt = groups[cands[0]]
            tgt.doc_ids.extend(row.doc_ids)
            tgt.filenames.extend(row.filenames)
            tgt.aliases.extend(a for a in row.aliases if a not in tgt.aliases)
            del groups[k]

    # 2c. one tenancy filed under label variants ("maplecrest" /
    #     "maple crest", "pinewoodbakery" / "pinewoodbakery fid", a typo'd
    #     "ostradner") is one tenancy: merge file-name groups whose labels
    #     are the same name once spacing and filing words are set aside,
    #     unless their documents name different legal tenants
    _merge_label_variants(groups)

    rent_roll = _rent_roll(conn, property_id, groups, unassigned_only) if use_rent_roll else {}
    for k, t in (rent_roll_terms or {}).items():
        t = {f: (v if isinstance(v, date) else parse_date(v)) for f, v in t.items() if f in ('start', 'end') and v}
        if any(t.values()):
            rent_roll[k] = {**rent_roll.get(k, {}), **{f: v for f, v in t.items() if v}}

    for k, row in groups.items():
        row.key = k
    for row in groups.values():
        if not row.tenant:
            # display: the legal name most documents agree on (ties -> the
            # later one, usually the assignee), else the file label
            if row.aliases:
                row.tenant = max(reversed(row.aliases), key=lambda a: alias_count.get(a, 0))
            else:
                row.tenant = row.label.title()
        # the legal name shares no word with the landlord's own file name
        # ("Rosa Lopez" filed as "Rosa's Tacos"): show the store name too,
        # as a d/b/a would ("Legal / Trade")
        if row.grouped_by == 'filename' and '/' not in row.tenant                 and not set(_tokens(row.tenant)) & set(_tokens(row.label)):
            names = [n for n in (_file_trade_name(f, row.label, common) for f in row.filenames) if n]
            trade = max(names, key=names.count) if names else None
            if trade and not set(_tokens(trade)) & set(_tokens(row.tenant))                     and _alpha(trade) not in _alpha(row.tenant) and _alpha(row.tenant) not in _alpha(trade):
                row.tenant = f'{row.tenant} / {trade}'

    # 3. per tenancy: facts + current rent over the whole lease chain
    out = []
    for k, row in groups.items():
        pairs = sorted(zip(row.doc_ids, row.filenames))
        row.doc_ids = [i for i, _ in pairs]
        row.filenames = [f for _, f in pairs]
        pages = [_pages(conn, i) for i in row.doc_ids]
        chain_text = '\n\n'.join('\n'.join(p) for p in pages)
        # rent evidence = the instruments: worksheets, the management
        # system's lease summaries and archived copies are left out of the
        # rent chain (their tables can't be placed and would block a high
        # answer, or an old lease's row would win)
        sel = assemble_rent_chain(list(zip(row.filenames, pages)))
        rent_text = sel.text
        kept_ids = [row.doc_ids[i] for i in sel.kept]
        ts = ([terms.get(i, {}) for i in kept_ids]
              + [terms.get(i, {}) for i in row.doc_ids if i not in kept_ids])

        def first(*types):
            for t in ts:
                for ty in types:
                    if t.get(ty) and t[ty].get('raw'):
                        return t[ty]
            return None

        sf = first('square_footage', 'square_feet', 'rentable_sf')
        if sf:
            v = sf.get('num') or _num(sf.get('raw'))
            v = v if v and 50 < v < 500_000 else None
            # a "square footage" that appears in the lease as a RENT amount is
            # a rent read as area (real: "annual minimum rent ... $64,491.00"
            # -> 64,491 SF). Only a rent context, or an implausibly big area,
            # counts: $1.00/SF charges legitimately equal the SF in dollars.
            if v:
                pat = r'\$\s*' + re.escape(f'{v:,.0f}') + r'(?:\.\d{2})?\b'
                for mm in re.finditer(pat, chain_text):
                    ctx = chain_text[max(0, mm.start() - 100):mm.start()].lower()
                    if 'rent' in ctx or v > 20_000:
                        row.flags.append('sf_is_a_rent_amount')
                        v = None
                        break
            row.square_feet = v
        # an expansion / relocation restates the premises later in the chain
        restated = _restated_sf(chain_text)
        if restated and restated != row.square_feet:
            row.square_feet = restated
            row.flags.append('sf_restated_by_amendment')
        comm = [parse_date(t['lease_commencement']['raw']) for t in ts
                if t.get('lease_commencement')]
        comm = [c for c in comm if c]
        row.commencement_guess = min(comm) if comm else None
        if confirmed.get(k):
            row.commencement, row.commencement_source = confirmed[k], 'confirmed'
        elif rent_roll.get(k, {}).get('start'):
            rr_start = rent_roll[k]['start']
            row.commencement, row.commencement_source = rr_start, 'rent_roll'
            row.commencement_guess = row.commencement_guess or rr_start
        elif comm:
            row.commencement, row.commencement_source = min(comm), 'lease'
        # per instrument: its governing expiration (lease + renewals rolled
        # forward), else the date it states — an extension amendment only
        # says "the Expiration Date shall be August 31, 2035" (lease_expiration).
        # A stated date already in the PAST is not used: later instruments in
        # the chain often extend the term without a date we could read (a 2nd
        # amendment's 2014 date, then a 3rd and 4th amendment), so it could
        # wrongly mark a live tenancy expired.
        exps = []
        for t in ts:
            g = t.get('governing_expiration')
            if g:
                exps.append(parse_date(g.get('exp') or g['raw']))
            elif t.get('lease_expiration'):
                s = parse_date(t['lease_expiration'].get('exp') or t['lease_expiration']['raw'])
                if s and s > as_of:
                    exps.append(s)
        exps = [e for e in exps if e]
        if not exps:
            # no expiration among the extracted terms: the instruments' own
            # stated term ends ("... continuing thereafter to and including
            # July 31, 2025", an amendment's "commencing February 1, 2022 and
            # expiring January 31, 2027"); the latest wins, a past one is left
            # out like a past stated date (extensions the file may not hold)
            for d in row.doc_ids:
                pages = _pages(conn, d)
                for ins in split_instruments(pages):
                    e = parse_date(instrument_term(pages, ins.page_from, ins.page_to).end)
                    if e and e > as_of:
                        exps.append(e)
        row.expiration_lease = max(exps) if exps else None
        row.expiration = row.expiration_lease
        row.expiration_source = 'lease' if row.expiration_lease else ''
        # the lease ends "the last day of the Nth full calendar month after the
        # Commencement Date" and the commencement was set later: a confirmed
        # commencement finishes the date (a rent roll's start can be a renewal
        # or a rent step; its own end date is used instead, below). A past one
        # is left out, like a past stated date (extensions the file may not hold).
        fm = first('term_full_months')
        if not row.expiration and fm and row.commencement_source == 'confirmed':
            n = _num(fm['raw'])
            end = _full_months_end(row.commencement, int(n)) if n else None
            if end and end > as_of:
                row.expiration, row.expiration_source = end, 'computed'
        # the rent roll is the landlord's current view: auto-renewals and
        # extensions that never reached paper. It wins for display / WALT /
        # expired status; a disagreement with the paper is flagged, not hidden
        rr_end = rent_roll.get(k, {}).get('end')
        if rr_end:
            row.expiration, row.expiration_source = rr_end, 'rent_roll'
            if row.expiration_lease and abs((rr_end - row.expiration_lease).days) > 31:
                row.flags.append('expiration_differs_from_lease')
        base = first('base_rent')
        esc = first('escalation_rate')
        r = derive_current_rent(
            as_of=as_of,
            year1_monthly=base['raw'] if base else None,
            escalation_pct=esc['raw'] if esc else None,
            commencement=row.commencement,
            expiration=row.expiration_lease,      # paper only — never the rent roll's
            schedule_text=rent_text,
            schedule_docs=sel.texts,
            commencement_is_term_start=row.commencement_source == 'rent_roll',
            term_end=row.expiration if row.expiration_source == 'rent_roll' else None,
            commencement_confirmed=row.commencement_source in ('confirmed', 'rent_roll'),
            square_feet=row.square_feet)
        row.monthly_rent, row.rent_method = r.monthly, r.method
        row.rent_confidence, row.rent_flags, row.rent_detail = r.confidence, list(r.flags), r.detail
        row.rent_flags.extend(sel.flags)
        # an amendment changed the premises (expansion / relocation): a rent
        # rolled forward from the ORIGINAL Year-1 rent no longer describes
        # them ("Base Rent shall be adjusted to reflect the expanded Premises")
        if 'sf_restated_by_amendment' in row.flags and r.method in ('escalated', 'flat'):
            row.rent_flags.append('premises_changed_since_year1')
            if row.rent_confidence == 'high':
                row.rent_confidence = 'medium'
        # plausibility: a shown rent must make sense for the space. > $80/SF/yr
        # (retail rarely exceeds it), or > $20k/mo with no SF to check, can't
        # be shown unreviewed — the classic error is an ANNUAL figure read as
        # monthly (a real row: $45,600 "monthly" = $3,800 x 12).
        m = row.monthly_rent
        if m and row.rent_confidence == 'high' and (
                (row.square_feet and m * 12 / row.square_feet > 80) or
                (not row.square_feet and m > 20_000)):
            row.rent_confidence = 'medium'
            row.rent_flags.append('rent_implausible_for_size')
        if rent_roll.get(k, {}).get('monthly'):
            _check_rent_roll(row, rent_roll[k]['monthly'], rent_text)
        # a document in the chain not analyzed yet may change the answer
        if not_analyzed & set(row.doc_ids):
            row.rent_flags.append('not_analyzed')
            if row.rent_confidence == 'high':
                row.rent_confidence = 'medium'
        row.kind = _kind(row, chain_text, as_of, common)
        row.needs = _needs(row)
        out.append(row)

    # the same SF on 3+ DIFFERENT tenants is a boilerplate number (e.g.
    # "within 500 feet" radius clauses), not their areas. One tenant's rows
    # (its current lease, an expired one, an archived copy) share their SF
    # and count once: rows are one tenant when their names share a word.
    def _name_words(row):
        names = [row.tenant, row.label or ''] + list(row.aliases)
        return {w for n in names for w in re.findall(r'[a-z]{4,}', (n or '').lower())
                if w not in _SF_NAME_NOISE}
    by_sf = {}
    for row in out:
        if row.square_feet:
            by_sf.setdefault(row.square_feet, []).append(_name_words(row))
    counts = {}
    for sf, groups in by_sf.items():
        tenants = []                       # merged word sets, one per distinct tenant
        for ws in groups:
            hit = [t for t in tenants if t & ws] if ws else []
            if hit:
                hit[0] |= ws
            else:
                tenants.append(set(ws))
        counts[sf] = len(tenants)
    for row in out:
        if row.square_feet and counts[row.square_feet] >= 3:
            row.flags.append('sf_repeated_across_tenants')
            row.square_feet = None

    order = {'tenancy': 0, 'expired': 1, 'other_agreement': 2, 'other_document': 3}
    out.sort(key=lambda x: (order.get(x.kind, 3), x.grouped_by == 'unassigned', x.tenant.lower()))
    return out


def _rent_roll(conn, property_id, groups, unassigned_only=False) -> dict:
    """{tenancy key: {'start': lease start, 'monthly': base rent}} from
    uploaded rent rolls, matched by the tenancy's file-label words or a
    legal-name alias — only when exactly one tenancy matches a rent-roll
    tenant. A tenancy on several suites (one lease, two spaces) gets the SUM
    of its suites' rents, from the latest rent roll only (older snapshots
    would double-count). Empty when no rent roll is loaded."""
    table = 'cur_rent_roll_entries'
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
    except sqlite3.Error:
        table = 'rent_roll_entries'
    q = (f"SELECT tenant_name, lease_start, monthly_rent, annual_rent, document_id, lease_end "
         f"FROM {table} WHERE tenant_name IS NOT NULL")
    args = []
    if unassigned_only:
        q += " AND property_id IS NULL"
    elif property_id is not None:
        q += " AND property_id = ?"
        args.append(property_id)
    try:
        rows = conn.execute(q, args).fetchall()
    except sqlite3.Error:
        return {}
    latest = max((r[4] for r in rows if r[4] is not None), default=None)
    out = {}
    for name, start, monthly, annual, doc_id, end in rows:
        nt = _alpha(name)
        if not nt:
            continue
        cands = [k for k, row in groups.items() if not k.startswith('unassigned') and (
            (row.label and all(w in nt for w in row.label.split())) or
            any(_tokens(a) and all(t in nt for t in _tokens(a)[:2]) for a in row.aliases))]
        if len(cands) != 1:
            continue
        e = out.setdefault(cands[0], {'start': None, 'monthly': None, 'end': None})
        d = parse_date(start)
        if d:
            e['start'] = min(e['start'], d) if e['start'] else d   # earliest = the lease's
        x = parse_date(end) if doc_id == latest else None
        if x:
            e['end'] = max(e['end'], x) if e['end'] else x         # latest suite end
        rent = monthly if monthly else (annual / 12 if annual else None)
        if rent and rent > 0 and doc_id == latest:
            e['monthly'] = round((e['monthly'] or 0) + rent, 2)
    return out


# The premises area as RESTATED later in the chain — expansions ("the Leased
# Premises shall thereafter contain approximately 15,026 square feet",
# "enlarging ... from 7,000 square feet to 10,360 square feet"), relocations
# ("the total rentable square feet of the New Premises is approximately
# 5,045"), amendment recitals ("Landlord is currently leasing to Tenant
# approximately 12,280 square feet") and estoppels ("Tenant is in possession
# of 12,280 square feet"). Numbers may be spelled out with the digits in
# brackets. Incidental areas (HVAC per 350 sq ft, signage criteria) don't
# match these forms.
_N = r'\(?\s*(\d{1,3}(?:,\d{3})+|\d{3,6})\s*\)?'
_SQF = r'\s*(?:rentable\s+|leasable\s+|usable\s+)?(?:ground\s+floor\s+)?square\s+f'
_W = r'[^.$]{0,120}?'
_RESTATED_SF = [re.compile(p, re.I) for p in (
    r'premises\s+shall\s+(?:thereafter\s+|then\s+|now\s+)?(?:contain|consist\s+of|be|include)\s+'
    + _W + _N + _SQF,
    r'(?:total|combined)\s+(?:rentable\s+|leasable\s+)?square\s+f(?:eet|oot(?:age)?)\s+(?:area\s+)?'
    r'of\s+the\s+(?:new\s+|leased\s+|combined\s+|relocat\w+\s+)?premises\s+(?:is|shall\s+be|being)\s+'
    + _W + _N,
    r'square\s+f(?:eet|oot)(?:\s+area)?\s+(?:in|of)\s+the\s+(?:new\s+|leased\s+|combined\s+)?'
    r'premises\s+being\s+' + _N,
    r'from\s+' + _N + r'\s*square\s+feet\s+to\s+' + _W + _N + r'\s*square\s+feet',
    r'(?:currently|presently)\s+leas(?:ing|es)\s+[^.$]{0,160}?' + _N + _SQF,
    r'tenant\s+is\s+in\s+possession\s+of\s+[^.$]{0,60}?' + _N + _SQF,
)]


@_text_memo
def _restated_sf(chain_text: str) -> Optional[float]:
    """The LAST premises restatement in the chain (later instrument = the
    current premises), or None.

    Two readers look: the lease reader's own premises scan (a relocation's
    "deliver to Tenant possession of that certain space containing N", a
    modification's "certain real property containing N", "shall thereafter
    contain" ...) and the roster's amendment wordings below. The one latest
    in the chain wins. Reading the text here, not only the stored term, means
    the roster shows the current premises even when the property's last
    analysis predates the reader that knows the wording."""
    flat = re.sub(r'\s+', ' ', chain_text or '')
    best = None
    for rx in _RESTATED_SF:
        for m in rx.finditer(flat):
            v = _num(m.group(m.lastindex))
            if v and 50 < v < 500_000 and (best is None or m.start() > best[0]):
                best = (m.start(), v)
    cands = []
    _sf_scan(flat, 0, 1, cands)
    if cands:
        pick = _sf_pick(cands)        # (score, value, page, restated, position)
        if pick[3] and (best is None or pick[4] > best[0]):
            best = (pick[4], float(pick[1]))
    return best[1] if best else None


def _within(a: float, b: float, tol: float = 0.02) -> bool:
    return bool(a and b) and abs(a - b) / b <= tol


def _check_rent_roll(row: 'RosterRow', rr_monthly: float, chain_text: str) -> None:
    """Paper vs rent roll, date-free (a rent roll is a snapshot of unknown
    date): agrees with the derived rent -> 'matches_rent_roll'; equals a
    different step of the lease's own schedule -> 'rent_roll_on_other_step'
    (a timing question); matches nothing on paper -> 'conflicts_with_rent_roll'.
    Either disagreement keeps the rent off the dashboard (never high)."""
    row.rent_roll_monthly = rr_monthly
    m = row.monthly_rent
    if not m:
        return
    if _within(m, rr_monthly):
        row.rent_flags.append('matches_rent_roll')
        # the SAME number to the dollar: we read the lease the way the
        # landlord bills it -> confirmed, whatever the paper alone could
        # prove (a flat Year-1 rent still in force, a schedule past the
        # stated expiration). Other open items stay on the row (_needs).
        if abs(m - rr_monthly) <= 1.0 and row.rent_confidence != 'high':
            row.rent_confidence = 'high'
            row.rent_flags.append('confirmed_by_rent_roll')
        return
    steps = [r.monthly for r in parse_dated_rows(chain_text) + parse_rent_schedule(chain_text)]
    row.rent_flags.append('rent_roll_on_other_step' if any(_within(v, rr_monthly) for v in steps)
                          else 'conflicts_with_rent_roll')
    if row.rent_confidence == 'high':
        row.rent_confidence = 'medium'


# ─── grouping helpers ─────────────────────────────────────────────

_DOC_WORDS = {
    'lease', 'leases', 'amendment', 'amendments', 'amend', 'agreement', 'agreements',
    'scanned', 'signed', 'executed', 'fully', 'final', 'draft', 'copy', 'pdf', 'doc', 'docx',
    'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth',
    'tenth', 'assignment', 'assumption', 'guaranty', 'guarantee', 'estoppel', 'snda',
    'subordination', 'extension', 'renewal', 'letter', 'notice', 'memorandum',
    'commencement', 'addendum', 'exhibit', 'rider', 'modification', 'the', 'and', 'for',
    'with', 'from', 'to', 'of', 'aka', 'fka', 'dba', 'suite', 'bay', 'unit', 'space',
    'premises', 'tenant', 'landlord', 'setting', 'term', 'order', 'waiver', 'consent',
    'sublease', 'covid', 'abatement', 'deferral', 'relief', 'january', 'february', 'march',
    'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
    'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec',
    # generic place words: part of a CENTER's name, never a tenancy's
    # ("Elm Court Shopping Center - Art Frame ..." vs "Elm Court - ...")
    'shopping', 'center', 'centre', 'plaza', 'mall', 'marketplace', 'commons',
    # the landlord's own paperwork filed beside the lease: a worksheet or a
    # summary named after the tenant must join that tenancy, not seed one
    'ners', 'ner', 'sheet', 'summary', 'summaries', 'checklist', 'checklists',
    'correspondence', 'misc', 'miscellaneous', 'docs', 'documents', 'files', 'file',
    'supporting', 'archived', 'archive', 'terminated', 'scans',
}


_FOLDER_WORDS = {'source', 'sources', 'documents', 'document', 'deliverables', 'scans', 'archived', 'summaries',
                 'summary', 'ners', 'files', 'file', 'misc', 'miscellaneous', 'supporting', 'harvest', 'part',
                 'module', 'zip', 'pdfs', 'scan', 'leasedocuments', 'leasefiles', 'leasing', 'checklists',
                 'property'}


def _drop_generic_folders(filename: str) -> str:
    """An imported file keeps its folder path joined by '__'
    ("05_source_documents__Elm Court - Rosa's Tacos Lease.pdf"). A leading
    folder that names no tenant (a numbered staging folder, "Scanned
    Leases", "Lease Summaries") is dropped; a folder that is the tenant
    ("LeaseDocuments__RosasTacos__...") stays."""
    parts = (filename or '').split('__')
    while len(parts) > 1:
        ws = [w.lower() for w in re.split(r'[^A-Za-z]+', parts[0]) if w]
        if re.match(r'^\d{1,3}[_\s-]', parts[0]) or all(w in _DOC_WORDS or w in _FOLDER_WORDS for w in ws):
            parts = parts[1:]
        else:
            break
    return '__'.join(parts)


def _fn_words(filename: str) -> list[str]:
    base = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', _drop_generic_folders(filename))
    return [w for w in (x.lower() for x in re.split(r'[^A-Za-z]+', base))
            if len(w) >= 3 and w not in _DOC_WORDS]


def _file_trade_name(filename: str, label: str, common: set) -> Optional[str]:
    """The store name as the file name spells it ("Elm Court - Rosa's Tacos
    Scanned Lease.pdf" -> "Rosa's Tacos"): from the tenancy label's words out
    to the property prefix, a " - " separator, a document word, a bracket or
    a number. None when the label's words aren't in this file name."""
    want = label.split()
    # uploads keep their names as given: "Elm_Court_-_Rosas_Tacos_Scanned_Lease.pdf"
    toks = re.sub(r'_+', ' ', re.sub(r'\.[A-Za-z0-9]{2,5}$', '', filename or '')).split()

    def words(t):
        return [w for w in re.split(r'[^a-z]+', t.lower()) if w]

    hits = [i for i, t in enumerate(toks) if set(words(t)) & set(want)]
    if not hits:
        return None

    def stops(t):
        ws = words(t)
        return (not ws and not re.fullmatch(r'[&+]', t)) or t[0] in '(['  \
            or any(c.isdigit() for c in t) or len(t) == 1 and t.isalpha() \
            or any(w in common or w in _DOC_WORDS - {'and'} for w in ws)

    a = b = hits[0]                     # the first mention only: "Pine Pets - EC-PP 4th ..."
    while a > 0 and not stops(toks[a - 1]):
        a -= 1
    while b + 1 < len(toks) and not stops(toks[b + 1]):
        b += 1
    name = ' '.join(toks[a:b + 1]).strip(' -&+')
    return name if len(_alpha(name)) >= 3 else None


def _common_filename_words(filenames: list[str]) -> set:
    """The property / portfolio PREFIX of a property's file names: the
    leading run of words that >= 90% of files start with (all of them when
    there are only 2) — "EC", "Elm Court", "Oak Square".

    Leading position, not mere frequency: a tenant word can sit in nearly
    every name of a small property ("Oak Square II Best Buy ..." x4 +
    "... Corner Liquor Best ...") without being the prefix."""
    lists = [_fn_words(f) for f in filenames]
    n = len(lists)
    if n < 2:
        return set()
    need = n if n < 3 else max(2, int(0.9 * n + 0.999))
    common, pos = set(), 0
    while pos < 5:
        at = {}
        for ws in lists:
            if len(ws) > pos:
                at[ws[pos]] = at.get(ws[pos], 0) + 1
        if not at:
            break
        w, c = max(at.items(), key=lambda kv: kv[1])
        if c < need:
            break
        common.add(w)
        pos += 1
    # a property CODE opening a good share of the files ("CPB - Bean Coffee
    # NERS", 3+ files and 30%): the landlord's own abbreviation, not a tenant
    codes = {}
    for f in filenames:
        base = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', _drop_generic_folders(f))
        m = re.match(r'([A-Z]{2,5})\s[-–]\s(\S+)', base)
        if m:
            codes.setdefault(m.group(1).lower(), []).append(m.group(2).lower())
    for code, rest in codes.items():
        # 3+ files, and the code is followed by different NAMES (a tenant's own
        # code is followed by its documents: "IBM - Lease", "IBM - Estoppel")
        names = {w for w in rest if w.strip('(),') not in _DOC_WORDS}
        if len(rest) >= 3 and len(names) >= 2:
            common.add(code)
    return common


_NAME_DASH = re.compile(r'\s[-–]\s')
# words a property's own name is spelt with in a file name, beside its prefix
# word ("Elm Court Office Centre-Building II - Bean Coffee (600)")
_PLACE_WORDS = {'office', 'building', 'bldg', 'tower', 'park', 'square', 'station', 'village', 'crossing',
                'north', 'south', 'east', 'west', 'i', 'ii', 'iii', 'iv', 'v', 'vi'}


def _after_property_prefix(base: str, common: set) -> str:
    """'Elm Court Office Centre-Building II - Bean Coffee (600)' or 'Elm
    Court II Bean Coffee 3-2-22': a name that OPENS with the property's
    prefix word, spelt out with place words and numerals, names the tenancy
    after that run (and after a ' - ' that closes it). A name whose first
    word is not the property's ('Bean Coffee - First Amendment') is left
    alone."""
    words = list(re.finditer(r'[A-Za-z]+', base))
    if not words or words[0].group(0).lower() not in common:
        return base
    end = 0
    for m in words:
        w = m.group(0).lower()
        if end and _NAME_DASH.search(base, end, m.start()):
            break                # the ' - ' closes the property's part of the name
        if w in common or w in _PLACE_WORDS or w in _DOC_WORDS or len(w) <= 2:
            end = m.end()
        else:
            break
    d = _NAME_DASH.match(base, end)
    return base[d.end() if d else end:]




def _filename_stem(filename: str, common: set) -> str:
    """The tenancy words of a file name: its first two distinctive words
    ('EC Bean Coffee - EC-BC 3rd Lease Amendment' -> 'bean coffee')."""
    base = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', _drop_generic_folders(filename))
    base = _after_property_prefix(base, common)
    ws = [w for w in (x.lower() for x in re.split(r'[^A-Za-z]+', base))
          if len(w) >= 3 and w not in _DOC_WORDS and w not in common]
    return ' '.join(ws[:2])


_BAD_IDENTITY = re.compile(
    r"(?i)d/b/a or|store brand|\bif st\b|\binsert\b|name of tenant|^tenant$|"
    r"licensee and|its affiliates|shopping center|limited partnership|\blandlord\b|"
    r"^associates\b|^(?:llc|inc|corp)\.?$")


def _clean_identity(name: Optional[str], common: set,
                    prop_words: Optional[set] = None) -> Optional[str]:
    """None for extracted 'tenant names' that are not a tenant: form
    placeholders echoed by the model, the landlord / center's own name,
    generic parties. (All seen on real pilot leases, 2026-09-29.)"""
    if not name:
        return None
    n = name.strip()
    if len(_alpha(n)) < 3 or _BAD_IDENTITY.search(n):
        return None
    toks = _tokens(n)
    if toks and all(t in common for t in toks):
        return None                     # "ELM COURT STATION" at Elm Court Station
    if prop_words and len(set(toks) & prop_words) >= 2:
        return None                     # "Elm Court Plz ..." at Elm Court Plaza
    return n


# ─── what a row is ────────────────────────────────────────────────

_OTHER_AGREEMENT = re.compile(
    r'(?i)\b(?:license agreement|easement|billboard|outdoor advertising|right of entry|'
    r'access agreement|telecommunications (?:license|easement|agreement)|antenna|'
    r'reciprocal easement|declaration of)\b')
_NOT_LEASE_FILE = re.compile(
    r'(?i)\b(?:defaults?|site ?plans?|service contracts?|environmental|budgets?|onedrive|'
    r'certificates? of insurance|coi|w-?9|invoices?|surveys?|title (?:commitment|policy)|appraisals?|'
    r'property tax|tax statements?|loan|closing|management (?:and leasing )?agreement|'
    r'(?:property )?condition assessments?|rules (?:and|&) reg(?:ulation)?s|square foot adjustments?|'
    r'inspections?|blueprints?|site assessments?|bay master|safety (?:reports?|checklists?)|certified mail|'
    r'listing agreement|floor plans?|plumbing|drawings?|warranty|contact info|account numbers|contact list|'
    r'lease expirations|fire sprinkler|hvac|(?:ir|infrared) (?:electrical )?scans?|right of refusal|vacancy|'
    r'water meter|pay app|licensing|bylaws|declaration|articles|elevator|aerial site map|meeting|quotation|settlement|'
    r'transfer of ownership|emergency (?:telephone|procedures?))\b')
_PROPERTY_FOLDER = re.compile(r'(?i)(?:^|__)(?:property (?:files?|documents?|records?|info(?:rmation)?)|pafs?|'
                              r'(?:\w+ - )?association documents?|service agreements?|cad)__')
_CARRIERS = re.compile(
    r'(?i)\b(?:charter communications|comcast|centurylink|century link|mediacom|'
    r'clear channel|sprint spectrum|at&t|verizon wireless|tds telecommunications)\b')


def _building_report(filename: str, common: set) -> bool:
    """'Tenant Contact List - Elm Court Office Center II': the building's own
    report, named the other way round from a tenant's file ('Elm Court -
    Bean Coffee'): the words AFTER the last ' - ' are the property's."""
    base = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', filename.split('__')[-1])
    parts = re.split(r'\s[-–]\s', base)
    if len(parts) < 2:
        return False
    head = ' '.join(parts[:-1])
    tail = [w.lower() for w in re.split(r'[^A-Za-z]+', parts[-1]) if w]
    # the head names a REPORT, not a tenant ("Birch Books - Elm Court" is a deal
    # sheet filed tenant-first)
    hw = [w.lower() for w in re.split(r'[^A-Za-z]+', head) if w]
    report = bool(_NOT_LEASE_FILE.search(head)) or (hw and all(w in _DOC_WORDS or w in _REPORT_WORDS for w in hw))
    return report and bool(tail) and any(w in common for w in tail) and all(
        w in common or w in _PLACE_WORDS or w in _DOC_WORDS or len(w) <= 2 for w in tail)


_REPORT_WORDS = {'tenant', 'tenants', 'list', 'detail', 'details', 'summary', 'report', 'reports', 'inventory', 'riser',
                 'notice', 'numbers', 'filter', 'grouped', 'by', 'ownership', 'entity', 'manager', 'type', 'agent'}


def _kind(row: RosterRow, text: str, as_of: date, common: set = frozenset()) -> str:
    """tenancy | other_agreement (telecom / billboard / easement / license —
    not an occupancy) | other_document (no lease: a default letter, a site
    plan, a service contract) | expired (paper ends > 90 days ago and no schedule
    covers today — a former tenant, or a renewal not in the file)."""
    # uploads keep their file names as given: "Tower_License_Agreement.pdf"
    files = re.sub(r'[_\s]+', ' ', ' '.join(row.filenames))
    names = ' '.join([row.tenant, row.label] + row.aliases + [files])
    if _CARRIERS.search(names) or _OTHER_AGREEMENT.search(text[:600]) \
            or _OTHER_AGREEMENT.search(files):
        return 'other_agreement'
    # papers filed in the property folder that are no lease at all (a default
    # letter, a site plan, a service contract, an environmental report): no
    # rent, area or date read from them, and every file named as such
    fnames = [re.sub(r'[_\s]+', ' ', f) for f in row.filenames]
    # the building's own paperwork filed in a property-level folder ("Scanned
    # Leases/Property Files/Elm Court - Watershed.pdf"): a tenancy only when a
    # file in it is named as a lease, or a legal tenant was read off its text
    if (row.filenames and all(_PROPERTY_FOLDER.search(f) or _building_report(f, common) for f in row.filenames)
            and not any(re.search(r'(?i)\blease\b|amendment|renewal|extension', f.split('__')[-1]) for f in row.filenames)
            and not row.aliases):
        return 'other_document'
    if fnames and all(_NOT_LEASE_FILE.search(f) for f in fnames) and (
            (not row.monthly_rent and not row.square_feet and not row.commencement and not row.expiration
             and not any(re.search(r'(?i)renewal|extension', f) for f in fnames))
            # every file is named as such and none as a lease: a figure or date read
            # off a condition assessment or a service contract is no tenancy's
            or not any(re.search(r'(?i)\blease\b|amendment|renewal|extension', f) for f in fnames)):
        return 'other_document'
    if (row.expiration and (as_of - row.expiration).days > 90
            and row.rent_method != 'schedule'):
        return 'expired'
    return 'tenancy'


def _needs(row: RosterRow) -> str:
    if row.kind in ('other_agreement', 'other_document'):
        return ''
    if row.kind == 'expired':
        return 'expired on paper — still occupying? add the renewal, or mark vacated'
    if row.grouped_by == 'unassigned':
        return 'assign this document to a tenant'
    f = set(row.rent_flags)
    if 'not_analyzed' in f:
        return 'not analyzed yet — run Analyze on the property page'
    # lease-status questions a confirmed rent doesn't answer
    if row.show_rent:
        if 'past_expiration' in f:
            return 'lease past its expiration on paper — add the renewal / holdover terms'
        return ''
    if 'conflicts_with_rent_roll' in f:
        return (f'reconcile: lease says ${row.monthly_rent:,.2f}/mo, '
                f'rent roll says ${row.rent_roll_monthly:,.2f}/mo')
    if 'rent_roll_on_other_step' in f:
        return (f'rent roll shows ${row.rent_roll_monthly:,.2f}/mo — another step of the '
                f'lease schedule; check which applies today')
    if 'premises_changed_since_year1' in f:
        return ('premises changed by amendment — the rent shown is from the original '
                'premises; confirm the current rent')
    if 'option_period_assumed' in f:
        return ('the scheduled term has ended — the renewal-option rent is shown; '
                'confirm the option was exercised, or add the renewal')
    if 'later_schedule_unplaced' in f:
        return ("a later amendment's rent table has no start date — "
                "check which rent applies today")
    if 'past_expiration' in f:
        return 'lease past its expiration on paper — add the renewal / holdover terms'
    if 'schedule_ends_before_date' in f:
        return 'rent schedule ends before today — a later amendment is missing'
    if 'after_schedule_end' in f:
        return ('the rent schedule on paper has ended — the last scheduled rent is shown; '
                'add the renewal or extension')
    if 'lump_sum_term' in f:
        return f'seasonal lease: {row.rent_detail} — not a monthly rent'
    if 'summaries_only' in f:
        return ('only rent worksheets / lease summaries on file — add the signed '
                'lease and its amendments')
    if 'no_rent_found' in f:
        return 'no rent schedule found — add the rent exhibit or amendment'
    if not row.commencement or 'no_commencement' in f or row.commencement_source == 'lease':
        return 'confirm the commencement date'
    return 'review the derived rent'


def portfolio_summary(db, as_of: Optional[date] = None) -> Optional[dict]:
    """Roster KPIs across every property with lease documents — what the
    dashboard shows when no units / rent roll exist. `db` is a
    database.Database (its connection + lease confirmations). None when the
    org has no leases."""
    as_of = as_of or date.today()
    pids = [r[0] for r in db.conn.execute(
        "SELECT DISTINCT property_id FROM documents WHERE document_type = 'lease'")]
    if not pids:
        return None
    names = {r[0]: r[1] for r in db.conn.execute("SELECT id, name FROM properties")}
    tot = {'tenancies': 0, 'leased_sf': 0, 'sf_known_for': 0, 'monthly_rent_confirmed': 0.0,
           'rent_confirmed_for': 0, 'needs_review': 0, 'needs_commencement': 0,
           'expiring_12mo': 0, 'properties': []}
    for pid in pids:
        # pid None = the documents not yet matched to a property (not the whole DB)
        rows = build_roster(db.conn, pid, as_of,
                            confirmed_commencements=db.get_lease_confirmations(pid),
                            unassigned_only=pid is None)
        s = summarize(rows, as_of)
        for k in ('tenancies', 'leased_sf', 'sf_known_for', 'rent_confirmed_for', 'expiring_12mo'):
            tot[k] += s[k]
        tot['monthly_rent_confirmed'] += s['monthly_rent_confirmed']
        tot['needs_review'] += len(s['needs_review'])
        n_comm = sum(1 for _, need in s['needs_review'] if need.startswith('confirm the commencement'))
        tot['needs_commencement'] += n_comm
        tot['properties'].append({'property_id': pid, 'name': names.get(pid, 'Unassigned documents'),
                                  'tenancies': s['tenancies'], 'needs_review': len(s['needs_review']),
                                  'needs_commencement': n_comm})
    tot['monthly_rent_confirmed'] = round(tot['monthly_rent_confirmed'], 2)
    # The dashboard links to properties[0] under the "just need a commencement
    # date" message, so lead with the property where a date unlocks the most.
    tot['properties'].sort(key=lambda p: (p['property_id'] is None,
                                          -p['needs_commencement'], -p['needs_review']))
    return tot


def summarize(rows: list[RosterRow], as_of: Optional[date] = None) -> dict:
    """Dashboard KPIs from a roster: only what the data supports."""
    as_of = as_of or date.today()
    ten = [r for r in rows if r.grouped_by != 'unassigned' and r.kind == 'tenancy']
    sf_known = [r for r in ten if r.square_feet]
    leased_sf = sum(r.square_feet for r in sf_known)
    rent_rows = [r for r in ten if r.show_rent]
    walt_rows = [r for r in sf_known if r.expiration and r.expiration > as_of]
    walt = (sum(r.square_feet * (r.expiration - as_of).days / 365.25 for r in walt_rows)
            / sum(r.square_feet for r in walt_rows)) if walt_rows else None
    return {
        'tenancies': len(ten),
        'leased_sf': round(leased_sf),
        'sf_known_for': len(sf_known),
        'walt_years': round(walt, 1) if walt is not None else None,
        'monthly_rent_confirmed': round(sum(r.monthly_rent for r in rent_rows), 2),
        'rent_confirmed_for': len(rent_rows),
        # expired leases have their own count; this matches the tenancies figure
        'needs_review': [(r.tenant, r.needs) for r in rows if r.needs and r.kind != 'expired'],
        'expiring_12mo': sum(1 for r in ten if r.expiration and 0 <= (r.expiration - as_of).days <= 365),
        'expired_on_paper': sum(1 for r in rows if r.kind == 'expired'),
        'other_agreements': sum(1 for r in rows if r.kind == 'other_agreement'),
    }
