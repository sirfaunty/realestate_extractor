"""
run_reports.py — Convenience script to print key reports from the built DB.
Usage: python run_reports.py
"""
from barrington_db.db import connect
from barrington_db.portfolio import Portfolio
from barrington_db.report import (property_cashflow_statement,
                                  portfolio_capital_summary, rollover_report)
from barrington_db.validate import validate_noi

conn = connect()

print("\n" + "#" * 70)
print("# NOI TIE-OUT (extracted vs source)")
print("#" * 70)
for code, got, exp, ok in validate_noi(conn):
    print(f"  {code:9} got={got:>13,.0f}  exp={exp:>13,.0f}  {'OK' if ok else 'DIFF'}")

print(portfolio_capital_summary(conn))
print(rollover_report(conn))

# One full property statement as an example
print(property_cashflow_statement(conn, "ONB"))

pf = Portfolio(conn)
print(f"\nPortfolio total NOI 2026: ${pf.total_noi(2026):,.0f}")
