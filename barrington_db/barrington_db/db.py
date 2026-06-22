"""
db.py — Database core: initialize schema, load dimensions, provide connection.
"""
import os
import sqlite3
from datetime import date

from .reference import PROPERTIES, LINE_ITEMS

DB_PATH = os.environ.get(
    "BARRINGTON_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "barrington.db"),
)
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def connect(db_path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str = None, reset: bool = False) -> sqlite3.Connection:
    """Create schema and load static dimensions (properties, line items, periods)."""
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if reset and os.path.exists(path):
        os.remove(path)

    conn = connect(path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    # Properties
    conn.executemany(
        "INSERT OR REPLACE INTO property "
        "(property_code, property_name, yardi_id, market, submarket) "
        "VALUES (?,?,?,?,?)",
        PROPERTIES,
    )
    # Line items
    conn.executemany(
        "INSERT OR REPLACE INTO line_item "
        "(line_item_code, label, section, sort_order, is_capital, is_subtotal) "
        "VALUES (?,?,?,?,?,?)",
        LINE_ITEMS,
    )
    # Periods: monthly 2026-2028
    periods = []
    for year in (2026, 2027, 2028):
        for month in range(1, 13):
            pid = year * 100 + month
            periods.append((pid, year, month, date(year, month, 1).isoformat()))
    conn.executemany(
        "INSERT OR REPLACE INTO period (period_id, year, month, period_date) "
        "VALUES (?,?,?,?)",
        periods,
    )
    conn.commit()
    return conn


# Month-column ordering used across all 2026 source files (Jan..Dec).
MONTHS_2026 = [202601, 202602, 202603, 202604, 202605, 202606,
               202607, 202608, 202609, 202610, 202611, 202612]

# Jan-Apr 2026 are actuals; May-Dec 2026 are reforecast.
def scenario_for_period(period_id: int) -> str:
    if period_id <= 202604:
        return "ACTUAL"
    if period_id <= 202612:
        return "REFORECAST"
    return "FORECAST"
