"""
Office Inventory Engine — reads pre-built Z-scores from DuckDB.

Provides query APIs for the web layer: property listing, detail,
tenant data, peer health, statistics, Z-score distributions,
geo panel time series, and search.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import duckdb

logger = logging.getLogger(__name__)

# 8 measures scored by the Z-engine
MEASURES = [
    'rent_sf_yr_mid',
    'vacancy_pct_sf_derived',
    'parking_ratio',
    'stories',
    'typ_floor_sf',
    'taxes_per_sf',
    'tenant_occ_sf_floor',
    'tenant_sector_hhi',
]

# 7 co-equal peer lenses
LENSES = [
    'status',
    'constr',
    'tenancy',
    'sectype',
    'submarket',
    'ownerocc',
    'sectorcon',
]

# Human-readable labels
MEASURE_LABELS = {
    'rent_sf_yr_mid': 'Rent $/SF/Yr',
    'vacancy_pct_sf_derived': 'Vacancy %',
    'parking_ratio': 'Parking Ratio',
    'stories': 'Stories',
    'typ_floor_sf': 'Typical Floor SF',
    'taxes_per_sf': 'Taxes $/SF',
    'tenant_occ_sf_floor': 'Tenant Occ SF/Floor',
    'tenant_sector_hhi': 'Sector HHI',
}

LENS_LABELS = {
    'status': 'Building Status',
    'constr': 'Construction',
    'tenancy': 'Tenancy',
    'sectype': 'Secondary Type',
    'submarket': 'Submarket',
    'ownerocc': 'Owner-Occupied',
    'sectorcon': 'Sector Concentration',
}

# Columns to SELECT for property list views (avoid SELECT *)
LIST_COLS = [
    'pid',
    '"Property Name"',
    '"Property Address"',
    '"Building Class"',
    '"Building Status"',
    'quality_tier',
    'rent_mid',
    'vac_pct_derived',
    '"RBA"',
    '"Number of Stories"',
    '"Submarket Name"',
    '"City"',
    '"Tenancy"',
    '"Year Built"',
    '"Percent Leased"',
]

# Columns for detail view (includes all list cols + more)
DETAIL_COLS = LIST_COLS + [
    '"Property Type"',
    '"Secondary Type"',
    '"Star Rating"',
    '"Energy Star"',
    '"LEED Certified"',
    '"Total Available Space (SF)"',
    '"Rent/SF/Yr"',
    '"Market Name"',
    '"Submarket Cluster"',
    '"State"',
    '"Zip"',
    '"County Name"',
    '"Year Renovated"',
    '"Typical Floor Size"',
    '"Parking Ratio"',
    '"Last Sale Date"',
    '"Last Sale Price"',
    '"Latitude"',
    '"Longitude"',
]


def _default_db_path():
    """Resolve default DuckDB path relative to project root."""
    # Walk up from this file to find project root
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(here))
    return os.path.join(project_root, 'data', 'office_inventory.duckdb')


class OfficeEngine:
    """Query engine for office inventory DuckDB."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        self._con = None

    @property
    def con(self):
        if self._con is None:
            if not os.path.exists(self.db_path):
                raise FileNotFoundError(
                    'Office DuckDB not found at ' + self.db_path)
            self._con = duckdb.connect(self.db_path, read_only=True)
            logger.info('Connected to office DuckDB: %s', self.db_path)
        return self._con

    def _query(self, sql: str, params=None) -> List[Dict]:
        """Execute SQL and return list of dicts."""
        try:
            if params:
                result = self.con.execute(sql, params)
            else:
                result = self.con.execute(sql)
            cols = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.error('Office query failed: %s — %s', sql[:200], e)
            return []

    def _query_one(self, sql: str, params=None) -> Optional[Dict]:
        """Execute SQL and return single dict or None."""
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ─── Property List ───────────────────────────────────────────────

    def get_properties(self, filters: Optional[Dict] = None,
                       sort: str = 'pid', order: str = 'asc',
                       limit: int = 100, offset: int = 0,
                       search: Optional[str] = None,
                       measure: str = 'rent_sf_yr_mid',
                       lens: str = 'status') -> Dict:
        """Query office_property with optional filters. Returns dict with rows and total."""
        # Build z-score column name for the selected measure/lens
        z_col = 'z__' + measure + '__' + lens
        n_col = 'n__' + measure + '__' + lens
        collapsed_col = 'collapsed__' + measure + '__' + lens

        select_cols = ', '.join(LIST_COLS)
        select_cols += ', "' + z_col + '" AS z_score'
        select_cols += ', "' + n_col + '" AS peer_n'
        select_cols += ', "' + collapsed_col + '" AS collapsed'

        where_clauses = []
        where_params = []

        if filters:
            if filters.get('building_class'):
                where_clauses.append('"Building Class" = ?')
                where_params.append(filters['building_class'])
            if filters.get('submarket'):
                where_clauses.append('"Submarket Name" = ?')
                where_params.append(filters['submarket'])
            if filters.get('building_status'):
                where_clauses.append('"Building Status" = ?')
                where_params.append(filters['building_status'])

        if search:
            where_clauses.append(
                '("Property Name" ILIKE ? OR "Property Address" ILIKE ?)')
            where_params.extend(['%' + search + '%', '%' + search + '%'])

        where_sql = ''
        if where_clauses:
            where_sql = 'WHERE ' + ' AND '.join(where_clauses)

        # Validate sort column
        allowed_sorts = {
            'pid': 'pid',
            'name': '"Property Name"',
            'address': '"Property Address"',
            'class': '"Building Class"',
            'submarket': '"Submarket Name"',
            'stories': '"Number of Stories"',
            'rba': '"RBA"',
            'rent': 'rent_mid',
            'vacancy': 'vac_pct_derived',
            'z_score': '"' + z_col + '"',
        }
        sort_col = allowed_sorts.get(sort, 'pid')
        sort_dir = 'DESC' if order.upper() == 'DESC' else 'ASC'
        # Put nulls last
        nulls = 'NULLS LAST' if sort_dir == 'ASC' else 'NULLS LAST'

        # Count total
        count_sql = 'SELECT COUNT(*) AS cnt FROM office_property ' + where_sql
        total_row = self._query_one(count_sql, where_params)
        total = total_row['cnt'] if total_row else 0

        # Fetch page
        sql = (
            'SELECT ' + select_cols
            + ' FROM office_property '
            + where_sql
            + ' ORDER BY ' + sort_col + ' ' + sort_dir + ' ' + nulls
            + ' LIMIT ? OFFSET ?'
        )
        rows = self._query(sql, where_params + [limit, offset])

        # Clean up None/NaN for JSON
        for row in rows:
            for k, v in row.items():
                if v is not None:
                    try:
                        import math
                        if math.isnan(v):
                            row[k] = None
                    except (TypeError, ValueError):
                        pass

        return {'rows': rows, 'total': total}

    # ─── Single Property ─────────────────────────────────────────────

    def get_property(self, pid: int) -> Optional[Dict]:
        """Get single property with all Z-scores, tenant rollup, demo summary."""
        # Detail columns
        select_cols = ', '.join(DETAIL_COLS)

        # Add all z/n/collapsed columns
        z_parts = []
        for m in MEASURES:
            for l in LENSES:
                z_parts.append('"z__' + m + '__' + l + '"')
                z_parts.append('"n__' + m + '__' + l + '"')
                z_parts.append('"collapsed__' + m + '__' + l + '"')
        select_cols += ', ' + ', '.join(z_parts)

        sql = 'SELECT ' + select_cols + ' FROM office_property WHERE pid = ?'
        prop = self._query_one(sql, [pid])
        if not prop:
            return None

        # Clean NaN
        import math
        for k, v in list(prop.items()):
            if v is not None:
                try:
                    if math.isnan(v):
                        prop[k] = None
                except (TypeError, ValueError):
                    pass

        # Structure z-scores into nested dict
        z_scores = {}
        for m in MEASURES:
            z_scores[m] = {}
            for l in LENSES:
                z_key = 'z__' + m + '__' + l
                n_key = 'n__' + m + '__' + l
                c_key = 'collapsed__' + m + '__' + l
                z_scores[m][l] = {
                    'z': prop.pop(z_key, None),
                    'n': prop.pop(n_key, None),
                    'collapsed': prop.pop(c_key, None),
                }
        prop['z_scores'] = z_scores

        # Tenant rollup
        rollup = self._query_one(
            'SELECT * FROM office_tenant_rollup WHERE pid = ?', [pid])
        prop['tenant_rollup'] = rollup

        # Demographic summary (top 5 by absolute z)
        demo = self._query(
            'SELECT demo_col, vintage_year, vintage_kind, value, z_demo, peer_n '
            'FROM office_demographic_z WHERE pid = ? '
            'ORDER BY ABS(z_demo) DESC LIMIT 10', [pid])
        prop['demographics'] = demo

        return prop

    # ─── Peer Health ─────────────────────────────────────────────────

    def get_peer_health(self) -> List[Dict]:
        """Return peer cell health table."""
        return self._query(
            'SELECT lens, quality_tier, lens_value, n, engine_stable '
            'FROM office_peer_health ORDER BY lens, quality_tier, lens_value')

    # ─── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Summary statistics for the office inventory."""
        stats = {}

        # Total properties
        row = self._query_one('SELECT COUNT(*) AS cnt FROM office_property')
        stats['total'] = row['cnt'] if row else 0

        # By class
        class_rows = self._query(
            'SELECT "Building Class" AS cls, COUNT(*) AS cnt '
            'FROM office_property GROUP BY "Building Class" ORDER BY cls')
        stats['by_class'] = {
            r['cls'] or 'Unknown': r['cnt'] for r in class_rows}

        # By submarket (top 15)
        sub_rows = self._query(
            'SELECT "Submarket Name" AS sub, COUNT(*) AS cnt '
            'FROM office_property GROUP BY "Submarket Name" '
            'ORDER BY cnt DESC LIMIT 15')
        stats['by_submarket'] = {
            r['sub'] or 'Unknown': r['cnt'] for r in sub_rows}

        # By status
        status_rows = self._query(
            'SELECT "Building Status" AS st, COUNT(*) AS cnt '
            'FROM office_property GROUP BY "Building Status" ORDER BY st')
        stats['by_status'] = {
            r['st'] or 'Unknown': r['cnt'] for r in status_rows}

        # Avg rent
        rent_row = self._query_one(
            'SELECT AVG(rent_mid) AS avg_rent FROM office_property '
            'WHERE rent_mid IS NOT NULL')
        stats['avg_rent'] = round(rent_row['avg_rent'], 2) if rent_row and rent_row['avg_rent'] else None

        # Avg vacancy
        vac_row = self._query_one(
            'SELECT AVG(vac_pct_derived) AS avg_vac FROM office_property '
            'WHERE vac_pct_derived IS NOT NULL')
        stats['avg_vacancy'] = round(vac_row['avg_vac'], 2) if vac_row and vac_row['avg_vac'] else None

        # Tenant match rate
        tenant_row = self._query_one(
            'SELECT COUNT(*) AS cnt FROM office_tenant_rollup')
        stats['tenant_matched'] = tenant_row['cnt'] if tenant_row else 0
        if stats['total'] > 0:
            stats['tenant_match_rate'] = round(
                100.0 * stats['tenant_matched'] / stats['total'], 1)
        else:
            stats['tenant_match_rate'] = 0

        # Distinct submarkets
        sub_count = self._query_one(
            'SELECT COUNT(DISTINCT "Submarket Name") AS cnt FROM office_property')
        stats['submarket_count'] = sub_count['cnt'] if sub_count else 0

        # Filter options for the UI
        stats['filter_options'] = {
            'building_classes': sorted(
                [r['cls'] for r in class_rows if r['cls']]),
            'submarkets': sorted(
                [r['sub'] for r in self._query(
                    'SELECT DISTINCT "Submarket Name" AS sub '
                    'FROM office_property WHERE "Submarket Name" IS NOT NULL '
                    'ORDER BY sub')]
            ),
            'building_statuses': sorted(
                [r['st'] for r in status_rows if r['st']]),
        }

        return stats

    # ─── Z Distribution ──────────────────────────────────────────────

    def get_z_distribution(self, measure: str = 'rent_sf_yr_mid',
                           lens: str = 'status',
                           building_class: Optional[str] = None) -> Dict:
        """Z-score distribution for a specific measure/lens."""
        if measure not in MEASURES or lens not in LENSES:
            return {'error': 'Invalid measure or lens'}

        z_col = 'z__' + measure + '__' + lens

        where = 'WHERE "' + z_col + '" IS NOT NULL'
        params = []
        if building_class:
            where += ' AND "Building Class" = ?'
            params.append(building_class)

        # Histogram buckets
        sql = (
            'SELECT '
            'COUNT(*) AS cnt, '
            'AVG("' + z_col + '") AS mean, '
            'STDDEV("' + z_col + '") AS stddev, '
            'MIN("' + z_col + '") AS min_z, '
            'MAX("' + z_col + '") AS max_z, '
            'PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY "' + z_col + '") AS p25, '
            'PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY "' + z_col + '") AS p50, '
            'PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY "' + z_col + '") AS p75 '
            'FROM office_property ' + where
        )
        summary = self._query_one(sql, params)

        # Histogram bins
        bin_sql = (
            'SELECT '
            'FLOOR("' + z_col + '" * 2) / 2.0 AS bin, '
            'COUNT(*) AS cnt '
            'FROM office_property ' + where + ' '
            'GROUP BY bin ORDER BY bin'
        )
        bins = self._query(bin_sql, params)

        import math
        if summary:
            for k, v in list(summary.items()):
                if v is not None:
                    try:
                        if math.isnan(v):
                            summary[k] = None
                    except (TypeError, ValueError):
                        pass

        return {
            'measure': measure,
            'lens': lens,
            'building_class': building_class,
            'summary': summary,
            'bins': bins,
        }

    # ─── Tenants ─────────────────────────────────────────────────────

    def get_tenants(self, pid: int) -> List[Dict]:
        """Get tenants for a specific property."""
        return self._query(
            'SELECT "Tenant Name", "SF Occupied", "Floor", "Space Use", '
            '"Industry", "Employees", "Occupancy Type", '
            '"Rent/SF/year" AS rent_sf, "% of Building" AS pct_building '
            'FROM office_tenant WHERE pid = ? '
            'ORDER BY "SF Occupied" DESC NULLS LAST',
            [pid])

    # ─── Geo Panel ───────────────────────────────────────────────────

    def get_geo_panel(self, measure: Optional[str] = None,
                      source: Optional[str] = None,
                      limit: int = 500) -> List[Dict]:
        """Time series from the geo panel table."""
        where_parts = []
        params = []
        if measure:
            where_parts.append('measure = ?')
            params.append(measure)
        if source:
            where_parts.append('source = ?')
            params.append(source)

        where_sql = ''
        if where_parts:
            where_sql = 'WHERE ' + ' AND '.join(where_parts)

        sql = (
            'SELECT source, geo_level, geo_name, period, measure, value, unit '
            'FROM office_geo_panel '
            + where_sql
            + ' ORDER BY period DESC LIMIT ?'
        )
        params.append(limit)
        return self._query(sql, params)

    # ─── Search ──────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> List[Dict]:
        """Search by property name or address."""
        select_cols = ', '.join(LIST_COLS)
        sql = (
            'SELECT ' + select_cols
            + ' FROM office_property '
            'WHERE "Property Name" ILIKE ? OR "Property Address" ILIKE ? '
            'ORDER BY "RBA" DESC NULLS LAST LIMIT ?'
        )
        pattern = '%' + query + '%'
        return self._query(sql, [pattern, pattern, limit])
