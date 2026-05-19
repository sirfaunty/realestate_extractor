"""
Inventory Z-Score Engine — adapted from partner's zscore_engine.py v4.

This wraps the partner's pure scoring functions and connects them
to the DuckDB warehouse for reads and writes. The original CLI-based
engine read/wrote parquets directly; this version:
  - Reads pre-computed z-scores from warehouse fact tables
  - Can re-score a single property on demand (using warehouse data as the peer pool)
  - Provides structured query APIs for the web layer

The actual z-score math is unchanged from the partner's engine.
"""

import gc
import hashlib
import logging
import re
from typing import List, Dict, Optional, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_PEER_N = 3

# Columns excluded from z-scoring
EXCLUDE = {
    "PropertyID", "Zip", "Parcel Number 1(Min)", "Parcel Number 2(Max)",
    "FIRM ID", "FIRM Panel Number", "FEMA Map Identifier",
    "Latitude", "Longitude", "Tax Year", "Month Built", "Month Renovated",
    "Year Renovated", "Vintage", "Year Built", "Exp Year",
    "Owner Phone", "Property Manager Phone", "True Owner Phone",
    "Recorded Owner Phone", "Leasing Company Phone",
    "Leasing Company Fax", "Sale Company Fax", "Sale Company Phone",
    "Sales Contact Phone",
}

ENGINE_COLS = {
    "Universe", "Affordability Pool", "Affordable Type Grouped",
    "Quality Tier", "Size Tier", "Vintage",
    "Geo_Market", "Geo_Area", "Geo_Submarket", "Geo_SubmarketCluster",
    "has_rents", "has_vacancy", "has_unit_mix", "completeness_score",
}


# ─── Enrichment functions (from partner's engine) ──────────────────

def assign_universe(df):
    return np.where(
        df["Secondary Type"] == "Manufactured Housing/Mobile Home Park",
        "Manufactured Housing",
        np.where(df["Market Segment"] == "Senior", "Senior", "Market Rate Apartments"),
    )


def assign_pool(df):
    pool = df["Rent Type"].fillna("Market")
    return pool.where(pool.isin(["Market", "Market/Affordable", "Affordable"]), "Market")


def assign_size_tier(units):
    return pd.cut(
        units,
        bins=[-np.inf, 50, 75, 150, 300, np.inf],
        labels=["1: 25-49", "2: 50-74", "3: 75-149", "4: 150-299", "5: 300+"],
        right=False,
    ).astype(str)


def assign_quality_tier(stars):
    out = pd.Series(None, index=stars.index, dtype=object)
    out[stars <= 2] = "1&2 Star"
    out[stars == 3] = "3 Star"
    out[stars >= 4] = "4&5 Star"
    return out


# ─── Core z-score computation (from partner's engine) ──────────────

def compute_z(pool_df, score_df, cut_cols, cut_name, universe, view, numeric_cols):
    """Compute z-scores for score_df properties against pool_df peers.

    Unchanged from partner's engine — same math, same output schema.
    """
    pool = pool_df.dropna(subset=cut_cols)
    scoring = score_df.dropna(subset=cut_cols)
    if len(pool) < MIN_PEER_N or len(scoring) == 0:
        return None
    g = pool.groupby(cut_cols, dropna=False, observed=True)
    means = g[numeric_cols].mean()
    stds = g[numeric_cols].std()
    counts = g[numeric_cols].count()
    keys = scoring.set_index(cut_cols)
    am = means.reindex(keys.index)
    as_ = stds.reindex(keys.index)
    ac = counts.reindex(keys.index)
    vals = scoring[numeric_cols].values
    mv, sv, cv = am.values, as_.values, ac.values
    valid = (sv > 0) & (cv >= MIN_PEER_N)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(valid, (vals - mv) / sv, np.nan)
    pk = scoring[cut_cols].astype(str).agg(" | ".join, axis=1)
    return pd.DataFrame({
        "PropertyID": np.repeat(scoring["PropertyID"].astype(str).values, len(numeric_cols)),
        "Universe": universe,
        "peer_cut": cut_name,
        "view": view,
        "peer_group_key": np.repeat(pk.values, len(numeric_cols)),
        "metric": np.tile(numeric_cols, len(scoring)),
        "value": vals.ravel(),
        "peer_mean": mv.ravel(),
        "peer_std": sv.ravel(),
        "peer_n": cv.ravel(),
        "z_score": z.ravel(),
    })


# ─── Warehouse-backed query layer ─────────────────────────────────

class InventoryEngine:
    """Query layer for inventory z-scores backed by the DuckDB warehouse."""

    def __init__(self, warehouse_engine):
        self.wh = warehouse_engine

    # ─── Property Queries ──────────────────────────────────────────

    def get_property_profile(self, property_id: str) -> Optional[Dict]:
        """Get full property profile: dimensions + z-score summary."""
        row = self.wh.conn.execute("""
            SELECT * FROM dim_property
            WHERE property_id = ? AND valid_to = '9999-12-31'
        """, [property_id]).fetchone()
        if not row:
            return None

        cols = [d[0] for d in self.wh.conn.description]
        prop = dict(zip(cols, row))

        # Z-score summary: count by peer_cut
        cuts = self.wh.conn.execute("""
            SELECT peer_cut, count(DISTINCT metric) as metrics,
                   avg(z_score) as avg_z,
                   min(z_score) as min_z, max(z_score) as max_z
            FROM fact_property_zscore
            WHERE property_id = ?
            GROUP BY peer_cut
            ORDER BY peer_cut
        """, [property_id]).fetchall()

        cut_cols = ['peer_cut', 'metrics', 'avg_z', 'min_z', 'max_z']
        prop['zscore_summary'] = [dict(zip(cut_cols, c)) for c in cuts]
        prop['total_zscores'] = sum(c[1] for c in cuts)

        return prop

    def get_zscores(self, property_id: str, peer_cut: str = None,
                    sort_by: str = 'abs_z', limit: int = 50) -> List[Dict]:
        """Get z-scores for a property, sorted by significance."""
        where = ["property_id = ?"]
        params = [property_id]

        if peer_cut:
            where.append("peer_cut = ?")
            params.append(peer_cut)

        order = {
            'abs_z': 'abs(z_score) DESC NULLS LAST',
            'z_asc': 'z_score ASC NULLS LAST',
            'z_desc': 'z_score DESC NULLS LAST',
            'metric': 'metric ASC',
        }.get(sort_by, 'abs(z_score) DESC NULLS LAST')

        sql = f"""
            SELECT property_id, universe, peer_cut, view, peer_group_key,
                   metric, value, peer_mean, peer_std, peer_n, z_score
            FROM fact_property_zscore
            WHERE {' AND '.join(where)}
            ORDER BY {order}
            LIMIT {limit}
        """
        rows = self.wh.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_outlier_metrics(self, property_id: str, threshold: float = 2.0,
                             peer_cut: str = None) -> Dict[str, List[Dict]]:
        """Get metrics where property is a significant outlier (|z| > threshold)."""
        where = ["property_id = ?", "abs(z_score) > ?"]
        params = [property_id, threshold]
        if peer_cut:
            where.append("peer_cut = ?")
            params.append(peer_cut)

        rows = self.wh.conn.execute(f"""
            SELECT metric, z_score, value, peer_mean, peer_std, peer_n,
                   peer_cut, view, peer_group_key
            FROM fact_property_zscore
            WHERE {' AND '.join(where)}
            ORDER BY z_score DESC
        """, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        results = [dict(zip(cols, r)) for r in rows]

        # Split into strengths and weaknesses
        strengths = [r for r in results if r['z_score'] > 0]
        weaknesses = [r for r in results if r['z_score'] < 0]
        return {'strengths': strengths, 'weaknesses': weaknesses}

    # ─── Peer Group Queries ────────────────────────────────────────

    def get_peer_group_detail(self, peer_group_key: str,
                               metric: str = None) -> List[Dict]:
        """Get stats for a specific peer group."""
        return self.wh.get_peer_group(peer_group_key, metric)

    def get_peer_properties(self, property_id: str, peer_cut: str) -> List[Dict]:
        """Find all properties in the same peer group as the given property."""
        # First find the peer group key
        pgk = self.wh.conn.execute("""
            SELECT DISTINCT peer_group_key FROM fact_property_zscore
            WHERE property_id = ? AND peer_cut = ?
            LIMIT 1
        """, [property_id, peer_cut]).fetchone()

        if not pgk:
            return []

        # Find all properties with scores in that peer group
        rows = self.wh.conn.execute("""
            SELECT DISTINCT z.property_id, p.property_name, p.address,
                   p.city, p.state, p.num_units, p.year_built, p.building_class,
                   avg(z.z_score) as avg_z
            FROM fact_property_zscore z
            JOIN dim_property p ON z.property_id = p.property_id AND p.valid_to = '9999-12-31'
            WHERE z.peer_group_key = ? AND z.peer_cut = ?
            GROUP BY z.property_id, p.property_name, p.address,
                     p.city, p.state, p.num_units, p.year_built, p.building_class
            ORDER BY avg_z DESC
            LIMIT 100
        """, [pgk[0], peer_cut]).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    # ─── Market-Level Queries ──────────────────────────────────────

    def get_market_stats(self, market: str) -> Dict:
        """Get inventory statistics for a market."""
        props = self.wh.conn.execute("""
            SELECT count(*) as total,
                   count(DISTINCT submarket) as submarkets,
                   avg(TRY_CAST(num_units AS DOUBLE)) as avg_units,
                   median(TRY_CAST(num_units AS DOUBLE)) as med_units,
                   avg(TRY_CAST(year_built AS DOUBLE)) as avg_year,
                   sum(TRY_CAST(num_units AS DOUBLE)) as total_units
            FROM dim_property
            WHERE market = ? AND valid_to = '9999-12-31'
        """, [market]).fetchone()

        if not props or props[0] == 0:
            return {'error': f'No properties found for market {market}'}

        cols = ['total_properties', 'submarkets', 'avg_units', 'median_units',
                'avg_year_built', 'total_units']
        stats = dict(zip(cols, props))
        stats['market'] = market

        # Z-score coverage
        scored = self.wh.conn.execute("""
            SELECT count(DISTINCT z.property_id)
            FROM fact_property_zscore z
            JOIN dim_property p ON z.property_id = p.property_id
            WHERE p.market = ?
        """, [market]).fetchone()[0]
        stats['scored_properties'] = scored
        stats['coverage_pct'] = round(scored / stats['total_properties'] * 100, 1) if stats['total_properties'] else 0

        # Building class distribution
        classes = self.wh.conn.execute("""
            SELECT building_class, count(*) as n
            FROM dim_property
            WHERE market = ? AND valid_to = '9999-12-31' AND building_class IS NOT NULL
            GROUP BY building_class
            ORDER BY n DESC
        """, [market]).fetchall()
        stats['building_classes'] = {c[0]: c[1] for c in classes}

        return stats

    def get_scored_markets(self) -> List[Dict]:
        """List all markets that have z-score data."""
        rows = self.wh.conn.execute("""
            SELECT p.market,
                   count(DISTINCT z.property_id) as scored,
                   count(DISTINCT p.property_id) as total,
                   count(DISTINCT z.peer_cut) as peer_cuts,
                   count(DISTINCT z.metric) as metrics
            FROM dim_property p
            LEFT JOIN fact_property_zscore z ON p.property_id = z.property_id
            WHERE p.market IS NOT NULL AND p.valid_to = '9999-12-31'
            GROUP BY p.market
            HAVING scored > 0
            ORDER BY scored DESC
        """).fetchall()
        cols = ['market', 'scored_properties', 'total_properties', 'peer_cuts', 'metrics']
        return [dict(zip(cols, r)) for r in rows]

    # ─── Property Search ───────────────────────────────────────────

    def search_properties(self, query: str = None, market: str = None,
                           min_units: int = None, max_units: int = None,
                           building_class: str = None,
                           scored_only: bool = False,
                           limit: int = 50) -> List[Dict]:
        """Search properties with flexible filtering."""
        where = ["p.valid_to = '9999-12-31'"]
        params = []

        if query:
            where.append("(lower(p.property_name) LIKE ? OR lower(p.address) LIKE ? OR p.property_id = ?)")
            params.extend([f"%{query.lower()}%", f"%{query.lower()}%", query])
        if market:
            where.append("p.market = ?")
            params.append(market)
        if min_units:
            where.append("p.num_units >= ?")
            params.append(min_units)
        if max_units:
            where.append("p.num_units <= ?")
            params.append(max_units)
        if building_class:
            where.append("p.building_class = ?")
            params.append(building_class)

        join = ""
        select_extra = ""
        if scored_only:
            join = "JOIN (SELECT DISTINCT property_id FROM fact_property_zscore) z ON p.property_id = z.property_id"
            select_extra = ", 1 as has_scores"
        else:
            join = "LEFT JOIN (SELECT DISTINCT property_id FROM fact_property_zscore) z ON p.property_id = z.property_id"
            select_extra = ", CASE WHEN z.property_id IS NOT NULL THEN 1 ELSE 0 END as has_scores"

        sql = f"""
            SELECT p.property_id, p.property_name, p.address, p.city, p.state,
                   p.market, p.submarket, p.num_units, p.year_built,
                   p.building_class {select_extra}
            FROM dim_property p
            {join}
            WHERE {' AND '.join(where)}
            ORDER BY p.num_units DESC NULLS LAST
            LIMIT {limit}
        """
        rows = self.wh.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    # ─── Identity Bridge ──────────────────────────────────────────

    def bridge_property(self, capactive_id: int, address: str,
                        city: str, state: str) -> Optional[Dict]:
        """Bridge a Capactive property to its CoStar warehouse record.

        Returns the matched warehouse property with z-score summary,
        or None if no match found.
        """
        costar_id = self.wh.property_identity_bridge(capactive_id, address, city, state)
        if costar_id:
            return self.get_property_profile(costar_id)
        return None
