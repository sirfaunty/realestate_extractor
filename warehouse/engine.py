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
            FROM read_parquet('{parquet_path}')
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
            FROM read_parquet('{parquet_path}')
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
            FROM read_parquet('{parquet_path}')
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
            FROM read_csv('{csv_path}', auto_detect=true, all_varchar=true)
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
            FROM read_csv('{csv_path}', auto_detect=true, all_varchar=true)
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
            SELECT * FROM read_csv('{csv_path}', auto_detect=true) LIMIT 0
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
            FROM read_csv('{csv_path}', auto_detect=true, all_varchar=true)
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

    # ─── Summary / Stats ────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """Return warehouse summary statistics."""
        stats = {}
        for table in ['dim_property', 'fact_property_zscore', 'fact_peer_group_stats',
                       'fact_sales_transaction', 'fact_cap_rate_aggregate',
                       'fact_pricing_aggregate', 'fact_ownership']:
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
