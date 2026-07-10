"""
Extract the Southtown tenant roster from the rent-roll PDF (local, on-device).

Parses occupied + new-lease rows into {suite, occupant, sf, exp}. Feeds the
co-tenancy engine (returns_model). The Required-Tenant classification and the
Kohl's first/second-floor split are applied in returns_model as documented
calibration overlays — they are lease/judgment inputs, not in the rent roll.

Usage:
    from extract_rent_roll import extract
    rows = extract("source_docs/returns/Southtown Shopping Center Rent Roll 5.31.26.pdf")
"""
import os
import re
import sys

_ROW = re.compile(
    r'^1026\s+(\S+)\s+(.+?)\s+(\d{1,2}/\d{1,2}/\d{4})\s+'
    r'(\d{1,2}/\d{1,2}/\d{4})\s+([\d,]+)\b')
_SECTIONS_KEEP = {"Occupied Suites", "New Leases"}
_SECTION_HEADERS = {"New Leases", "Vacant Suites", "Occupied Suites"}

# Default location once staged locally (gitignored).
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PDF = os.path.join(_HERE, "source_docs", "returns",
                           "Southtown Shopping Center Rent Roll 5.31.26.pdf")


def extract(pdf_path=DEFAULT_PDF):
    """Return [{suite, occupant, sf, exp}] for occupied + new-lease tenants."""
    import pdfplumber
    rows = []
    section = None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").split("\n"):
                s = line.strip()
                if s in _SECTION_HEADERS:
                    section = s
                    continue
                m = _ROW.match(s)
                if m and section in _SECTIONS_KEEP:
                    suite, occ, _rs, exp, sf = m.groups()
                    sf = int(sf.replace(",", ""))
                    if sf <= 0:
                        continue  # skip lot towers / zero-SF entries
                    rows.append({"suite": suite, "occupant": occ.strip(),
                                 "exp": exp, "sf": sf})
    return rows


def available(pdf_path=DEFAULT_PDF):
    return os.path.exists(pdf_path)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    rows = extract(path)
    print(f"Extracted {len(rows)} tenants, total {sum(r['sf'] for r in rows):,} SF")
    for r in rows:
        print(f"  {r['suite']:9} {r['sf']:>7,}  exp {r['exp']:10}  {r['occupant'][:42]}")
