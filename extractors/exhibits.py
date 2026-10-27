"""
Exhibits: every exhibit, rider, addendum or schedule attached to an
instrument, with its title as printed and its pages, parented to the
instrument it is filed in.

An exhibit starts where its heading sits at the top of a page ("EXHIBIT A",
"EXHIBIT C - LANDLORD'S WORK", "RIDER NO. 1", "ADDENDUM") and runs to the
next exhibit or the end of its instrument. A heading in running text
("as shown on Exhibit A") is a reference, never a start.

    find_exhibits(pages, instruments) -> [Exhibit]
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HEAD_LINES = 4
_HEAD = re.compile(r'^\s*(EXHIBIT|RIDER|ADDENDUM|SCHEDULE|ATTACHMENT)\s*(?:NO\.?\s*)?'
                   r'["“”\']?([A-Z]{1,2}(?:-\d{1,2})?|\d{1,2}(?:\.\d)?|[IVX]{1,5})?["“”\']?\s*'
                   r'(?:[:.\-–—]\s*)?(.{0,90})$', re.I)


@dataclass
class Exhibit:
    label: str              # "Exhibit A", "Rider 1", "Addendum"
    title: str              # the title as printed, if any
    page_from: int
    page_to: int
    parent_seq: int | None  # the instrument it is filed in (1-based, split_instruments order)
    heading: str            # the heading line, verbatim


def _heading(page: str):
    lines = [ln.strip() for ln in (page or '').splitlines() if ln.strip()][:HEAD_LINES]
    for i, ln in enumerate(lines):
        m = _HEAD.match(ln)
        if not m:
            continue
        kind, ident, rest = m.group(1).title(), (m.group(2) or '').upper(), (m.group(3) or '').strip()
        if kind == 'Schedule' and not ident:
            continue                    # "Schedule of rent" is a table, not an attached schedule
        if rest and not re.search(r'[A-Z]{3}', rest) and len(rest) > 40:
            continue                    # a sentence, not a title
        title = rest
        if not title and i + 1 < len(lines) and lines[i + 1].isupper() and len(lines[i + 1]) <= 80:
            title = lines[i + 1]
        return f'{kind} {ident}'.strip(), title.strip(' -–—:'), ln
    return None


def find_exhibits(pages: list, instruments: list | None = None) -> list[Exhibit]:
    spans = [(k, i.page_from, i.page_to) for k, i in enumerate(instruments or [], 1)]

    def parent(p):
        return next((k for k, a, b in spans if a <= p <= b), None)

    starts = []
    for n, page in enumerate(pages, 1):
        h = _heading(page)
        if h:
            label, title, line = h
            if starts and starts[-1][1] == label and starts[-1][0] == n - 1:
                continue                # the same exhibit's heading repeated on its next page
            starts.append((n, label, title, line))
    out = []
    for k, (n, label, title, line) in enumerate(starts):
        par = parent(n)
        end = (starts[k + 1][0] - 1) if k + 1 < len(starts) else len(pages)
        if par is not None:
            end = min(end, next(b for kk, a, b in spans if kk == par))
            nxt = next((a for kk, a, b in spans if a > n), None)
            if nxt:
                end = min(end, nxt - 1)
        out.append(Exhibit(label, title, n, max(n, end), par, line))
    return out
