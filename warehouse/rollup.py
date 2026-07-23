"""
Fund / sub-fund / portfolio rollup.

Aggregates the deal-analytics warehouse facts across every deal leaf that rolls
up into a registry node (fund, sub-fund, portfolio, or a single deal). This is
the Phase 2 groundwork: the macro view over the same facts the per-deal modules
show at the micro level.

Design:
  * The aggregation math (`aggregate_summaries`, `aggregate_annual`) is pure and
    unit-tested without a warehouse.
  * The DuckDB queries (`fetch_*`) are isolated and dedup the bitemporal tables
    to the latest ingestion. `fact_deal_summary` is written by more than one
    engine, so we take the latest *non-null* value per column (max_by ... FILTER).
  * `rollup_for_node` glues the registry (which deals) to the warehouse (their
    facts). With a single deal under a node, the rollup equals that deal's own
    figures, so it degrades cleanly today and scales when more deals land.
"""

from __future__ import annotations

import os
from typing import Any, Optional

DEFAULT_WAREHOUSE = os.path.join("data", "warehouse.duckdb")

# Additive dollar quantities summed across deals.
_SUMMARY_SUM_COLS = [
    "initial_equity", "acquisition_cost_basis", "gross_sale_price",
    "net_sale_proceeds", "loan_repayment_at_sale", "total_distributed",
]
# Per-deal reference metrics kept for the breakdown (not summed).
_SUMMARY_REF_COLS = ["hold_years", "levered_irr", "equity_multiple",
                     "avg_dscr", "deal_irr", "deal_em"]
_SUMMARY_COLS = _SUMMARY_SUM_COLS + _SUMMARY_REF_COLS

_ANNUAL_SUM_COLS = ["noi", "debt_service", "capex", "levered_cf"]


# ─── Pure aggregation (unit-tested; no warehouse) ──────────────────────────

def aggregate_summaries(per_deal: list[dict]) -> dict[str, Any]:
    """Combine per-deal summary rows into a portfolio summary: sum the additive
    dollar columns and derive a portfolio equity multiple. IRR is intentionally
    not summed — a portfolio IRR needs pooled cash flows, which is a later step."""
    agg = {c: 0.0 for c in _SUMMARY_SUM_COLS}
    n = 0
    for d in per_deal:
        n += 1
        for c in _SUMMARY_SUM_COLS:
            v = d.get(c)
            if v is not None:
                agg[c] += v
    equity = agg["initial_equity"]
    agg["portfolio_equity_multiple"] = (
        agg["total_distributed"] / equity if equity else None)
    agg["n_deals"] = n
    return agg


def aggregate_annual(per_deal_rows: list[dict]) -> list[dict]:
    """Sum NOI / debt service / capex / levered CF by calendar year across deals."""
    by_year: dict[int, dict] = {}
    for r in per_deal_rows:
        y = r.get("calendar_year")
        if y is None:
            continue
        acc = by_year.setdefault(y, {"calendar_year": y,
                                     **{c: 0.0 for c in _ANNUAL_SUM_COLS}})
        for c in _ANNUAL_SUM_COLS:
            v = r.get(c)
            if v is not None:
                acc[c] += v
    return [by_year[y] for y in sorted(by_year)]


# ─── Warehouse queries (bitemporal dedup) ──────────────────────────────────

def _connect(warehouse_path: str):
    import duckdb
    return duckdb.connect(warehouse_path, read_only=True)


def fetch_deal_summaries(con, deal_ids: list[str],
                         scenario: str = "baseline") -> list[dict]:
    """Latest non-null summary per deal (fact_deal_summary is multi-writer)."""
    if not deal_ids:
        return []
    ph = ",".join(["?"] * len(deal_ids))
    sel = ", ".join(
        f"max_by({c}, ingestion_id) FILTER (WHERE {c} IS NOT NULL) AS {c}"
        for c in _SUMMARY_COLS)
    q = (f"SELECT deal_id, {sel} FROM fact_deal_summary "
         f"WHERE tif_scenario = ? AND deal_id IN ({ph}) GROUP BY deal_id")
    rows = con.execute(q, [scenario, *deal_ids]).fetchall()
    cols = ["deal_id"] + _SUMMARY_COLS
    return [dict(zip(cols, r)) for r in rows]


def fetch_proforma_annual(con, deal_ids: list[str],
                          scenario: str = "baseline") -> list[dict]:
    """Latest-ingestion proforma annual rows per deal/year (aggregated in Python)."""
    if not deal_ids:
        return []
    ph = ",".join(["?"] * len(deal_ids))
    q = (f"SELECT deal_id, calendar_year, noi, debt_service, capex, levered_cf "
         f"FROM fact_proforma_annual "
         f"WHERE tif_scenario = ? AND deal_id IN ({ph}) "
         f"QUALIFY row_number() OVER "
         f"(PARTITION BY deal_id, year ORDER BY ingestion_id DESC) = 1")
    rows = con.execute(q, [scenario, *deal_ids]).fetchall()
    cols = ["deal_id", "calendar_year", "noi", "debt_service", "capex", "levered_cf"]
    return [dict(zip(cols, r)) for r in rows]


# ─── Glue: registry node -> aggregated rollup ──────────────────────────────

def rollup_for_node(node_id: str, scenario: str = "baseline",
                    warehouse_path: str = DEFAULT_WAREHOUSE) -> dict[str, Any]:
    """Aggregate deal facts across every deal under `node_id`. Returns the node,
    the deals included, a summed portfolio summary, per-deal breakdown, and a
    combined annual cash-flow series."""
    from registry import get_registry
    reg = get_registry()
    node = reg.get_entity(node_id)
    deals = reg.descendant_deals(node_id)
    deal_ids = [d.get("warehouse_deal_id") or d["id"] for d in deals]

    con = _connect(warehouse_path)
    try:
        summaries = fetch_deal_summaries(con, deal_ids, scenario)
        annual_rows = fetch_proforma_annual(con, deal_ids, scenario)
    finally:
        con.close()

    return {
        "node": {
            "id": node_id,
            "label": node.get("label") if node else node_id,
            "type": node.get("type") if node else None,
        },
        "scenario": scenario,
        "deal_ids": deal_ids,
        "summary": aggregate_summaries(summaries),
        "per_deal": summaries,
        "annual": aggregate_annual(annual_rows),
    }
