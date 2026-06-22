"""
ingest_into_capactive.py — Bring the Barrington portfolio into Capactive's
document store the same way any other property is ingested: create a portfolio,
create the 10 office properties, and register the 19 source documents (cash
flows -> operating_statement, rent rolls -> rent_roll), each linked to its asset.

Runs locally against the dev org database. Idempotent: re-running is a no-op if
the portfolio already exists.

    cd ~/realestate_extractor && python barrington_db/ingest_into_capactive.py
"""
import os
import sys
import shutil

os.environ.setdefault('CAPACTIVE_DEV_MODE', '1')

HERE = os.path.dirname(os.path.abspath(__file__))        # .../realestate_extractor/barrington_db
REPO = os.path.dirname(HERE)                             # .../realestate_extractor
sys.path.insert(0, REPO)                                 # top-level sibling modules
sys.path.insert(0, os.path.dirname(REPO))                # parent -> realestate_extractor package

import logging
logging.disable(logging.WARNING)
from realestate_extractor import webapp

# (code, name, yardi_id, market, submarket)
PROPERTIES = [
    ("DRAKE",    "Drake Oakbrook Plaza",      "o0493400", "Chicago",   "Oak Brook"),
    ("ONB",      "One Northbrook Place",      "o0047200", "Chicago",   "Northbrook"),
    ("OHP1",     "O'Hare Plaza I",            "o0454700", "Chicago",   "O'Hare"),
    ("OHP2",     "O'Hare Plaza II",           "o0475200", "Chicago",   "O'Hare"),
    ("CCI",      "Corporate Center I",        "o0449001", "Chicago",   "Northbrook"),
    ("CCII",     "Corporate Center II",       "o0448900", "Chicago",   "Northbrook"),
    ("COMBINED", "Combined Centre",           None,       "Chicago",   "Northbrook"),
    ("JTM",      "Milwaukee Portfolio - JTM", "o0175401", "Milwaukee", "CBD"),
    ("MB",       "MB MKE",                    "o0175405", "Milwaukee", "CBD"),
    ("WACKER",   "Wacker",                    None,       "Chicago",   "CBD"),
]
CASHFLOW_FILES = {
    "DRAKE": "03 Drake Cash Flow 2026-04.xlsx", "ONB": "03 ONB Cash Flow 2026-04.pdf",
    "OHP1": "03 OHP I Cash Flow 04.2026.pdf", "OHP2": "03 Cash Flow OHP II 04.2026.pdf",
    "COMBINED": "03 Combined Cash Flow 2026-04.pdf", "JTM": "03 JTM Cash Flow 2026-04.pdf",
    "MB": "03 MB- Cashflow 2026-04.pdf", "WACKER": "Wacker Cash Flow 4-26.pdf",
}
RENTROLL_FILES = {
    "DRAKE": "Drake RR 2026-04.pdf", "ONB": "ONB RR 2026-04.pdf",
    "OHP1": "OHP I RR 2026.4.pdf", "OHP2": "OHP II RR 2026.4.pdf",
    "CCI": "CCI RR 2026-04.pdf", "CCII": "CCII RR 2026-04.pdf",
    "COMBINED": "Combined RR 2026-04.pdf", "JTM": "JTM RR 2026-04.pdf",
    "MB": "MB RR 2026-04.pdf", "WACKER": "Wacker Rent Roll 4-26.pdf",
}
# The corp-consolidation cash flow carries CCI/CCII detail; file it under CCI.
CORPCONSOL = ("CCI", "03 CorpConsol Cash Flow 2026-04.pdf")

CF_DIR = os.path.join(HERE, "source_docs", "April 2026 Cash Flows")
RR_DIR = os.path.join(HERE, "source_docs", "April 2026 Rent Rolls")
UPLOADS = os.path.join(REPO, "uploads")
PORTFOLIO_NAME = "Barrington Office Portfolio"


def main():
    if not os.path.isdir(CF_DIR) or not os.path.isdir(RR_DIR):
        print("Source folders not found under barrington_db/source_docs/. Aborting.")
        return 1
    os.makedirs(UPLOADS, exist_ok=True)

    db = webapp.get_org_db('dev')

    if any(p['name'] == PORTFOLIO_NAME for p in db.list_portfolios()):
        print(f"'{PORTFOLIO_NAME}' already exists — nothing to do (idempotent).")
        return 0

    pf_id = db.create_portfolio(
        PORTFOLIO_NAME,
        description="April 2026 office portfolio — 10 assets (cash flow + rent roll).")

    name_by_code = {}
    for code, name, yardi, market, sub in PROPERTIES:
        state = "WI" if market == "Milwaukee" else "IL"
        db.create_property(
            name=name, property_type='office', portfolio_id=pf_id,
            city=market, state=state,
            metadata={'barrington_code': code, 'yardi_id': yardi, 'submarket': sub})
        name_by_code[code] = name

    def ingest(code, src_dir, filename, dtype):
        src = os.path.join(src_dir, filename)
        if not os.path.exists(src):
            print("  MISSING:", filename)
            return 0
        dst = os.path.join(UPLOADS, filename)
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        db.insert_document(filename=filename, filepath=dst, document_type=dtype,
                           property_name=name_by_code[code], auto_create_property=False)
        return 1

    n = 0
    for code, fn in CASHFLOW_FILES.items():
        n += ingest(code, CF_DIR, fn, 'operating_statement')
    for code, fn in RENTROLL_FILES.items():
        n += ingest(code, RR_DIR, fn, 'rent_roll')
    n += ingest(CORPCONSOL[0], CF_DIR, CORPCONSOL[1], 'operating_statement')

    print(f"Created portfolio '{PORTFOLIO_NAME}' (id={pf_id}) with "
          f"{len(PROPERTIES)} properties and ingested {n} documents.")
    print("Refresh the dashboard — the portfolio and its 10 properties should appear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
