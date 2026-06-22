"""
extract_rentroll.py — Parse tenant/lease-level data from the April 2026 rent
rolls (Yardi "Tenancy Schedule I" format) into the rent_roll table.

The tenant header row for each lease contains, left-to-right:
    <Property name>  <Unit(s)>  <Tenant name>  <Lease Type>  <Area>
    <Lease From>  <Lease To>  <Term>  <Tenancy Yrs>  <Monthly Rent>
    <Mo Rent/Area> <Annual Rent> <Ann Rent/Area> <Ann Rec/Area> ...
We anchor on the property-name prefix to find tenant rows, then pull fields by
parsing the token stream (dates, area, rents). Month-to-month leases have a
Lease From but no Lease To.

Rent Steps / Charge Schedule sub-rows are skipped (they begin with charge codes
like BROFF/EOPXR or the words 'Rent Steps'/'Charge').
"""
import re
import os
from datetime import datetime

import pdfplumber

# Property-name prefixes that mark a tenant header row, per property code.
PROPERTY_ROW_PREFIX = {
    "DRAKE":    ["Drake Oakbrook"],
    "ONB":      ["One Northbrook Place,", "One Northbrook"],
    "OHP1":     ["O'Hare Plaza (o0454700)", "O'Hare Plaza ("],
    "OHP2":     ["O'Hare Plaza II", "O'Hare Plaza (o0475200)"],
    "CCI":      ["Corporate Center I"],
    "CCII":     ["Corporate Center II"],
    "COMBINED": ["Combined Centre"],
    "JTM":      ["Milwaukee Portfolio - JTM"],
    "MB":       ["15800 W. Bluemound", "Road - MB", "MB MKE"],
    "WACKER":   [],  # different format; handled separately
}

DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
AREA_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
CHARGE_CODE_RE = re.compile(r"^[A-Z]{4,6}$")  # BROFF, EOPXR, ETXRC, etc.


def _iso(m, d, y):
    try:
        return datetime(int(y), int(m), int(d)).date().isoformat()
    except ValueError:
        return None


def _num(s):
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _is_tenant_row(text, prefixes):
    if not any(text.startswith(p) for p in prefixes):
        return False
    # must contain at least one date and an area-like number
    if not DATE_RE.search(text):
        return False
    # exclude charge schedule lines that slipped through
    if " GLA " in text or text.strip().startswith(("BROFF", "Rent Steps", "Charge")):
        return False
    return True


def _parse_tenant_row(tokens, prefixes):
    """
    tokens: list of word strings for the row, in x order.
    Returns dict of parsed fields or None.
    Strategy:
      - Strip the property-name prefix tokens.
      - The remaining stream begins with Unit(s) (may be comma-joined codes),
        then Tenant name (may include '(tXXXX)'), then Lease Type
        (Office Net/General/Gross/Retail ...), then Area, From, [To], Term...
      - We locate the AREA token (first '#,###.##') as a pivot: everything
        before the lease-type marker is unit+tenant; dates follow area.
    """
    text = " ".join(tokens)
    # Find lease type marker to split tenant-name vs numerics.
    lt_match = re.search(r"(Office (?:Net|General|Gross)|Retail (?:Net|Gross|General)|"
                         r"Storage|Parking|Industrial \w+|Medical \w+)", text)
    if not lt_match:
        return None
    lease_type = lt_match.group(1)
    head = text[: lt_match.start()].strip()
    tail = text[lt_match.end():].strip()

    # head = "<Property...> <Unit(s)> <Tenant name (tID)>"
    # remove property prefix
    for p in prefixes:
        if head.startswith(p):
            head = head[len(p):].strip()
            break
    # remove a leading yardi id like "(o0454700)"
    head = re.sub(r"^\(o\d+\)\s*", "", head)
    # Units = leading tokens that look like unit codes (digits/letters, commas)
    htoks = head.split()
    unit_toks = []
    i = 0
    while i < len(htoks) and re.match(r"^[\dA-Z]{1,6},?$", htoks[i]):
        unit_toks.append(htoks[i].rstrip(","))
        i += 1
    units = ", ".join(unit_toks) if unit_toks else None
    tenant = " ".join(htoks[i:]).strip()
    tenant = re.sub(r"\(t\d+\)", "", tenant).strip()  # drop tenant id

    # tail = "<Area> <From> [<To>] <Term> <TenancyYrs> <MonthlyRent>
    #         <MoRent/Area> <AnnualRent> <AnnRent/Area> <AnnRec/Area> ..."
    ttoks = tail.split()
    area = None
    area_idx = None
    for j, tk in enumerate(ttoks):
        if AREA_RE.match(tk):
            area = _num(tk)
            area_idx = j
            break

    dates = DATE_RE.findall(tail)
    lease_from = lease_to = None
    if len(dates) >= 1:
        lease_from = _iso(*dates[0])
    if len(dates) >= 2:
        lease_to = _iso(*dates[1])

    monthly_rent = annual_rent = annual_rent_psf = None
    if area_idx is not None:
        rest = ttoks[area_idx + 1:]
        # Drop the date tokens from the stream.
        rest = [t for t in rest if not DATE_RE.match(t)]
        # rest now: [Term, TenancyYrs, MonthlyRent, MoRent/Area, AnnualRent, ...]
        # Term is an integer (months); TenancyYrs has a decimal; both are small.
        # Monthly rent is the first value > 50 with a decimal or thousands sep.
        # Annual rent is monthly * 12 region (largest of the next few).
        nums = []
        for t in rest:
            v = _num(t)
            if v is not None:
                nums.append(v)
        # Find monthly rent: first value >= 50 that is not the term/tenancy.
        # Term (e.g. 170) and tenancy (e.g. 5.08) come first; monthly rent is
        # typically the 3rd numeric. Guard for month-to-month (no term).
        cand = [v for v in nums if v is not None]
        # monthly rent: first value with magnitude consistent with rent
        # (>= 100 and not equal to area). Use the value at index 2 if present
        # and large, else first large value after the first two.
        rent_pool = cand[2:] if len(cand) > 2 else cand
        big = [v for v in rent_pool if v >= 50]
        if big:
            monthly_rent = big[0]
            # annual rent is roughly monthly*12; pick the closest large value.
            target = monthly_rent * 12
            ann_cand = [v for v in rent_pool if v > monthly_rent]
            if ann_cand:
                annual_rent = min(ann_cand, key=lambda v: abs(v - target))
            if area and annual_rent:
                annual_rent_psf = round(annual_rent / area, 2)

    return {
        "units": units,
        "tenant_name": tenant or None,
        "lease_type": lease_type,
        "area_sf": area,
        "lease_from": lease_from,
        "lease_to": lease_to,
        "monthly_rent": monthly_rent,
        "annual_rent": annual_rent,
        "annual_rent_psf": annual_rent_psf,
        "is_vacant": 1 if tenant and "vacant" in tenant.lower() else 0,
    }


def extract_rentroll(path, property_code, as_of="2026-04-30"):
    prefixes = PROPERTY_ROW_PREFIX.get(property_code, [])
    if not prefixes:
        return []
    fname = os.path.basename(path)
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            rows = {}
            for w in words:
                rows.setdefault(round(w["top"]), []).append(w)
            for top in sorted(rows):
                ws = sorted(rows[top], key=lambda w: w["x0"])
                toks = [w["text"] for w in ws]
                text = " ".join(toks)
                if not _is_tenant_row(text, prefixes):
                    continue
                rec = _parse_tenant_row(toks, prefixes)
                if not rec or not rec.get("area_sf"):
                    continue
                rec["property_code"] = property_code
                rec["as_of_date"] = as_of
                rec["source_file"] = fname
                out.append(rec)
    return out


def load_rentroll(conn, records):
    cols = ["property_code", "as_of_date", "units", "tenant_name", "lease_type",
            "area_sf", "lease_from", "lease_to", "monthly_rent", "annual_rent",
            "annual_rent_psf", "is_vacant", "source_file"]
    rows = [[r.get(c) for c in cols] for r in records]
    conn.executemany(
        f"INSERT INTO rent_roll ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})", rows)
    conn.commit()
    return len(rows)
