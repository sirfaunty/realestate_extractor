"""
Labels (depth layer C): every "LABEL: value" a page prints -- the fields of
a deal sheet, a lease summary, a checklist, a letter's heading -- as its own
row, the label and the value each quoted as printed.

    find_labels(page_text) -> [LabelValue]

A label is a short name before a colon at the start of a line or after the
previous pair's value ("SPACE: 220 SF: 3,153 CARRY COST: $0.00" is three
pairs). Never a clock time (9:35), a URL (https:), a ratio, or a sentence
that happens to hold a colon: a label is at most six words and starts with a
letter. A label printed with nothing after it is not a pair.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LABEL_WORDS = 6
MAX_VALUE = 160

# a label: a letter first, up to six words of letters / digits / light
# punctuation, then a colon followed by a space or the end of the line
_LABEL = re.compile(r"(?<![A-Za-z0-9:/])([A-Za-z][A-Za-z0-9.&/#()'’\-]*(?:[ \t]+[A-Za-z0-9.&/#()'’\-]+){0,5})[ \t]*:(?=[ \t]|$)")
_NOT_LABEL = re.compile(r'^(?:https?|mailto|ftp|re|cc|bcc|attn|note|notes|see|i\.?e|e\.?g)$', re.I)


@dataclass
class LabelValue:
    label: str          # as printed, without the colon
    value: str          # as printed, up to the next label on the line
    line_no: int        # 1-based line of the page


def _caps(word: str) -> bool:
    letters = [ch for ch in word if ch.isalpha()]
    return all(ch.isupper() for ch in letters)      # "&", "#" go with either side


def _is_label(lbl: str) -> bool:
    words = lbl.split()
    if not words or len(words) > MAX_LABEL_WORDS or _NOT_LABEL.match(lbl):
        return False
    letters = sum(ch.isalpha() for ch in lbl)
    if letters < 2:
        return False
    # a sentence fragment is lower case after its first word; a label is
    # capitalised or upper case ("SQ. FT.", "Report Date", "Tenant's Address")
    caps = sum(1 for w in words if w[:1].isupper() or w[:1].isdigit() or not w[:1].isalpha())
    return caps >= max(1, (len(words) + 1) // 2)


def find_labels(page: str) -> list[LabelValue]:
    out = []
    for n, line in enumerate((page or '').splitlines(), 1):
        hits = []                  # (label start, label, end of the colon)
        for m in _LABEL.finditer(line):
            start, lbl = m.start(1), m.group(1).strip()
            # "Elm Court OWNERSHIP:": on a form the label is the upper-case run
            # before the colon; the words in front are the previous value
            words = lbl.split()
            run = 0
            while run < len(words) and _caps(words[-1 - run]):
                run += 1
            if 0 < run < len(words) and any(not _caps(w) for w in words[:-run]):
                lbl = ' '.join(words[-run:])
                start = m.start(1) + m.group(1).rfind(lbl)
            if _is_label(lbl):
                hits.append((start, lbl, m.end()))
        # a later label must follow some value: "Lease Date: May 1, 2020 Term: 5 years"
        pairs = []
        for k, (start, lbl, end_colon) in enumerate(hits):
            end = hits[k + 1][0] if k + 1 < len(hits) else len(line)
            value = line[end_colon:end].strip(' \t;,|')
            if k + 1 < len(hits) and not value:
                continue          # "PROPOSED TENANT:" heading over the next pair, nothing printed
            pairs.append((lbl, value[:MAX_VALUE]))
        for lbl, value in pairs:
            if value and re.search(r'[A-Za-z0-9$]', value):
                out.append(LabelValue(lbl, value, n))
    return out
