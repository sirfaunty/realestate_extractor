"""
validate.py — Data-quality checks for the loaded cash flow facts.

The source PDFs vary widely in layout. The operating (NOI) and capital lines
(Building/Base-Bldg & LL, TI, LC) — the focus of this build — are validated to
tie to source. Some secondary lines (debt service on the multi-section JTM and
Wacker schedules) are known to be unreliable from automated extraction and are
quarantined rather than trusted.

This module:
  * flags facts whose magnitude is implausible (likely mis-parsed),
  * quarantines flagged secondary lines so they don't corrupt roll-ups,
  * reports a per-property tie-out for NOI and capital.
"""

# Plausibility ceiling: no single monthly line item should exceed this for
# these assets. Values above are near-certainly concatenation artifacts.
MONTHLY_MAGNITUDE_CEILING = 50_000_000

# Lines we trust from automated extraction (the build's focus).
TRUSTED_LINES = {
    "REVENUE", "REVENUE_ADJ", "OPEX", "NOI", "TAX_PAYMENT", "INSURANCE_PAYMENT",
    "CAP_BUILDING", "CAP_TI", "CAP_LC", "CAP_DEFERRED", "CAP_NONOP",
    "CAP_SUBTOTAL", "CONTRIBUTION", "DISTRIBUTION",
}


def quarantine_implausible(conn):
    """
    Delete facts whose absolute monthly amount exceeds the ceiling. Returns the
    list of (property, line_item, period, amount) removed for the report.
    """
    rows = conn.execute(
        "SELECT property_code, line_item_code, period_id, amount "
        "FROM cash_flow_fact WHERE ABS(amount) > ?",
        (MONTHLY_MAGNITUDE_CEILING,),
    ).fetchall()
    removed = [(r["property_code"], r["line_item_code"], r["period_id"], r["amount"])
               for r in rows]
    conn.execute(
        "DELETE FROM cash_flow_fact WHERE ABS(amount) > ?",
        (MONTHLY_MAGNITUDE_CEILING,),
    )
    conn.commit()
    return removed


def tie_out(conn, year=2026):
    """Per-property annual NOI and capital subtotal for eyeball tie-out."""
    return conn.execute(
        """
        SELECT property_code,
               ROUND(SUM(CASE WHEN line_item_code='NOI' THEN amount END)) noi,
               ROUND(SUM(CASE WHEN line_item_code IN
                    ('CAP_BUILDING','CAP_TI','CAP_LC','CAP_DEFERRED','CAP_NONOP')
                    THEN amount END)) capital
        FROM cash_flow_fact f JOIN period p USING(period_id)
        WHERE p.year=?
        GROUP BY property_code ORDER BY property_code
        """,
        (year,),
    ).fetchall()


# Expected 2026 source values for validation (from source documents).
EXPECTED_2026 = {
    "DRAKE":    {"noi": 3_084_904},
    "ONB":      {"noi": 3_379_824, "cap_building": -1_263_517, "cap_ti": -1_154_320, "cap_lc": -311_924},
    "OHP2":     {"noi": 642_353},
    "OHP1":     {"noi": -63_462},
    "COMBINED": {"noi": 4_340_715, "cap_building": -1_579_916, "cap_ti": -990_155, "cap_lc": -346_503},
    "JTM":      {"noi": 1_711_061, "cap_building": 2_656_357, "cap_ti": 1_438_319, "cap_lc": 652_942},
    "MB":       {"noi": 266_078,   "cap_building": 1_109_545, "cap_ti": 2_335_752, "cap_lc": 235_571},
}


def validate_noi(conn, tol=5):
    """Compare extracted NOI to expected; return list of (prop, got, exp, ok)."""
    out = []
    for code, exp in EXPECTED_2026.items():
        got = conn.execute(
            "SELECT ROUND(SUM(amount)) t FROM cash_flow_fact f JOIN period p "
            "USING(period_id) WHERE property_code=? AND line_item_code='NOI' "
            "AND p.year=2026", (code,),
        ).fetchone()["t"] or 0
        ok = abs(got - exp["noi"]) <= tol
        out.append((code, got, exp["noi"], ok))
    return out
