"""
portfolio.py — Portfolio roll-up module.

Aggregates every property into portfolio-level cash flow and capital views.
All ten properties are independent and summed directly (Combined Centre is its
own Northbrook complex; Corporate Center I & II are separate properties).
"""
from .property_module import PropertyModule
from .db import MONTHS_2026

# Properties summed in the portfolio roll-up. CORPCONSOL is an accounting
# consolidation file, not an operating property, so it is excluded.
PORTFOLIO_MEMBERS = ["DRAKE", "ONB", "OHP1", "OHP2", "CCI", "CCII",
                     "COMBINED", "JTM", "MB", "WACKER"]


class Portfolio:
    def __init__(self, conn, members=None):
        self.conn = conn
        self.members = members or PORTFOLIO_MEMBERS
        self.properties = {c: PropertyModule(conn, c) for c in self.members}

    def annual_cashflow(self, year=2026):
        """Portfolio annual total per canonical line item."""
        placeholders = ",".join("?" for _ in self.members)
        rows = self.conn.execute(
            f"""
            SELECT li.line_item_code, li.label, li.section, li.sort_order,
                   ROUND(SUM(f.amount),2) tot
            FROM cash_flow_fact f
            JOIN line_item li USING(line_item_code)
            JOIN period p USING(period_id)
            WHERE f.property_code IN ({placeholders}) AND p.year=?
            GROUP BY li.line_item_code
            ORDER BY li.sort_order
            """,
            (*self.members, year),
        ).fetchall()
        return rows

    def capital_by_property(self, year=2026):
        """Matrix: capital line totals per property (TI/LC/Base Bldg/etc.)."""
        placeholders = ",".join("?" for _ in self.members)
        rows = self.conn.execute(
            f"""
            SELECT f.property_code, li.line_item_code, li.label,
                   ROUND(SUM(f.amount),2) tot
            FROM cash_flow_fact f
            JOIN line_item li USING(line_item_code)
            JOIN period p USING(period_id)
            WHERE f.property_code IN ({placeholders}) AND p.year=?
              AND li.is_capital=1 AND li.is_subtotal=0
            GROUP BY f.property_code, li.line_item_code
            ORDER BY f.property_code, li.sort_order
            """,
            (*self.members, year),
        ).fetchall()
        return rows

    def noi_by_property(self, year=2026):
        return {c: self.properties[c].noi(year) for c in self.members}

    def total_noi(self, year=2026):
        return round(sum(self.noi_by_property(year).values()), 2)

    def rollover_by_year(self, start="2026-04-30", end="2028-12-31"):
        """Portfolio expiring SF and count by year."""
        placeholders = ",".join("?" for _ in self.members)
        return self.conn.execute(
            f"""
            SELECT substr(lease_to,1,4) yr, COUNT(*) leases,
                   ROUND(SUM(area_sf)) sf, ROUND(SUM(annual_rent)) rent
            FROM rent_roll
            WHERE property_code IN ({placeholders})
              AND lease_to IS NOT NULL AND lease_to > ? AND lease_to <= ?
            GROUP BY yr ORDER BY yr
            """,
            (*self.members, start, end),
        ).fetchall()

    def summary_table(self):
        return [self.properties[c].summary() for c in self.members]
