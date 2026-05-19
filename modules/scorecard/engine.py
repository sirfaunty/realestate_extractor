"""
Scorecard Engine — warehouse-backed query + scoring layer.

Bridges the tilt engine (pure math) with the DuckDB warehouse (data).
Provides:
  - Market score storage/retrieval
  - Score computation from warehouse metrics
  - Drill-down explanations
  - Config management
"""

import json
import logging
from datetime import date
from dataclasses import asdict
from typing import List, Dict, Optional, Any

import numpy as np

from .tilt_engine import (
    ScorecardConfig, DEFAULT_CONFIG, MarketScore, PeriodScores, MetricZScores,
    score_market_period, score_market_all_periods, score_all_markets,
    compute_final_rankings, signal_indicator_z_scores_batch,
    get_duration_weights,
)

logger = logging.getLogger(__name__)


# ─── Schema for market score storage ────────────────────────────────

SCORE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS fact_market_score (
    market          VARCHAR NOT NULL,
    tier            VARCHAR NOT NULL,
    period          VARCHAR,
    score_type      VARCHAR NOT NULL,
    score_value     DOUBLE,
    rank            INTEGER,
    ds_score        DOUBLE,
    occ_score       DOUBLE,
    rent_score      DOUBLE,
    config_json     VARCHAR,
    scored_at       TIMESTAMP DEFAULT current_timestamp,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER
);
"""


class ScorecardEngine:
    """Query and scoring layer for the market scorecard."""

    def __init__(self, warehouse_engine):
        self.wh = warehouse_engine
        self._ensure_schema()

    def _ensure_schema(self):
        """Create the market score table if it doesn't exist."""
        try:
            self.wh.conn.execute(SCORE_TABLE_DDL)
        except Exception as e:
            if 'already exists' not in str(e).lower():
                logger.warning(f"Scorecard schema warning: {e}")

    # ─── Score Retrieval ──────────────────────────────────────────────

    def get_rankings(self, tier: str = None, limit: int = 100) -> List[Dict]:
        """Get latest market rankings."""
        where = ["score_type = 'final'"]
        params = []

        if tier:
            where.append("tier = ?")
            params.append(tier)

        sql = f"""
            SELECT market, tier, score_value as final_score,
                   ds_score, occ_score, rent_score, rank,
                   scored_at, knowledge_date
            FROM fact_market_score
            WHERE {' AND '.join(where)}
              AND scored_at = (SELECT MAX(scored_at) FROM fact_market_score
                               WHERE score_type = 'final')
            ORDER BY rank ASC NULLS LAST
            LIMIT {limit}
        """
        rows = self.wh.conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self.wh.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_market_score(self, market: str) -> Optional[Dict]:
        """Get the latest full score breakdown for a market."""
        # Get final score
        final = self.wh.conn.execute("""
            SELECT market, tier, score_value, ds_score, occ_score,
                   rent_score, rank, scored_at, knowledge_date, config_json
            FROM fact_market_score
            WHERE market = ? AND score_type = 'final'
            ORDER BY scored_at DESC
            LIMIT 1
        """, [market]).fetchone()

        if not final:
            return None

        cols = ['market', 'tier', 'final_score', 'ds_score', 'occ_score',
                'rent_score', 'rank', 'scored_at', 'knowledge_date', 'config_json']
        result = dict(zip(cols, final))

        # Get period-level scores
        periods = self.wh.conn.execute("""
            SELECT period, score_type, score_value, ds_score, occ_score, rent_score
            FROM fact_market_score
            WHERE market = ? AND score_type = 'period'
              AND scored_at = (SELECT MAX(scored_at) FROM fact_market_score
                               WHERE market = ? AND score_type = 'period')
            ORDER BY period
        """, [market, market]).fetchall()

        p_cols = ['period', 'score_type', 'mf_score', 'ds_score', 'occ_score', 'rent_score']
        result['period_scores'] = [dict(zip(p_cols, p)) for p in periods]

        # Get tier-level scores
        tiers = self.wh.conn.execute("""
            SELECT tier, score_value, ds_score, occ_score, rent_score
            FROM fact_market_score
            WHERE market = ? AND score_type = 'tier'
              AND scored_at = (SELECT MAX(scored_at) FROM fact_market_score
                               WHERE market = ? AND score_type = 'tier')
            ORDER BY tier
        """, [market, market]).fetchall()

        t_cols = ['tier', 'final_score', 'ds_score', 'occ_score', 'rent_score']
        result['tier_scores'] = [dict(zip(t_cols, t)) for t in tiers]

        return result

    def get_score_history(self, market: str) -> List[Dict]:
        """Get scoring history for a market."""
        rows = self.wh.conn.execute("""
            SELECT market, score_value as final_score, rank,
                   ds_score, occ_score, rent_score,
                   scored_at, knowledge_date
            FROM fact_market_score
            WHERE market = ? AND score_type = 'final'
            ORDER BY scored_at DESC
            LIMIT 20
        """, [market]).fetchall()
        cols = ['market', 'final_score', 'rank', 'ds_score', 'occ_score',
                'rent_score', 'scored_at', 'knowledge_date']
        return [dict(zip(cols, r)) for r in rows]

    # ─── Scoring ─────────────────────────────────────────────────────

    def score_from_warehouse(self, config: ScorecardConfig = None) -> Dict:
        """
        Score markets using warehouse data (cap rates, pricing, z-scores).

        This is the "lite" scoring path that derives market-level signals
        from existing warehouse data without requiring CoStar quarterly exports.
        """
        if config is None:
            config = DEFAULT_CONFIG

        # Get markets with enough data
        markets = self._get_scoreable_markets()
        if not markets:
            return {'error': 'No markets with sufficient data for scoring'}

        logger.info(f"Scoring {len(markets)} markets from warehouse data")

        # Build market-level metrics from warehouse
        market_metrics = {}
        for market_name in markets:
            metrics = self._build_market_metrics(market_name)
            if metrics:
                market_metrics[market_name] = metrics

        if not market_metrics:
            return {'error': 'Could not build metrics for any market'}

        # Score using tilt engine (single "All" tier for warehouse-derived data)
        all_market_data = {"All": {}}
        for market_name, metrics in market_metrics.items():
            all_market_data["All"][market_name] = self._metrics_to_tilt_input(
                metrics, config)

        tier_scores = score_all_markets(all_market_data, config)
        rankings = compute_final_rankings(tier_scores, config)

        # Store results
        self._store_scores(tier_scores, rankings, config)

        return {
            'markets_scored': len(market_metrics),
            'top_10': rankings.head(10).to_dict('records') if len(rankings) > 0 else [],
            'config': {
                'analysis_duration': config.analysis_duration_years,
                'ds_weight': config.ds_weight,
                'occ_weight': config.occ_weight,
                'rg_weight': config.rg_weight,
            },
        }

    def _get_scoreable_markets(self) -> List[str]:
        """Find markets with enough data for scoring."""
        rows = self.wh.conn.execute("""
            SELECT DISTINCT market FROM (
                SELECT market FROM fact_cap_rate_aggregate
                WHERE market IS NOT NULL AND granularity = 'market'
                INTERSECT
                SELECT market FROM fact_sales_transaction
                WHERE market IS NOT NULL
            )
            ORDER BY market
        """).fetchall()
        return [r[0] for r in rows]

    def _build_market_metrics(self, market: str) -> Optional[Dict]:
        """Build scoring metrics for a market from warehouse data."""
        try:
            # Cap rate trends → proxy for demand/supply signal
            cap_rates = self.wh.conn.execute("""
                SELECT period, cap_rate_median, cap_rate_mean, cap_rate_std, n_deals
                FROM fact_cap_rate_aggregate
                WHERE market = ? AND period_type = 'year' AND is_clean = true
                  AND granularity = 'market'
                ORDER BY period DESC
                LIMIT 12
            """, [market]).fetchall()

            if len(cap_rates) < 3:
                return None

            # Pricing trends → proxy for rent growth
            pricing = self.wh.conn.execute("""
                SELECT sale_year, median_ppu, mean_ppu, median_ppsf, n_deals
                FROM fact_pricing_aggregate
                WHERE market = ? AND granularity = 'market'
                ORDER BY sale_year DESC
                LIMIT 12
            """, [market]).fetchall()

            # Transaction volume → proxy for absorption/liquidity
            volume = self.wh.conn.execute("""
                SELECT sale_year, count(*) as deals,
                       CAST(sum(sale_price) AS DOUBLE) as total_volume,
                       median(price_per_unit) as med_ppu,
                       median(cap_rate_actual) as med_cap
                FROM fact_sales_transaction
                WHERE market = ? AND sale_price IS NOT NULL
                GROUP BY sale_year
                ORDER BY sale_year DESC
                LIMIT 12
            """, [market]).fetchall()

            # Z-score coverage → property quality signal
            zscore_stats = self.wh.conn.execute("""
                SELECT avg(z.z_score) as avg_z, count(DISTINCT z.property_id) as scored
                FROM fact_property_zscore z
                JOIN dim_property p ON z.property_id = p.property_id
                WHERE p.market = ?
            """, [market]).fetchone()

            return {
                'market': market,
                'cap_rates': [dict(zip(['period', 'median', 'mean', 'std', 'n'], r))
                              for r in cap_rates],
                'pricing': [dict(zip(['year', 'med_ppu', 'mean_ppu', 'med_ppsf', 'n'], r))
                            for r in pricing],
                'volume': [dict(zip(['year', 'deals', 'total_vol', 'med_ppu', 'med_cap'], r))
                           for r in volume],
                'avg_z': zscore_stats[0] if zscore_stats else 0.0,
                'scored_properties': zscore_stats[1] if zscore_stats else 0,
            }
        except Exception as e:
            logger.warning(f"Failed to build metrics for {market}: {e}")
            return None

    def _metrics_to_tilt_input(self, metrics: Dict,
                                config: ScorecardConfig) -> Dict[str, dict]:
        """Convert warehouse metrics to the format expected by score_market_all_periods."""
        periods = {}

        # Derive signals from cap rate & pricing data
        cap_rates = metrics.get('cap_rates', [])
        pricing = metrics.get('pricing', [])
        volume = metrics.get('volume', [])

        # Use cap rate compression as demand signal (falling caps = strong demand)
        cap_medians = [cr['median'] for cr in cap_rates if cr['median'] is not None]
        ppu_values = [p['med_ppu'] for p in pricing if p['med_ppu'] is not None]
        vol_deals = [v['deals'] for v in volume if v['deals'] is not None]

        # Create period data with available signals
        for period_name in config.period_weights:
            if config.period_weights[period_name] == 0:
                continue

            # Build signal indicators from available data
            signal_indicators = {}
            volatility_indicators = {}

            # Cap rate signal: lower = better demand (invert direction)
            if cap_medians:
                signal_indicators['absorption'] = -np.mean(cap_medians[:3]) if len(cap_medians) >= 3 else 0.0
                signal_indicators['deliveries'] = 0.0
                signal_indicators['abs_del'] = signal_indicators['absorption']
                volatility_indicators['absorption'] = np.std(cap_medians[:5]) if len(cap_medians) >= 5 else 0.0

            # Volume signal: more deals = more liquid market
            if vol_deals:
                signal_indicators['under_construction'] = 0.0
                signal_indicators['yrs_to_stab'] = 0.0

            # PPU signal → rent growth proxy
            if len(ppu_values) >= 2:
                ppu_change = (ppu_values[0] - ppu_values[-1]) / ppu_values[-1] if ppu_values[-1] else 0.0
                for rk in config.rent_metric_weights:
                    signal_indicators[rk] = ppu_change
                    volatility_indicators[rk] = 0.0

            # Occupancy proxied from z-score data
            avg_z = metrics.get('avg_z', 0.0) or 0.0
            signal_indicators['actual_occ'] = avg_z * 0.5
            signal_indicators['effective_occ'] = avg_z * 0.5
            signal_indicators['blended_occ'] = avg_z * 0.5

            ds_period_z = config.ds_period_signal_z.get(period_name, 0.9)
            occ_period_z = config.occ_period_signal_z.get(period_name, 0.7)
            rent_period_z = config.rent_period_signal_z.get(period_name, 0.5)

            periods[period_name] = {
                "signal_indicators": signal_indicators,
                "volatility_indicators": volatility_indicators,
                "ds_category_values": {k: list(signal_indicators.values())
                                       for k in config.ds_metric_weights},
                "occ_category_values": {},
                "rent_category_values": {k: list(signal_indicators.values())
                                         for k in config.rent_metric_weights},
                "ds_period_signal_z": ds_period_z,
                "occ_period_signal_z": occ_period_z,
                "rent_period_signal_z": rent_period_z,
                "tilt_value": 1.0,
            }

        return periods

    def _store_scores(self, tier_scores: Dict, rankings, config: ScorecardConfig):
        """Store scoring results in the warehouse."""
        today = date.today().isoformat()
        config_json = json.dumps({
            'analysis_duration': config.analysis_duration_years,
            'ds_weight': config.ds_weight,
            'occ_weight': config.occ_weight,
            'rg_weight': config.rg_weight,
        })

        rows = []

        # Store per-tier scores
        for tier, markets in tier_scores.items():
            for market_id, ms in markets.items():
                rows.append((market_id, tier, None, 'tier', ms.final_score,
                             None, ms.duration_weighted_ds, ms.duration_weighted_occ,
                             ms.duration_weighted_rent, config_json, today, None))

                # Store per-period scores
                for period, ps in ms.period_scores.items():
                    rows.append((market_id, tier, period, 'period', ps.overall_mf,
                                 None, ps.overall_ds_adj, ps.overall_occ_adj,
                                 ps.overall_rent_adj, None, today, None))

        # Store final rankings
        if len(rankings) > 0:
            for _, row in rankings.iterrows():
                rows.append((row['market_id'], 'weighted', None, 'final',
                             row['final_score'], int(row['rank']),
                             row['ds_score'], row.get('occ_score', 0.0),
                             row['rent_score'], config_json, today, None))

        if rows:
            self.wh.conn.executemany("""
                INSERT INTO fact_market_score
                (market, tier, period, score_type, score_value, rank,
                 ds_score, occ_score, rent_score, config_json, knowledge_date, ingestion_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            logger.info(f"Stored {len(rows)} score records")

    # ─── Market Queries ──────────────────────────────────────────────

    def get_scored_markets(self) -> List[Dict]:
        """List all markets that have been scored."""
        rows = self.wh.conn.execute("""
            SELECT market, score_value as final_score, rank,
                   ds_score, occ_score, rent_score, scored_at
            FROM fact_market_score
            WHERE score_type = 'final'
              AND scored_at = (SELECT MAX(scored_at) FROM fact_market_score
                               WHERE score_type = 'final')
            ORDER BY rank ASC NULLS LAST
        """).fetchall()
        cols = ['market', 'final_score', 'rank', 'ds_score', 'occ_score',
                'rent_score', 'scored_at']
        return [dict(zip(cols, r)) for r in rows]

    def get_config(self) -> Dict:
        """Return the default scoring configuration as a dict."""
        cfg = DEFAULT_CONFIG
        return {
            'tier_weights': cfg.tier_weights,
            'category_weights': {
                'demand_supply': cfg.ds_weight,
                'occupancy': cfg.occ_weight,
                'rent_growth': cfg.rg_weight,
            },
            'analysis_duration_years': cfg.analysis_duration_years,
            'period_weights': cfg.period_weights,
            'momentum': {
                'knob': cfg.mom_knob,
                'config': {k: {'hl_steps': v[0], 'max_tilt': v[1], 'hl_qtrs': v[2]}
                           for k, v in cfg.momentum_config.items()},
            },
            'occupancy_blend': {
                'actual': cfg.actual_occ_weight,
                'effective': cfg.effective_occ_weight,
            },
            'indicators': {
                'category': {'cap': cfg.category_indicator[0],
                             'w_impact': cfg.category_indicator[1],
                             'floor': cfg.category_indicator[2]},
                'volatility': {'cap': cfg.volatility_indicator[0],
                               'w_impact': cfg.volatility_indicator[1],
                               'floor': cfg.volatility_indicator[2]},
                'period': {'cap': cfg.period_indicator[0],
                           'w_impact': cfg.period_indicator[1],
                           'floor': cfg.period_indicator[2]},
            },
            'z_clamp': {'cap': cfg.total_z_cap, 'floor': cfg.total_z_floor},
        }

    def explain_score(self, market: str) -> Optional[Dict]:
        """Generate a detailed breakdown of how a market's score was computed."""
        score = self.get_market_score(market)
        if not score:
            return None

        explanation = {
            'market': market,
            'final_score': score.get('final_score', 0),
            'rank': score.get('rank'),
            'components': {
                'demand_supply': {
                    'score': score.get('ds_score', 0),
                    'weight': DEFAULT_CONFIG.ds_weight,
                    'contribution': (score.get('ds_score', 0) or 0) * DEFAULT_CONFIG.ds_weight,
                },
                'occupancy': {
                    'score': score.get('occ_score', 0),
                    'weight': DEFAULT_CONFIG.occ_weight,
                    'contribution': (score.get('occ_score', 0) or 0) * DEFAULT_CONFIG.occ_weight,
                },
                'rent_growth': {
                    'score': score.get('rent_score', 0),
                    'weight': DEFAULT_CONFIG.rg_weight,
                    'contribution': (score.get('rent_score', 0) or 0) * DEFAULT_CONFIG.rg_weight,
                },
            },
            'periods': score.get('period_scores', []),
            'tiers': score.get('tier_scores', []),
        }

        return explanation

    # ─── Scenario Comparison ─────────────────────────────────────────

    def compare_scenarios(self, market: str, scenarios: List[Dict]) -> List[Dict]:
        """
        Run multiple scoring scenarios for a market with different configs.

        Each scenario: {'name': str, 'config_overrides': dict}
        """
        base_metrics = self._build_market_metrics(market)
        if not base_metrics:
            return []

        results = []
        for scenario in scenarios:
            cfg = ScorecardConfig()
            overrides = scenario.get('config_overrides', {})

            # Apply overrides
            if 'ds_weight' in overrides:
                cfg.ds_weight = overrides['ds_weight']
            if 'occ_weight' in overrides:
                cfg.occ_weight = overrides['occ_weight']
            if 'rg_weight' in overrides:
                cfg.rg_weight = overrides['rg_weight']
            if 'analysis_duration' in overrides:
                cfg.analysis_duration_years = overrides['analysis_duration']

            periods = self._metrics_to_tilt_input(base_metrics, cfg)
            ms = score_market_all_periods(periods, cfg)

            results.append({
                'name': scenario.get('name', 'unnamed'),
                'final_score': ms.final_score,
                'ds_score': ms.duration_weighted_ds,
                'occ_score': ms.duration_weighted_occ,
                'rent_score': ms.duration_weighted_rent,
                'config': overrides,
            })

        return results
