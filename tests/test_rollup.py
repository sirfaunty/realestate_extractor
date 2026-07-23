"""
Tests for the Phase 2 rollup groundwork:
  * registry descendant-deal resolution across the hierarchy (optional sub-fund
    / portfolio levels, empty nodes, a deal rolling up to itself);
  * the pure warehouse aggregation math (sums, null-skipping, portfolio EM,
    annual by-year combination).

The DuckDB-backed fetch_* functions are validated against the live warehouse
separately; here we keep it warehouse-free so it runs anywhere.

Run:  pytest tests/test_rollup.py
"""

import pytest

from registry.store import RegistryStore
from warehouse.rollup import aggregate_summaries, aggregate_annual


# ─── Registry: descendant deals ────────────────────────────────────────────

@pytest.fixture
def reg(tmp_path):
    store = RegistryStore(str(tmp_path / "registry.db"))
    store.connect()
    # fund -> subfund -> portfolio -> d1 ; d2 under subfund ; d3 under fund ; empty portfolio
    store.upsert_entity("fund", "fund", "Fund")
    store.upsert_entity("sf", "subfund", "Sub-fund", parent_id="fund")
    store.upsert_entity("port", "portfolio", "Portfolio", parent_id="sf")
    store.upsert_entity("d1", "deal", "Deal 1", parent_id="port")
    store.upsert_entity("d2", "deal", "Deal 2", parent_id="sf")
    store.upsert_entity("d3", "deal", "Deal 3", parent_id="fund")
    store.upsert_entity("empty", "portfolio", "Empty", parent_id="fund")
    return store


def _ids(deals):
    return sorted(d["id"] for d in deals)


def test_descendant_deals_rolls_up_all_levels(reg):
    assert _ids(reg.descendant_deals("fund")) == ["d1", "d2", "d3"]
    assert _ids(reg.descendant_deals("sf")) == ["d1", "d2"]
    assert _ids(reg.descendant_deals("port")) == ["d1"]


def test_deal_rolls_up_to_itself(reg):
    assert [d["id"] for d in reg.descendant_deals("d1")] == ["d1"]


def test_empty_node_has_no_deals(reg):
    assert reg.descendant_deals("empty") == []


def test_reparent_changes_rollup(reg):
    reg.move_entity("d3", "empty")          # move a deal into the empty portfolio
    assert _ids(reg.descendant_deals("empty")) == ["d3"]
    assert _ids(reg.descendant_deals("fund")) == ["d1", "d2", "d3"]  # still all under fund


# ─── Pure aggregation ──────────────────────────────────────────────────────

def test_aggregate_summaries_sums_and_derives_em():
    per_deal = [
        {"initial_equity": 1_000_000, "total_distributed": 2_500_000,
         "net_sale_proceeds": 800_000, "acquisition_cost_basis": 5_000_000},
        {"initial_equity": 3_000_000, "total_distributed": 4_500_000,
         "net_sale_proceeds": None, "acquisition_cost_basis": 9_000_000},
    ]
    agg = aggregate_summaries(per_deal)
    assert agg["n_deals"] == 2
    assert agg["initial_equity"] == 4_000_000
    assert agg["total_distributed"] == 7_000_000
    assert agg["net_sale_proceeds"] == 800_000          # None skipped, not treated as 0-error
    assert agg["portfolio_equity_multiple"] == pytest.approx(1.75)  # 7.0M / 4.0M


def test_aggregate_summaries_handles_zero_equity():
    agg = aggregate_summaries([{"initial_equity": 0, "total_distributed": 100}])
    assert agg["portfolio_equity_multiple"] is None      # no divide-by-zero


def test_aggregate_annual_combines_by_year():
    rows = [
        {"calendar_year": 2026, "noi": 100, "levered_cf": 10},
        {"calendar_year": 2026, "noi": 200, "levered_cf": 20},
        {"calendar_year": 2027, "noi": 300, "levered_cf": None},
    ]
    out = aggregate_annual(rows)
    assert [r["calendar_year"] for r in out] == [2026, 2027]
    assert out[0]["noi"] == 300 and out[0]["levered_cf"] == 30
    assert out[1]["noi"] == 300 and out[1]["levered_cf"] == 0
