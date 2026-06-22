"""
load_cashflow.py — Orchestrate loading of all 2026 property cash flows into
the database, applying the right extractor per source format.
"""
import os

from .db import init_db, connect
from .extract_cashflow import extract_drake_xlsx, load_facts
from .extract_pdf_coords import extract_pdf_cashflow_coords
from .reference import CASHFLOW_FILES, PROPERTY_EXTRACT_CONFIG


def load_all_cashflows(source_dir, conn=None, reset=False):
    """Extract and load every property's 2026 cash flow. Returns a load report."""
    if conn is None:
        conn = init_db(reset=reset)
    report = {}

    # Drake — Excel
    drake_path = os.path.join(source_dir, CASHFLOW_FILES["DRAKE"])
    facts = extract_drake_xlsx(drake_path)
    report["DRAKE"] = load_facts(conn, facts)

    # PDF properties
    pdf_props = ["ONB", "OHP1", "OHP2", "COMBINED", "JTM", "MB", "WACKER"]
    for code in pdf_props:
        path = os.path.join(source_dir, CASHFLOW_FILES[code])
        cfg = PROPERTY_EXTRACT_CONFIG.get(code, {})
        facts = extract_pdf_cashflow_coords(
            path, code,
            capital_label_map=cfg.get("capital_label_map"),
            noi_label_substring=cfg.get("noi_label_substring"),
            skip_generic_capital=cfg.get("skip_generic_capital", False),
        )
        if cfg.get("flip_capital_sign"):
            for f in facts:
                if f["line_item_code"].startswith("CAP_"):
                    f["amount"] = -f["amount"]
        report[code] = load_facts(conn, facts)

    return conn, report


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/barrington/April 2026 Cash Flows"
    conn, rep = load_all_cashflows(src, reset=True)
    print("Loaded facts per property:")
    for k, v in rep.items():
        print(f"  {k:9} {v:4} facts")
