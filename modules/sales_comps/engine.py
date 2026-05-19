"""
Sales Comps Engine — query layer for transactions, cap rates, pricing, ownership.

All reads go through the DuckDB warehouse. Provides structured queries
for the web layer and API.
"""

import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


class SalesCompsEngine:
    """Query layer for sales comp data backed by the DuckDB warehouse."""

    def __init__(self, warehouse_engine):
        self.wh = warehouse_engine

    # ─── Transaction Search ────────────────────────────────────────

    def search_transactions(self, market: str = None, submarket: str = None,
                             min_year: int = None, max_year: int = None,
                             min_price: float = None, max_price: float = None,
                             min_units: int = None, max_units: int = None,
                             building_class: str = None, asset_class: str = None,
                             buyer: str = None, seller: str = None,
                             property_name: str = None,
                             sort_by: str = 'sale_date', sort_dir: str = 'DESC',
                             limit: int = 100) -> List[Dict]:
        """Flexible transaction search with multiple filters."""
        where = []
        params = []

        if market:
            where.append("market = ?")
            params.append(market)
        if submarket:
            where.append("submarket = ?")
            params.append(submarket)
        if min_year:
            where.append("sale_year >= ?")
            params.append(min_year)
        if max_year:
            where.append("sale_year <= ?")
            params.append(max_year)
        if min_price:
            where.append("sale_price >= ?")
            params.append(min_price)
        if max_price:
            where.append("sale_price <= ?")
            params.append(max_price)
        if min_units:
            where.append("num_units >= ?")
            params.append(min_units)
        if max_units:
            where.append("num_units <= ?")
            params.append(max_units)
        if building_class:
            where.append("building_class = ?")
            params.append(building_class)
        if asset_class:
            where.append("asset_class = ?")
            params.append(asset_class)
        if buyer:
            where.append("lower(buyer_name) LIKE ?")
            params.append(f"%{buyer.lower()}%")
        if seller:
            where.append("lower(seller_name) LIKE ?")
            params.append(f"%{seller.lower()}%")
        if property_name:
            where.append("(lower(property_name) LIKE ? OR lower(property_address) LIKE ?)")
            params.extend([f"%{property_name.lower()}%", f"%{property_name.lower()}%"])

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        allowed_sorts = {
            'sale_date': 'sale_date', 'sale_price': 'sale_price',
            'cap_rate': 'cap_rate_actual', 'ppu': 'price_per_unit',
            'units': 'num_units', 'market': 'market',
        }
        order_col = allowed_sorts.get(sort_by, 'sale_date')
        order_dir = 'ASC' if sort_dir.upper() == 'ASC' else 'DESC'

        sql = f"""
            SELECT transaction_id, property_id, asset_class, property_name,
                   property_address, city, state, market, submarket,
                   sale_date, sale_year, sale_quarter,
                   sale_price, cap_rate_actual, cap_rate_proforma,
                   price_per_unit, price_per_sf,
                   num_units, year_built, building_class,
                   buyer_name, seller_name
            FROM fact_sales_transaction
            {where_clause}
            ORDER BY {order_col} {order_dir} NULLS LAST
            LIMIT {min(limit, 500)}
        """
        rows = self.wh.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_transaction(self, transaction_id: str) -> Optional[Dict]:
        """Get a single transaction by ID."""
        row = self.wh.conn.execute("""
            SELECT * FROM fact_sales_transaction WHERE transaction_id = ?
        """, [transaction_id]).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.wh.conn.description]
        return dict(zip(cols, row))

    # ─── Comparable Properties ─────────────────────────────────────

    def find_comps(self, market: str, num_units: int = None,
                    year_built: int = None, building_class: str = None,
                    radius_years: int = 3, radius_units_pct: float = 0.5,
                    limit: int = 25) -> List[Dict]:
        """Find comparable transactions for a target property profile."""
        where = ["market = ?", "sale_price IS NOT NULL"]
        params = [market]

        if num_units and radius_units_pct:
            lo = int(num_units * (1 - radius_units_pct))
            hi = int(num_units * (1 + radius_units_pct))
            where.append("num_units BETWEEN ? AND ?")
            params.extend([lo, hi])

        if year_built and radius_years:
            where.append("year_built BETWEEN ? AND ?")
            params.extend([year_built - radius_years * 5, year_built + radius_years * 5])

        if building_class:
            where.append("building_class = ?")
            params.append(building_class)

        sql = f"""
            SELECT transaction_id, property_name, property_address, city, state,
                   sale_date, sale_year, sale_price,
                   cap_rate_actual, price_per_unit, price_per_sf,
                   num_units, year_built, building_class,
                   buyer_name, seller_name
            FROM fact_sales_transaction
            WHERE {' AND '.join(where)}
            ORDER BY sale_date DESC
            LIMIT {limit}
        """
        rows = self.wh.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    # ─── Cap Rate Queries ──────────────────────────────────────────

    def get_cap_rate_trend(self, market: str = None, period_type: str = 'year',
                            is_clean: bool = True, asset_class: str = None) -> List[Dict]:
        """Get cap rate time series for a market or national."""
        where = ["period_type = ?", "is_clean = ?"]
        params = [period_type, is_clean]

        if market:
            where.append("market = ?")
            params.append(market)
            where.append("granularity = 'market'")
        else:
            # National: yearly data is in national_by_class, quarterly in national
            if period_type == 'year':
                where.append("granularity = 'national_by_class'")
            else:
                where.append("granularity = 'national'")

        if asset_class:
            where.append("asset_class = ?")
            params.append(asset_class)

        sql = f"""
            SELECT period, asset_class, n_deals,
                   cap_rate_median, cap_rate_mean, cap_rate_std,
                   cap_rate_p25, cap_rate_p75
            FROM fact_cap_rate_aggregate
            WHERE {' AND '.join(where)}
            ORDER BY asset_class, period
        """
        rows = self.wh.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_cap_rate_snapshot(self, period: str = None,
                               is_clean: bool = True) -> List[Dict]:
        """Get cap rates across all markets for a given period."""
        if not period:
            period = self.wh.conn.execute("""
                SELECT MAX(period) FROM fact_cap_rate_aggregate
                WHERE period_type = 'year' AND is_clean = ?
            """, [is_clean]).fetchone()[0]

        rows = self.wh.conn.execute("""
            SELECT market, asset_class, n_deals,
                   cap_rate_median, cap_rate_mean,
                   cap_rate_p25, cap_rate_p75
            FROM fact_cap_rate_aggregate
            WHERE period = ? AND period_type = 'year'
              AND is_clean = ? AND granularity = 'market'
            ORDER BY market, asset_class
        """, [period, is_clean]).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    # ─── Pricing Queries ──────────────────────────────────────────

    def get_pricing_trend(self, market: str = None,
                           building_class: str = None) -> List[Dict]:
        """Get pricing ($/unit) time series."""
        where = []
        params = []

        if market:
            where.append("market = ?")
            params.append(market)
            if building_class:
                where.append("building_class = ?")
                params.append(building_class)
                where.append("granularity = 'market_by_class'")
            else:
                where.append("granularity = 'market'")
        else:
            if building_class:
                where.append("building_class = ?")
                params.append(building_class)
                where.append("granularity LIKE '%class%'")
            else:
                where.append("granularity = 'national'")

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        sql = f"""
            SELECT sale_year, market, building_class, n_deals,
                   total_volume, median_price, median_ppu,
                   p25_ppu, p75_ppu, mean_ppu, median_ppsf
            FROM fact_pricing_aggregate
            {where_clause}
            ORDER BY sale_year
        """
        rows = self.wh.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    # ─── Ownership Queries ─────────────────────────────────────────

    def get_ownership_history(self, property_id: str) -> List[Dict]:
        """Get ownership chain for a property."""
        rows = self.wh.conn.execute("""
            SELECT owner_canonical, acquisition_date, disposition_date,
                   acquisition_price, disposition_price, hold_months, is_current
            FROM fact_ownership
            WHERE property_id = ?
            ORDER BY acquisition_date NULLS LAST
        """, [property_id]).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def search_owners(self, owner_name: str, limit: int = 50) -> List[Dict]:
        """Search for owners and their portfolios."""
        rows = self.wh.conn.execute("""
            SELECT owner_canonical,
                   count(DISTINCT property_id) as properties,
                   count(CASE WHEN is_current THEN 1 END) as current_holdings,
                   CAST(sum(acquisition_price) AS BIGINT) as total_acquired,
                   min(acquisition_date) as earliest, max(acquisition_date) as latest,
                   avg(hold_months) as avg_hold
            FROM fact_ownership
            WHERE lower(owner_canonical) LIKE ?
            GROUP BY owner_canonical
            ORDER BY properties DESC
            LIMIT ?
        """, [f"%{owner_name.lower()}%", limit]).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_owner_portfolio(self, owner_name: str) -> List[Dict]:
        """Get all properties owned by a specific entity."""
        rows = self.wh.conn.execute("""
            SELECT o.property_id, o.owner_canonical,
                   o.acquisition_date, o.disposition_date,
                   o.acquisition_price, o.disposition_price,
                   o.hold_months, o.is_current,
                   t.property_name, t.property_address, t.city, t.state,
                   t.market, t.num_units
            FROM fact_ownership o
            LEFT JOIN fact_sales_transaction t ON o.property_id = t.property_id
            WHERE lower(o.owner_canonical) = lower(?)
            ORDER BY o.acquisition_date DESC NULLS LAST
        """, [owner_name]).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        # Deduplicate (multiple transactions per property)
        seen = set()
        results = []
        for r in rows:
            d = dict(zip(cols, r))
            key = (d['property_id'], d['acquisition_date'])
            if key not in seen:
                seen.add(key)
                results.append(d)
        return results

    # ─── Market Summary ────────────────────────────────────────────

    def get_market_summary(self, market: str) -> Dict:
        """Comprehensive market summary: deals, cap rates, pricing, top players."""
        stats = self.wh.conn.execute("""
            SELECT count(*) as deals,
                   count(CASE WHEN cap_rate_actual IS NOT NULL THEN 1 END) as has_cap,
                   CAST(median(sale_price) AS BIGINT) as med_price,
                   CAST(sum(sale_price) AS BIGINT) as total_volume,
                   median(cap_rate_actual) as med_cap,
                   median(price_per_unit) as med_ppu,
                   min(sale_year) as min_year, max(sale_year) as max_year
            FROM fact_sales_transaction
            WHERE market = ? AND sale_price IS NOT NULL
        """, [market]).fetchone()

        labels = ['deals', 'has_cap_rate', 'median_price', 'total_volume',
                  'median_cap_rate', 'median_ppu', 'min_year', 'max_year']
        result = dict(zip(labels, stats))
        result['market'] = market

        # Top buyers
        buyers = self.wh.conn.execute("""
            SELECT buyer_name, count(*) as deals,
                   CAST(sum(sale_price) AS BIGINT) as volume
            FROM fact_sales_transaction
            WHERE market = ? AND buyer_name IS NOT NULL
            GROUP BY buyer_name ORDER BY deals DESC LIMIT 10
        """, [market]).fetchall()
        result['top_buyers'] = [{'name': b[0], 'deals': b[1], 'volume': b[2]} for b in buyers]

        # Top sellers
        sellers = self.wh.conn.execute("""
            SELECT seller_name, count(*) as deals,
                   CAST(sum(sale_price) AS BIGINT) as volume
            FROM fact_sales_transaction
            WHERE market = ? AND seller_name IS NOT NULL
            GROUP BY seller_name ORDER BY deals DESC LIMIT 10
        """, [market]).fetchall()
        result['top_sellers'] = [{'name': s[0], 'deals': s[1], 'volume': s[2]} for s in sellers]

        # Year-over-year
        yoy = self.wh.conn.execute("""
            SELECT sale_year, count(*) as deals,
                   CAST(sum(sale_price) AS BIGINT) as volume,
                   median(cap_rate_actual) as med_cap,
                   median(price_per_unit) as med_ppu
            FROM fact_sales_transaction
            WHERE market = ? AND sale_price IS NOT NULL
            GROUP BY sale_year ORDER BY sale_year
        """, [market]).fetchall()
        result['year_over_year'] = [
            {'year': y[0], 'deals': y[1], 'volume': y[2], 'cap_rate': y[3], 'ppu': y[4]}
            for y in yoy
        ]

        return result

    def list_markets(self) -> List[Dict]:
        """List all markets with transaction counts."""
        rows = self.wh.conn.execute("""
            SELECT market, count(*) as deals,
                   CAST(median(sale_price) AS BIGINT) as med_price,
                   min(sale_year) as min_yr, max(sale_year) as max_yr
            FROM fact_sales_transaction
            WHERE market IS NOT NULL AND sale_price IS NOT NULL
            GROUP BY market
            ORDER BY deals DESC
        """).fetchall()
        cols = ['market', 'deals', 'median_price', 'min_year', 'max_year']
        return [dict(zip(cols, r)) for r in rows]
