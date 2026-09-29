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

Read-only. Rent method + accuracy measured in
portfolio_ownership/re_import/rent_tie_out.py (2026-09-29: schedule-derived
high-confidence answers 23/24 within 2% of Landlord's MRI rent roll).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .extractors.rent_derivation import (
    derive_current_rent, parse_date, _num)

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
    grouped_by: str = 'identity'    # identity | filename | text | unassigned

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
    try:
        rows = conn.execute(
            "SELECT content FROM document_fulltext WHERE CAST(document_id AS INTEGER)=? "
            "ORDER BY CAST(page_number AS INTEGER)", (doc_id,)).fetchall()
    except sqlite3.Error:
        return ''
    t = '\n'.join(r[0] or '' for r in rows)
    return t[:limit] if limit else t


def build_roster(conn: sqlite3.Connection, property_id: Optional[int] = None,
                 as_of: Optional[date] = None,
                 confirmed_commencements: Optional[dict] = None) -> list[RosterRow]:
    """Tenancies for one property (or the whole DB when property_id is None).

    confirmed_commencements: {tenant_key: date} a user confirmed — the one
    input that unlocks relative rent schedules (see RosterRow.needs)."""
    as_of = as_of or date.today()
    confirmed = confirmed_commencements or {}
    where, args = "WHERE document_type = 'lease'", []
    if property_id is not None:
        where += " AND property_id = ?"
        args.append(property_id)
    docs = [dict(id=r[0], filename=r[1] or '') for r in conn.execute(
        f"SELECT id, filename FROM documents {where} ORDER BY id", args)]
    terms = _terms(conn, [d['id'] for d in docs])

    # 1. tenancies anchored by an extracted tenant identity
    groups: dict[str, RosterRow] = {}
    loose = []
    for d in docs:
        t = terms.get(d['id'], {})
        name = (t.get('tenant_identity') or t.get('tenant_name') or {}).get('raw')
        k = _key(name) if name else ''
        if k:
            row = groups.setdefault(k, RosterRow(tenant=name.strip()))
            row.doc_ids.append(d['id'])
            row.filenames.append(d['filename'])
        else:
            loose.append(d)

    # 2. attach amendments / letters by a distinctive token in the filename,
    #    then in the opening text — only when exactly ONE tenancy matches
    for d in loose:
        fn = _alpha(d['filename'])
        cands = [k for k, row in groups.items()
                 if any(tok in fn for tok in _tokens(row.tenant))]
        how = 'filename'
        if len(cands) != 1:
            head = _alpha(_text(conn, d['id'], limit=3000))
            cands = [k for k, row in groups.items()
                     if _tokens(row.tenant) and all(tok in head for tok in _tokens(row.tenant)[:2])]
            how = 'text'
        if len(cands) == 1:
            row = groups[cands[0]]
            row.doc_ids.append(d['id'])
            row.filenames.append(d['filename'])
            if row.grouped_by == 'identity':
                row.grouped_by = f'identity+{how}'
        else:
            k = f'unassigned:{d["id"]}'
            groups[k] = RosterRow(tenant=f'(unassigned) {d["filename"]}', doc_ids=[d['id']],
                                  filenames=[d['filename']], grouped_by='unassigned')

    # 3. per tenancy: facts + current rent over the whole lease chain
    out = []
    for k, row in groups.items():
        row.doc_ids.sort()
        ts = [terms.get(i, {}) for i in row.doc_ids]

        def first(*types):
            for t in ts:
                for ty in types:
                    if t.get(ty) and t[ty].get('raw'):
                        return t[ty]
            return None

        sf = first('square_footage', 'square_feet', 'rentable_sf')
        if sf:
            v = sf.get('num') or _num(sf.get('raw'))
            row.square_feet = v if v and 50 < v < 500_000 else None
        comm = [parse_date(t['lease_commencement']['raw']) for t in ts
                if t.get('lease_commencement')]
        comm = [c for c in comm if c]
        row.commencement = confirmed.get(k) or (min(comm) if comm else None)
        exps = [parse_date(t['governing_expiration'].get('exp') or t['governing_expiration']['raw'])
                for t in ts if t.get('governing_expiration')]
        exps = [e for e in exps if e]
        row.expiration = max(exps) if exps else None
        base = first('base_rent')
        esc = first('escalation_rate')
        r = derive_current_rent(
            as_of=as_of,
            year1_monthly=base['raw'] if base else None,
            escalation_pct=esc['raw'] if esc else None,
            commencement=row.commencement,
            expiration=row.expiration,
            schedule_text='\n\n'.join(_text(conn, i) for i in row.doc_ids))
        row.monthly_rent, row.rent_method = r.monthly, r.method
        row.rent_confidence, row.rent_flags, row.rent_detail = r.confidence, r.flags, r.detail
        row.needs = _needs(row)
        out.append(row)
    out.sort(key=lambda x: (x.grouped_by == 'unassigned', x.tenant.lower()))
    return out


def _needs(row: RosterRow) -> str:
    if row.grouped_by == 'unassigned':
        return 'assign this document to a tenant'
    if row.show_rent:
        return ''
    f = set(row.rent_flags)
    if 'past_expiration' in f:
        return 'lease past its expiration on paper — add the renewal / holdover terms'
    if 'schedule_ends_before_date' in f:
        return 'rent schedule ends before today — a later amendment is missing'
    if 'no_rent_found' in f:
        return 'no rent schedule found — add the rent exhibit or amendment'
    if not row.commencement or 'no_commencement' in f:
        return 'confirm the commencement date'
    return 'review the derived rent'


def summarize(rows: list[RosterRow], as_of: Optional[date] = None) -> dict:
    """Dashboard KPIs from a roster: only what the data supports."""
    as_of = as_of or date.today()
    ten = [r for r in rows if r.grouped_by != 'unassigned']
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
        'needs_review': [(r.tenant, r.needs) for r in rows if r.needs],
        'expiring_12mo': sum(1 for r in ten if r.expiration and 0 <= (r.expiration - as_of).days <= 365),
    }
