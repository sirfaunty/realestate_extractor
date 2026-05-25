"""
Warehouse Engine — DuckDB-backed analytical data store.

This is the shared data access layer for all Capactive modules.
Modules never touch DuckDB directly; they use this API.

Usage:
    from warehouse.engine import WarehouseEngine

    wh = WarehouseEngine()
    wh.connect()

    # Register a data load
    ingestion_id = wh.register_ingestion(
        source='costar_inventory',
        source_vintage='Aug 2024',
        knowledge_date='2024-08-31',
    )

    # Query z-scores for a property
    scores = wh.get_property_zscores('1234567', peer_cut='Market x Size x Quality')

    # Get cap rates for a market
    caps = wh.get_cap_rates(market='Minneapolis', period_type='year')

    wh.close()
"""

import duckdb
import hashlib
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


def _safe_path(path: str) -> str:
    """Sanitize a file path for use in DuckDB SQL string literals.

    DuckDB's read_parquet()/read_csv() require a string literal —
    parameterised placeholders aren't supported for file paths.
    We validate and escape to prevent SQL injection.
    """
    resolved = os.path.realpath(path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Data file not found: {resolved}")
    # Escape single quotes for SQL string literal
    return resolved.replace("'", "''")

# Default warehouse path: data/warehouse.duckdb alongside org_dev.db
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'warehouse.duckdb'
)

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema.sql')


class WarehouseEngine:
    """DuckDB-backed analytical warehouse with bitemporal support."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.conn = None

    def connect(self):
        """Open connection and ensure schema exists."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = duckdb.connect(self.db_path)
        self._ensure_schema()
        logger.info(f"Warehouse connected: {self.db_path}")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _ensure_schema(self):
        """Create tables if they don't exist."""
        with open(_SCHEMA_PATH) as f:
            sql = f.read()
        # Strip comment-only lines, then split by semicolons
        lines = []
        for line in sql.split('\n'):
            stripped = line.strip()
            if stripped.startswith('--'):
                continue
            lines.append(line)
        cleaned = '\n'.join(lines)
        for stmt in cleaned.split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    self.conn.execute(stmt)
                except Exception as e:
                    # Skip errors from already-existing objects
                    if 'already exists' not in str(e).lower():
                        logger.warning(f"Schema statement warning: {e}")

    # ─── Zone A: Ingestion Provenance ───────────────────────────────

    def register_ingestion(self, source: str, knowledge_date: str,
                           source_vintage: str = None, file_hash: str = None,
                           file_path: str = None, record_count: int = None,
                           notes: str = None) -> int:
        """Register a data load and return the ingestion_id."""
        result = self.conn.execute("""
            INSERT INTO raw_ingestion_log
                (ingestion_id, source, source_vintage, knowledge_date,
                 file_hash, file_path, record_count, notes)
            VALUES (nextval('seq_ingestion_id'), ?, ?, ?, ?, ?, ?, ?)
            RETURNING ingestion_id
        """, [source, source_vintage, knowledge_date,
              file_hash, file_path, record_count, notes]).fetchone()
        ingestion_id = result[0]
        logger.info(f"Registered ingestion #{ingestion_id}: {source} ({source_vintage})")
        return ingestion_id

    def get_ingestion_log(self) -> List[Dict]:
        """Return all ingestion records."""
        rows = self.conn.execute(
            "SELECT * FROM raw_ingestion_log ORDER BY ingestion_id"
        ).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    # ─── Zone B: Property Dimension ─────────────────────────────────

    def upsert_property(self, property_id: str, **kwargs) -> int:
        """Insert or update a property in the dimension table.

        Returns the property_key.
        """
        # Check if exists
        existing = self.conn.execute(
            "SELECT property_key FROM dim_property WHERE property_id = ? AND valid_to = '9999-12-31'",
            [property_id]
        ).fetchone()

        if existing:
            # Update changed fields
            sets = []
            vals = []
            for k, v in kwargs.items():
                if v is not None:
                    sets.append(f"{k} = ?")
                    vals.append(v)
            if sets:
                vals.append(property_id)
                self.conn.execute(
                    f"UPDATE dim_property SET {', '.join(sets)} WHERE property_id = ? AND valid_to = '9999-12-31'",
                    vals
                )
            return existing[0]
        else:
            # Compute address hash for sales comp join
            addr = (kwargs.get('address') or '').lower().strip()
            city = (kwargs.get('city') or '').lower().strip()
            state = (kwargs.get('state') or '').lower().strip()
            addr_hash = hashlib.md5(f"{addr}{city}{state}".encode()).hexdigest() if addr else None

            result = self.conn.execute("""
                INSERT INTO dim_property
                    (property_key, property_id, address, city, state, zip,
                     market, submarket, submarket_cluster, lat, lon,
                     year_built, num_units, building_class, style,
                     property_name, address_hash, capactive_id)
                VALUES (nextval('seq_property_key'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?)
                RETURNING property_key
            """, [
                property_id,
                kwargs.get('address'), kwargs.get('city'), kwargs.get('state'),
                kwargs.get('zip'), kwargs.get('market'), kwargs.get('submarket'),
                kwargs.get('submarket_cluster'), kwargs.get('lat'), kwargs.get('lon'),
                kwargs.get('year_built'), kwargs.get('num_units'),
                kwargs.get('building_class'), kwargs.get('style'),
                kwargs.get('property_name'), addr_hash, kwargs.get('capactive_id'),
            ]).fetchone()
            return result[0]

    # ─── Zone B: Bulk Data Loading ──────────────────────────────────

    def load_inventory_parquet(self, parquet_path: str, knowledge_date: str,
                               source_vintage: str = 'Aug 2024') -> int:
        """Load the national inventory master parquet into dim_property.

        Returns count of properties loaded.
        """
        ingestion_id = self.register_ingestion(
            source='costar_inventory',
            source_vintage=source_vintage,
            knowledge_date=knowledge_date,
            file_path=parquet_path,
        )

        # Load directly from parquet into dim_property
        count = self.conn.execute(f"""
            INSERT INTO dim_property
                (property_key, property_id, address, city, state, zip,
                 market, submarket, property_name, year_built, num_units,
                 building_class, style, lat, lon, address_hash)
            SELECT
                nextval('seq_property_key'),
                CAST("PropertyID" AS VARCHAR),
                "Property Address",
                "City",
                "State",
                "Zip",
                "Market Name",
                "Submarket Name",
                "Property Name",
                TRY_CAST("Year Built" AS INTEGER),
                TRY_CAST("Number Of Units" AS INTEGER),
                "Building Class",
                "Style",
                TRY_CAST("Latitude" AS DOUBLE),
                TRY_CAST("Longitude" AS DOUBLE),
                md5(lower(coalesce("Property Address",'')) ||
                    lower(coalesce("City",'')) ||
                    lower(coalesce("State",'')))
            FROM read_parquet('{_safe_path(parquet_path)}')
            WHERE "PropertyID" IS NOT NULL
        """).fetchone()

        row_count = self.conn.execute("SELECT count(*) FROM dim_property").fetchone()[0]

        # Update ingestion record
        self.conn.execute(
            "UPDATE raw_ingestion_log SET record_count = ? WHERE ingestion_id = ?",
            [row_count, ingestion_id]
        )

        logger.info(f"Loaded {row_count} properties from inventory parquet")
        return row_count

    def load_zscore_parquet(self, parquet_path: str, knowledge_date: str,
                            ingestion_id: int = None) -> int:
        """Load a market's z-scores long parquet into fact_property_zscore."""
        if ingestion_id is None:
            ingestion_id = self.register_ingestion(
                source='zscore_engine',
                knowledge_date=knowledge_date,
                file_path=parquet_path,
            )

        self.conn.execute(f"""
            INSERT INTO fact_property_zscore
                (property_id, universe, peer_cut, view, peer_group_key,
                 metric, value, peer_mean, peer_std, peer_n, z_score,
                 knowledge_date, ingestion_id)
            SELECT
                CAST("PropertyID" AS VARCHAR),
                "Universe", "peer_cut", "view", "peer_group_key",
                "metric",
                TRY_CAST("value" AS DOUBLE),
                TRY_CAST("peer_mean" AS DOUBLE),
                TRY_CAST("peer_std" AS DOUBLE),
                TRY_CAST("peer_n" AS INTEGER),
                TRY_CAST("z_score" AS DOUBLE),
                '{knowledge_date}',
                {ingestion_id}
            FROM read_parquet('{_safe_path(parquet_path)}')
            WHERE "PropertyID" IS NOT NULL
        """)

        count = self.conn.execute(f"""
            SELECT count(*) FROM fact_property_zscore WHERE ingestion_id = {ingestion_id}
        """).fetchone()[0]

        logger.info(f"Loaded {count:,} z-score rows from {parquet_path}")
        return count

    def load_peer_stats_parquet(self, parquet_path: str, knowledge_date: str,
                                 ingestion_id: int = None) -> int:
        """Load peer group stats parquet."""
        if ingestion_id is None:
            ingestion_id = self.register_ingestion(
                source='zscore_engine_stats',
                knowledge_date=knowledge_date,
                file_path=parquet_path,
            )

        self.conn.execute(f"""
            INSERT INTO fact_peer_group_stats
                (universe, peer_cut, view, peer_group_key, metric,
                 peer_n, peer_mean, peer_std, knowledge_date, ingestion_id)
            SELECT
                "Universe", "peer_cut", "view", "peer_group_key", "metric",
                TRY_CAST("peer_n" AS INTEGER),
                TRY_CAST("peer_mean" AS DOUBLE),
                TRY_CAST("peer_std" AS DOUBLE),
                '{knowledge_date}',
                {ingestion_id}
            FROM read_parquet('{_safe_path(parquet_path)}')
        """)

        count = self.conn.execute(f"""
            SELECT count(*) FROM fact_peer_group_stats WHERE ingestion_id = {ingestion_id}
        """).fetchone()[0]

        logger.info(f"Loaded {count:,} peer stat rows from {parquet_path}")
        return count

    def load_sales_comps_csv(self, csv_path: str, knowledge_date: str) -> int:
        """Load sales comp transactions CSV into fact_sales_transaction."""
        ingestion_id = self.register_ingestion(
            source='costar_sales_comps',
            knowledge_date=knowledge_date,
            file_path=csv_path,
        )

        self.conn.execute(f"""
            INSERT INTO fact_sales_transaction
                (transaction_id, property_id, asset_class,
                 sale_date, sale_year, sale_quarter,
                 sale_price, cap_rate_actual, cap_rate_proforma,
                 price_per_unit, price_per_sf, num_units, year_built,
                 building_class, property_name, property_address,
                 city, state, market, submarket,
                 buyer_name, seller_name,
                 source_file, source_sheet, source_row,
                 knowledge_date, ingestion_id)
            SELECT
                "transaction_id",
                "property_id",
                "asset_class",
                TRY_CAST("sale_date" AS DATE),
                TRY_CAST("sale_year" AS INTEGER),
                "sale_quarter",
                TRY_CAST("sale_price" AS DOUBLE),
                TRY_CAST("actual_cap_rate" AS DOUBLE),
                TRY_CAST("pro_forma_cap_rate" AS DOUBLE),
                TRY_CAST("price_per_unit" AS DOUBLE),
                TRY_CAST("price_per_sf" AS DOUBLE),
                TRY_CAST("number_of_units" AS INTEGER),
                TRY_CAST("year_built" AS INTEGER),
                "building_class",
                "property_name",
                "property_address",
                "property_city",
                "property_state",
                "market",
                "submarket",
                "buyer_company",
                "seller_company",
                "source_file",
                "source_sheet",
                TRY_CAST("source_row" AS INTEGER),
                '{knowledge_date}',
                {ingestion_id}
            FROM read_csv('{_safe_path(csv_path)}', auto_detect=true, all_varchar=true)
        """)

        count = self.conn.execute(f"""
            SELECT count(*) FROM fact_sales_transaction WHERE ingestion_id = {ingestion_id}
        """).fetchone()[0]

        self.conn.execute(
            "UPDATE raw_ingestion_log SET record_count = ? WHERE ingestion_id = ?",
            [count, ingestion_id]
        )

        logger.info(f"Loaded {count:,} sales transactions")
        return count

    def load_cap_rate_csv(self, csv_path: str, knowledge_date: str,
                           granularity: str, period_type: str,
                           is_clean: bool = True) -> int:
        """Load cap rate aggregate CSV."""
        ingestion_id = self.register_ingestion(
            source='cap_rate_aggregator',
            knowledge_date=knowledge_date,
            file_path=csv_path,
        )

        # Determine period column name and build expressions
        period_col = 'sale_year' if period_type == 'year' else 'sale_quarter'
        market_expr = '"market"' if granularity != 'national' else 'NULL'
        class_expr = '"building_class"' if 'class' in granularity else 'NULL'
        period_expr = f'CAST("{period_col}" AS VARCHAR)'

        self.conn.execute(f"""
            INSERT INTO fact_cap_rate_aggregate
                (market, asset_class, period, period_type, granularity,
                 building_class, n_deals, cap_rate_median, cap_rate_mean,
                 cap_rate_std, cap_rate_p25, cap_rate_p75,
                 is_clean, knowledge_date, ingestion_id)
            SELECT
                {market_expr},
                "asset_class",
                {period_expr},
                '{period_type}',
                '{granularity}',
                {class_expr},
                TRY_CAST("n_deals" AS INTEGER),
                TRY_CAST("cap_rate_median" AS DOUBLE),
                TRY_CAST("cap_rate_mean" AS DOUBLE),
                TRY_CAST("cap_rate_std" AS DOUBLE),
                TRY_CAST("cap_rate_p25" AS DOUBLE),
                TRY_CAST("cap_rate_p75" AS DOUBLE),
                {is_clean},
                '{knowledge_date}',
                {ingestion_id}
            FROM read_csv('{_safe_path(csv_path)}', auto_detect=true, all_varchar=true)
        """)

        count = self.conn.execute(f"""
            SELECT count(*) FROM fact_cap_rate_aggregate WHERE ingestion_id = {ingestion_id}
        """).fetchone()[0]

        logger.info(f"Loaded {count:,} cap rate aggregate rows ({granularity}, {period_type})")
        return count

    def load_pricing_csv(self, csv_path: str, knowledge_date: str,
                          granularity: str) -> int:
        """Load pricing aggregate CSV."""
        ingestion_id = self.register_ingestion(
            source='pricing_comps',
            knowledge_date=knowledge_date,
            file_path=csv_path,
        )

        # Detect columns present
        cols = self.conn.execute(f"""
            SELECT * FROM read_csv('{_safe_path(csv_path)}', auto_detect=true) LIMIT 0
        """).description
        col_names = [c[0] for c in cols]

        has_class = 'building_class' in col_names
        has_vintage = 'vintage_bucket' in col_names
        has_ppsf = 'median_ppsf' in col_names or 'median_psf' in col_names
        ppsf_col = 'median_ppsf' if 'median_ppsf' in col_names else 'median_psf'
        has_market = 'market' in col_names

        # Build dynamic column expressions (avoid backslashes in f-strings)
        market_expr = '"market"' if has_market else 'NULL'
        class_expr = '"building_class"' if has_class else 'NULL'
        vintage_expr = '"vintage_bucket"' if has_vintage else 'NULL'
        ppsf_expr = f'TRY_CAST("{ppsf_col}" AS DOUBLE)' if has_ppsf else 'NULL'

        self.conn.execute(f"""
            INSERT INTO fact_pricing_aggregate
                (market, building_class, vintage_bucket, sale_year, granularity,
                 n_deals, total_volume, median_price, median_ppu,
                 p25_ppu, p75_ppu, mean_ppu, median_ppsf,
                 knowledge_date, ingestion_id)
            SELECT
                {market_expr},
                {class_expr},
                {vintage_expr},
                TRY_CAST("sale_year" AS INTEGER),
                '{granularity}',
                TRY_CAST("n_deals" AS INTEGER),
                TRY_CAST("total_volume_usd" AS DOUBLE),
                TRY_CAST("median_price" AS DOUBLE),
                TRY_CAST("median_ppu" AS DOUBLE),
                TRY_CAST("p25_ppu" AS DOUBLE),
                TRY_CAST("p75_ppu" AS DOUBLE),
                TRY_CAST("mean_ppu" AS DOUBLE),
                {ppsf_expr},
                '{knowledge_date}',
                {ingestion_id}
            FROM read_csv('{_safe_path(csv_path)}', auto_detect=true, all_varchar=true)
        """)

        count = self.conn.execute(f"""
            SELECT count(*) FROM fact_pricing_aggregate WHERE ingestion_id = {ingestion_id}
        """).fetchone()[0]

        logger.info(f"Loaded {count:,} pricing aggregate rows ({granularity})")
        return count

    # ─── Query API ──────────────────────────────────────────────────

    def get_property_zscores(self, property_id: str,
                              peer_cut: str = None,
                              metrics: List[str] = None,
                              as_of: str = None) -> List[Dict]:
        """Get z-scores for a property, optionally filtered."""
        where = ["property_id = ?"]
        params = [property_id]

        if peer_cut:
            where.append("peer_cut = ?")
            params.append(peer_cut)
        if metrics:
            placeholders = ','.join(['?' for _ in metrics])
            where.append(f"metric IN ({placeholders})")
            params.extend(metrics)
        if as_of:
            where.append("knowledge_date <= ?")
            params.append(as_of)

        sql = f"""
            SELECT * FROM fact_property_zscore
            WHERE {' AND '.join(where)}
            ORDER BY peer_cut, metric
        """
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_peer_group(self, peer_group_key: str,
                        metric: str = None) -> List[Dict]:
        """Get peer group statistics."""
        where = ["peer_group_key = ?"]
        params = [peer_group_key]

        if metric:
            where.append("metric = ?")
            params.append(metric)

        sql = f"""
            SELECT * FROM fact_peer_group_stats
            WHERE {' AND '.join(where)}
            ORDER BY metric
        """
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_cap_rates(self, market: str = None,
                       period_type: str = 'year',
                       is_clean: bool = True) -> List[Dict]:
        """Get cap rate aggregates."""
        where = ["period_type = ?", "is_clean = ?"]
        params = [period_type, is_clean]

        if market:
            where.append("market = ?")
            params.append(market)
        else:
            where.append("market IS NULL")

        sql = f"""
            SELECT * FROM fact_cap_rate_aggregate
            WHERE {' AND '.join(where)}
            ORDER BY period
        """
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_sales_comps(self, market: str = None,
                         property_id: str = None,
                         min_year: int = None) -> List[Dict]:
        """Query sales transactions."""
        where = []
        params = []

        if market:
            where.append("market = ?")
            params.append(market)
        if property_id:
            where.append("property_id = ?")
            params.append(property_id)
        if min_year:
            where.append("sale_year >= ?")
            params.append(min_year)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT * FROM fact_sales_transaction
            {where_clause}
            ORDER BY sale_date DESC
            LIMIT 500
        """
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def find_property(self, address: str = None, name: str = None,
                       market: str = None) -> List[Dict]:
        """Search for properties in the dimension table."""
        where = ["valid_to = '9999-12-31'"]
        params = []

        if address:
            where.append("lower(address) LIKE ?")
            params.append(f"%{address.lower()}%")
        if name:
            where.append("lower(property_name) LIKE ?")
            params.append(f"%{name.lower()}%")
        if market:
            where.append("market = ?")
            params.append(market)

        sql = f"""
            SELECT property_key, property_id, property_name, address,
                   city, state, market, submarket, num_units,
                   year_built, building_class
            FROM dim_property
            WHERE {' AND '.join(where)}
            LIMIT 50
        """
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def property_identity_bridge(self, capactive_property_id: int,
                                  address: str, city: str, state: str) -> Optional[str]:
        """Find a CoStar PropertyID matching a Capactive property by address hash.

        This is the cross-module join: Capactive's SQLite properties.id
        → warehouse's dim_property.property_id (CoStar).
        """
        addr_hash = hashlib.md5(
            f"{address.lower().strip()}{city.lower().strip()}{state.lower().strip()}".encode()
        ).hexdigest()

        result = self.conn.execute("""
            SELECT property_id FROM dim_property
            WHERE address_hash = ? AND valid_to = '9999-12-31'
            LIMIT 1
        """, [addr_hash]).fetchone()

        if result:
            # Link the Capactive ID
            self.conn.execute("""
                UPDATE dim_property SET capactive_id = ?
                WHERE address_hash = ? AND valid_to = '9999-12-31'
            """, [capactive_property_id, addr_hash])
            return result[0]
        return None

    # ─── Deal Analytics: Write Methods ────────────────────────────────

    def store_deal_summary(self, deal_id: str, tif_scenario: str,
                           summary: Dict[str, Any],
                           knowledge_date: str = None) -> int:
        """Store a deal-level summary row (one per deal × TIF scenario).

        Args:
            deal_id: Deal identifier (e.g. 'chamberlain')
            tif_scenario: TIF scenario id
            summary: Dict with keys matching fact_deal_summary columns
            knowledge_date: Defaults to today
        """
        kd = knowledge_date or date.today().isoformat()
        ingestion_id = self.register_ingestion(
            source='deal_summary',
            knowledge_date=kd,
            notes=f'{deal_id}/{tif_scenario}',
        )

        # Delete previous entry for this deal+scenario+date (idempotent)
        self.conn.execute("""
            DELETE FROM fact_deal_summary
            WHERE deal_id = ? AND tif_scenario = ? AND knowledge_date = ?
        """, [deal_id, tif_scenario, kd])

        self.conn.execute("""
            INSERT INTO fact_deal_summary
                (deal_id, tif_scenario, hold_years, initial_equity,
                 acquisition_cost_basis, levered_irr, equity_multiple,
                 avg_dscr, exit_cap_rate, gross_sale_price,
                 net_sale_proceeds, loan_repayment_at_sale,
                 total_distributed, deal_irr, deal_em,
                 knowledge_date, ingestion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            deal_id, tif_scenario,
            summary.get('hold_years'),
            summary.get('initial_equity'),
            summary.get('acquisition_cost_basis'),
            summary.get('levered_irr'),
            summary.get('equity_multiple'),
            summary.get('avg_dscr'),
            summary.get('exit_cap_rate'),
            summary.get('gross_sale_price'),
            summary.get('net_sale_proceeds'),
            summary.get('loan_repayment_at_sale'),
            summary.get('total_distributed'),
            summary.get('deal_irr'),
            summary.get('deal_em'),
            kd, ingestion_id,
        ])

        logger.info(f"Stored deal summary: {deal_id}/{tif_scenario}")
        return ingestion_id

    def store_proforma_annual(self, deal_id: str, tif_scenario: str,
                               years: List[Dict[str, Any]],
                               knowledge_date: str = None) -> int:
        """Store annual proforma projections.

        Args:
            deal_id: Deal identifier
            tif_scenario: TIF scenario id
            years: List of dicts with year, calendar_year, noi, debt_service,
                   capex, non_operating, levered_cf, dscr
        """
        kd = knowledge_date or date.today().isoformat()
        ingestion_id = self.register_ingestion(
            source='proforma_engine',
            knowledge_date=kd,
            notes=f'{deal_id}/{tif_scenario}',
            record_count=len(years),
        )

        # Clear previous for idempotency
        self.conn.execute("""
            DELETE FROM fact_proforma_annual
            WHERE deal_id = ? AND tif_scenario = ? AND knowledge_date = ?
        """, [deal_id, tif_scenario, kd])

        rows = []
        for y in years:
            rows.append([
                deal_id, tif_scenario,
                y.get('year'), y.get('calendar_year'),
                y.get('noi'), y.get('debt_service'),
                y.get('capex'), y.get('non_operating'),
                y.get('levered_cf'), y.get('dscr'),
                kd, ingestion_id,
            ])

        self.conn.executemany("""
            INSERT INTO fact_proforma_annual
                (deal_id, tif_scenario, year, calendar_year,
                 noi, debt_service, capex, non_operating,
                 levered_cf, dscr, knowledge_date, ingestion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        logger.info(f"Stored {len(rows)} proforma annual rows: {deal_id}/{tif_scenario}")
        return ingestion_id

    def store_distribution_annual(self, deal_id: str, tif_scenario: str,
                                   partner_years: List[Dict[str, Any]],
                                   knowledge_date: str = None) -> int:
        """Store annual distribution allocations per partner.

        Args:
            deal_id: Deal identifier
            tif_scenario: TIF scenario id
            partner_years: List of dicts with year, calendar_year, partner_id,
                          distribution, pref_accrued, pref_paid, cash_on_cash
        """
        kd = knowledge_date or date.today().isoformat()
        ingestion_id = self.register_ingestion(
            source='distribution_engine',
            knowledge_date=kd,
            notes=f'{deal_id}/{tif_scenario}',
            record_count=len(partner_years),
        )

        self.conn.execute("""
            DELETE FROM fact_distribution_annual
            WHERE deal_id = ? AND tif_scenario = ? AND knowledge_date = ?
        """, [deal_id, tif_scenario, kd])

        rows = []
        for py in partner_years:
            rows.append([
                deal_id, tif_scenario,
                py.get('year'), py.get('calendar_year'),
                py.get('partner_id'),
                py.get('distribution'), py.get('pref_accrued'),
                py.get('pref_paid'), py.get('cash_on_cash'),
                kd, ingestion_id,
            ])

        self.conn.executemany("""
            INSERT INTO fact_distribution_annual
                (deal_id, tif_scenario, year, calendar_year, partner_id,
                 distribution, pref_accrued, pref_paid, cash_on_cash,
                 knowledge_date, ingestion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        logger.info(f"Stored {len(rows)} distribution annual rows: {deal_id}/{tif_scenario}")
        return ingestion_id

    def store_debt_annual(self, deal_id: str, tif_scenario: str,
                           years: List[Dict[str, Any]],
                           knowledge_date: str = None) -> int:
        """Store annual debt metrics (amortization, DSCR, LTV, MIP).

        Args:
            deal_id: Deal identifier
            tif_scenario: TIF scenario id
            years: List of dicts with year, calendar_year, beginning_balance,
                   ending_balance, total_payment, total_principal, total_interest,
                   noi, debt_service, dscr, dscr_with_mip, mip_amount, ltv,
                   estimated_value
        """
        kd = knowledge_date or date.today().isoformat()
        ingestion_id = self.register_ingestion(
            source='debt_engine',
            knowledge_date=kd,
            notes=f'{deal_id}/{tif_scenario}',
            record_count=len(years),
        )

        self.conn.execute("""
            DELETE FROM fact_debt_annual
            WHERE deal_id = ? AND tif_scenario = ? AND knowledge_date = ?
        """, [deal_id, tif_scenario, kd])

        rows = []
        for y in years:
            rows.append([
                deal_id, tif_scenario,
                y.get('year'), y.get('calendar_year'),
                y.get('beginning_balance'), y.get('ending_balance'),
                y.get('total_payment'), y.get('total_principal'),
                y.get('total_interest'),
                y.get('noi'), y.get('debt_service'),
                y.get('dscr'), y.get('dscr_with_mip'),
                y.get('mip_amount'), y.get('ltv'),
                y.get('estimated_value'),
                kd, ingestion_id,
            ])

        self.conn.executemany("""
            INSERT INTO fact_debt_annual
                (deal_id, tif_scenario, year, calendar_year,
                 beginning_balance, ending_balance, total_payment,
                 total_principal, total_interest,
                 noi, debt_service, dscr, dscr_with_mip,
                 mip_amount, ltv, estimated_value,
                 knowledge_date, ingestion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        logger.info(f"Stored {len(rows)} debt annual rows: {deal_id}/{tif_scenario}")
        return ingestion_id

    def store_tif_annual(self, deal_id: str, tif_scenario: str,
                          years: List[Dict[str, Any]],
                          knowledge_date: str = None) -> int:
        """Store annual TIF projections.

        Args:
            deal_id: Deal identifier
            tif_scenario: TIF scenario id
            years: List of dicts with year, tmv, ntc, captured_ntc,
                   tax_increment, osa, admin, net_tif, note_beg_bal,
                   note_interest, note_principal, note_end_bal, property_tax
        """
        kd = knowledge_date or date.today().isoformat()
        ingestion_id = self.register_ingestion(
            source='tif_engine',
            knowledge_date=kd,
            notes=f'{deal_id}/{tif_scenario}',
            record_count=len(years),
        )

        self.conn.execute("""
            DELETE FROM fact_tif_annual
            WHERE deal_id = ? AND tif_scenario = ? AND knowledge_date = ?
        """, [deal_id, tif_scenario, kd])

        rows = []
        for y in years:
            rows.append([
                deal_id, tif_scenario, y.get('year'),
                y.get('tmv'), y.get('ntc'), y.get('captured_ntc'),
                y.get('tax_increment'), y.get('osa'), y.get('admin'),
                y.get('net_tif'),
                y.get('note_beg_bal'), y.get('note_interest'),
                y.get('note_principal'), y.get('note_end_bal'),
                y.get('property_tax'),
                kd, ingestion_id,
            ])

        self.conn.executemany("""
            INSERT INTO fact_tif_annual
                (deal_id, tif_scenario, year,
                 tmv, ntc, captured_ntc, tax_increment, osa, admin, net_tif,
                 note_beg_bal, note_interest, note_principal, note_end_bal,
                 property_tax, knowledge_date, ingestion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        logger.info(f"Stored {len(rows)} TIF annual rows: {deal_id}/{tif_scenario}")
        return ingestion_id

    def store_tif_comparison(self, deal_id: str,
                              comparisons: List[Dict[str, Any]],
                              knowledge_date: str = None) -> int:
        """Store TIF scenario comparison results."""
        kd = knowledge_date or date.today().isoformat()
        ingestion_id = self.register_ingestion(
            source='tif_comparison',
            knowledge_date=kd,
            notes=deal_id,
            record_count=len(comparisons),
        )

        self.conn.execute("""
            DELETE FROM fact_tif_comparison
            WHERE deal_id = ? AND knowledge_date = ?
        """, [deal_id, kd])

        rows = []
        for c in comparisons:
            rows.append([
                deal_id, c.get('name'),
                c.get('payoff_year'), c.get('total_net_tif'),
                c.get('npv_net_tif'), c.get('npv_property_tax'),
                c.get('nominal_tax_savings'), c.get('nominal_tif_reduction'),
                c.get('nominal_net_benefit'),
                c.get('npv_tax_savings'), c.get('npv_tif_reduction'),
                c.get('npv_net_benefit'), c.get('attorney_fees'),
                kd, ingestion_id,
            ])

        self.conn.executemany("""
            INSERT INTO fact_tif_comparison
                (deal_id, tif_scenario, payoff_year, total_net_tif,
                 npv_net_tif, npv_property_tax,
                 nominal_tax_savings, nominal_tif_reduction, nominal_net_benefit,
                 npv_tax_savings, npv_tif_reduction, npv_net_benefit,
                 attorney_fees, knowledge_date, ingestion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        logger.info(f"Stored {len(rows)} TIF comparison rows: {deal_id}")
        return ingestion_id

    def store_partners(self, deal_id: str,
                        partners: List[Dict[str, Any]]) -> None:
        """Store or update partner dimension entries."""
        for p in partners:
            pid = p.get('id') or p.get('partner_id')
            existing = self.conn.execute("""
                SELECT partner_key FROM dim_partner
                WHERE deal_id = ? AND partner_id = ? AND valid_to = '9999-12-31'
            """, [deal_id, pid]).fetchone()

            if not existing:
                self.conn.execute("""
                    INSERT INTO dim_partner
                        (partner_key, deal_id, partner_id, partner_name,
                         role, ownership_pct, distribution_pct, pref_rate,
                         initial_equity)
                    VALUES (nextval('seq_partner_key'), ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    deal_id, pid,
                    p.get('name'), p.get('role'),
                    p.get('ownership_pct'), p.get('distribution_pct'),
                    p.get('pref_rate'), p.get('initial_equity'),
                ])

        logger.info(f"Stored {len(partners)} partners for deal {deal_id}")

    # ─── Deal Analytics: Read Methods ──────────────────────────────────

    def get_deal_summary(self, deal_id: str,
                          tif_scenario: str = None) -> List[Dict]:
        """Get deal summaries, optionally filtered by TIF scenario."""
        where = ["deal_id = ?"]
        params = [deal_id]
        if tif_scenario:
            where.append("tif_scenario = ?")
            params.append(tif_scenario)

        # Latest knowledge_date only
        where.append("""knowledge_date = (
            SELECT MAX(knowledge_date) FROM fact_deal_summary
            WHERE deal_id = ?)""")
        params.append(deal_id)

        sql = f"""
            SELECT * FROM fact_deal_summary
            WHERE {' AND '.join(where)}
            ORDER BY tif_scenario
        """
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_deal_annual(self, deal_id: str, tif_scenario: str) -> List[Dict]:
        """Get the joined annual view for a deal × scenario."""
        try:
            rows = self.conn.execute("""
                SELECT * FROM v_deal_annual_summary
                WHERE deal_id = ? AND tif_scenario = ?
                ORDER BY year
            """, [deal_id, tif_scenario]).fetchall()
            cols = [d[0] for d in self.conn.description]
            return [dict(zip(cols, row)) for row in rows]
        except Exception:
            return []

    def get_market_cap_rates_for_exit(self, market: str,
                                       building_class: str = None) -> Dict[str, Any]:
        """Get current market cap rate data for exit cap validation.

        Returns a dict with median, mean, p25, p75 cap rates for the market,
        useful for benchmarking a deal's assumed exit cap rate.
        """
        where = ["is_clean = true", "period_type = 'year'", "market = ?"]
        params = [market]

        if building_class:
            where.append("building_class = ?")
            params.append(building_class)
        else:
            where.append("building_class IS NULL")

        # Get latest year's data
        sql = f"""
            SELECT period, cap_rate_median, cap_rate_mean,
                   cap_rate_p25, cap_rate_p75, n_deals
            FROM fact_cap_rate_aggregate
            WHERE {' AND '.join(where)}
            ORDER BY period DESC
            LIMIT 5
        """
        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return {}

        cols = [d[0] for d in self.conn.description]
        history = [dict(zip(cols, row)) for row in rows]

        latest = history[0]
        return {
            'market': market,
            'latest_period': latest['period'],
            'cap_rate_median': latest['cap_rate_median'],
            'cap_rate_mean': latest['cap_rate_mean'],
            'cap_rate_p25': latest['cap_rate_p25'],
            'cap_rate_p75': latest['cap_rate_p75'],
            'n_deals': latest['n_deals'],
            'history': history,
        }

    def get_market_rent_benchmarks(self, market: str,
                                     peer_cut: str = 'Market x Size x Quality',
                                     metrics: List[str] = None) -> List[Dict]:
        """Get market-level rent and occupancy benchmarks from z-score stats.

        Useful for lease analysis benchmarking against market averages.
        """
        target_metrics = metrics or [
            'Asking Rent Per Unit', 'Asking Rent Per SF',
            'Effective Rent Per Unit', 'Effective Rent Per SF',
            'Vacancy Rate', 'Occupancy Rate',
        ]

        placeholders = ','.join(['?' for _ in target_metrics])
        sql = f"""
            SELECT peer_group_key, metric, peer_n, peer_mean, peer_std
            FROM fact_peer_group_stats
            WHERE peer_cut = ?
              AND peer_group_key LIKE ?
              AND metric IN ({placeholders})
              AND knowledge_date = (
                  SELECT MAX(knowledge_date) FROM fact_peer_group_stats
              )
            ORDER BY metric
        """
        params = [peer_cut, f'{market}%'] + target_metrics
        rows = self.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    # ─── Summary / Stats ────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """Return warehouse summary statistics."""
        stats = {}
        for table in ['dim_property', 'dim_partner',
                       'fact_property_zscore', 'fact_peer_group_stats',
                       'fact_sales_transaction', 'fact_cap_rate_aggregate',
                       'fact_pricing_aggregate', 'fact_ownership',
                       'fact_deal_summary', 'fact_proforma_annual',
                       'fact_distribution_annual', 'fact_debt_annual',
                       'fact_tif_annual', 'fact_tif_comparison']:
            try:
                count = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                stats[table] = count
            except Exception:
                stats[table] = 0

        stats['ingestion_count'] = self.conn.execute(
            "SELECT count(*) FROM raw_ingestion_log"
        ).fetchone()[0]

        stats['markets'] = self.conn.execute(
            "SELECT count(DISTINCT market) FROM dim_property WHERE market IS NOT NULL"
        ).fetchone()[0]

        return stats
