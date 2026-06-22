"""
load_rentroll.py — Load all property rent rolls into the database.
"""
import os

from .extract_rentroll import extract_rentroll, load_rentroll
from .reference import RENTROLL_FILES


def load_all_rentrolls(source_dir, conn, reset_table=True):
    if reset_table:
        conn.execute("DELETE FROM rent_roll")
        conn.commit()
    report = {}
    # Skip COMBINED here if loading CCI/CCII separately would double count —
    # but Combined Centre is its own complex, and CCI/CCII are separate
    # properties, so all are loaded.
    for code, fn in RENTROLL_FILES.items():
        path = os.path.join(source_dir, fn)
        if not os.path.exists(path):
            report[code] = 0
            continue
        recs = extract_rentroll(path, code)
        report[code] = load_rentroll(conn, recs)
    return report
