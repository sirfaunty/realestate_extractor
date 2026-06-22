"""
build.py — Build the Barrington portfolio database end-to-end from source docs.

Usage:
    python -m barrington_db.build <cashflow_dir> <rentroll_dir>
"""
import sys
import os

from .db import init_db
from .load_cashflow import load_all_cashflows
from .load_rentroll import load_all_rentrolls
from .leasing import seed_leasing_assumptions
from .validate import quarantine_implausible


def build(cashflow_dir, rentroll_dir, db_path=None):
    conn = init_db(db_path, reset=True)
    conn2, cf_report = load_all_cashflows(cashflow_dir, conn=conn)
    quarantined = quarantine_implausible(conn)
    rr_report = load_all_rentrolls(rentroll_dir, conn)
    n_assump = seed_leasing_assumptions(conn)
    return conn, {"cashflow": cf_report, "rentroll": rr_report,
                  "leasing_assumptions": n_assump,
                  "quarantined": quarantined}


if __name__ == "__main__":
    cf = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/barrington/April 2026 Cash Flows"
    rr = sys.argv[2] if len(sys.argv) > 2 else "/home/claude/barrington/April 2026 Rent Rolls"
    conn, report = build(cf, rr)
    print("=== Build report ===")
    print("Cash flow facts:", report["cashflow"])
    print("Rent roll rows :", report["rentroll"])
    print("Leasing assumptions seeded:", report["leasing_assumptions"])
