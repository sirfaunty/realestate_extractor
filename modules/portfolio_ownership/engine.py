"""
Portfolio Ownership Engine — read-only access to the KA portfolio warehouse.

The master (portfolio_warehouse.db) is maintained EXCLUSIVELY by the
aggregation workflow (merge harnesses + 214-check verify suite). This engine
never writes. Connections are per-thread (SQLite thread affinity) and keyed
on the file's mtime, so a completed merge shows up in the UI on the next
request without an app restart.
"""

import os
import re
import sqlite3
import threading
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DB_CANDIDATES = [
    os.path.join(_REPO_ROOT, 'portfolio_ownership',
                 'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db'),
    os.path.join(_REPO_ROOT, 'portfolio_ownership', 'portfolio_warehouse.db'),
]

# Lease status normalization (display grouping only — raw status is preserved)
_STATUS_GROUPS = {
    'current': {'current', 'active', 'occupied'},
    'former': {'former', 'terminated', 'expired'},
    'license': {'license'},
    'pipeline': {'development', 'proposed/loi', 'prospective', 'subtenant',
                 'seasonal_temp', 'litigation'},
    'vacant': {'vacant'},
}
STATUS_GROUP_ORDER = ['current', 'license', 'pipeline', 'vacant', 'former', 'other']


def status_group(raw) -> str:
    s = (raw or '').strip().lower()
    for grp, members in _STATUS_GROUPS.items():
        if s in members:
            return grp
    return 'other'


def severity_group(raw) -> str:
    """Normalize the open-item severity vocabulary for display."""
    s = (raw or '').strip().upper()
    if not s:
        return 'UNSPECIFIED'
    if s.startswith('MED'):
        return 'MED'
    if s in ('HIGH', 'WARN', 'LOW', 'INFO', 'DEFECT', 'BLOCKER'):
        return s
    return s

SEVERITY_ORDER = ['BLOCKER', 'DEFECT', 'HIGH', 'MED', 'WARN', 'LOW', 'INFO', 'UNSPECIFIED']


def parse_lease_date(raw):
    """Parse M/D/YYYY (or ISO) strings; None on anything else."""
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class OwnershipEngine:
    """Read-only query layer over the KA portfolio warehouse."""

    def __init__(self, db_path: str = None):
        self._explicit_path = db_path
        self._local = threading.local()

    # ── Connection management ───────────────────────────────────────

    def db_path(self):
        if self._explicit_path:
            return self._explicit_path if os.path.exists(self._explicit_path) else None
        for p in DB_CANDIDATES:
            if os.path.exists(p):
                return p
        return None

    def available(self) -> bool:
        return self.db_path() is not None

    @property
    def con(self):
        path = self.db_path()
        if path is None:
            raise FileNotFoundError(
                'KA portfolio_warehouse.db not found under portfolio_ownership/')
        mtime = os.path.getmtime(path)
        cur = getattr(self._local, 'con', None)
        key = getattr(self._local, 'key', None)
        if cur is not None and key == (path, mtime):
            return cur
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only = ON')
        self._local.con = con
        self._local.key = (path, mtime)
        logger.info(f'Ownership engine connected (ro): {path}')
        return con

    def _rows(self, sql, params=()):
        return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def _one(self, sql, params=()):
        r = self.con.execute(sql, params).fetchone()
        return (dict(r) if r else None)

    def _val(self, sql, params=()):
        r = self.con.execute(sql, params).fetchone()
        return r[0] if r else None

    # ── Portfolio level ─────────────────────────────────────────────

    def portfolio_summary(self) -> dict:
        s = {
            'spine_properties': self._val('SELECT count(*) FROM dim_property'),
            'covered_properties': self._val(
                'SELECT count(DISTINCT property_id) FROM lease_provision'),
            'provisions': self._val('SELECT count(*) FROM lease_provision'),
            'leases': self._val('SELECT count(*) FROM lease_lease'),
            'prop_docs': self._val('SELECT count(*) FROM prop_document'),
            'open_items': self._val('SELECT count(*) FROM wh_open_item'),
        }
        sev = {}
        for r in self._rows('SELECT severity, count(*) n FROM wh_open_item GROUP BY severity'):
            g = severity_group(r['severity'])
            sev[g] = sev.get(g, 0) + r['n']
        s['open_high'] = sev.get('HIGH', 0) + sev.get('BLOCKER', 0) + sev.get('DEFECT', 0)
        s['open_by_severity'] = sev
        return s

    def properties(self):
        """Spine properties that have lease-layer coverage, with counts."""
        rows = self._rows("""
            SELECT p.property_id,
                   count(*) AS provisions,
                   count(DISTINCT p.tenant_key) AS tenant_keys
            FROM lease_provision p
            GROUP BY p.property_id
        """)
        lease_counts = {r['property_id']: r for r in self._rows("""
            SELECT property_id, count(*) AS leases,
                   sum(CASE WHEN lower(coalesce(status,'')) IN ('current','active','occupied')
                       THEN 1 ELSE 0 END) AS current_leases
            FROM lease_lease GROUP BY property_id
        """)}
        doc_counts = {r['property_id']: r['docs'] for r in self._rows(
            'SELECT property_id, count(*) AS docs FROM prop_document GROUP BY property_id')}
        spine = {r['property_key']: r for r in self._rows(
            'SELECT * FROM dim_property')}
        out = []
        for r in rows:
            pid = r['property_id']
            sp = spine.get(pid, {})
            lc = lease_counts.get(pid, {})
            out.append({
                'property_id': pid,
                'name': sp.get('property_name') or pid,
                'fund': sp.get('fund'),
                'product_type': sp.get('product_type'),
                'owning_entity': sp.get('owning_entity'),
                'lender': sp.get('lender'),
                'provisions': r['provisions'],
                'tenant_keys': r['tenant_keys'],
                'leases': lc.get('leases', 0),
                'current_leases': lc.get('current_leases', 0) or 0,
                'prop_docs': doc_counts.get(pid, 0),
                'on_spine': pid in spine,
            })
        out.sort(key=lambda x: -x['provisions'])
        return out

    # ── Property level ──────────────────────────────────────────────

    def property_info(self, property_id: str):
        info = self._one('SELECT * FROM dim_property WHERE property_key = ?', (property_id,))
        if info is None:
            # Off-spine keys used in the lease layer (e.g. CMX, STC)
            n = self._val('SELECT count(*) FROM lease_provision WHERE property_id = ?',
                          (property_id,))
            if not n:
                return None
            info = {'property_key': property_id, 'property_name': property_id}
        return info

    def leases(self, property_id: str):
        rows = self._rows("""
            SELECT lease_id, tenant_key, trade_name, legal_tenant, suite_id, suite,
                   status, sf, commencement, expiration, base_rent_monthly,
                   base_rent_psf, renewal_options, source, source_pages
            FROM lease_lease WHERE property_id = ? ORDER BY trade_name
        """, (property_id,))
        prov_counts = {r['tenant_key']: r['n'] for r in self._rows(
            'SELECT tenant_key, count(*) n FROM lease_provision WHERE property_id = ? '
            'GROUP BY tenant_key', (property_id,))}
        today = date.today()
        for r in rows:
            r['group'] = status_group(r['status'])
            r['provisions'] = prov_counts.get(r['tenant_key'], 0)
            exp = parse_lease_date(r['expiration'])
            r['exp_date'] = exp.isoformat() if exp else None
            r['months_to_exp'] = (
                round(((exp - today).days) / 30.4) if exp and r['group'] == 'current' else None)
        return rows

    def provision_categories(self, property_id: str, limit: int = 200):
        return self._rows("""
            SELECT category, count(*) n FROM lease_provision
            WHERE property_id = ? GROUP BY category ORDER BY n DESC LIMIT ?
        """, (property_id, limit))

    def tenants(self, property_id: str):
        return self._rows("""
            SELECT tenant_key, count(*) n,
                   (SELECT trade_name FROM lease_lease l
                     WHERE l.property_id = p.property_id AND l.tenant_key = p.tenant_key
                     LIMIT 1) AS trade_name
            FROM lease_provision p WHERE property_id = ?
            GROUP BY tenant_key ORDER BY n DESC
        """, (property_id,))

    def provisions(self, property_id: str, tenant_key: str = None, category: str = None,
                   q: str = None, limit: int = 50, offset: int = 0):
        where = ['property_id = ?']
        params = [property_id]
        if tenant_key:
            where.append('tenant_key = ?')
            params.append(tenant_key)
        if category:
            where.append('category = ?')
            params.append(category)
        if q:
            where.append('(detail LIKE ? OR category LIKE ?)')
            params.extend([f'%{q}%', f'%{q}%'])
        w = ' AND '.join(where)
        total = self._val(f'SELECT count(*) FROM lease_provision WHERE {w}', params)
        rows = self._rows(
            f'SELECT prov_id, tenant_key, category, detail, source, source_pages '
            f'FROM lease_provision WHERE {w} ORDER BY tenant_key, category '
            f'LIMIT ? OFFSET ?', params + [limit, offset])
        return rows, total

    def prop_documents(self, property_id: str):
        docs = self._rows("""
            SELECT doc_id, doc_type, title, recorded_ref, parties, effective_date, pages
            FROM prop_document WHERE property_id = ? ORDER BY doc_type, effective_date
        """, (property_id,))
        counts = {r['doc_id']: r['n'] for r in self._rows(
            'SELECT doc_id, count(*) n FROM prop_provision WHERE property_id = ? '
            'GROUP BY doc_id', (property_id,))}
        for d in docs:
            d['provisions'] = counts.get(d['doc_id'], 0)
        return docs

    def rollovers(self, months: int = 24):
        """Current leases expiring within N months, portfolio-wide."""
        rows = self._rows("""
            SELECT property_id, tenant_key, trade_name, suite_id, suite, sf,
                   expiration, base_rent_monthly, status
            FROM lease_lease
            WHERE lower(coalesce(status,'')) IN ('current','active','occupied')
              AND expiration IS NOT NULL
        """)
        today = date.today()
        out = []
        for r in rows:
            exp = parse_lease_date(r['expiration'])
            if exp is None:
                continue
            m = ((exp - today).days) / 30.4
            if -1 <= m <= months:
                r['exp_date'] = exp.isoformat()
                r['months_to_exp'] = round(m, 1)
                out.append(r)
        out.sort(key=lambda x: x['exp_date'])
        return out

    # ── Open items ──────────────────────────────────────────────────

    def open_items(self, module: str = None, severity: str = None, q: str = None,
                   limit: int = 100, offset: int = 0):
        where, params = [], []
        if module:
            where.append('module_id = ?')
            params.append(module)
        if q:
            where.append('(description LIKE ? OR category LIKE ? OR item_id LIKE ?)')
            params.extend([f'%{q}%', f'%{q}%', f'%{q}%'])
        w = ('WHERE ' + ' AND '.join(where)) if where else ''
        rows = self._rows(
            f'SELECT module_id, item_id, category, description, severity, source_table '
            f'FROM wh_open_item {w}', params)
        for r in rows:
            r['sev'] = severity_group(r['severity'])
        if severity:
            rows = [r for r in rows if r['sev'] == severity]
        total = len(rows)
        rows.sort(key=lambda r: (SEVERITY_ORDER.index(r['sev'])
                                 if r['sev'] in SEVERITY_ORDER else 99, r['module_id']))
        return rows[offset:offset + limit], total

    def open_item_facets(self):
        mods = self._rows(
            'SELECT module_id, count(*) n FROM wh_open_item GROUP BY module_id ORDER BY n DESC')
        sev = {}
        for r in self._rows('SELECT severity, count(*) n FROM wh_open_item GROUP BY severity'):
            g = severity_group(r['severity'])
            sev[g] = sev.get(g, 0) + r['n']
        sevs = [{'sev': k, 'n': sev[k]} for k in SEVERITY_ORDER if k in sev]
        return mods, sevs

    # ── Portfolio-wide search ───────────────────────────────────────

    def search_provisions(self, q: str, limit: int = 100):
        like = f'%{q}%'
        return self._rows("""
            SELECT property_id, tenant_key, category, detail, source, source_pages
            FROM lease_provision
            WHERE detail LIKE ? OR category LIKE ?
            ORDER BY property_id, tenant_key LIMIT ?
        """, (like, like, limit))

    # ── Financials (fin_ module: bare entity codes, FY2017–2026) ────

    def _spine_by_entity(self):
        return {r['entity_code']: r for r in self._rows(
            'SELECT entity_code, property_key, property_name, fund, product_type '
            'FROM dim_property WHERE entity_code IS NOT NULL')}

    def fin_properties(self):
        """66 fin properties: FY coverage, labeled rental revenue, latest occupancy."""
        spine = self._spine_by_entity()
        rows = self._rows("""
            SELECT property_id,
                   min(fiscal_year) fy_min, max(fiscal_year) fy_max,
                   count(DISTINCT fiscal_year) fy_n
            FROM fin_op_statement_line GROUP BY property_id
        """)
        # Rental revenue = categories explicitly labeled REVENUE/INCOME (transparent
        # rule — we do NOT compute NOI here; the account-class map is partial).
        rev = {(r['property_id'], r['fiscal_year']): r['amt'] for r in self._rows("""
            SELECT property_id, fiscal_year, sum(annual_total) amt
            FROM fin_op_statement_line
            WHERE upper(coalesce(category,'')) LIKE '%REVENUE%'
               OR upper(coalesce(category,'')) LIKE '%INCOME%'
            GROUP BY property_id, fiscal_year
        """)}
        occ = {}
        for r in self._rows("""
            SELECT property_id, report_date, occupied_pct FROM fin_rent_roll_summary
        """):
            d = parse_lease_date(r['report_date'])
            cur = occ.get(r['property_id'])
            if d and (cur is None or d > cur[0]):
                occ[r['property_id']] = (d, r['occupied_pct'])
        out = []
        for r in rows:
            code = r['property_id']
            sp = spine.get(code, {})
            latest_rev = rev.get((code, r['fy_max']))
            o = occ.get(code)
            out.append({
                'entity_code': code,
                'property_key': sp.get('property_key'),
                'name': sp.get('property_name') or code,
                'fund': sp.get('fund'),
                'fy_min': r['fy_min'], 'fy_max': r['fy_max'], 'fy_n': r['fy_n'],
                'latest_revenue': latest_rev,
                'occupied_pct': o[1] if o else None,
                'occ_asof': o[0].isoformat() if o else None,
            })
        out.sort(key=lambda x: -(x['latest_revenue'] or 0))
        return out

    def fin_property_matrix(self, entity_code: str):
        """Category × fiscal-year annual totals for one property (as recorded)."""
        rows = self._rows("""
            SELECT coalesce(category,'(uncategorized)') category, fiscal_year,
                   sum(annual_total) amt, count(*) n
            FROM fin_op_statement_line WHERE property_id = ?
            GROUP BY category, fiscal_year
        """, (entity_code,))
        years = sorted({r['fiscal_year'] for r in rows})
        cats = {}
        for r in rows:
            cats.setdefault(r['category'], {})[r['fiscal_year']] = r['amt']
        # order: revenue-ish first, then alpha
        def key(c):
            u = c.upper()
            return (0 if ('REVENUE' in u or 'INCOME' in u) else 1, c)
        matrix = [{'category': c, 'by_year': cats[c]} for c in sorted(cats, key=key)]
        occ = self._rows("""
            SELECT report_date, occupied_pct, occupied_sqft, vacant_sqft, total_sqft
            FROM fin_rent_roll_summary WHERE property_id = ? ORDER BY report_date
        """, (entity_code,))
        for o in occ:
            d = parse_lease_date(o['report_date'])
            o['sort'] = d.isoformat() if d else ''
        occ.sort(key=lambda o: o['sort'])
        return years, matrix, occ

    # ── Loans (loan_ module) ────────────────────────────────────────

    def loan_facilities(self):
        facs = self._rows("""
            SELECT facility_id, property_key, entity_code, property_name, lender,
                   borrower, is_cross_collateralized, collateral_property_count
            FROM loan_facility ORDER BY property_name
        """)
        def latest(table, val, datecol):
            best = {}
            for r in self._rows(f'SELECT facility_id, {val} v, {datecol} d FROM {table}'):
                d = parse_lease_date(r['d']) or (r['d'] and str(r['d']))
                cur = best.get(r['facility_id'])
                if cur is None or str(d) > str(cur[0]):
                    best[r['facility_id']] = (d, r['v'])
            return best
        bal = latest('loan_balance_asof', 'balance', 'asof_date')
        res = {}
        for r in self._rows("""
            SELECT facility_id, field, value_text, value_num FROM loan_term_resolution
        """):
            res.setdefault(r['facility_id'], {})[r['field']] = (
                r['value_num'] if r['value_num'] is not None else r['value_text'])
        facts = {}
        for r in self._rows('SELECT facility_id, field, value_text, value_num FROM loan_fact'):
            facts.setdefault(r['facility_id'], {}).setdefault(
                r['field'], r['value_num'] if r['value_num'] is not None else r['value_text'])
        docs = {r['facility_id']: r['n'] for r in self._rows("""
            SELECT f.facility_id, count(d.doc_id) n
            FROM loan_facility f LEFT JOIN loan_document d ON d.property_key = f.property_key
            GROUP BY f.facility_id
        """)}
        for f in facs:
            fid = f['facility_id']
            r = res.get(fid, {})
            ft = facts.get(fid, {})
            b = bal.get(fid)
            f['maturity'] = r.get('maturity_date') or ft.get('maturity_date')
            f['rate'] = r.get('interest_rate_pct') or ft.get('interest_rate_pct')
            f['original_principal'] = ft.get('original_principal')
            f['latest_balance'] = b[1] if b else None
            f['balance_asof'] = str(b[0]) if b else None
            f['docs'] = docs.get(fid, 0)
        return facs

    def loan_facility_detail(self, facility_id: str):
        fac = self._one('SELECT * FROM loan_facility WHERE facility_id = ?', (facility_id,))
        if not fac:
            return None
        fac['terms'] = self._rows("""
            SELECT field, value_text, value_num, source_doc_type, source_file, binding,
                   conflict, evidence
            FROM loan_term_resolution WHERE facility_id = ? ORDER BY field
        """, (facility_id,))
        fac['facts'] = self._rows("""
            SELECT field, value_text, value_num, confidence, evidence, source_file
            FROM loan_fact WHERE facility_id = ? ORDER BY field
        """, (facility_id,))
        fac['balances'] = self._rows("""
            SELECT asof_date, balance, basis, note FROM loan_balance_asof
            WHERE facility_id = ? ORDER BY asof_date
        """, (facility_id,))
        fac['balloon'] = self._rows(
            'SELECT maturity_date, balloon_balance, basis, note FROM loan_balloon '
            'WHERE facility_id = ?', (facility_id,))
        fac['provisions'] = self._rows("""
            SELECT category, why_it_matters, kind, evidence, doc_type, source_file
            FROM loan_abstract_provision WHERE facility_id = ? ORDER BY category
        """, (facility_id,))
        fac['amort'] = self._rows("""
            SELECT basis, rows_n, first_date, last_date, first_balance, last_balance,
                   reconciliation
            FROM loan_amort_source WHERE facility_id = ?
        """, (facility_id,))
        return fac

    # ── Vacancy (vac_ module: 2021 historical archive) ──────────────

    def vac_snapshot(self):
        """Suite-level vacancy is CURRENT (reports through 2026); the
        property-level summary table is a 2021-era rollup. Each uses its own
        latest report_date."""
        suite_latest = self._val('SELECT max(report_date) FROM vac_suite')
        suites = self._rows("""
            SELECT property, property_key, unit, vacant_sf, date_vacated, months_vacant,
                   carry_cost, quoted_rent, mtm_upcoming
            FROM vac_suite WHERE report_date = ?
            ORDER BY (months_vacant IS NULL), months_vacant DESC LIMIT 150
        """, (suite_latest,))
        summary_latest = self._val('SELECT max(report_date) FROM vac_summary_property')
        props = self._rows("""
            SELECT property, property_key, rentable_sf, rented_sf, vacant_sf, pct_vacant, logic
            FROM vac_summary_property WHERE report_date = ?
            ORDER BY vacant_sf DESC
        """, (summary_latest,))
        return suite_latest, summary_latest, props, suites
