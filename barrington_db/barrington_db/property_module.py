"""
property_module.py — Per-property cash flow module.

Wraps a single property with convenient accessors: monthly cash flow waterfall,
annual totals, capital detail (TI / LC / Base Building & LL work), and rent-roll
/ rollover views. Each property in the portfolio is represented by one
PropertyModule instance (see portfolio.py for the roll-up).
"""
from .db import MONTHS_2026


class PropertyModule:
    def __init__(self, conn, property_code):
        self.conn = conn
        self.code = property_code
        row = conn.execute(
            "SELECT * FROM property WHERE property_code=?", (property_code,)
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown property: {property_code}")
        self.name = row["property_name"]
        self.market = row["market"]
        self.yardi_id = row["yardi_id"]

    # ---- Cash flow ----------------------------------------------------------
    def monthly(self, year=2026):
        """Return {line_item_code: {period_id: amount}} for the year."""
        rows = self.conn.execute(
            """
            SELECT f.line_item_code, f.period_id, SUM(f.amount) amt
            FROM cash_flow_fact f JOIN period p USING(period_id)
            WHERE f.property_code=? AND p.year=?
            GROUP BY f.line_item_code, f.period_id
            """,
            (self.code, year),
        ).fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["line_item_code"], {})[r["period_id"]] = r["amt"]
        return out

    def annual(self, year=2026):
        """Return {line_item_code: annual_total} for the year."""
        rows = self.conn.execute(
            """
            SELECT f.line_item_code, ROUND(SUM(f.amount),2) tot
            FROM cash_flow_fact f JOIN period p USING(period_id)
            WHERE f.property_code=? AND p.year=?
            GROUP BY f.line_item_code
            """,
            (self.code, year),
        ).fetchall()
        return {r["line_item_code"]: r["tot"] for r in rows}

    def capital_detail(self, year=2026):
        """Annual capital by canonical capital line (TI / LC / Base Bldg / etc.)."""
        rows = self.conn.execute(
            """
            SELECT li.line_item_code, li.label, ROUND(SUM(f.amount),2) tot
            FROM cash_flow_fact f
            JOIN line_item li USING(line_item_code)
            JOIN period p USING(period_id)
            WHERE f.property_code=? AND p.year=?
              AND li.is_capital=1 AND li.is_subtotal=0
            GROUP BY li.line_item_code
            ORDER BY li.sort_order
            """,
            (self.code, year),
        ).fetchall()
        return [(r["line_item_code"], r["label"], r["tot"]) for r in rows]

    def noi(self, year=2026):
        r = self.conn.execute(
            """
            SELECT ROUND(SUM(f.amount),2) tot
            FROM cash_flow_fact f JOIN period p USING(period_id)
            WHERE f.property_code=? AND p.year=? AND f.line_item_code='NOI'
            """,
            (self.code, year),
        ).fetchone()
        return r["tot"] or 0.0

    # ---- Rent roll ----------------------------------------------------------
    def rent_roll(self):
        return self.conn.execute(
            "SELECT * FROM rent_roll WHERE property_code=? ORDER BY lease_to",
            (self.code,),
        ).fetchall()

    def occupied_sf(self):
        r = self.conn.execute(
            "SELECT ROUND(SUM(area_sf)) sf FROM rent_roll WHERE property_code=?",
            (self.code,),
        ).fetchone()
        return r["sf"] or 0

    def expirations(self, start="2026-04-30", end="2028-12-31"):
        return self.conn.execute(
            """
            SELECT units, tenant_name, area_sf, lease_to, annual_rent, annual_rent_psf
            FROM rent_roll
            WHERE property_code=? AND lease_to IS NOT NULL
              AND lease_to > ? AND lease_to <= ?
            ORDER BY lease_to
            """,
            (self.code, start, end),
        ).fetchall()

    def summary(self):
        return {
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "noi_2026": self.noi(2026),
            "occupied_sf": self.occupied_sf(),
            "tenants": len(self.rent_roll()),
            "expiring_thru_2028": len(self.expirations()),
            "capital_2026": {c: t for c, _, t in self.capital_detail(2026)},
        }
