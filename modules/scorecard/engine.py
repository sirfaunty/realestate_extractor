"""
Scorecard Engine — warehouse-backed query + scoring layer.

Bridges the tilt engine (pure math) with the DuckDB warehouse (data).
Provides:
  - Market score storage/retrieval
  - Score computation from warehouse metrics
  - Drill-down explanations
  - Config management
"""

import contextlib
import io
import json
import logging
import os
import pickle
import threading
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
            # Market name resolution: fact tables use "City, ST" but
            # dim_property uses bare "City".  Try exact match first,
            # then fall back to the bare city name.
            bare_market = market.split(',')[0].strip()
            zscore_stats = self.wh.conn.execute("""
                SELECT avg(z.z_score) as avg_z, count(DISTINCT z.property_id) as scored
                FROM fact_property_zscore z
                JOIN dim_property p ON z.property_id = p.property_id
                WHERE p.market = ? OR p.market = ?
            """, [market, bare_market]).fetchone()

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
                "occ_category_values": {k: list(signal_indicators.values())
                                        for k in config.occ_metric_weights},
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

    # ─── Demographics scoring (ported from capactive-scorecard) ─────

    def _build_demo_config(self, config_overrides: dict):
        """Map flat config overrides (frontend getDemoConfig) → DemoScorecardConfig."""
        from .demo_engine import DemoScorecardConfig
        config = DemoScorecardConfig()
        co = dict(config_overrides) if config_overrides else {}

        # Category weights + duration
        if "analysis_duration_years" in co:
            config.analysis_duration_years = int(co["analysis_duration_years"])
        if "pop_weight" in co:
            config.pop_weight = float(co["pop_weight"])
        if "afford_weight" in co:
            config.afford_weight = float(co["afford_weight"])
        if "emp_weight" in co:
            config.emp_weight = float(co["emp_weight"])

        # Affordability sub-weights
        if "afford_overall_weight" in co:
            config.afford_overall_weight = float(co["afford_overall_weight"])
        if "afford_snapshot_weight" in co:
            config.afford_snapshot_weight = float(co["afford_snapshot_weight"])
        if "afford_unit_dispersion_weight" in co:
            config.afford_unit_dispersion_weight = float(co["afford_unit_dispersion_weight"])

        # Broad dispersion weight
        if "demo_dispersion_weight" in co:
            config.demo_dispersion_weight = float(co["demo_dispersion_weight"])
        if "demo_dispersion_cap" in co:
            config.demo_dispersion_cap = float(co["demo_dispersion_cap"])
        if "demo_dispersion_floor" in co:
            config.demo_dispersion_floor = float(co["demo_dispersion_floor"])

        # Drivers vs Context weights
        if "pop_drivers_weight" in co:
            config.pop_drivers_weight = float(co["pop_drivers_weight"])
        if "emp_drivers_weight" in co:
            config.emp_drivers_weight = float(co["emp_drivers_weight"])

        # Demographic Growth Drivers (DGD) sub-weights
        if "dgd_hhpop_weight" in co:
            config.dgd_hhpop_weight = float(co["dgd_hhpop_weight"])
        if "dgd_hh_formation_weight" in co:
            config.dgd_hh_formation_weight = float(co["dgd_hh_formation_weight"])
        if "dgd_income_weight" in co:
            config.dgd_income_weight = float(co["dgd_income_weight"])
        if "dgd_emp_weight" in co:
            config.dgd_emp_weight = float(co["dgd_emp_weight"])
        if "dgd_hh_weight" in co:
            config.dgd_hh_weight = float(co["dgd_hh_weight"])
        if "dgd_pop_weight" in co:
            config.dgd_pop_weight = float(co["dgd_pop_weight"])
        if "dgd_emp_total_weight" in co:
            config.dgd_emp_total_weight = float(co["dgd_emp_total_weight"])
        if "dgd_emp_office_weight" in co:
            config.dgd_emp_office_weight = float(co["dgd_emp_office_weight"])
        if "dgd_emp_industrial_weight" in co:
            config.dgd_emp_industrial_weight = float(co["dgd_emp_industrial_weight"])

        # Tilt applicator indicator params
        if "vol_weight" in co:
            v = float(co["vol_weight"])
            config.volatility_indicator = (config.volatility_indicator[0], v, config.volatility_indicator[2])
        if "cat_weight" in co:
            c = float(co["cat_weight"])
            config.category_indicator = (config.category_indicator[0], c, config.category_indicator[2])
        if "period_weight" in co:
            p = float(co["period_weight"])
            config.period_indicator = (config.period_indicator[0], p, config.period_indicator[2])

        # Momentum
        if "mom_knob" in co:
            config.mom_knob = float(co["mom_knob"])
        if "momentum_mult" in co:
            config.recent_momentum_tilt_multiplier = float(co["momentum_mult"])

        # Z-score caps
        if "z_cap" in co:
            config.total_z_cap = float(co["z_cap"])
        if "z_floor" in co:
            config.total_z_floor = float(co["z_floor"])

        # Per-period momentum config overrides
        if "momentum_config" in co and isinstance(co["momentum_config"], dict):
            for period_name, vals in co["momentum_config"].items():
                if isinstance(vals, list) and len(vals) >= 2:
                    hl = float(vals[0])
                    mt = float(vals[1])
                    existing = config.momentum_config.get(period_name, (hl, mt, 8))
                    hl_q = existing[2] if len(existing) >= 3 else 8
                    config.momentum_config[period_name] = (hl, mt, hl_q)

        # Direction overrides — flip signal Z direction for specific metrics
        dir_ov = {}
        if co.get("flip_inv_pop"):
            dir_ov["mf_inv_pop"] = True
            dir_ov["mf_inv_pop_growth"] = True
        if dir_ov:
            config.direction_overrides = dir_ov

        return config

    def _demo_peer_display(self, classifications, inventory_tier, region, region_type):
        """Build the Z-score peer group set and the display-filter set.

        Peer group (Z-score normalization population): markets in the selected
        inventory tier, or None (all markets = the Over-50K dataset) for "All".
        Display filter: which markets to show — tier, optionally narrowed by
        region. None means show all scored markets.
        """
        cols = getattr(classifications, "columns", [])

        def tier_markets(tier):
            if "inventory_tier" in cols:
                return set(classifications[classifications["inventory_tier"] == tier]["market"].unique())
            return set()

        peer = tier_markets(inventory_tier) if inventory_tier != "All" else None

        display = None
        if inventory_tier != "All":
            display = tier_markets(inventory_tier)
        if region != "All" and region_type == "specific" and "specific_region" in cols:
            region_markets = set(
                classifications[classifications["specific_region"] == region]["market"].unique()
            )
            display = (display & region_markets) if display is not None else region_markets

        return peer, display

    def score_demographics(self, costar_file: str, config_overrides: dict = None) -> Dict:
        """Score markets on the Demographics model (population, affordability,
        employment). Ported from capactive-scorecard's run_demo_scoring; reuses
        the cached CoStar detail results and the same market classifications."""
        from .demo_engine import (
            score_demo_tier, CAT_POP_METRICS, CAT_AFFORD_METRICS, CAT_EMP_METRICS,
        )

        all_detail_results, classifications = self._get_costar_cache(costar_file)
        co = dict(config_overrides) if config_overrides else {}
        config = self._build_demo_config(co)

        property_class = co.get("property_class", "All")
        region = co.get("region", "All")
        region_type = co.get("region_type", "general")
        inventory_tier = co.get("inventory_tier", "All")

        peer, display = self._demo_peer_display(
            classifications, inventory_tier, region, region_type
        )

        def _demo_group(mk):
            if mk in CAT_POP_METRICS:
                return "Pop/HH"
            if mk in CAT_AFFORD_METRICS:
                return "Afford"
            if mk in CAT_EMP_METRICS:
                return "Employ"
            return "Other"

        scores = score_demo_tier(
            all_detail_results,
            property_class=property_class,
            config=config,
            peer_group_markets=peer,
        )

        rows = []
        for market, ms in scores.items():
            if display is not None and market not in display:
                continue
            row = {
                "market": market,
                "demo_score": round(ms.final_score, 4),
                "pop_score": round(ms.duration_weighted_pop, 4),
                "afford_score": round(ms.duration_weighted_afford, 4),
                "emp_score": round(ms.duration_weighted_emp, 4),
                "metrics": {},
            }
            drill_period = None
            for pname in ["Window", "Q1"]:
                if pname in ms.period_scores:
                    drill_period = ms.period_scores[pname]
                    break
            if drill_period is None and ms.period_scores:
                drill_period = next(iter(ms.period_scores.values()))

            if drill_period is not None:
                all_metric_z = {}
                all_metric_z.update(drill_period.pop_metric_z)
                all_metric_z.update(drill_period.afford_metric_z)
                all_metric_z.update(drill_period.emp_metric_z)
                for mk, mz in all_metric_z.items():
                    row["metrics"][mk] = {
                        "signal_z": round(mz.signal_z, 4),
                        "vol_z": round(mz.volatility_z, 4),
                        "cat_z": round(mz.category_z, 4),
                        "total_z": round(mz.total_z, 4),
                        "group": _demo_group(mk),
                    }
            rows.append(row)

        rows.sort(key=lambda r: r["demo_score"], reverse=True)
        n = len(rows)
        for i, r in enumerate(rows):
            r["demo_rank"] = i + 1
            r["demo_percentile"] = round((1 - i / max(n - 1, 1)) * 100, 1) if n > 1 else 50.0

        for key, fld in [("pop_score", "pop_rank"), ("afford_score", "afford_rank"), ("emp_score", "emp_rank")]:
            sorted_rows = sorted(rows, key=lambda r: r[key], reverse=True)
            for i, r in enumerate(sorted_rows):
                r[fld] = i + 1

        return {
            "markets": rows,
            "market_count": n,
            "property_class": property_class,
        }

    # Peer-group results are expensive (each slice scores MF + demographics;
    # unfiltered configs generate ~60 slices → minutes). Results are pure
    # functions of (filter config, source data), so cache them in memory and
    # on disk, keyed on the filter fields + source mtimes.
    _pg_lock = threading.Lock()

    def _pg_store_path(self, costar_file: str) -> str:
        base, _ = os.path.splitext(costar_file)
        return base + ".peergroups.pkl"

    def _pg_data_version(self, costar_file: str):
        try:
            v = os.path.getmtime(costar_file)
            ref = os.path.join(os.path.dirname(__file__), "reference_data.json")
            if os.path.exists(ref):
                v = max(v, os.path.getmtime(ref))
            return v
        except Exception:
            return None

    def peer_group_summary(self, costar_file: str, config_overrides: dict = None) -> Dict:
        """Cached peer-group comparison across tier/unit/region slices."""
        co = dict(config_overrides) if config_overrides else {}
        # Only these fields influence the output (the rest are ignored by the
        # computation, mirroring the source app).
        key_fields = {
            "inventory_tier": co.get("inventory_tier", "All"),
            "property_class": co.get("property_class", "All"),
            "region": co.get("region", "All"),
            "region_type": co.get("region_type", "general"),
            "analysis_duration_years": int(co.get("analysis_duration_years", 10)),
        }
        # region_type is irrelevant when no region is selected — normalize it
        # so 'general' and 'specific' map to the same cache entry.
        if key_fields["region"] == "All":
            key_fields["region_type"] = "general"
        key = json.dumps(key_fields, sort_keys=True)
        version = self._pg_data_version(costar_file)

        mem = getattr(self, "_pg_cache", None)
        if mem is not None and mem.get("version") == version and key in mem["entries"]:
            logger.info("Peer group summary: memory cache hit")
            return mem["entries"][key]

        with self._pg_lock:
            # Re-check under the lock.
            mem = getattr(self, "_pg_cache", None)
            if mem is not None and mem.get("version") == version and key in mem["entries"]:
                return mem["entries"][key]

            # Disk tier.
            store_path = self._pg_store_path(costar_file)
            if (mem is None or mem.get("version") != version) and os.path.exists(store_path):
                try:
                    disk = pickle.loads(open(store_path, "rb").read())
                    if disk.get("version") == version:
                        self._pg_cache = disk
                        mem = disk
                        if key in disk["entries"]:
                            logger.info("Peer group summary: disk cache hit")
                            return disk["entries"][key]
                except Exception as e:
                    logger.warning(f"Peer group cache load failed ({e}); recomputing.")

            # Compute and persist.
            result = self._compute_peer_group_summary(costar_file, config_overrides)
            if mem is None or mem.get("version") != version:
                mem = {"version": version, "entries": {}}
            mem["entries"][key] = result
            self._pg_cache = mem
            try:
                tmp = store_path + ".tmp"
                with open(tmp, "wb") as fh:
                    pickle.dump(mem, fh, protocol=pickle.HIGHEST_PROTOCOL)
                os.replace(tmp, store_path)
            except Exception as e:
                logger.warning(f"Peer group cache save skipped ({e}).")
            return result

    def _compute_peer_group_summary(self, costar_file: str, config_overrides: dict = None) -> Dict:
        """Peer-group comparison across tier/unit/region slices.

        Ported from capactive-scorecard run_peer_group_summary. For each
        eligible slice, scores both MF fundamentals and demographics against
        that slice's peer group and returns aggregate means.
        """
        import numpy as np
        from .z_score_engine import score_tier
        from .tilt_engine import ScorecardConfig
        from .demo_engine import score_demo_tier, DemoScorecardConfig

        all_detail_results, classifications = self._get_costar_cache(costar_file)
        if hasattr(classifications, "set_index"):
            cls_dict = classifications.set_index("market").to_dict(orient="index")
        else:
            cls_dict = dict(classifications)
        all_markets = set(cls_dict.keys())

        def build_peer(tier):
            if tier != "All":
                return set(m for m, c in cls_dict.items() if c.get("inventory_tier") == tier)
            return set(all_markets)

        def build_display(base, region_type, region, tier):
            result = None
            if tier != "All":
                result = set(m for m, c in cls_dict.items() if c.get("inventory_tier") == tier)
            if region != "All" and region_type == "specific":
                region_markets = set(
                    m for m, c in cls_dict.items() if c.get("specific_region") == region
                )
                result = result.intersection(region_markets) if result is not None else region_markets
            return result

        co = dict(config_overrides) if config_overrides else {}
        active_inv_tier = co.pop("inventory_tier", "All")
        active_unit_tier = co.pop("property_class", "All")
        active_region = co.pop("region", "All")
        active_region_type = co.pop("region_type", "general")
        duration_years = int(co.pop("analysis_duration_years", 10))

        has_inv_filter = active_inv_tier != "All"
        has_region_filter = active_region != "All"
        has_unit_filter = active_unit_tier != "All"

        inv_tiers = sorted(set(c.get("inventory_tier") for c in cls_dict.values() if c.get("inventory_tier")))
        regions = sorted(set(c.get("specific_region") for c in cls_dict.values() if c.get("specific_region")))
        unit_tiers = ["4 & 5 Star", "3 Star", "1 & 2 Star"]

        slices = []
        if not has_inv_filter and not has_region_filter and not has_unit_filter:
            slices.append({"label": "All Markets", "inventory_tier": "All", "region": "All",
                           "region_type": "general", "property_class": "All", "category": "All Markets"})
        if not has_inv_filter:
            for tier in inv_tiers:
                slices.append({"label": tier, "inventory_tier": tier,
                               "region": active_region if has_region_filter else "All",
                               "region_type": active_region_type if has_region_filter else "general",
                               "property_class": active_unit_tier if has_unit_filter else "All",
                               "category": "Inventory Tier"})
        if not has_unit_filter:
            for ut in unit_tiers:
                slices.append({"label": ut, "inventory_tier": active_inv_tier if has_inv_filter else "All",
                               "region": active_region if has_region_filter else "All",
                               "region_type": active_region_type if has_region_filter else "general",
                               "property_class": ut, "category": "Unit Tier"})
        if not has_region_filter:
            for region in regions:
                slices.append({"label": region, "inventory_tier": active_inv_tier if has_inv_filter else "All",
                               "region": region, "region_type": "specific",
                               "property_class": active_unit_tier if has_unit_filter else "All",
                               "category": "Region"})
        if not has_inv_filter and not has_unit_filter:
            for tier in inv_tiers:
                for ut in unit_tiers:
                    slices.append({"label": f"{tier} + {ut}", "inventory_tier": tier,
                                   "region": active_region if has_region_filter else "All",
                                   "region_type": active_region_type if has_region_filter else "general",
                                   "property_class": ut, "category": "Tier + Unit"})
        if not has_region_filter and not has_unit_filter:
            for ut in unit_tiers:
                for reg in regions:
                    slices.append({"label": f"{reg} + {ut}",
                                   "inventory_tier": active_inv_tier if has_inv_filter else "All",
                                   "region": reg, "region_type": "specific", "property_class": ut,
                                   "category": "Region + Unit", "subgroup": ut})
        if not has_inv_filter and not has_region_filter:
            for tier in inv_tiers:
                for reg in regions:
                    slices.append({"label": f"{tier} + {reg}", "inventory_tier": tier,
                                   "region": reg, "region_type": "specific",
                                   "property_class": active_unit_tier if has_unit_filter else "All",
                                   "category": "Tier + Region"})

        groups = []
        for sl in slices:
            try:
                base_peer = build_peer(sl["inventory_tier"])
                peer = build_display(base_peer, sl["region_type"], sl["region"], sl["inventory_tier"])
                if peer is None:
                    peer = base_peer
                if len(peer) < 3:
                    continue

                config = ScorecardConfig()
                config.analysis_duration_years = duration_years
                scores = score_tier(all_detail_results, property_class=sl["property_class"],
                                    config=config, peer_group_markets=peer)

                mf_vals, ds_vals, occ_vals, rent_vals = [], [], [], []
                for m, ms in scores.items():
                    if m in peer:
                        ps = ms.period_scores.get("Q1")
                        if ps:
                            mf_vals.append(ps.overall_mf)
                            ds_vals.append(ps.overall_ds_adj)
                            occ_vals.append(ps.overall_occ_adj)
                            rent_vals.append(ps.overall_rent_adj)
                if not mf_vals:
                    continue

                entry = {
                    "label": sl["label"], "category": sl["category"], "market_count": len(peer),
                    "mf_mean": round(float(np.mean(mf_vals)), 4),
                    "mf_median": round(float(np.median(mf_vals)), 4),
                    "ds_mean": round(float(np.mean(ds_vals)), 4),
                    "occ_mean": round(float(np.mean(occ_vals)), 4),
                    "rent_mean": round(float(np.mean(rent_vals)), 4),
                    "mf_std": round(float(np.std(mf_vals)), 4),
                    "mf_min": round(float(np.min(mf_vals)), 4),
                    "mf_max": round(float(np.max(mf_vals)), 4),
                }
                if sl.get("subgroup"):
                    entry["subgroup"] = sl["subgroup"]

                try:
                    demo_config = DemoScorecardConfig()
                    demo_config.analysis_duration_years = duration_years
                    demo_scores = score_demo_tier(all_detail_results, property_class=sl["property_class"],
                                                  config=demo_config, peer_group_markets=peer)
                    demo_vals = [ds.final_score for m, ds in demo_scores.items() if m in peer]
                    entry["demo_mean"] = round(float(np.mean(demo_vals)), 4) if demo_vals else None
                except Exception:
                    entry["demo_mean"] = None

                groups.append(entry)
            except Exception as e:
                logger.warning(f"Peer group slice '{sl['label']}' failed: {e}")
                continue

        _cat_order = {"All Markets": 0, "Inventory Tier": 1, "Unit Tier": 2, "Region": 3,
                      "Tier + Unit": 4, "Region + Unit": 5, "Tier + Region": 6}
        _unit_order = {"4 & 5 Star": 0, "3 Star": 1, "1 & 2 Star": 2}
        groups.sort(key=lambda g: (
            _cat_order.get(g["category"], 9),
            _unit_order.get(g.get("subgroup") or g.get("label", ""), 99),
            -g["mf_mean"],
        ))

        return {
            "groups": groups,
            "active_filters": {
                "inventory_tier": active_inv_tier,
                "unit_tier": active_unit_tier,
                "region": active_region,
            },
        }

    # ─── CoStar Data Cache (mirrors original _engine_cache) ─────────

    _costar_cache_file = None
    _costar_data = None
    _costar_classifications = None
    _costar_detail_results = None
    # Serializes cold-cache population so concurrent first-calls don't each
    # recompute the ~25s metric-detail pass (thundering-herd guard).
    _costar_cache_lock = threading.Lock()

    @staticmethod
    def _costar_detail_cache_path(costar_file: str) -> str:
        """Sidecar path for the processed detail-results cache."""
        base, _ = os.path.splitext(costar_file)
        return base + ".details.pkl"

    def _get_costar_cache(self, costar_file: str):
        """
        Load and cache CoStar data, classifications, and metric details.

        Three-tier cache, fastest first:
          1. In-memory (per-process) — instant after the first call.
          2. On-disk processed sidecar (<file>.details.pkl) — survives restarts,
             skips the ~25s compute_detail pass. Keyed on source mtime.
          3. Full recompute (parse + classify + 18x compute_detail), then both
             caches are populated for next time.

        A lock ensures only one thread performs the cold population; others
        wait and then hit the in-memory cache.
        """
        # Fast path: already in memory.
        if self._costar_detail_results is not None and self._costar_cache_file == costar_file:
            logger.info("Using cached CoStar data (instant)")
            return self._costar_detail_results, self._costar_classifications

        with self._costar_cache_lock:
            # Re-check under the lock — another thread may have just populated it.
            if self._costar_detail_results is not None and self._costar_cache_file == costar_file:
                return self._costar_detail_results, self._costar_classifications

            # Tier 2: on-disk processed sidecar, valid if newer than the sources.
            # The cached payload embeds classifications, which depend on BOTH the
            # CoStar export and reference_data.json (region/tier assignments) —
            # so a reference-data update must also invalidate the sidecar.
            sidecar = self._costar_detail_cache_path(costar_file)
            ref_data = os.path.join(os.path.dirname(__file__), "reference_data.json")
            try:
                source_mtime = os.path.getmtime(costar_file)
                if os.path.exists(ref_data):
                    source_mtime = max(source_mtime, os.path.getmtime(ref_data))
                if (os.path.exists(sidecar)
                        and os.path.getmtime(sidecar) >= source_mtime):
                    logger.info(f"Loading processed detail cache: {sidecar}")
                    payload = pickle.loads(open(sidecar, "rb").read())
                    self._costar_cache_file = costar_file
                    self._costar_data = payload.get("data")
                    self._costar_classifications = payload["classifications"]
                    self._costar_detail_results = payload["detail_results"]
                    logger.info(
                        f"Detail cache hit: {len(self._costar_detail_results)} metrics, "
                        f"{len(self._costar_classifications)} markets")
                    return self._costar_detail_results, self._costar_classifications
            except Exception as e:
                logger.warning(f"Detail cache load failed ({e}); recomputing.")

            # Tier 3: full recompute.
            return self._compute_costar_cache(costar_file, sidecar)

    def _compute_costar_cache(self, costar_file: str, sidecar: str):
        """Parse + classify + compute all metric details, then persist both caches."""
        from .data_loader import load_costar_export
        from .metric_calculator import compute_detail, METRIC_DEFINITIONS
        from .market_classifier import classify_markets

        logger.info(f"Loading CoStar data from {costar_file} (first call — will cache)")
        # These library functions print hundreds of progress lines; on a Windows
        # console that stdout I/O dominates the runtime. Silence it during the
        # build (logging still flows to handlers).
        with contextlib.redirect_stdout(io.StringIO()):
            data = load_costar_export(costar_file)
            classifications = classify_markets(data)

            logger.info("Computing metric details (18 metrics)...")
            all_detail_results = {}
            for mk in METRIC_DEFINITIONS:
                try:
                    all_detail_results[mk] = compute_detail(
                        data, mk, classifications, half_life=8,
                    )
                except Exception as e:
                    logger.warning(f"Metric {mk} failed: {e}")

        # Populate in-memory cache.
        self._costar_cache_file = costar_file
        self._costar_data = data
        self._costar_classifications = classifications
        self._costar_detail_results = all_detail_results

        # Persist processed sidecar so the next cold start skips the compute pass.
        try:
            tmp = sidecar + ".tmp"
            with open(tmp, "wb") as fh:
                pickle.dump(
                    {"classifications": classifications,
                     "detail_results": all_detail_results},
                    fh, protocol=pickle.HIGHEST_PROTOCOL,
                )
            os.replace(tmp, sidecar)
            logger.info(f"Saved processed detail cache: {sidecar}")
        except Exception as e:
            logger.warning(f"Could not write detail cache ({e}); continuing.")

        logger.info(f"Cached: {len(all_detail_results)} metrics, "
                    f"{len(classifications)} markets")
        return all_detail_results, classifications

    # ─── CoStar Data Pipeline ───────────────────────────────────────

    def score_from_costar(
        self,
        costar_file: str,
        config: ScorecardConfig = None,
        config_overrides: dict = None,
        property_class: str = "All",
        inventory_tier: str = "All",
    ) -> Dict:
        """
        Score markets using the full CoStar data pipeline.

        This is the "full" scoring path that uses CoStar quarterly Excel exports
        for 11 individual metrics with proper cross-market Z-score normalization.

        Parameters
        ----------
        costar_file       : Path to CoStar quarterly Excel export
        config            : ScorecardConfig (or None for defaults)
        config_overrides  : dict of flat config overrides (same format as API)
        property_class    : "All", "4 & 5 Star", "3 Star", or "1 & 2 Star"
        inventory_tier    : Inventory tier for peer group filtering

        Returns
        -------
        dict with keys: markets_scored, rankings (list of dicts),
                        config, data_source
        """
        from .z_score_engine import score_tier

        if config is None:
            config = ScorecardConfig()
        if config_overrides:
            config = self._apply_config_overrides(config, config_overrides)

        # Steps 1-3: Load data, classify, compute details (cached after first call)
        all_detail_results, classifications = self._get_costar_cache(costar_file)

        # Step 4: Build peer group for Z-score denominator
        peer_group = None
        if inventory_tier != "All":
            tier_markets = classifications[
                classifications['inventory_tier'] == inventory_tier
            ]['market'].unique()
            peer_group = set(tier_markets)

        # Step 5: Score using the full pipeline
        logger.info(f"Scoring tier {property_class}...")
        scores = score_tier(
            all_detail_results,
            property_class=property_class,
            config=config,
            peer_group_markets=peer_group,
        )

        # Step 5b: Filter output to only peer group markets (display filter)
        if peer_group:
            scores = {m: v for m, v in scores.items() if m in peer_group}

        # Step 6: Format results (rich format for frontend drill-down)
        results = self._format_scores_rich(scores, config, property_class)

        # Step 7: Store in warehouse
        all_tier_data = {property_class: scores}
        import pandas as pd
        simple_results = [
            {'market_id': r['market'], 'final_score': r['dw_mf'],
             'ds_score': r['dw_ds'], 'occ_score': r.get('dw_occ', 0),
             'rent_score': r['dw_rent'], 'rank': r['mf_rank']}
            for r in results['markets']
        ]
        rankings_df = pd.DataFrame(simple_results)
        if not rankings_df.empty:
            self._store_scores(all_tier_data, rankings_df, config)

        logger.info(f"Scored {results['market_count']} markets from CoStar data")

        results['data_source'] = 'costar'
        results['inventory_tier'] = inventory_tier
        return results

    def score_from_costar_composite(
        self,
        costar_file: str,
        config: ScorecardConfig = None,
        config_overrides: dict = None,
        inventory_tier: str = "All",
    ) -> Dict:
        """
        Score markets using composite scoring across all property class tiers.

        This runs the full multi-tier pipeline: scores each property class
        independently, then tier-weights them into a composite rank.
        """
        from .composite_scorer import compute_composite_scores

        if config is None:
            config = ScorecardConfig()
        if config_overrides:
            config = self._apply_config_overrides(config, config_overrides)

        # Load data from cache (same as score_from_costar)
        all_detail_results, classifications = self._get_costar_cache(costar_file)

        # Peer group
        peer_group = None
        if inventory_tier != "All":
            tier_markets = classifications[
                classifications['inventory_tier'] == inventory_tier
            ]['market'].unique()
            peer_group = set(tier_markets)

        # Run composite scoring
        logger.info("Running composite scoring across all tiers...")
        result = compute_composite_scores(
            all_detail_results,
            classifications,
            config=config,
            peer_group_markets=peer_group,
        )

        # Store results
        if result.get('tier_scores'):
            rankings = result.get('rankings')
            if rankings is not None and not rankings.empty:
                self._store_scores(result['tier_scores'], rankings, config)

        ranked = []
        if result.get('rankings') is not None and not result['rankings'].empty:
            for _, row in result['rankings'].iterrows():
                ranked.append({
                    'rank': int(row.get('rank', 0)),
                    'market_id': row.get('market_id', ''),
                    'final_score': round(float(row.get('final_score', 0)), 4),
                    'ds_score': round(float(row.get('ds_score', 0)), 4),
                    'occ_score': round(float(row.get('occ_score', 0)), 4),
                    'rent_score': round(float(row.get('rent_score', 0)), 4),
                })

        logger.info(f"Composite scoring: {len(ranked)} markets ranked")

        return {
            'markets_scored': len(ranked),
            'data_source': 'costar_composite',
            'inventory_tier': inventory_tier,
            'tiers_scored': list(result.get('tier_scores', {}).keys()),
            'rankings': ranked[:25],
            'config': {
                'analysis_duration': config.analysis_duration_years,
                'ds_weight': config.ds_weight,
                'occ_weight': config.occ_weight,
                'rg_weight': config.rg_weight,
                'period_mode': config.period_mode,
                'tier_weights': config.tier_weights,
            },
        }

    @staticmethod
    def _apply_config_overrides(config: ScorecardConfig, overrides: dict) -> ScorecardConfig:
        """Apply a flat dict of overrides to a ScorecardConfig."""
        if "ds_weight" in overrides:
            config.ds_weight = float(overrides["ds_weight"])
        if "occ_weight" in overrides:
            config.occ_weight = float(overrides["occ_weight"])
        if "rg_weight" in overrides:
            config.rg_weight = float(overrides["rg_weight"])
        if "actual_occ_weight" in overrides:
            config.actual_occ_weight = float(overrides["actual_occ_weight"])
        if "effective_occ_weight" in overrides:
            config.effective_occ_weight = float(overrides["effective_occ_weight"])
        if "vol_weight" in overrides:
            cap, w, floor = config.volatility_indicator
            config.volatility_indicator = (cap, float(overrides["vol_weight"]), floor)
        if "cat_weight" in overrides:
            cap, w, floor = config.category_indicator
            config.category_indicator = (cap, float(overrides["cat_weight"]), floor)
        if "period_weight" in overrides:
            cap, w, floor = config.period_indicator
            config.period_indicator = (cap, float(overrides["period_weight"]), floor)
        if "dispersion_weight" in overrides:
            config.dispersion_weight = float(overrides["dispersion_weight"])
        if "analysis_duration_years" in overrides:
            config.analysis_duration_years = int(overrides["analysis_duration_years"])
        if "period_mode" in overrides:
            mode = overrides["period_mode"]
            if mode in ("cumulative", "standalone"):
                config.period_mode = mode
        if "total_z_cap" in overrides:
            config.total_z_cap = float(overrides["total_z_cap"])
        if "total_z_floor" in overrides:
            config.total_z_floor = float(overrides["total_z_floor"])
        if "mom_knob" in overrides:
            config.mom_knob = float(overrides["mom_knob"])
        if "recent_momentum_tilt_multiplier" in overrides:
            config.recent_momentum_tilt_multiplier = float(overrides["recent_momentum_tilt_multiplier"])
        if "dispersion_cap" in overrides:
            config.dispersion_cap = float(overrides["dispersion_cap"])
        if "dispersion_floor" in overrides:
            config.dispersion_floor = float(overrides["dispersion_floor"])
        if "auto_duration_weights" in overrides:
            val = overrides["auto_duration_weights"]
            config.auto_duration_weights = val in (True, "true", "True", "1", 1)
        if "rent_overall_weight" in overrides:
            config.rent_metric_weights["eff_rent_overall"] = float(overrides["rent_overall_weight"])
        if "flip_deliveries" in overrides and overrides["flip_deliveries"]:
            config.direction_overrides = config.direction_overrides or {}
            config.direction_overrides["net_deliveries"] = True

        # Per-period momentum half-life and max-tilt overrides
        for period in ["Quarterly", "Annual", "2Yr", "3Yr", "5Yr", "10Yr", "12Yr"]:
            hl_key = f"momentum_hl_{period}"
            mt_key = f"momentum_mt_{period}"
            if hl_key in overrides or mt_key in overrides:
                existing = config.momentum_config.get(period, (0.0, 0.0, 0))
                hl = float(overrides.get(hl_key, existing[0]))
                mt = float(overrides.get(mt_key, existing[1]))
                hlq = existing[2] if len(existing) > 2 else 0
                config.momentum_config[period] = (hl, mt, hlq)

        # Duration weight table overrides
        if "duration_weight_overrides" in overrides:
            from .tilt_engine import DURATION_WEIGHT_TABLE
            dwo = overrides["duration_weight_overrides"]
            for dur_str, period_map in dwo.items():
                dur = int(dur_str)
                DURATION_WEIGHT_TABLE[dur] = {k: float(v) for k, v in period_map.items()}

        # Standalone period weights
        if "standalone_period_weights" in overrides:
            spw = overrides["standalone_period_weights"]
            if isinstance(spw, dict):
                config.standalone_period_weights = {k: float(v) for k, v in spw.items()}

        # Per-metric weight overrides
        for mk in list(config.ds_metric_weights.keys()):
            key = f"ds_mw_{mk}"
            if key in overrides:
                config.ds_metric_weights[mk] = float(overrides[key])
        for mk in list(config.occ_metric_weights.keys()):
            key = f"occ_mw_{mk}"
            if key in overrides:
                config.occ_metric_weights[mk] = float(overrides[key])
        for mk in list(config.rent_metric_weights.keys()):
            key = f"rent_mw_{mk}"
            if key in overrides:
                config.rent_metric_weights[mk] = float(overrides[key])

        return config

    @staticmethod
    def _z_to_percentile(z: float, skew: float = 0.0) -> float:
        """Convert Z-score to percentile using Cornish-Fisher expansion."""
        import math
        z_adj = z + (skew / 6.0) * (z * z - 1.0)
        a1, a2, a3 = 0.4361836, -0.1201676, 0.9372980
        p = 0.33267
        t = 1.0 / (1.0 + p * abs(z_adj))
        phi = 1.0 - (a1 * t + a2 * t * t + a3 * t * t * t) * math.exp(
            -z_adj * z_adj / 2.0
        ) / math.sqrt(2.0 * math.pi)
        if z_adj < 0:
            phi = 1.0 - phi
        return round(min(99.9, max(0.1, phi * 100.0)), 1)

    def _format_scores_rich(
        self, scores: Dict, config: ScorecardConfig, property_class: str
    ) -> Dict:
        """
        Format MarketScore objects into the rich JSON structure the frontend
        expects — matches the original webapp's format_scores() output.

        Returns dict with 'markets' (list of rich rows), 'config', 'market_count',
        'property_class', 'active_periods', etc.
        """
        SKEWNESS = {
            "absorption": 0.3, "deliveries": 0.5, "abs_del": 0.2,
            "blended_occ": -0.1, "under_construction": 0.6, "yrs_to_stab": 0.8,
            "eff_rent_overall": 0.1, "eff_rent_1br": 0.1, "eff_rent_studio": 0.2,
            "eff_rent_2br": 0.1, "eff_rent_3br": 0.2, "_default": 0.0,
        }
        EXCLUDED = {"sf_permits_yoy", "renter_weighted_pop_yoy", "pop_20_34_share"}
        z2p = self._z_to_percentile

        rows = []
        active_periods = None

        for market, ms in scores.items():
            # Primary period: Q1 for cumulative, Yr1 for standalone
            ps = ms.period_scores.get("Q1") or ms.period_scores.get("Yr1")
            if not ps:
                continue

            if active_periods is None:
                active_periods = list(ms.period_scores.keys())

            row = {
                "market": market,
                "ds_raw": round(ps.overall_ds_raw, 4),
                "ds_adj": round(ps.overall_ds_adj, 4),
                "occ_raw": round(ps.overall_occ_raw, 4),
                "occ_adj": round(ps.overall_occ_adj, 4),
                "rent_raw": round(ps.overall_rent_raw, 4),
                "rent_adj": round(ps.overall_rent_adj, 4),
                "mf_score": round(ps.overall_mf, 4),
                "dw_ds": round(ms.duration_weighted_ds, 4),
                "dw_occ": round(ms.duration_weighted_occ, 4),
                "dw_rent": round(ms.duration_weighted_rent, 4),
                "dw_mf": round(ms.duration_weighted_mf, 4),
                "metrics": {},
            }

            # Per-metric detail for primary period
            for mk, mz in ps.ds_metric_z.items():
                if mk in EXCLUDED:
                    continue
                skew = SKEWNESS.get(mk, SKEWNESS["_default"])
                row["metrics"][mk] = {
                    "signal_z": round(mz.signal_z, 4),
                    "vol_z": round(mz.volatility_z, 4),
                    "cat_z": round(mz.category_z, 4),
                    "total_z": round(mz.total_z, 4),
                    "percentile": z2p(mz.total_z, skew),
                    "group": "S&D",
                    "weight": config.ds_metric_weights.get(mk, 1.0),
                }
            for mk, mz in ps.occ_metric_z.items():
                skew = SKEWNESS.get(mk, SKEWNESS["_default"])
                row["metrics"][mk] = {
                    "signal_z": round(mz.signal_z, 4),
                    "vol_z": round(mz.volatility_z, 4),
                    "cat_z": round(mz.category_z, 4),
                    "total_z": round(mz.total_z, 4),
                    "percentile": z2p(mz.total_z, skew),
                    "group": "Occ",
                    "weight": config.occ_metric_weights.get(mk, 1.0),
                }
            for mk, mz in ps.rent_metric_z.items():
                skew = SKEWNESS.get(mk, SKEWNESS["_default"])
                row["metrics"][mk] = {
                    "signal_z": round(mz.signal_z, 4),
                    "vol_z": round(mz.volatility_z, 4),
                    "cat_z": round(mz.category_z, 4),
                    "total_z": round(mz.total_z, 4),
                    "percentile": z2p(mz.total_z, skew),
                    "group": "Rent",
                    "weight": config.rent_metric_weights.get(mk, 1.0),
                }

            # Per-period breakdown
            period_detail = {}
            for period_name, period_ps in ms.period_scores.items():
                pd_entry = {
                    "ds_raw": round(period_ps.overall_ds_raw, 4),
                    "ds_adj": round(period_ps.overall_ds_adj, 4),
                    "occ_raw": round(period_ps.overall_occ_raw, 4),
                    "occ_adj": round(period_ps.overall_occ_adj, 4),
                    "rent_raw": round(period_ps.overall_rent_raw, 4),
                    "rent_adj": round(period_ps.overall_rent_adj, 4),
                    "mf_score": round(period_ps.overall_mf, 4),
                    "tilt_value": round(period_ps.tilt_value, 4),
                    "metrics": {},
                }
                for mk, mz in period_ps.ds_metric_z.items():
                    if mk in EXCLUDED:
                        continue
                    skew = SKEWNESS.get(mk, SKEWNESS["_default"])
                    pd_entry["metrics"][mk] = {
                        "signal_z": round(mz.signal_z, 4),
                        "vol_z": round(mz.volatility_z, 4),
                        "cat_z": round(mz.category_z, 4),
                        "total_z": round(mz.total_z, 4),
                        "percentile": z2p(mz.total_z, skew),
                        "group": "S&D",
                    }
                for mk, mz in period_ps.occ_metric_z.items():
                    skew = SKEWNESS.get(mk, SKEWNESS["_default"])
                    pd_entry["metrics"][mk] = {
                        "signal_z": round(mz.signal_z, 4),
                        "vol_z": round(mz.volatility_z, 4),
                        "cat_z": round(mz.category_z, 4),
                        "total_z": round(mz.total_z, 4),
                        "percentile": z2p(mz.total_z, skew),
                        "group": "Occ",
                    }
                for mk, mz in period_ps.rent_metric_z.items():
                    skew = SKEWNESS.get(mk, SKEWNESS["_default"])
                    pd_entry["metrics"][mk] = {
                        "signal_z": round(mz.signal_z, 4),
                        "vol_z": round(mz.volatility_z, 4),
                        "cat_z": round(mz.category_z, 4),
                        "total_z": round(mz.total_z, 4),
                        "percentile": z2p(mz.total_z, skew),
                        "group": "Rent",
                    }
                period_detail[period_name] = pd_entry
            row["periods"] = period_detail
            rows.append(row)

        # Sort by duration-weighted MF score
        rows.sort(key=lambda r: r["dw_mf"], reverse=True)

        # Add ranks and percentiles
        n = len(rows)
        for i, r in enumerate(rows):
            r["mf_rank"] = i + 1
            r["mf_percentile"] = round((1 - i / max(n - 1, 1)) * 100, 1) if n > 1 else 50.0

        ds_sorted = sorted(rows, key=lambda r: r["dw_ds"], reverse=True)
        for i, r in enumerate(ds_sorted):
            r["ds_rank"] = i + 1
            r["ds_percentile"] = round((1 - i / max(n - 1, 1)) * 100, 1) if n > 1 else 50.0

        rent_sorted = sorted(rows, key=lambda r: r["dw_rent"], reverse=True)
        for i, r in enumerate(rent_sorted):
            r["rent_rank"] = i + 1
            r["rent_percentile"] = round((1 - i / max(n - 1, 1)) * 100, 1) if n > 1 else 50.0

        occ_sorted = sorted(rows, key=lambda r: r.get("dw_occ", 0), reverse=True)
        for i, r in enumerate(occ_sorted):
            r["occ_rank"] = i + 1
            r["occ_percentile"] = round((1 - i / max(n - 1, 1)) * 100, 1) if n > 1 else 50.0

        config_info = {
            "ds_weight": config.ds_weight,
            "occ_weight": config.occ_weight,
            "rg_weight": config.rg_weight,
            "actual_occ_weight": config.actual_occ_weight,
            "effective_occ_weight": config.effective_occ_weight,
            "vol_indicator": list(config.volatility_indicator),
            "cat_indicator": list(config.category_indicator),
            "period_indicator": list(config.period_indicator),
            "dispersion_weight": config.dispersion_weight,
            "dispersion_cap": config.dispersion_cap,
            "dispersion_floor": config.dispersion_floor,
            "analysis_duration_years": config.analysis_duration_years,
            "auto_duration_weights": config.auto_duration_weights,
            "period_mode": config.period_mode,
            "property_class": property_class,
            "total_z_cap": config.total_z_cap,
            "total_z_floor": config.total_z_floor,
            "recent_momentum_tilt_multiplier": config.recent_momentum_tilt_multiplier,
            "mom_knob": config.mom_knob,
            "momentum_config": {
                period: {"hl_steps": hl, "max_tilt": mt}
                for period, (hl, mt, _) in config.momentum_config.items()
            },
            "ds_metric_weights": config.ds_metric_weights,
            "occ_metric_weights": config.occ_metric_weights,
            "rent_metric_weights": config.rent_metric_weights,
        }

        return {
            "markets": rows,
            "config": config_info,
            "market_count": len(rows),
            "property_class": property_class,
            "active_periods": active_periods or ["Q1"],
        }
