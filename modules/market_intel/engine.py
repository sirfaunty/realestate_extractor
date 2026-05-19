"""
engine.py — MarketIntelEngine

Queries the DuckDB warehouse and scorecard module to produce
structured market intelligence briefs.

Data surfaces:
  1. Market overview   — property count, unit count, vintage distribution
  2. Cap rate trends   — annual + quarterly time series, spread analysis
  3. Sales activity    — volume, deal count, price-per-unit trends
  4. Pricing trends    — $/unit and $/SF by class and vintage
  5. Scorecard context — tilt score, rank, D&S/Occ/Rent components
  6. Top owners        — largest holders by unit count
  7. Market comparison — side-by-side metrics for 2+ markets
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ─── Data classes ──────────────────────────────────────────────────

@dataclass
class MarketOverview:
    """High-level market stats from dim_property."""
    market: str
    property_count: int = 0
    total_units: int = 0
    submarkets: int = 0
    submarket_list: list = field(default_factory=list)
    class_distribution: dict = field(default_factory=dict)  # class -> count
    vintage_distribution: dict = field(default_factory=dict)  # decade -> count
    avg_year_built: int = 0
    avg_units: float = 0.0


@dataclass
class CapRateSummary:
    """Cap rate trend summary for a market."""
    market: str
    latest_median: Optional[float] = None
    latest_mean: Optional[float] = None
    latest_spread: Optional[float] = None  # p75 - p25
    yoy_change_bps: Optional[float] = None
    five_year_avg: Optional[float] = None
    annual_series: list = field(default_factory=list)
    quarterly_series: list = field(default_factory=list)
    n_deals_latest: int = 0


@dataclass
class SalesActivitySummary:
    """Sales transaction summary for a market."""
    market: str
    total_transactions: int = 0
    total_volume: float = 0.0
    avg_price_per_unit: float = 0.0
    median_cap_rate: Optional[float] = None
    annual_volume: dict = field(default_factory=dict)  # year -> volume
    annual_deals: dict = field(default_factory=dict)  # year -> count
    recent_transactions: list = field(default_factory=list)
    top_buyers: list = field(default_factory=list)
    top_sellers: list = field(default_factory=list)


@dataclass
class PricingTrendSummary:
    """Pricing trend summary ($/unit, $/SF) for a market."""
    market: str
    latest_median_ppu: Optional[float] = None
    latest_median_ppsf: Optional[float] = None
    yoy_ppu_change_pct: Optional[float] = None
    annual_series: list = field(default_factory=list)  # [{year, median_ppu, median_ppsf, n}]
    by_class: dict = field(default_factory=dict)  # class -> {median_ppu, n}


@dataclass
class MarketBrief:
    """Complete market intelligence brief combining all data surfaces."""
    market: str
    overview: Optional[MarketOverview] = None
    cap_rates: Optional[CapRateSummary] = None
    sales: Optional[SalesActivitySummary] = None
    pricing: Optional[PricingTrendSummary] = None
    scorecard: Optional[dict] = None
    top_owners: list = field(default_factory=list)
    signals: list = field(default_factory=list)  # text signals/narratives


# ─── Engine ────────────────────────────────────────────────────────

class MarketIntelEngine:
    """Queries warehouse + scorecard to build market intelligence."""

    def __init__(self, warehouse_engine=None):
        """
        warehouse_engine: a connected WarehouseEngine instance.
        If None, will create and connect one lazily.
        """
        self._wh = warehouse_engine
        self._scorecard = None
        self._market_alias_cache: Dict[str, str] = {}  # bare name -> fact table name

    @property
    def wh(self):
        if self._wh is None:
            from warehouse.engine import WarehouseEngine
            self._wh = WarehouseEngine()
            self._wh.connect()
        return self._wh

    def _resolve_fact_market(self, market: str) -> str:
        """Resolve a dim_property market name to the fact-table variant.

        dim_property uses bare names ('New York'), but fact tables use
        'New York, NY'.  Cache the mapping for performance.
        """
        if market in self._market_alias_cache:
            return self._market_alias_cache[market]

        conn = self.wh.conn
        # Try exact match first
        row = conn.execute(
            "SELECT market FROM fact_sales_transaction WHERE market = ? LIMIT 1",
            [market],
        ).fetchone()
        if row:
            self._market_alias_cache[market] = market
            return market

        # Try with state suffix — look up state from dim_property
        row = conn.execute(
            "SELECT state FROM dim_property WHERE market = ? AND valid_to = '9999-12-31' LIMIT 1",
            [market],
        ).fetchone()
        if row and row[0]:
            candidate = f"{market}, {row[0]}"
            hit = conn.execute(
                "SELECT market FROM fact_sales_transaction WHERE market = ? LIMIT 1",
                [candidate],
            ).fetchone()
            if hit:
                self._market_alias_cache[market] = candidate
                return candidate

        # Try LIKE match as fallback
        row = conn.execute(
            "SELECT DISTINCT market FROM fact_sales_transaction WHERE market LIKE ? LIMIT 1",
            [f"{market}%"],
        ).fetchone()
        if row:
            self._market_alias_cache[market] = row[0]
            return row[0]

        # No match — return original
        self._market_alias_cache[market] = market
        return market

    @property
    def scorecard(self):
        if self._scorecard is None:
            try:
                from modules.scorecard.engine import ScorecardEngine
                self._scorecard = ScorecardEngine(self.wh)
            except Exception as e:
                logger.warning(f"Could not init ScorecardEngine: {e}")
        return self._scorecard

    # ─── 1. Market overview ─────────────────────────────────────────

    def get_market_overview(self, market: str) -> MarketOverview:
        """Property inventory summary for a market."""
        conn = self.wh.conn
        ov = MarketOverview(market=market)

        # Property counts
        row = conn.execute("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(num_units), 0) AS units,
                   COUNT(DISTINCT submarket) AS subs
            FROM dim_property
            WHERE market = ? AND valid_to = '9999-12-31'
        """, [market]).fetchone()
        ov.property_count = row[0]
        ov.total_units = int(row[1])
        ov.submarkets = row[2]

        # Submarket list
        rows = conn.execute("""
            SELECT submarket, COUNT(*) AS cnt, COALESCE(SUM(num_units), 0) AS units
            FROM dim_property
            WHERE market = ? AND valid_to = '9999-12-31' AND submarket IS NOT NULL
            GROUP BY submarket ORDER BY units DESC
        """, [market]).fetchall()
        ov.submarket_list = [
            {"name": r[0], "properties": r[1], "units": int(r[2])} for r in rows
        ]

        # Class distribution
        rows = conn.execute("""
            SELECT building_class, COUNT(*) AS cnt
            FROM dim_property
            WHERE market = ? AND valid_to = '9999-12-31' AND building_class IS NOT NULL
            GROUP BY building_class ORDER BY cnt DESC
        """, [market]).fetchall()
        ov.class_distribution = {r[0]: r[1] for r in rows}

        # Vintage distribution (by decade)
        rows = conn.execute("""
            SELECT (year_built / 10) * 10 AS decade, COUNT(*) AS cnt
            FROM dim_property
            WHERE market = ? AND valid_to = '9999-12-31' AND year_built IS NOT NULL
            GROUP BY decade ORDER BY decade
        """, [market]).fetchall()
        ov.vintage_distribution = {str(int(r[0])) + "s": r[1] for r in rows if r[0]}

        # Averages
        row = conn.execute("""
            SELECT AVG(year_built), AVG(num_units)
            FROM dim_property
            WHERE market = ? AND valid_to = '9999-12-31'
              AND year_built IS NOT NULL AND num_units IS NOT NULL
        """, [market]).fetchone()
        ov.avg_year_built = int(row[0]) if row[0] else 0
        ov.avg_units = round(row[1], 1) if row[1] else 0.0

        return ov

    # ─── 2. Cap rate trends ─────────────────────────────────────────

    def get_cap_rate_summary(self, market: str) -> CapRateSummary:
        """Cap rate time series and spread analysis."""
        conn = self.wh.conn
        market_fact = self._resolve_fact_market(market)
        cs = CapRateSummary(market=market)

        # Annual series
        rows = conn.execute("""
            SELECT period, cap_rate_median, cap_rate_mean, cap_rate_std,
                   cap_rate_p25, cap_rate_p75, n_deals
            FROM fact_cap_rate_aggregate
            WHERE market = ? AND period_type = 'year' AND is_clean = true
            ORDER BY period
        """, [market_fact]).fetchall()

        for r in rows:
            cs.annual_series.append({
                "year": r[0], "median": r[1], "mean": r[2], "std": r[3],
                "p25": r[4], "p75": r[5], "n_deals": r[6],
            })

        if cs.annual_series:
            latest = cs.annual_series[-1]
            cs.latest_median = latest["median"]
            cs.latest_mean = latest["mean"]
            cs.latest_spread = (latest["p75"] - latest["p25"]) if latest["p75"] and latest["p25"] else None
            cs.n_deals_latest = latest["n_deals"] or 0

            if len(cs.annual_series) >= 2:
                prev = cs.annual_series[-2]
                if latest["median"] and prev["median"]:
                    cs.yoy_change_bps = round((latest["median"] - prev["median"]) * 10000)

            recent_5 = cs.annual_series[-5:]
            medians = [s["median"] for s in recent_5 if s["median"]]
            if medians:
                cs.five_year_avg = round(statistics.mean(medians), 4)

        # Quarterly series
        rows = conn.execute("""
            SELECT period, cap_rate_median, n_deals
            FROM fact_cap_rate_aggregate
            WHERE market = ? AND period_type = 'quarter' AND is_clean = true
            ORDER BY period
        """, [market_fact]).fetchall()

        cs.quarterly_series = [
            {"quarter": r[0], "median": r[1], "n_deals": r[2]} for r in rows
        ]

        return cs

    # ─── 3. Sales activity ──────────────────────────────────────────

    def get_sales_activity(self, market: str, min_year: int = None) -> SalesActivitySummary:
        """Sales transaction summary with top buyers/sellers."""
        conn = self.wh.conn
        market_fact = self._resolve_fact_market(market)
        sa = SalesActivitySummary(market=market)

        where_extra = f" AND sale_year >= {min_year}" if min_year else ""

        # Aggregate stats
        row = conn.execute(f"""
            SELECT COUNT(*) AS cnt,
                   COALESCE(SUM(sale_price), 0) AS vol,
                   AVG(price_per_unit) AS avg_ppu,
                   MEDIAN(cap_rate_actual) AS med_cap
            FROM fact_sales_transaction
            WHERE market = ?{where_extra}
        """, [market_fact]).fetchone()
        sa.total_transactions = row[0]
        sa.total_volume = float(row[1])
        sa.avg_price_per_unit = round(float(row[2]), 0) if row[2] else 0.0
        sa.median_cap_rate = round(float(row[3]), 4) if row[3] else None

        # Annual volume
        rows = conn.execute(f"""
            SELECT sale_year, COUNT(*) AS cnt, COALESCE(SUM(sale_price), 0) AS vol
            FROM fact_sales_transaction
            WHERE market = ? AND sale_year IS NOT NULL{where_extra}
            GROUP BY sale_year ORDER BY sale_year
        """, [market_fact]).fetchall()
        sa.annual_volume = {int(r[0]): float(r[2]) for r in rows}
        sa.annual_deals = {int(r[0]): r[1] for r in rows}

        # Recent transactions (top 20)
        rows = conn.execute("""
            SELECT property_name, property_address, city, sale_date,
                   sale_price, cap_rate_actual, price_per_unit, num_units,
                   buyer_name, seller_name, building_class
            FROM fact_sales_transaction
            WHERE market = ?
            ORDER BY sale_date DESC
            LIMIT 20
        """, [market_fact]).fetchall()
        cols = ['name', 'address', 'city', 'sale_date', 'price', 'cap_rate',
                'ppu', 'units', 'buyer', 'seller', 'class']
        sa.recent_transactions = [dict(zip(cols, r)) for r in rows]

        # Top buyers
        rows = conn.execute(f"""
            SELECT buyer_name, COUNT(*) AS deals, SUM(sale_price) AS vol,
                   SUM(num_units) AS units
            FROM fact_sales_transaction
            WHERE market = ? AND buyer_name IS NOT NULL{where_extra}
            GROUP BY buyer_name ORDER BY vol DESC LIMIT 10
        """, [market_fact]).fetchall()
        sa.top_buyers = [
            {"name": r[0], "deals": r[1], "volume": float(r[2]) if r[2] else 0,
             "units": int(r[3]) if r[3] else 0}
            for r in rows
        ]

        # Top sellers
        rows = conn.execute(f"""
            SELECT seller_name, COUNT(*) AS deals, SUM(sale_price) AS vol,
                   SUM(num_units) AS units
            FROM fact_sales_transaction
            WHERE market = ? AND seller_name IS NOT NULL{where_extra}
            GROUP BY seller_name ORDER BY vol DESC LIMIT 10
        """, [market_fact]).fetchall()
        sa.top_sellers = [
            {"name": r[0], "deals": r[1], "volume": float(r[2]) if r[2] else 0,
             "units": int(r[3]) if r[3] else 0}
            for r in rows
        ]

        return sa

    # ─── 4. Pricing trends ─────────────────────────────────────────

    def get_pricing_trends(self, market: str) -> PricingTrendSummary:
        """$/unit and $/SF pricing trends by year and class."""
        conn = self.wh.conn
        market_fact = self._resolve_fact_market(market)
        pt = PricingTrendSummary(market=market)

        # Annual series
        rows = conn.execute("""
            SELECT sale_year, median_ppu, median_ppsf, n_deals, total_volume
            FROM fact_pricing_aggregate
            WHERE market = ? AND granularity = 'market'
              AND building_class IS NULL AND vintage_bucket IS NULL
            ORDER BY sale_year
        """, [market_fact]).fetchall()

        for r in rows:
            pt.annual_series.append({
                "year": int(r[0]), "median_ppu": r[1], "median_ppsf": r[2],
                "n_deals": r[3], "volume": float(r[4]) if r[4] else 0,
            })

        if pt.annual_series:
            latest = pt.annual_series[-1]
            pt.latest_median_ppu = latest["median_ppu"]
            pt.latest_median_ppsf = latest["median_ppsf"]

            if len(pt.annual_series) >= 2:
                prev = pt.annual_series[-2]
                if latest["median_ppu"] and prev["median_ppu"] and prev["median_ppu"] > 0:
                    pt.yoy_ppu_change_pct = round(
                        (latest["median_ppu"] - prev["median_ppu"]) / prev["median_ppu"] * 100, 1
                    )

        # By class
        rows = conn.execute("""
            SELECT building_class, median_ppu, median_ppsf, n_deals
            FROM fact_pricing_aggregate
            WHERE market = ? AND granularity = 'market'
              AND building_class IS NOT NULL AND vintage_bucket IS NULL
              AND sale_year = (SELECT MAX(sale_year) FROM fact_pricing_aggregate
                               WHERE market = ? AND granularity = 'market')
            ORDER BY building_class
        """, [market_fact, market_fact]).fetchall()

        pt.by_class = {
            r[0]: {"median_ppu": r[1], "median_ppsf": r[2], "n_deals": r[3]}
            for r in rows
        }

        return pt

    # ─── 5. Scorecard context ──────────────────────────────────────

    def get_scorecard_context(self, market: str) -> Optional[dict]:
        """Get scorecard ranking and score breakdown for a market."""
        if not self.scorecard:
            return None
        try:
            score = self.scorecard.get_market_score(market)
            if score:
                return score
            # Try case-insensitive match
            scored = self.scorecard.get_scored_markets()
            for m in scored:
                if m.get("market", "").lower() == market.lower():
                    return self.scorecard.get_market_score(m["market"])
        except Exception as e:
            logger.warning(f"Scorecard lookup failed for {market}: {e}")
        return None

    # ─── 6. Top owners ─────────────────────────────────────────────

    def get_top_owners(self, market: str, limit: int = 15) -> list:
        """Largest current owners in a market by unit count."""
        conn = self.wh.conn
        rows = conn.execute("""
            SELECT fo.owner_canonical,
                   COUNT(DISTINCT fo.property_id) AS properties,
                   SUM(dp.num_units) AS total_units,
                   AVG(dp.year_built) AS avg_vintage
            FROM fact_ownership fo
            JOIN dim_property dp ON dp.property_id = fo.property_id
                AND dp.valid_to = '9999-12-31'
            WHERE dp.market = ?
              AND fo.is_current = true
              AND fo.owner_canonical IS NOT NULL
            GROUP BY fo.owner_canonical
            ORDER BY total_units DESC
            LIMIT ?
        """, [market, limit]).fetchall()

        return [
            {"owner": r[0], "properties": r[1],
             "units": int(r[2]) if r[2] else 0,
             "avg_vintage": int(r[3]) if r[3] else None}
            for r in rows
        ]

    # ─── 7. Market list ────────────────────────────────────────────

    def get_markets(self) -> list:
        """List all markets with basic stats for the dashboard."""
        conn = self.wh.conn
        rows = conn.execute("""
            SELECT market,
                   COUNT(*) AS properties,
                   COALESCE(SUM(num_units), 0) AS units,
                   COUNT(DISTINCT submarket) AS submarkets
            FROM dim_property
            WHERE valid_to = '9999-12-31' AND market IS NOT NULL
            GROUP BY market
            ORDER BY units DESC
        """).fetchall()

        markets = []
        for r in rows:
            markets.append({
                "market": r[0],
                "properties": r[1],
                "units": int(r[2]),
                "submarkets": r[3],
            })
        return markets

    def get_markets_with_scores(self) -> list:
        """Markets list enriched with scorecard rankings."""
        markets = self.get_markets()
        if not self.scorecard:
            return markets

        try:
            scored = self.scorecard.get_scored_markets()
            score_map = {m["market"]: m for m in scored}
        except Exception:
            score_map = {}

        for m in markets:
            sc = score_map.get(m["market"])
            if sc:
                m["score"] = sc.get("final_score")
                m["rank"] = sc.get("rank")
                m["ds_score"] = sc.get("ds_score")
                m["occ_score"] = sc.get("occ_score")
                m["rent_score"] = sc.get("rent_score")
            else:
                m["score"] = None
                m["rank"] = None

        return markets

    # ─── 8. Full market brief ──────────────────────────────────────

    def build_market_brief(self, market: str) -> MarketBrief:
        """Assemble a complete market intelligence brief."""
        brief = MarketBrief(market=market)

        brief.overview = self.get_market_overview(market)
        brief.cap_rates = self.get_cap_rate_summary(market)
        brief.sales = self.get_sales_activity(market)
        brief.pricing = self.get_pricing_trends(market)
        brief.scorecard = self.get_scorecard_context(market)
        brief.top_owners = self.get_top_owners(market)

        # Generate narrative signals
        brief.signals = self._generate_signals(brief)

        return brief

    # ─── 9. Market comparison ──────────────────────────────────────

    def compare_markets(self, market_names: list) -> list:
        """Side-by-side comparison of 2+ markets."""
        results = []
        for mkt in market_names:
            brief = self.build_market_brief(mkt)
            row = {
                "market": mkt,
                "properties": brief.overview.property_count if brief.overview else 0,
                "units": brief.overview.total_units if brief.overview else 0,
                "submarkets": brief.overview.submarkets if brief.overview else 0,
                "avg_vintage": brief.overview.avg_year_built if brief.overview else None,
                "cap_rate": brief.cap_rates.latest_median if brief.cap_rates else None,
                "cap_rate_yoy_bps": brief.cap_rates.yoy_change_bps if brief.cap_rates else None,
                "cap_rate_spread": brief.cap_rates.latest_spread if brief.cap_rates else None,
                "total_sales_volume": brief.sales.total_volume if brief.sales else 0,
                "total_deals": brief.sales.total_transactions if brief.sales else 0,
                "avg_ppu": brief.sales.avg_price_per_unit if brief.sales else 0,
                "median_ppu": brief.pricing.latest_median_ppu if brief.pricing else None,
                "ppu_yoy_pct": brief.pricing.yoy_ppu_change_pct if brief.pricing else None,
            }

            if brief.scorecard:
                row["score"] = brief.scorecard.get("final_score")
                row["rank"] = brief.scorecard.get("rank")

            results.append(row)

        return results

    # ─── Signal generation ─────────────────────────────────────────

    def _generate_signals(self, brief: MarketBrief) -> list:
        """Derive narrative signals from the data."""
        signals = []

        # Cap rate direction
        if brief.cap_rates and brief.cap_rates.yoy_change_bps is not None:
            bps = brief.cap_rates.yoy_change_bps
            if bps > 25:
                signals.append({
                    "type": "cap_rate", "sentiment": "negative",
                    "text": f"Cap rates expanded {bps:+.0f}bps YoY — buyer caution or repricing"
                })
            elif bps < -25:
                signals.append({
                    "type": "cap_rate", "sentiment": "positive",
                    "text": f"Cap rates compressed {bps:+.0f}bps YoY — capital chasing yield"
                })
            else:
                signals.append({
                    "type": "cap_rate", "sentiment": "neutral",
                    "text": f"Cap rates stable ({bps:+.0f}bps YoY)"
                })

        # Pricing momentum
        if brief.pricing and brief.pricing.yoy_ppu_change_pct is not None:
            pct = brief.pricing.yoy_ppu_change_pct
            if pct > 10:
                signals.append({
                    "type": "pricing", "sentiment": "positive",
                    "text": f"Price per unit up {pct:+.1f}% YoY — strong buyer conviction"
                })
            elif pct < -5:
                signals.append({
                    "type": "pricing", "sentiment": "negative",
                    "text": f"Price per unit down {pct:+.1f}% YoY — value correction"
                })

        # Deal volume trend
        if brief.sales and len(brief.sales.annual_deals) >= 2:
            years = sorted(brief.sales.annual_deals.keys())
            if len(years) >= 2:
                curr = brief.sales.annual_deals[years[-1]]
                prev = brief.sales.annual_deals[years[-2]]
                if prev > 0:
                    vol_chg = (curr - prev) / prev * 100
                    if vol_chg > 30:
                        signals.append({
                            "type": "volume", "sentiment": "positive",
                            "text": f"Deal count surged {vol_chg:+.0f}% YoY ({curr} deals)"
                        })
                    elif vol_chg < -30:
                        signals.append({
                            "type": "volume", "sentiment": "negative",
                            "text": f"Deal count dropped {vol_chg:+.0f}% YoY ({curr} deals)"
                        })

        # Scorecard
        if brief.scorecard:
            score = brief.scorecard.get("final_score")
            rank = brief.scorecard.get("rank")
            if score is not None and rank is not None:
                if rank <= 20:
                    signals.append({
                        "type": "scorecard", "sentiment": "positive",
                        "text": f"Top-20 market (rank #{rank}, score {score:.2f})"
                    })
                elif rank >= 180:
                    signals.append({
                        "type": "scorecard", "sentiment": "negative",
                        "text": f"Bottom-tier market (rank #{rank}, score {score:.2f})"
                    })

        # Spread analysis
        if brief.cap_rates and brief.cap_rates.latest_spread:
            spread = brief.cap_rates.latest_spread * 10000  # to bps
            if spread > 200:
                signals.append({
                    "type": "dispersion", "sentiment": "neutral",
                    "text": f"Wide cap rate spread ({spread:.0f}bps) — bifurcated market"
                })

        return signals
