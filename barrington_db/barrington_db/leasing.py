"""
leasing.py — Lease rollover analysis and forward leasing assumptions.

Flags every lease that expires between the as-of date (2026-04-30) and the end
of the forecast horizon (2028-12-31), and seeds one editable leasing_assumption
row per expiring lease. These rows are where renewal / vacate / re-lease
outcomes are set; they drive forecast TI, LC, and base-building spend in
2027-2028.
"""
from datetime import date

HORIZON_END = "2028-12-31"
AS_OF = "2026-04-30"


def expiring_leases(conn, start=AS_OF, end=HORIZON_END):
    """Return rent_roll rows with lease_to within (start, end], ordered by date."""
    return conn.execute(
        """
        SELECT rent_roll_id, property_code, units, tenant_name, area_sf,
               lease_to, annual_rent, annual_rent_psf
        FROM rent_roll
        WHERE lease_to IS NOT NULL
          AND lease_to > ?
          AND lease_to <= ?
        ORDER BY property_code, lease_to
        """,
        (start, end),
    ).fetchall()


def rollover_summary(conn, start=AS_OF, end=HORIZON_END):
    """SF and count of expirations per property per year through the horizon."""
    return conn.execute(
        """
        SELECT property_code,
               substr(lease_to,1,4) AS expiry_year,
               COUNT(*)             AS leases,
               ROUND(SUM(area_sf))  AS sf,
               ROUND(SUM(annual_rent)) AS expiring_annual_rent
        FROM rent_roll
        WHERE lease_to IS NOT NULL AND lease_to > ? AND lease_to <= ?
        GROUP BY property_code, expiry_year
        ORDER BY property_code, expiry_year
        """,
        (start, end),
    ).fetchall()


# Default market leasing assumptions by market, used to seed (editable) rows.
# These are placeholders to make the forecast runnable; intended to be reviewed.
DEFAULT_ASSUMPTIONS = {
    # market:     renewal_prob, downtime_mo, new_rent_psf, ti_psf, lc_psf, free_rent_mo
    "Chicago":   dict(renewal_prob=0.70, downtime_months=9,  ti_psf=45.0, lc_psf=18.0, free_rent_months=4),
    "Milwaukee": dict(renewal_prob=0.70, downtime_months=9,  ti_psf=35.0, lc_psf=15.0, free_rent_months=3),
}


def seed_leasing_assumptions(conn, seed_defaults=True):
    """
    Create one leasing_assumption row per expiring lease (idempotent: clears
    and rebuilds). When seed_defaults, fill TI/LC/downtime from market defaults
    and set outcome='TBD' (renewal vs vacate left for review). new_rent_psf is
    seeded at the in-place rent (flat renewal) as a neutral starting point.
    """
    conn.execute("DELETE FROM leasing_assumption")
    rows = conn.execute(
        """
        SELECT r.rent_roll_id, r.property_code, r.units, r.tenant_name,
               r.area_sf, r.lease_to, r.annual_rent_psf, p.market
        FROM rent_roll r JOIN property p USING(property_code)
        WHERE r.lease_to IS NOT NULL
          AND r.lease_to > ? AND r.lease_to <= ?
        """,
        (AS_OF, HORIZON_END),
    ).fetchall()

    payload = []
    for r in rows:
        d = DEFAULT_ASSUMPTIONS.get(r["market"], DEFAULT_ASSUMPTIONS["Chicago"]) if seed_defaults else {}
        payload.append((
            r["property_code"], r["rent_roll_id"], r["units"], r["tenant_name"],
            r["area_sf"], r["lease_to"], "TBD",
            d.get("downtime_months", 0),
            r["annual_rent_psf"],            # new_rent_psf seeded flat
            d.get("ti_psf"), d.get("lc_psf"),
            d.get("free_rent_months", 0),
            "Seeded from rollover; set outcome (RENEW/VACATE/RELEASE) and rents.",
        ))
    conn.executemany(
        """
        INSERT INTO leasing_assumption
        (property_code, rent_roll_id, units, tenant_name, area_sf, expiry_date,
         outcome, downtime_months, new_rent_psf, ti_psf, lc_psf,
         free_rent_months, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        payload,
    )
    conn.commit()
    return len(payload)
