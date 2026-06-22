"""
extract_pdf_coords.py — Robust PDF cash-flow extraction via word coordinates.

Why this exists: the PDF cash flows render numbers with inconsistent spacing
(pdfplumber fragments '$ 3 61,732'), and several properties put capital totals
on indented detail rows rather than the label row. A regex-on-text approach is
too brittle. Instead we:

  1. Detect the 12 monthly column x-anchors from the header (Jan..Dec).
  2. Group words into visual rows by y-position.
  3. For each row, rejoin the leading label words and bucket the trailing
     numeric fragments into the nearest month column (rejoining fragmented
     digits within a bucket).
  4. Match the row label to a canonical line item.

For properties whose capital lives on '... TOTAL' rows (JTM/MB) or detailed
schedules (OHP1/Wacker), aliases include those total labels.
"""
import re
import os

import pdfplumber

from .db import MONTHS_2026, scenario_for_period
from .reference import LINE_ITEM_ALIASES

MONTH_HEADER_ORDER = ["jan", "feb", "mar", "apr", "may", "jun",
                      "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH_RE = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|march|sept)", re.I)
# A numeric fragment: digits, commas, parens, $, decimal, or a lone dash.
FRAG_RE = re.compile(r"^[\$\(\)\-\d,\.]+$")


def _to_float(s: str):
    s = s.strip()
    if s in ("", "-", "$", "$-"):
        return None
    neg = "(" in s
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _canon(label: str):
    if not label:
        return None
    key = re.sub(r"\s+", " ", label.strip().lower()).rstrip(":")
    key = re.sub(r"^(less|add|deduct|plus)\s*", "", key).strip().rstrip(":")
    return LINE_ITEM_ALIASES.get(key)


def _detect_columns(words):
    """
    Detect the 12 monthly column boundaries from the header row.
    Returns (edges, right_cutoff) where:
      edges = list of 13 x-boundaries delimiting the 12 month columns
      right_cutoff = x beyond which words are TOTAL/Budget/Difference columns
    or None if the header can't be located.

    Month columns are right-aligned, so we anchor on each header word's RIGHT
    edge (x1) and build midpoint boundaries between consecutive months.
    """
    by_row = {}
    for w in words:
        if MONTH_RE.match(w["text"]):
            by_row.setdefault(round(w["top"]), []).append(w)
    if not by_row:
        return None
    # Header row = the y with the most DISTINCT month names.
    def distinct_months(ws):
        ms = set()
        for w in ws:
            ms.add(MONTH_RE.match(w["text"]).group(1).lower()[:3])
        return len(ms)
    top = max(by_row, key=lambda t: distinct_months(by_row[t]))
    hdr = sorted(by_row[top], key=lambda w: w["x0"])

    rights = []
    seen = []
    for w in hdr:
        m = MONTH_RE.match(w["text"]).group(1).lower()[:3]
        if m in seen:
            continue
        seen.append(m)
        rights.append(w["x1"])
        if len(rights) == 12:
            break
    if len(rights) < 12:
        return None
    rights.sort()
    # Build boundaries: left edge before col0, midpoints between, right after col11.
    edges = [rights[0] - (rights[1] - rights[0])]  # left of first
    for i in range(11):
        edges.append((rights[i] + rights[i + 1]) / 2)
    # right edge of Dec col: extend by typical column width
    col_w = rights[11] - rights[10]
    edges.append(rights[11] + col_w * 0.7)
    right_cutoff = edges[-1]
    return edges, right_cutoff


def _cluster_columns(words, label_cutoff_x):
    """
    Learn the 12 monthly column right-edges by clustering the x1 (right edge)
    of all numeric word fragments to the right of the label region. Office
    cash-flow PDFs right-align every monthly value, so the dominant clusters
    of x1 positions ARE the columns. The 12 leftmost clusters are Jan..Dec;
    everything further right (TOTAL/Budget/Difference) is excluded by taking
    only the first 12.

    Returns (edges, right_cutoff), or None.
    """
    xs = []
    # Premerge fragments row-by-row so clustering sees whole numbers' right edges.
    rows = {}
    for w in words:
        if w["x0"] >= label_cutoff_x and FRAG_RE.match(w["text"]) and re.search(r"\d", w["text"]):
            rows.setdefault(round(w["top"]), []).append(w)
    for top, rw in rows.items():
        for m in _premerge_fragments(rw):
            xs.append(m["x1"])
    if len(xs) < 12:
        return None
    xs.sort()
    tol = 8.0
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    centers = [(max(c), len(c)) for c in clusters]
    strong = [ctr for ctr, n in centers if n >= 3]
    if len(strong) < 12:
        strong = [ctr for ctr, n in centers if n >= 2]
    strong.sort()
    if len(strong) < 12:
        return None
    month_rights = strong[:12]
    edges = [label_cutoff_x]
    for i in range(11):
        edges.append((month_rights[i] + month_rights[i + 1]) / 2)
    col_w = month_rights[11] - month_rights[10]
    edges.append(month_rights[11] + col_w * 0.5)
    return edges, edges[-1]


def _premerge_fragments(numeric_words, gap_tol=4.0):
    """
    Merge adjacent numeric word-fragments that belong to a single value.

    Two cases:
      (a) tiny horizontal gap (<= gap_tol): straightforward split, e.g.
          '$' + '(380)'  ->  '$(380)'.
      (b) thousands-fragmentation: a short leading group (1-3 bare digits or
          '$' + digits) immediately followed by a token beginning with a comma
          group, e.g. '3' + '3,169' -> '33,169', '$' '9' '3,382' -> '93,382'.
          These can have a slightly larger gap, so we also merge when the right
          token matches a ',ddd' continuation pattern.
    """
    if not numeric_words:
        return []
    ordered = sorted(numeric_words, key=lambda w: w["x0"])
    merged = [dict(text=ordered[0]["text"], x0=ordered[0]["x0"], x1=ordered[0]["x1"])]
    for w in ordered[1:]:
        prev = merged[-1]
        gap = w["x0"] - prev["x1"]
        prev_tail = prev["text"].lstrip("$()").replace(",", "")
        # continuation looks like 'd,ddd' or 'dd,ddd' or 'ddd' starting a group
        is_thousands_cont = bool(re.match(r"^\d{1,3},\d{3}", w["text"])) or \
                            bool(re.match(r"^\d{3}(?:[.)]|$)", w["text"]))
        prev_is_short_lead = bool(re.match(r"^\$?\(?\d{1,3}$", prev["text"]))
        if gap <= gap_tol or (is_thousands_cont and prev_is_short_lead and gap <= 14):
            prev["text"] += w["text"]
            prev["x1"] = w["x1"]
        else:
            merged.append(dict(text=w["text"], x0=w["x0"], x1=w["x1"]))
    return merged


def _bucket_row(numeric_words, edges):
    """
    Assign numeric words to 12 columns using boundary edges (13 values).
    Words are placed by their right edge (x1) since columns are right-aligned.
    Fragments within the same column are rejoined in x order.
    Returns list of 12 floats/None.
    """
    buckets = [[] for _ in range(12)]
    for w in numeric_words:
        x = w["x1"]
        # find column i such that edges[i] <= x < edges[i+1]
        placed = False
        for i in range(12):
            if edges[i] <= x < edges[i + 1]:
                buckets[i].append((w["x0"], w["text"]))
                placed = True
                break
        # words at/after the last edge are trailing (TOTAL/Budget) -> ignore
    vals = []
    for b in buckets:
        if not b:
            vals.append(None)
            continue
        joined = "".join(t for _, t in sorted(b))
        vals.append(_to_float(joined))
    return vals


def extract_pdf_cashflow_coords(path, property_code, capital_label_map=None,
                                noi_label_substring=None, skip_generic_capital=False):
    """
    capital_label_map: optional list of (raw_substring_lowercase, canonical_code)
      pairs, evaluated in order, for properties whose capital totals use custom
      labels.
    noi_label_substring: optional lowercase substring that identifies the NOI
      row when its label doesn't match a standard alias (e.g. Wacker).
    skip_generic_capital: when True, ignore generic capital aliases (Building/
      TI/LC) so only the custom capital_label_map rows are captured. Use for
      properties with multi-section capital schedules (e.g. Wacker).
    """
    facts = []
    fname = os.path.basename(path)
    seen = set()

    last_edges = None
    last_label_cutoff = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            if not words:
                continue
            hdr = _detect_columns(words)
            if hdr:
                hdr_edges, _ = hdr
                label_cutoff_x = hdr_edges[0] - 5
                detected = _cluster_columns(words, label_cutoff_x)
                if detected:
                    last_edges, _ = detected
                    last_label_cutoff = label_cutoff_x
            # Reuse last known geometry on header-less continuation pages.
            if last_edges is None:
                continue
            edges = last_edges
            right_cutoff = edges[-1]
            first_col_x = last_label_cutoff

            # Group words into rows by y.
            rows = {}
            for w in words:
                rows.setdefault(round(w["top"]), []).append(w)

            for top in sorted(rows):
                rw = sorted(rows[top], key=lambda w: w["x0"])
                label_words = [w["text"] for w in rw if w["x1"] < first_col_x]
                numeric_words = [w for w in rw
                                 if w["x0"] >= first_col_x and FRAG_RE.match(w["text"])]
                label = " ".join(label_words).strip()
                if not label or len(numeric_words) < 3:
                    continue

                code = _canon(label)
                low = label.lower()
                if not code and noi_label_substring and noi_label_substring in low:
                    code = "NOI"
                # Optionally suppress generic capital aliases so only custom
                # capital_label_map rows are used (multi-section schedules).
                if skip_generic_capital and code in (
                    "CAP_BUILDING", "CAP_TI", "CAP_LC", "CAP_DEFERRED",
                    "CAP_NONOP", "CAP_SUBTOTAL"):
                    code = None
                if not code and capital_label_map:
                    for sub, c in capital_label_map:
                        if sub in low:
                            code = c
                            break
                if not code or code in seen:
                    continue

                vals = _bucket_row(_premerge_fragments(numeric_words), edges)
                if sum(1 for v in vals if v is not None) < 3:
                    continue
                seen.add(code)
                for period_id, amt in zip(MONTHS_2026, vals):
                    if amt is None:
                        continue
                    facts.append({
                        "property_code": property_code,
                        "period_id": period_id,
                        "line_item_code": code,
                        "scenario": scenario_for_period(period_id),
                        "amount": float(amt),
                        "source_file": fname,
                        "source_label": label,
                    })
    return facts
