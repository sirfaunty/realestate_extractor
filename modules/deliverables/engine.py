"""
Deliverables Engine — assembles deliverable-ready data from the verified
KA portfolio warehouse plus the canonical module DBs (rent roll).

Read-only everywhere: the master is maintained exclusively by the
aggregation workflow; the rent-roll module DB is canonical per its README.
Per-thread connections keyed on file mtime (same pattern as the
portfolio_ownership engine) so completed merges appear without a restart.
"""

import os
import re
import sqlite3
import threading
import logging

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MASTER_CANDIDATES = [
    os.path.join(_REPO_ROOT, 'portfolio_ownership',
                 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db'),
    os.path.join(_REPO_ROOT, 'portfolio_ownership', 'portfolio_warehouse.db'),
]
OPS_CANDIDATES = [
    os.path.join(_REPO_ROOT, 'portfolio_ownership', 'inbox',
                 'Portfolio Financial Source Data & Modules',
                 'Financial Modules', 'Final Portfolio Rent Roll Module_7.10.26',
                 'database', 'portfolio_rentroll.db'),
    # flat fallback (Mac/sample exports drop the db here)
    os.path.join(_REPO_ROOT, 'portfolio_ownership', 'portfolio_rentroll.db'),
]

OUTPUT_DIR = os.path.join(_REPO_ROOT, 'data', 'deliverables')

_local = threading.local()


def _master_path():
    for p in MASTER_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _conn(path, attr):
    """Per-thread read-only connection keyed on file mtime."""
    if not path or not os.path.exists(path):
        return None
    key = (path, os.path.getmtime(path))
    cache = getattr(_local, attr, None)
    if cache and cache[0] == key:
        return cache[1]
    conn = sqlite3.connect(f'file:{path.replace(os.sep, "/")}?mode=ro',
                           uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    setattr(_local, attr, (key, conn))
    return conn


def master():
    return _conn(_master_path(), '_master')


def ops():
    for p in OPS_CANDIDATES:
        if os.path.exists(p):
            return _conn(p, '_ops')
    return None


def lease_properties():
    """Properties with a lease layer: [{property_id, name, tenancies, provisions}]."""
    m = master()
    if m is None:
        return []
    rows = m.execute("""
        SELECT lp.property_id,
               COALESCE(dp.property_name, lp.property_id) AS name,
               COUNT(DISTINCT lp.tenant_key) AS tenancies,
               COUNT(*) AS provisions
        FROM lease_provision lp
        LEFT JOIN dim_property dp ON dp.property_key = lp.property_id
        GROUP BY lp.property_id ORDER BY name
    """).fetchall()
    return [dict(r) for r in rows]


def _rent_roll_for(property_id):
    """{occupant_alpha: row} for the property's latest snapshot, plus meta."""
    o = ops()
    if o is None:
        return {}, None
    snap = o.execute("SELECT MAX(snapshot_date) FROM rent_roll WHERE property_key=?",
                     (property_id,)).fetchone()[0]
    if not snap:
        return {}, None
    out = {}
    for r in o.execute(
            "SELECT occupant, suite, expiration, sqft, monthly_base_rent, "
            "rate_psf, source_file, source_page FROM rent_roll "
            "WHERE property_key=? AND snapshot_date=?", (property_id, snap)):
        occ = (r['occupant'] or '').strip()
        if occ and occ.lower() != 'vacant':
            out[re.sub(r'[^a-z]', '', occ.lower())] = dict(r)
    return out, snap


def _match_rr(rr, tenant_key, trade_name):
    """Map a master tenancy to its rent-roll row by name tokens."""
    tk = re.sub(r'[^a-z]', '', (tenant_key or '').lower())
    toks = [re.sub(r'[^a-z]', '', w.lower())
            for w in re.split(r'[^A-Za-z]+', trade_name or '') if len(w) >= 4]
    shorts = [re.sub(r'[^a-z]', '', w.lower())
              for w in re.split(r'[^A-Za-z]+', trade_name or '')
              if 2 <= len(w) <= 3 and w.lower() not in ('the', 'inc', 'llc', 'of')]
    for occ_a, row in rr.items():
        if tk and tk in occ_a:
            return row
        if toks and all(t in occ_a for t in toks[:2]):
            return row
        if shorts and any(re.search(rf'\b{s}\b', (row['occupant'] or '').lower())
                          for s in shorts):
            return row
    return None


_CAT_ART = re.compile(r'^(Article|Section)\s+(\d{1,3})\s*:\s*(.*)$')


def _category_sort_key(cat):
    """Article/Section-form categories numerically first, then sweeps A-Z."""
    m = _CAT_ART.match(cat or '')
    if m:
        return (0 if m.group(1) == 'Article' else 1, int(m.group(2)), cat)
    return (2, 0, (cat or '').lower())


def compendium_data(property_id):
    """Everything the compendium builder needs, in render order."""
    m = master()
    if m is None:
        raise RuntimeError('KA master warehouse not found')
    prop = m.execute(
        "SELECT property_key, entity_code, property_name, owning_entity, fund, "
        "product_type, property_manager FROM dim_property WHERE property_key=?",
        (property_id,)).fetchone()
    prop = dict(prop) if prop else {'property_key': property_id,
                                    'property_name': property_id}

    rr, snap = _rent_roll_for(property_id)

    tenancies = []
    for t in m.execute(
            "SELECT tenant_key, trade_name, legal_tenant, status, sf, suite, "
            "expiration, use_type, guaranty, renewal_options, security_deposit, "
            "base_rent_monthly, notes FROM lease_lease WHERE property_id=? "
            "ORDER BY CASE WHEN LOWER(COALESCE(status,'')) IN "
            "('current','active','occupied') THEN 0 ELSE 1 END, trade_name",
            (property_id,)):
        t = dict(t)
        t['rent_roll'] = _match_rr(rr, t['tenant_key'], t['trade_name'])
        provs = [dict(r) for r in m.execute(
            "SELECT category, detail, refi_impact, source, source_pages "
            "FROM lease_provision WHERE property_id=? AND tenant_key=? ",
            (property_id, t['tenant_key']))]
        provs.sort(key=lambda p: _category_sort_key(p['category']))
        t['provisions'] = provs
        t['refi_count'] = sum(1 for p in provs
                              if (p['refi_impact'] or '').strip()
                              and (p['refi_impact'] or '').strip().lower()
                              not in ('none', 'n/a', 'no', '-'))
        tenancies.append(t)

    return {'property': prop, 'tenancies': tenancies,
            'rent_roll_snapshot': snap,
            'provision_total': sum(len(t['provisions']) for t in tenancies)}
