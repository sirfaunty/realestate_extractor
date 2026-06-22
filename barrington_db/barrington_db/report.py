"""
report.py — Human-readable cash flow reports for a property or the portfolio.
"""
from .db import MONTHS_2026, connect
from .property_module import PropertyModule
from .portfolio import Portfolio

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Display order for the cash flow waterfall.
DISPLAY_ORDER = [
    ("REVENUE", "Revenue"),
    ("OPEX", "Operating Expenses"),
    ("NOI", "NET OPERATING INCOME"),
    ("INTEREST", "  Mortgage Interest"),
    ("PRINCIPAL", "  Mortgage Principal"),
    ("CAP_BUILDING", "  Building / Base Bldg & LL Work"),
    ("CAP_TI", "  Tenant Improvements"),
    ("CAP_LC", "  Leasing Commissions"),
    ("CAP_DEFERRED", "  Deferred / Other Leasing"),
    ("CAP_NONOP", "  Non-Op / Legal Capital"),
    ("CAP_SUBTOTAL", "  Subtotal: Capital Items"),
    ("CONTRIBUTION", "  Ownership Contribution"),
    ("DISTRIBUTION", "  Ownership Distribution"),
]


def property_cashflow_statement(conn, property_code, year=2026):
    pm = PropertyModule(conn, property_code)
    monthly = pm.monthly(year)
    lines = []
    lines.append(f"\n{pm.name} ({pm.code}) — {year} Cash Flow")
    lines.append("=" * 110)
    header = f"{'Line Item':32}" + "".join(f"{m:>9}" for m in MONTH_LABELS) + f"{'TOTAL':>13}"
    lines.append(header)
    lines.append("-" * 110)
    for code, label in DISPLAY_ORDER:
        if code not in monthly:
            continue
        vals = [monthly[code].get(pid, 0) or 0 for pid in MONTHS_2026]
        total = sum(vals)
        row = f"{label:32}" + "".join(f"{v/1000:>9,.0f}" for v in vals) + f"{total/1000:>13,.0f}"
        lines.append(row)
    lines.append("-" * 110)
    lines.append("(values in $000s)")
    return "\n".join(lines)


def portfolio_capital_summary(conn, year=2026):
    pf = Portfolio(conn)
    mat = {}
    for r in pf.capital_by_property(year):
        mat.setdefault(r["property_code"], {})[r["line_item_code"]] = r["tot"]
    items = [("CAP_BUILDING", "Base Bldg/LL"), ("CAP_TI", "TI"),
             ("CAP_LC", "LC"), ("CAP_DEFERRED", "Deferred"),
             ("CAP_NONOP", "Non-Op")]
    lines = [f"\nPortfolio Forecasted Capital by Property — {year}", "=" * 90]
    lines.append(f"{'Property':10}" + "".join(f"{lbl:>15}" for _, lbl in items) + f"{'Total':>15}")
    lines.append("-" * 90)
    col_tot = {c: 0 for c, _ in items}
    grand = 0
    for code in sorted(mat):
        row = mat[code]
        rowtot = sum(row.get(c, 0) or 0 for c, _ in items)
        grand += rowtot
        for c, _ in items:
            col_tot[c] += row.get(c, 0) or 0
        lines.append(f"{code:10}" + "".join(f"{(row.get(c,0) or 0)/1000:>15,.0f}" for c, _ in items)
                     + f"{rowtot/1000:>15,.0f}")
    lines.append("-" * 90)
    lines.append(f"{'TOTAL':10}" + "".join(f"{col_tot[c]/1000:>15,.0f}" for c, _ in items)
                 + f"{grand/1000:>15,.0f}")
    lines.append("(values in $000s; outflows negative)")
    return "\n".join(lines)


def rollover_report(conn):
    pf = Portfolio(conn)
    lines = ["\nLease Rollover Through 2028 (drives forward TI/LC/Base-Bldg)", "=" * 70]
    lines.append(f"{'Year':6}{'Leases':>10}{'Expiring SF':>15}{'Expiring Rent':>18}")
    lines.append("-" * 70)
    for r in pf.rollover_by_year():
        lines.append(f"{r['yr']:6}{r['leases']:>10}{r['sf']:>15,.0f}{r['rent']:>18,.0f}")
    return "\n".join(lines)
