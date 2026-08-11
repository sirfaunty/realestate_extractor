"""
Refi-package data assembly — everything a refinance diligence packet needs
for one property, read-only from the KA master + rent-roll module.

Two-key discipline: the loan layer keys facilities by EITHER the composite
property key (ENGELS-2010) or the bare entity code (1603) — always join on
both. Lease rollover is crossed against loan maturity; lender-relevant
lease provisions are selected by aggregator refi flags first, then by
category (SNDA, assignment, termination, co-tenancy, exclusives), labeled
by which net caught them.
"""

import re
import logging
from datetime import date

from .engine import master, _rent_roll_for

logger = logging.getLogger(__name__)

# categories a lender's counsel reads first, when no explicit refi flag
LENDER_CATEGORY_RX = re.compile(
    r'(?i)snda|subordination|estoppel|assign|sublet|termination right|'
    r'co.?tenancy|exclusive|kick.?out|purchase option|rofr|first refusal|'
    r'renewal|extension option|guaranty')


def _refi_flagged(refi):
    r = (refi or '').strip()
    return bool(r) and r.lower() not in ('none', 'n/a', 'no', '-')


def loan_lease_properties():
    """Properties carrying BOTH a lease layer and loan facilities."""
    m = master()
    if m is None:
        return []
    rows = m.execute("""
        SELECT dp.property_key AS property_id, dp.property_name AS name,
               (SELECT count(*) FROM loan_facility lf
                WHERE lf.property_key IN (dp.property_key, dp.entity_code)) AS facilities,
               (SELECT count(DISTINCT tenant_key) FROM lease_provision lp
                WHERE lp.property_id = dp.property_key) AS tenancies
        FROM dim_property dp
        WHERE facilities > 0 AND tenancies > 0
        ORDER BY dp.property_name""").fetchall()
    return [dict(r) for r in rows]


def _norm_date(s):
    if not s:
        return None
    s = str(s)
    mm = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if mm:
        return date(int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))
    mm = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if mm:
        return date(int(mm.group(3)), int(mm.group(1)), int(mm.group(2)))
    return None


def refi_data(property_id):
    m = master()
    if m is None:
        raise RuntimeError('KA master warehouse not found')
    prop = m.execute(
        "SELECT property_key, entity_code, property_name, owning_entity, fund, "
        "product_type, lender, property_manager FROM dim_property "
        "WHERE property_key=?", (property_id,)).fetchone()
    prop = dict(prop) if prop else {'property_key': property_id,
                                    'property_name': property_id}
    keys = tuple({property_id, prop.get('entity_code') or property_id})

    # ── facilities + balances + balloons + collateral ──
    facilities = []
    for f in m.execute(
            f"SELECT * FROM loan_facility WHERE property_key IN "
            f"({','.join('?' * len(keys))}) ORDER BY loan_status, maturity_date",
            keys):
        f = dict(f)
        fid = f['facility_id']
        bal = m.execute(
            "SELECT asof_date, balance, basis FROM loan_balance_asof "
            "WHERE facility_id=? AND balance IS NOT NULL "
            "ORDER BY asof_date DESC LIMIT 1", (fid,)).fetchone()
        f['balance'] = dict(bal) if bal else None
        blln = m.execute(
            "SELECT maturity_date, balloon_balance, basis FROM loan_balloon "
            "WHERE facility_id=? LIMIT 1", (fid,)).fetchone()
        f['balloon'] = dict(blln) if blln else None
        f['collateral'] = [dict(r) for r in m.execute(
            "SELECT * FROM loan_collateral WHERE facility_id=?", (fid,))]
        facilities.append(f)

    # ── loan document provisions, grouped by category ──
    loan_provs = {}
    for r in m.execute(
            f"SELECT category, why_it_matters, kind, evidence, source_file "
            f"FROM loan_abstract_provision WHERE property_key IN "
            f"({','.join('?' * len(keys))}) ORDER BY category", keys):
        loan_provs.setdefault(r['category'] or 'other', []).append(dict(r))

    # ── open items ──
    open_items = [dict(r) for r in m.execute(
        f"SELECT workstream, item, why, priority FROM loan_open_item "
        f"WHERE property_key IN ({','.join('?' * len(keys))}) "
        f"ORDER BY priority", keys)]

    # ── lease side: roster + lender-relevant provisions ──
    rr, snap = _rent_roll_for(property_id)
    current_maturities = [
        _norm_date(f.get('maturity_date')) for f in facilities
        if (f.get('loan_status') or '').lower() == 'current']
    current_maturities = [d for d in current_maturities if d]
    earliest_maturity = min(current_maturities) if current_maturities else None

    tenancies = []
    for t in m.execute(
            "SELECT tenant_key, trade_name, legal_tenant, status, sf, suite, "
            "expiration FROM lease_lease WHERE property_id=? "
            "ORDER BY trade_name", (property_id,)):
        t = dict(t)
        exp = _norm_date(t.get('expiration'))
        t['expires_before_maturity'] = (
            bool(exp and earliest_maturity and exp <= earliest_maturity))
        flagged, category_hits = [], []
        for p in m.execute(
                "SELECT category, detail, refi_impact, source, source_pages "
                "FROM lease_provision WHERE property_id=? AND tenant_key=?",
                (property_id, t['tenant_key'])):
            p = dict(p)
            if _refi_flagged(p['refi_impact']):
                flagged.append(p)
            elif LENDER_CATEGORY_RX.search(p['category'] or ''):
                category_hits.append(p)
        t['refi_provisions'] = flagged
        t['lender_provisions'] = category_hits
        tenancies.append(t)

    occupied = [t for t in tenancies
                if (t.get('status') or '').lower() in
                ('current', 'active', 'occupied')]
    rollover_before = [t for t in occupied if t['expires_before_maturity']]

    return {
        'property': prop,
        'facilities': facilities,
        'loan_provisions': loan_provs,
        'open_items': open_items,
        'tenancies': tenancies,
        'occupied_count': len(occupied),
        'rollover_before_maturity': rollover_before,
        'earliest_current_maturity': (earliest_maturity.isoformat()
                                      if earliest_maturity else None),
        'rent_roll_snapshot': snap,
    }
