"""
Segment-first lease extraction — the pattern validated on a pilot property
(7% -> 71% tie-out vs the landlord's lease master; 100% of
document-derivable fields) generalized for the Capactive engine.

Three stages, all operating on a page list [(page_number, text), ...]:

1. segment_pages()      — instrument-chain detection (lease -> amendments ->
                          estoppel/SNDA/exhibits) + article/section
                          segmentation with TOC suppression. Handles both
                          lease families seen in the portfolio corpus:
                          `ARTICLE N` (title on next line) and numbered
                          paragraphs (`1. PREMISES:`, OCR paren form
                          `12.) INSURANCE.`).
2. build_clause_records() — one provision record per recovered segment
                          (depth parity groundwork; deterministic, no LLM).
3. extract_targeted()   — small per-segment LLM prompts for identity /
                          term / SF / rent with chain-awareness (later
                          instruments supersede), auto-renewal roll-forward,
                          computed expirations (commencement + stated term),
                          and contingent-commencement suppression: if the
                          lease defines commencement as "the earlier of
                          opening / delivery + N days", no calendar
                          expiration is derivable from the instrument —
                          better a flagged null than a wrong computed date.

Flag, don't fabricate: every value carries a section_ref/page; computed
values say so in their label; nulls stay null.
"""

import datetime
import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

# ─── Heading + instrument grammar (validated 90% vs a lease master) ───────────

ROMAN = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7,
         'viii': 8, 'ix': 9, 'x': 10, 'xi': 11, 'xii': 12, 'xiii': 13,
         'xiv': 14, 'xv': 15, 'xvi': 16, 'xvii': 17, 'xviii': 18, 'xix': 19,
         'xx': 20, 'xxi': 21, 'xxii': 22, 'xxiii': 23, 'xxiv': 24, 'xxv': 25,
         'xxvi': 26, 'xxvii': 27, 'xxviii': 28, 'xxix': 29, 'xxx': 30}

ORDINALS = ('FIRST', 'SECOND', 'THIRD', 'FOURTH', 'FIFTH', 'SIXTH', 'SEVENTH',
            'EIGHTH', 'NINTH', 'TENTH')

INSTRUMENT_PATTERNS = [
    # a fresh lease agreement mid-package (renewals shipped as complete
    # leases — common for national salon / service chains) starts its own instrument
    (r'^(?:SHOPPING\s+CENTER\s+)?LEASE\s+AGREEMENT\s*$', 'lease'),
    (r'^RENEWAL\s+(?:OF\s+)?LEASE', 'lease'),
    (r'^(?:' + '|'.join(ORDINALS) + r')\s+AMENDMENT\s+(?:TO|OF)\s+LEASE', 'amendment'),
    (r'^AMENDMENT\s+(?:TO|OF)\s+LEASE', 'amendment'),
    (r'^AMENDMENT\s*$', 'amendment'),
    (r'^LEASE\s+AMENDMENT', 'amendment'),
    (r'^ASSIGNMENT\s+(?:AND\s+ASSUMPTION\s+)?OF\s+LEASE', 'assignment'),
    (r'^LEASE\s+TERMINATION', 'termination'),
    (r'^TERMINATION\s+(?:OF|AGREEMENT)', 'termination'),
    (r'^(?:SHOPPING\s+CENTER\s+)?SUBLEASE(?:\s+AGREEMENT)?\s*$', 'sublease'),
    (r'^MEMORANDUM\s+OF\s+(?:SUB)?LEASE', 'memorandum'),
    (r'^SUBORDINATION[,\s]+NON.?DISTURBANCE', 'snda'),
    (r'^ESTOPPEL\s+CERTIFICATE', 'estoppel'),
    (r'(?i)^estoppel\s+certificate\s*$', 'estoppel'),
    (r'^GUARANTY(?:\s+OF\s+LEASE)?\s*$', 'guaranty'),
    (r'^LICENSE\s+AGREEMENT', 'license'),
    (r'^EXHIBIT\s*"?([A-Z])"?\s*$', 'exhibit'),
    (r'^EXHIBIT([A-Z])\s*$', 'exhibit'),          # OCR: "EXHIBITB"
]

RE_ARTICLE = re.compile(r'^ARTICLE\s+(\d{1,2}|[IVXL]+)\s*[:.]?\s*(.*)$', re.I)
RE_NUMPARA_CAPS = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Z &/,'’\-]{2,60}?)\s*(?:[:.~]|$)")
RE_NUMPARA_PAREN = re.compile(r"^(\d{1,2})\s*[.,]?\)\s*([A-Z][A-Z &/,'’\-]{2,60}?)\s*(?:[:.~]|$)")
RE_NUMPARA_TITLE = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Za-z ,&/'’-]{2,60}\.?)\s*$")
RE_SECTION = re.compile(r'^SECTION\s+(\d{1,2})\s*[:.]?\s*(.*)$', re.I)

LEASE_BODY_KINDS = ('lease', 'amendment', 'assignment', 'license', 'sublease')

WORD_NUMS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
             'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'fifteen': 15,
             'twenty': 20, 'twentyfive': 25, 'thirty': 30}

MONTHS = {m: i + 1 for i, m in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july',
     'august', 'september', 'october', 'november', 'december'])}

SYSTEM_PROMPT = (
    "You are a commercial lease abstraction assistant. ONLY the lease "
    "language provided governs. Never guess, never use outside knowledge. "
    "If a value is not stated in the text, return null. Respond with JSON only.")

# "Earlier of opening / delivery+N days" — commencement is an operational
# fact, not a document fact; expiration cannot be computed from the paper.
RE_CONTINGENT = re.compile(
    r'earlier\s+of[^.]{0,200}?(open|possession|delivery)', re.I | re.S)


def _art_num(tok):
    tok = tok.strip().lower()
    if tok.isdigit():
        return int(tok)
    return ROMAN.get(tok)


# ─── Stage 1: instruments + segments ─────────────────────────────────────

def _find_instruments(pages, skip_pages=frozenset()):
    """skip_pages: TOC pages — their EXHIBIT A/B/... index lines would
    otherwise read as instrument boundaries and swallow the lease body."""
    bounds = []
    for idx, (pg, text) in enumerate(pages):
        if pg in skip_pages:
            continue
        top = [l.strip() for l in text.split('\n') if l.strip()][:6]
        for line in top:
            for pat, kind in INSTRUMENT_PATTERNS:
                if re.match(pat, line):
                    bounds.append((idx, kind, line[:80]))
                    break
            else:
                continue
            break
    starts = [0] + [b[0] for b in bounds if b[0] != 0]
    kinds = ['lease'] + [b[1] for b in bounds if b[0] != 0]
    heads = ['(primary instrument)'] + [b[2] for b in bounds if b[0] != 0]
    if bounds and bounds[0][0] == 0:
        kinds[0], heads[0] = bounds[0][1], bounds[0][2]
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(pages)
        out.append({'kind': kinds[i], 'header': heads[i],
                    'page_start': pages[s][0], 'page_end': pages[e - 1][0],
                    'pages': pages[s:e], 'segments': []})
    return out


def _toc_pages(pages):
    out, density = set(), {}
    for pg, text in pages:
        lines = text.split('\n')
        # Banner anywhere on the page — OCR reading order can push
        # "TABLE OF CONTENTS" below the exhibit index it labels.
        joined = ' '.join(lines).upper()
        nonblank = [l.strip() for l in lines if l.strip()]
        # Dot leaders count wherever they appear in the line: OCR mangles
        # "Term ......... 4" into "Term ..... cc cecessess", which no longer
        # *ends* with dots but still contains the leader run.
        toc_like = sum(1 for l in nonblank
                       if RE_NUMPARA_TITLE.match(l) or re.search(r'\.{4,}', l))
        density[pg] = toc_like
        top = ' '.join(lines[:12]).upper()
        if ('TABLE OF CONTENTS' in joined or 'INDEX TO LEASE' in joined or
                ('ARTICLE' in top and 'DESCRIPTION' in top and 'PAGE' in top)):
            out.add(pg)
            continue
        if len(nonblank) >= 4 and toc_like >= 4 and toc_like / len(nonblank) >= 0.4:
            out.add(pg)
    changed = True
    while changed:
        changed = False
        for pg in list(out):
            for nb in (pg - 1, pg + 1):
                if nb not in out and density.get(nb, 0) >= 2:
                    out.add(nb)
                    changed = True
    return out


def _segment_instrument(inst, skip_pages=frozenset()):
    flat, gi = [], 0
    for pg, text in inst['pages']:
        for l in text.split('\n'):
            flat.append((pg, gi, l))
            gi += 1
    cands_article, cands_numpara = [], []
    for k, (pg, _gi, l) in enumerate(flat):
        if pg in skip_pages:
            continue
        s = l.strip()
        m = RE_ARTICLE.match(s)
        if m and len(s) < 70:
            n = _art_num(m.group(1))
            title = m.group(2).strip()
            if not title:
                for _, _, nl in flat[k + 1:k + 4]:
                    if nl.strip():
                        title = nl.strip()
                        break
            if n:
                cands_article.append((k, pg, n, title))
            continue
        m = RE_SECTION.match(s)
        if m and len(s) < 70:
            cands_article.append((k, pg, int(m.group(1)), m.group(2).strip()))
            continue
        m = RE_NUMPARA_CAPS.match(s) or RE_NUMPARA_PAREN.match(s)
        if m:
            cands_numpara.append((k, pg, int(m.group(1)), m.group(2).strip()))
            continue
        m = RE_NUMPARA_TITLE.match(s)
        if m:
            cands_numpara.append((k, pg, int(m.group(1)), m.group(2).strip()))

    def ascending(cands):
        out, last = [], 0
        for item in cands:
            if item[2] >= last:
                out.append(item)
                last = item[2]
        return out

    arts, nums = ascending(cands_article), ascending(cands_numpara)
    heads = arts if len(arts) >= len(nums) else nums
    style = 'article' if heads is arts else 'numbered'
    if len(heads) < 4:
        # CAPS-heading fallback (the Barnes & Noble form): standalone
        # all-caps title lines with no numbering scheme at all
        caps = []
        for k, (pg, _gi, l) in enumerate(flat):
            if pg in skip_pages:
                continue
            s = l.strip()
            if (re.match(r"^[A-Z][A-Z &,'\-/]{8,50}$", s)
                    and not re.search(r'\d', s)
                    and not re.match(r'^(EXHIBIT|ARTICLE|SECTION|WITNESS|'
                                     r'LANDLORD|TENANT|GUARANTOR)\b', s)):
                caps.append((k, pg, len(caps) + 1, s.title()))
        if len(caps) >= 6 and len(caps) > len(heads):
            heads, style = caps, 'caps'
    segments = []
    if heads:
        k0 = heads[0][0]
        if k0 > 0:
            pre = '\n'.join(t for (_, _, t) in flat[:k0]).strip()
            if pre:
                segments.append({'num': 0,
                                 'title': '(preamble / cover / fundamental provisions)',
                                 'page_start': flat[0][0], 'page_end': flat[k0 - 1][0],
                                 'chars': len(pre), 'text': pre,
                                 'lines': [(p, t) for (p, _, t) in flat[:k0]]})
        for j, (k, pg, n, title) in enumerate(heads):
            k_end = heads[j + 1][0] if j + 1 < len(heads) else len(flat)
            text = '\n'.join(t for (_, _, t) in flat[k:k_end]).strip()
            segments.append({'num': n, 'title': title, 'page_start': pg,
                             'page_end': flat[k_end - 1][0] if k_end > k else pg,
                             'chars': len(text), 'text': text,
                             'lines': [(p, t) for (p, _, t) in flat[k:k_end]]})
    elif flat:
        whole = '\n'.join(t for (_, _, t) in flat).strip()
        if whole:
            segments.append({'num': 0, 'title': '(unsegmented instrument)',
                             'page_start': flat[0][0], 'page_end': flat[-1][0],
                             'chars': len(whole), 'text': whole,
                             'lines': [(p, t) for (p, _, t) in flat]})
    return style, segments


def segment_pages(pages):
    """pages: [(page_number:int, text:str), ...] -> instrument list."""
    pages = [(int(pg), t or '') for pg, t in pages]
    skip = _toc_pages(pages)
    instruments = _find_instruments(pages, skip)
    for inst in instruments:
        if inst['kind'] in LEASE_BODY_KINDS:
            style, segs = _segment_instrument(inst, skip)
            inst['style'] = style
            inst['segments'] = segs
    return instruments


# ─── Stage 2: provision depth (deterministic) ────────────────────────────

_CLAUSE_TYPE_MAP = [
    (r'rent|minimum rent|percentage', 'rent'),
    (r'term|commencement', 'term'),
    (r'premises|leased premises', 'premises'),
    (r'\buse\b|permitted', 'use'),
    (r'insurance|indemn|waiver', 'insurance_indemnity'),
    (r'assign|sublet', 'assignment'),
    (r'default|remedies', 'default'),
    (r'repair|maintenance|care of', 'maintenance'),
    (r'tax', 'taxes'),
    (r'utilities', 'utilities'),
    (r'sign', 'signage'),
    (r'subordination|estoppel|attornment', 'subordination'),
    (r'casualty|damage|destruction|eminent|condemn', 'casualty_condemnation'),
    (r'surrender|holdover|holding over', 'surrender_holdover'),
    (r'alteration|installation|fixture', 'alterations'),
    (r'common area|operating (expense|cost)|cam', 'operating_costs'),
    (r'notice', 'notices'),
    (r'guaranty', 'guaranty'),
]


def _clause_type(title):
    t = (title or '').lower()
    for pat, ct in _CLAUSE_TYPE_MAP:
        if re.search(pat, t):
            return ct
    return 'provision'


# Section-level sub-provisions — the master's granularity ("Section 3: If
# applicable, for purposes of calculating ..."):
RE_SUBSEC_ART = re.compile(r'^Section\s+(\d{1,2})\s*[.:]\s*(.*)$', re.I)
RE_SUBSEC_NUM = re.compile(r'^(\d{1,2})\.(\d{1,2})\.?\s+(.*)$')


RE_INLINE_SUBSEC = re.compile(
    r'(?=(?:^|\s)(\d{1,2})\.(\d{1,2})\.\s+[A-Z“"])')


def _explode_inline(lines):
    """The Ross form flows 'N.N. Title.' section starts mid-paragraph in
    the extracted text. Split such lines at inline section marks so the
    normal mark loop sees them at line starts."""
    out = []
    for pg, l in lines:
        pieces = RE_INLINE_SUBSEC.split(l)
        # re.split with lookahead+groups interleaves; rebuild simply
        if len(pieces) <= 1:
            out.append((pg, l))
            continue
        idx = [m.start() for m in RE_INLINE_SUBSEC.finditer(l)]
        prev = 0
        for i in idx:
            chunk = l[prev:i].strip()
            if chunk:
                out.append((pg, chunk))
            prev = i
        tail = l[prev:].strip()
        if tail:
            out.append((pg, tail))
    return out


def split_subprovisions(seg, style):
    """Split an article/numbered segment into section-level provisions.

    Article form: 'Section N.' lines inside each ARTICLE.
    Numbered form: decimal subsections ('12.1 INSURANCE BY TENANT.').
    Ross form: inline 'N.N. Title.' starts exploded to line starts first.
    Returns [] when the segment has no internal sections (short articles).
    """
    lines = seg.get('lines') or [(seg['page_start'], l)
                                 for l in seg['text'].split('\n')]
    if style in ('numbered', 'article'):
        # quick census: if line-start decimal marks are sparse but inline
        # ones are plentiful, explode lines at inline section starts
        line_marks = sum(1 for _pg, l in lines
                         if RE_SUBSEC_NUM.match(l.strip()))
        if line_marks < 4:
            inline = sum(len(list(RE_INLINE_SUBSEC.finditer(l)))
                         for _pg, l in lines)
            if inline >= 8:
                lines = _explode_inline(lines)
    marks = []
    for i, (pg, l) in enumerate(lines):
        s = l.strip()
        if style in ('article', 'caps'):
            # both notations occur under ARTICLE headings: 'Section N.'
            # lines AND decimal '2.5. Percentage Rent.' (a national-tenant form)
            mm = RE_SUBSEC_ART.match(s)
            if mm and len(s) > 12:      # a bare 'Section 2' TOC echo is noise
                marks.append((i, pg, mm.group(1), mm.group(2)))
                continue
            mm = RE_SUBSEC_NUM.match(s)
            if mm and len(s) > 8:
                marks.append((i, pg, f'{mm.group(1)}.{mm.group(2)}', mm.group(3)))
        else:
            mm = RE_SUBSEC_NUM.match(s)
            if mm:
                marks.append((i, pg, f'{mm.group(1)}.{mm.group(2)}', mm.group(3)))
    if style == 'numbered' and marks:
        # ascending (major, minor) filter — screens inline false positives
        # like dates ("12.31.") that mimic section numbers
        kept, last = [], (0, 0)
        for m_ in marks:
            try:
                parts = str(m_[2]).split('.')
                key = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
            except (ValueError, IndexError):
                continue
            if key >= last:
                kept.append(m_)
                last = key
        marks = kept
    if len(marks) < 2:
        return []
    subs = []
    for j, (i, pg, num, first) in enumerate(marks):
        i_end = marks[j + 1][0] if j + 1 < len(marks) else len(lines)
        text = '\n'.join(t for _p, t in lines[i:i_end]).strip()
        subs.append({'sub': num, 'first_words': first.strip()[:60],
                     'page_start': pg, 'page_end': lines[i_end - 1][0],
                     'chars': len(text), 'text': text})
    return subs


def build_clause_records(instruments, max_text=6000):
    """One clause record per recovered segment — the master's article-level
    granularity, deterministically, before any LLM summarization."""
    out = []
    _ANCILLARY = {'estoppel': 'subordination', 'snda': 'subordination',
                  'guaranty': 'guaranty', 'termination': 'surrender_holdover',
                  'memorandum': 'provision'}
    for inst in instruments:
        # ancillary instruments (not segmented) still carry provisions the
        # master abstracts — one whole-instrument record each
        if inst['kind'] in _ANCILLARY and not inst.get('segments'):
            text = '\n'.join(t for _pg, t in inst['pages']).strip()
            if text:
                out.append({
                    'clause_type': _ANCILLARY[inst['kind']],
                    'clause_title': f"{inst['kind']}: {inst['header']}"[:200],
                    'full_text': text[:max_text],
                    'summary': None,
                    'section_ref': f"{inst['kind']} p{inst['page_start']}",
                    'page_number': inst['page_start'],
                    'confidence': 0.85,
                })
            continue
        style = inst.get('style', 'article')
        for s in inst.get('segments', []):
            if s['num'] == 0 and inst['kind'] != 'lease':
                continue
            label = ('Article' if style == 'article' else 'Section')
            ref = (f"{label} {s['num']}" if s['num'] else 'Preamble')
            if inst['kind'] != 'lease':
                ref = f"{inst['kind']}: {ref}"
            out.append({
                'clause_type': _clause_type(s['title']),
                'clause_title': (f"{ref}: {s['title']}" if s['title'] else ref)[:200],
                'full_text': s['text'][:max_text],
                'summary': None,
                'section_ref': ref,
                'page_number': s['page_start'],
                'confidence': 0.9,
            })
            # section-level depth: one provision per internal section,
            # mirroring the master's granularity
            for sub in split_subprovisions(s, style):
                sub_ref = (f"{ref}, Section {sub['sub']}" if style == 'article'
                           else f"Section {sub['sub']}")
                if inst['kind'] != 'lease' and not sub_ref.startswith(inst['kind']):
                    sub_ref = f"{inst['kind']}: {sub_ref}"
                out.append({
                    'clause_type': _clause_type(s['title']),
                    'clause_title': f"{sub_ref}: {sub['first_words']}"[:200],
                    'full_text': sub['text'][:max_text],
                    'summary': None,
                    'section_ref': sub_ref[:100],
                    'page_number': sub['page_start'],
                    'confidence': 0.85,
                })
    return out


# ─── Stage 3: targeted terms (chain-aware) ───────────────────────────────

def _valid_mdy(mth, d, y):
    return 1 <= mth <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100


def _to_mdy(s):
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        mth, d, y = int(m.group(2)), int(m.group(3)), int(m.group(1))
        return f'{mth}/{d}/{y}' if _valid_mdy(mth, d, y) else None
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:      # day-first slip ("13/05/2027")
            a, b = b, a
        return f'{a}/{b}/{y}' if _valid_mdy(a, b, y) else None
    m = re.search(r'([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', s)
    if m and m.group(1).lower() in MONTHS:
        mth, d, y = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
        return f'{mth}/{d}/{y}' if _valid_mdy(mth, d, y) else None
    # "the 31st day of August, 2029"
    m = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+([A-Za-z]+),?\s+(\d{4})', s)
    if m and m.group(2).lower() in MONTHS:
        mth, d, y = MONTHS[m.group(2).lower()], int(m.group(1)), int(m.group(3))
        return f'{mth}/{d}/{y}' if _valid_mdy(mth, d, y) else None
    return None


MONTH_NAMES = {v: k for k, v in MONTHS.items()}


def _date_in_text(mdy, text):
    """Flag-don't-fabricate enforcement: an LLM-returned date is only
    accepted if that calendar date literally appears in the segment it was
    extracted from (numeric, 'Month D, YYYY', or 'Dth day of Month, YYYY',
    OCR-tolerant spacing)."""
    if not mdy:
        return False
    m, d, y = (int(x) for x in mdy.split('/'))
    if not _valid_mdy(m, d, y):     # belt and suspenders — never KeyError
        return False
    name = MONTH_NAMES[m]
    pats = [
        rf'{m}\s*[/\-.]\s*0?{d}\s*[/\-.]\s*{y}',
        rf'0?{m}\s*[/\-.]\s*0?{d}\s*[/\-.]\s*{str(y)[2:]}\b',
        rf'(?i){name[:3]}\w*\.?\s+0?{d}(?:st|nd|rd|th)?\s*,?\s*{y}',
        rf'(?i)0?{d}(?:st|nd|rd|th)?\s+day\s+of\s+{name[:3]}\w*\s*,?\s*{y}',
    ]
    return any(re.search(p, text) for p in pats)


def _term_months(s):
    if not s or not isinstance(s, str):
        return None
    s = s.lower()
    m = re.search(r'(\d{1,3})\s*\)?\s*(year|month)', s)
    if m:
        return int(m.group(1)) * (12 if m.group(2) == 'year' else 1)
    w = re.search(r'\b([a-z]+)\s*(?:\(\d+\))?\s*(year|month)', s)
    if w and w.group(1) in WORD_NUMS:
        return WORD_NUMS[w.group(1)] * (12 if w.group(2) == 'year' else 1)
    return None


def _add_months_minus_day(mdy, months):
    mth, d, y = (int(x) for x in mdy.split('/'))
    total = (mth - 1) + months
    y2, m2 = y + total // 12, total % 12 + 1
    while True:
        try:
            end = datetime.date(y2, m2, d)
            break
        except ValueError:
            d -= 1
    end -= datetime.timedelta(days=1)
    return f'{end.month}/{end.day}/{end.year}'


def _roll_forward(mdy, period_months, as_of):
    mth, d, y = (int(x) for x in mdy.split('/'))
    cur = datetime.date(y, mth, d)
    limit = datetime.date(*as_of)
    rolls = 0
    while cur < limit and rolls < 12:
        total = (cur.month - 1) + period_months
        y2, m2 = cur.year + total // 12, total % 12 + 1
        dd = cur.day
        while True:
            try:
                cur = datetime.date(y2, m2, dd)
                break
            except ValueError:
                dd -= 1
        rolls += 1
    return f'{cur.month}/{cur.day}/{cur.year}', rolls


RE_DATE_RANGE = re.compile(
    r'(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})\s*(?:to|through|thru|[-–])\s*'
    r'(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4})')
_MONTH_DATE = (r'(?:January|February|March|April|May|June|July|August|September|'
               r'October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|'
               r'Nov|Dec)\.?\s+\d{1,2}\s*,?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}')
_DAY_OF = (r'\d{1,2}(?:st|nd|rd|th)?\s+day\s+of\s+(?:January|February|March|April|May|June|'
           r'July|August|September|October|November|December),?\s+\d{4}')
RE_EXPIRE_STMT = re.compile(
    rf'(?i)(?:expire|expiring|ending|terminat\w+)[^.]{{0,140}}?on\s+({_MONTH_DATE})'
    # summary label: "1.7 EXPIRATION DATE: July 31, 2018"
    rf'|(?i:expiration\s+date)\s*[:.\-]\s*({_MONTH_DATE})'
    # term clause: "commencing February 1, 2018 and ending January 31, 2021"
    rf'|(?i:commenc\w+|beginning|starting|began|begins)\s+(?:on\s+)?(?:{_MONTH_DATE})\s*'
    rf'(?:\([^)]{{0,40}}\))?\s*,?\s*(?i:and\s+ending|and\s+ends)\s+(?:on\s+)?({_MONTH_DATE})'
    # amendment: "... through August 31, 2030 (the “Expiration Date”)"
    rf'|({_MONTH_DATE}|{_DAY_OF})\s*\(\s*(?:the\s+)?["“]?(?i:expiration|termination)\s+date'
    # "continuing thereafter to and including the 31st day of August, 2029"
    rf'|(?i:continuing\s+(?:thereafter\s+)?(?:to\s+and\s+including|through|until)|'
    rf'expire\s+on|ending\s+on)\s+(?:the\s+)?({_DAY_OF})')

# lease-summary rent tables: "7/1/2033 6/30/2034 $29,601.00" (no "to")
RE_DATE_ROW = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}/\d{1,2}/\d{4})\s+\$')


def _scan_expiration(instruments):
    """Deterministic expiration recovery when the LLM pass finds nothing:

    1. explicit statements — 'shall expire ... on January 31, 2033';
    2. rent-schedule ranges — '(8/1/2027 to 7/31/2028)': the latest period
       end across the lease package implies the term end.

    Dates come straight from the text, so they are verified by construction.
    Returns (mdy, source_desc) or (None, None).
    """
    import datetime as _dt

    def parse(s):
        mdy = _to_mdy(s)
        if not mdy:
            return None
        m, d, y = (int(x) for x in mdy.split('/'))
        try:
            return _dt.date(y, m, d)
        except ValueError:
            return None

    stated, ranges = [], []
    for inst in instruments:
        if inst['kind'] not in LEASE_BODY_KINDS + ('exhibit',):
            continue
        for pg, text in inst['pages']:
            # explicit statements only from operative instruments — exhibits
            # quote all sorts of dates (estoppels, letters, prior tenancies)
            if inst['kind'] in LEASE_BODY_KINDS:
                for mm in RE_EXPIRE_STMT.finditer(text):
                    dt = parse(next(g for g in mm.groups() if g))
                    # an unexercised option period's end is not the term's end —
                    # judged by the statement's OWN sentence (a nearby option
                    # clause is normal next to the real term)
                    sent = text[max(0, text.rfind('.', 0, mm.start()) + 1):mm.start()]
                    if dt and not re.search(r'option\s+(?:period|term)', sent, re.I):
                        stated.append((dt, f'stated p{pg}', pg))
            for rx in (RE_DATE_RANGE, RE_DATE_ROW):
                for mm in rx.finditer(text):
                    dt = parse(mm.group(2))
                    # a schedule row labelled "(2nd Option)" or "Option Period:
                    # 7/1/2013 - 6/30/2018" is not the term
                    if dt and not re.search(r'^\W{0,4}\(?\s*\w{0,4}\s*option',
                                            text[mm.end():mm.end() + 25], re.I) \
                            and not re.search(r'option\s*(?:period|term|years?)?\s*[:\-]?\s*$',
                                              text[max(0, mm.start() - 30):mm.start()], re.I):
                        ranges.append((dt, f'rent-schedule end p{pg}', pg))

    def fmt(b):
        return f'{b[0].month}/{b[0].day}/{b[0].year}', b[1]

    best_r = max(ranges) if len(ranges) >= 2 else None   # a lone range is too weak
    if stated:
        best = max(stated)
        # a LATER instrument's dated schedule running past the latest stated
        # date (e.g. a relocation amendment's rent table) governs
        if best_r and best_r[0] > best[0] and best_r[2] > best[2]:
            return fmt(best_r)
        return fmt(best)
    if best_r:
        return fmt(best_r)
    return None, None


def _ask(llm, seg_text, question_json, label):
    if llm is None:
        return {}
    # cap under the LLM client's 10K threshold: prompts land in the 60s
    # timeout tier instead of 90s — faster answers, cheaper failures
    if len(seg_text) > 9500:
        seg_text = seg_text[:9500]
    prompt = (f'LEASE TEXT ({label}):\n"""\n{seg_text}\n"""\n\n'
              f'Extract exactly this JSON (null for anything not stated):\n'
              f'{question_json}\n')
    try:
        out = llm.generate_structured(prompt, SYSTEM_PROMPT)
    except Exception as e:
        logger.warning(f'segment LLM call failed ({label}): {e}')
        return {}
    if not isinstance(out, dict):
        return {}
    # Placeholder-echo guard: some models copy the field DESCRIPTION from
    # the question template as the answer (qwen3:8b returned "the d/b/a"
    # for trade_name on real leases, 2026-09-29 bake-off). Any value that
    # is just (a fragment of) its own hint, or a template token, is noise.
    hints = dict(re.findall(r'"(\w+)":\s*"([^"]+)"', question_json))
    for k, v in list(out.items()):
        if not isinstance(v, str):
            continue
        s = v.strip().lower().strip('.')
        h = hints.get(k, '').lower()
        if (s in ('...', '…', 'null', 'none', 'n/a', 'mm/dd/yyyy')
                or (h and (s == h or (len(s) >= 5 and s in h)))
                or s.startswith('the d/b/a')):
            out[k] = None
        elif isinstance(out.get(k), str) and ' / the d/b/a' in out[k].lower():
            out[k] = re.split(r'\s*/\s*the d/b/a', out[k], flags=re.I)[0]
    return out


def _route_segments(instruments):
    tgt = {'identity': [], 'term': [], 'sf': [], 'rent': [], 'amend': []}
    lease_insts = [i for i in instruments if i.get('segments')
                   and i['kind'] == 'lease']
    # primary = the RICHEST lease instrument: the mid-package LEASE
    # AGREEMENT boundary can make a cover page its own tiny 'lease'
    # instrument ahead of the body
    primary = (max(lease_insts, key=lambda i: len(i['segments']))
               if lease_insts else None)
    if primary:
        segs = primary['segments']
        for s in segs:
            title = (s['title'] or '').lower()
            if s['num'] == 0:
                tgt['identity'].append(s)
                tgt['sf'].append(s)
            if re.search(r'term|commencement', title):
                tgt['term'].append(s)
            if re.search(r'premises|lease provisions|fundamental', title):
                tgt['sf'].append(s)
                tgt['term'].append(s)
            if re.search(r'\brent\b|rental', title) and len(tgt['rent']) < 1:
                tgt['rent'].append(s)
        if not tgt['identity'] and segs:
            tgt['identity'].append(segs[0])

        def dedup(lst):
            seen, out = set(), []
            for s in lst:
                if id(s) not in seen:
                    seen.add(id(s))
                    out.append(s)
            return out
        tgt['term'] = dedup(tgt['term'])[:3]
        tgt['sf'] = dedup(tgt['sf'])[:3]
    for a in instruments:
        if a['kind'] == 'amendment':
            text = '\n'.join(t for _pg, t in a['pages']).strip()
            tgt['amend'].append({'title': a['header'],
                                 'page_start': a['page_start'],
                                 'page_end': a['page_end'], 'text': text})
    return tgt


def extract_targeted(pages, instruments, llm=None, as_of=None):
    """Chain-aware targeted extraction. Returns engine-format term dicts.

    as_of: (y, m, d) date auto-renewing terms are rolled forward to
    (default: today).
    """
    as_of = as_of or (lambda t: (t.year, t.month, t.day))(datetime.date.today())
    tgt = _route_segments(instruments)
    body = '\n'.join(t for _pg, t in pages)
    terms = []

    # identity — LLM on preamble, deterministic definition-clause net always
    ident_names = None
    # the landlord as the lease itself defines it — a model "tenant" that
    # matches it is the landlord-confusion failure (llama3.1 named Engelsma
    # LP as UPS's tenant, 2026-09-29 bake-off)
    _flat_head = re.sub(r'\s+', ' ', body[:20000])
    _lease_head = re.sub(r'\s+', ' ', ' '.join(
        s['text'] for s in tgt['identity'][:2]))
    _det_ll = ((_lease_head and _party(_lease_head, 'Landlord'))
               or _party(_flat_head, 'Landlord') or '')
    _det_ll_toks = {w for w in re.findall(r'[a-z]{5,}', _det_ll.lower())
                    if w not in ('limited', 'partnership', 'company',
                                 'corporation', 'properties', 'holdings')}
    for s in tgt['identity'][:2]:
        r = _ask(llm, s['text'],
                 '{"tenant_legal_name": ..., '
                 '"trade_name": "the d/b/a or store brand if stated", '
                 '"landlord_name": ...}', f'identity p{s["page_start"]}')
        name = r.get('tenant_legal_name') or r.get('trade_name')
        if name:
            # landlord-confusion guard, two nets:
            # (a) the model's own landlord_name shares a distinctive token
            #     with its "tenant" (OCR junk prefixes like TAENGELSMA make
            #     exact matching useless — substring both ways);
            # (b) the name sits next to a ("Landlord") definition in the body.
            generic = {'limited', 'partnership', 'company', 'corporation',
                       'holdings', 'properties', 'group'}
            nm = alpha_l = re.sub(r'[^a-z]', '', str(name).lower())
            ll = str(r.get('landlord_name') or '')
            ll_toks = [re.sub(r'[^a-z]', '', w.lower())
                       for w in re.split(r'[^A-Za-z]+', ll)]
            confused = any(t and len(t) >= 6 and t not in generic
                           and (t in nm or nm in t) for t in ll_toks)
            lo = re.escape(str(name).strip()[:40])
            body_head = '\n'.join(t for _pg, t in pages)[:20000]
            legal = str(r.get('tenant_legal_name') or '').lower()
            is_det_landlord = bool(_det_ll_toks) and bool(
                _det_ll_toks & set(re.findall(r'[a-z]{5,}', legal)))
            if is_det_landlord and r.get('trade_name'):
                # keep the brand, drop the landlord-as-legal-name
                r['tenant_legal_name'] = None
                name = r.get('trade_name')
                is_det_landlord = False
            brand = str(r.get('trade_name') or '').strip()
            brand_clean = brand and not any(
                t and len(t) >= 6 and t in re.sub(r'[^a-z]', '', brand.lower())
                for t in ll_toks + list(_det_ll_toks))
            if (confused or is_det_landlord) and brand_clean and \
                    brand.lower() not in legal:
                # legal name is contaminated by the landlord, the brand isn't:
                # keep what the model got right
                ident_names = (brand, s['page_start'])
                break
            if is_det_landlord or confused or re.search(
                    rf'(?i){lo}[^.\n]{{0,80}}[("“]+\s*Landlord', body_head):
                logger.info(f'rejecting identity {name!r} — matches the '
                            f'Landlord, not the Tenant')
                continue
            ident_names = (' / '.join(str(v) for v in
                                      (r.get('tenant_legal_name'),
                                       r.get('trade_name')) if v),
                           s['page_start'])
            break
    found = []
    m = re.search(r"([A-Z][A-Za-z0-9 .,&'’-]{2,60}?)\s*[,(]\s*"
                  r'(?:an?\s+[^()\n]{0,60}?)?\(?["“]?Tenant["”]?\)',
                  body[:20000])
    if m:
        found.append(m.group(1).strip(' ,'))
    m = re.search(r"d[./]?b[./]?a[./]?\s*([A-Z][A-Za-z0-9 .&'’-]{2,40})",
                  body[:20000])
    if m:
        found.append(m.group(1).strip())
    net = ' / '.join(dict.fromkeys(found)) if found else None
    if net and not _looks_like_party(net):
        net = None                      # an address caught before ("Tenant")
    if ident_names is None:
        # the party definition clause beats the loose net (which cut
        # "Dover Saddlery Retail, Inc." to "Dover" on a blind-test lease)
        det_tn = ((_lease_head and _party(_lease_head, 'Tenant'))
                  or _party(_flat_head, 'Tenant'))
        if det_tn:
            ident_names = (det_tn, None)
        elif net:
            ident_names = (net, None)
    if ident_names:
        terms.append({'term_type': 'tenant_identity',
                      'term_label': 'Tenant (segmented)',
                      'value_raw': ident_names[0], 'confidence': 0.8,
                      'section_ref': f'p{ident_names[1]}' if ident_names[1] else 'body scan',
                      'page_number': ident_names[1]})
        if net and net.lower() not in ident_names[0].lower():
            terms.append({'term_type': 'tenant_identity',
                          'term_label': 'Tenant (definition clause)',
                          'value_raw': net, 'confidence': 0.7,
                          'section_ref': 'body scan'})

    # term + expiration, chain-aware
    contingent = bool(RE_CONTINGENT.search(body))
    governing_exp = governing_src = commencement = None
    tmonths = None
    for s in tgt['term']:
        r = _ask(llm, s['text'],
                 '{"commencement_date": "MM/DD/YYYY or null", '
                 '"expiration_date": "MM/DD/YYYY or null", '
                 '"term_length": "as stated, e.g. ten (10) years", '
                 '"renewal_options": ...}',
                 f'term p{s["page_start"]}-{s["page_end"]}')
        c, e = _to_mdy(r.get('commencement_date')), _to_mdy(r.get('expiration_date'))
        # verification gate: only accept dates that appear in the text read
        if c and not _date_in_text(c, s['text']):
            logger.info(f'discarding unverified commencement {c} '
                        f'(not found in segment p{s["page_start"]})')
            c = None
        if e and not _date_in_text(e, s['text']):
            logger.info(f'discarding unverified expiration {e} '
                        f'(not found in segment p{s["page_start"]})')
            e = None
        commencement = commencement or c
        tmonths = tmonths or _term_months(r.get('term_length'))
        if e:
            governing_exp, governing_src = e, f'lease p{s["page_start"]}'
    # A date the lease STATES beats one we COMPUTE. Previously the
    # computation (commencement + term) ran first and the deterministic
    # stated-expiration scan only as a last resort — so when the LLM took
    # the signing date for the commencement (it IS in the text, so it
    # passes verification), every expiration came out wrong (2026-09-28
    # demo tie-out: 0/12 with LLM, 12/12 without).
    if not governing_exp:
        e, src = _scan_expiration(instruments)
        if e:
            governing_exp, governing_src = e, f'deterministic: {src}'
    if not governing_exp and commencement:
        # prefer the commencement the lease literally defines ("commencing
        # on …", earliest candidate) over the model's read
        det = [t for t in _deterministic_lease_fields(pages, tgt, set())
               if t['term_type'] == 'lease_commencement']
        if det:
            commencement = _to_mdy(det[0]['value_raw']) or commencement
    if not governing_exp and commencement and tmonths:
        if contingent:
            terms.append({'term_type': 'commencement_contingent',
                          'term_label': 'Commencement is contingent '
                                        '(earlier-of opening/delivery) — '
                                        'expiration not derivable from '
                                        'instrument',
                          'value_raw': f'stated term {tmonths} months; '
                                       f'no calendar commencement in lease',
                          'confidence': 0.9, 'section_ref': 'term clause'})
        else:
            governing_exp = _add_months_minus_day(commencement, tmonths)
            governing_src = (f'computed: commencement {commencement} '
                             f'+ {tmonths} months')
    auto_period = None
    for a in tgt['amend']:
        if not a['text']:
            continue
        r = _ask(llm, a['text'],
                 '{"new_expiration_date": "MM/DD/YYYY or null", '
                 '"expiration_date": "MM/DD/YYYY or null", '
                 '"auto_renewal": "true only if the term automatically '
                 'renews unless notice is given, else false", '
                 '"renewal_period": "length of each renewal period as '
                 'stated, or null"}',
                 f'amendment p{a["page_start"]}-{a["page_end"]}')
        e = _to_mdy(r.get('new_expiration_date')) or _to_mdy(r.get('expiration_date'))
        if e and not _date_in_text(e, a['text']):
            logger.info(f'discarding unverified amendment expiration {e} '
                        f'(not found in instrument p{a["page_start"]})')
            e = None
        terms.append({'term_type': 'instrument_expiration',
                      'term_label': a['title'][:60],
                      'value_raw': e or '(no expiration stated)',
                      'expiration_date': e, 'confidence': 0.8,
                      'section_ref': f'p{a["page_start"]}',
                      'page_number': a['page_start']})
        if e:
            governing_exp, governing_src = e, f'amendment p{a["page_start"]}'
        if str(r.get('auto_renewal')).lower() == 'true':
            auto_period = _term_months(r.get('renewal_period')) or auto_period
    if governing_exp and auto_period:
        rolled, n = _roll_forward(governing_exp, auto_period, as_of)
        if n:
            governing_src += f'; auto-renewed {n}x{auto_period}mo'
            governing_exp = rolled
    if not governing_exp:
        e, src = _scan_expiration(instruments)
        if e:
            governing_exp, governing_src = e, f'deterministic: {src}'
    if governing_exp:
        terms.append({'term_type': 'governing_expiration',
                      'term_label': f'Expiration (governing: {governing_src})'[:120],
                      'value_raw': governing_exp,
                      'effective_date': commencement,
                      'expiration_date': governing_exp,
                      'confidence': 0.85, 'section_ref': governing_src[:120]})

    # square footage — LLM then a SCORED regex net: all candidates across
    # sf+term segments, ranked so the operative premises statement beats
    # boilerplate ("...storage area of 500 square feet" on cover sheets)
    def _despace(t):
        """Collapse letter-spaced extractions ('s q u a r e  f e e t')."""
        return re.sub(r'(?<=\b\w) (?=\w\b)', '', t)

    sf = None
    for s in tgt['sf']:
        r = _ask(llm, s['text'], '{"square_feet": ...}', f'sf p{s["page_start"]}')
        v = r.get('square_feet')
        if isinstance(v, str):
            v = re.sub(r'[^\d.]', '', v) or None
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        if v and 100 < v < 100000:
            sf = (v, s['page_start'])
            break
    if sf is None:
        cands = []
        for s in tgt['sf'] + tgt['term']:
            base = 2 if s['num'] != 0 else 0     # real article beats preamble
            text = s['text']
            if 'square' not in text.lower():
                text = _despace(text)
            _sf_scan(text, base, s['page_start'], cands)
        # later instruments that RESTATE the premises (expansion, relocation,
        # surrender) govern over the original lease — always considered
        for inst in instruments:
            if inst['kind'] == 'amendment':
                _sf_scan('\n'.join(t for _pg, t in inst['pages']), 2,
                         inst['page_start'], cands, restate_only=True)
        if not any(c[0] >= 2 for c in cands):
            # last resort: amendments/exhibits (expansion riders live there)
            for inst in instruments:
                if inst['kind'] in ('amendment', 'exhibit'):
                    _sf_scan('\n'.join(t for _pg, t in inst['pages']), -1,
                             inst['page_start'], cands)
        if not any(c[0] >= 2 for c in cands):
            # the summary / basic-terms page is often not a routed segment
            # ("1.5 Approximate leasable area: 7,139"; "PREMISES: ... containing
            # approximately 57,593 Rentable square feet"). Unrouted text needs
            # positive evidence — a bare number in a CAM example is not the SF.
            extra = []
            for pg, t in pages[:12]:
                _sf_scan(t or '', 0, pg, extra)
            cands += [c for c in extra if c[0] >= 2]
        if cands:
            best = max(cands, key=lambda c: c[0])
            # every mention looked like boilerplate (CAM example, radius,
            # building total) -> blank, not the least-bad guess
            if best[0] >= 0:
                sf = (best[1], best[2])
    if sf:
        terms.append({'term_type': 'square_footage',
                      'term_label': 'Premises SF (segmented)',
                      'value_raw': f'{sf[0]:.0f} SF', 'value_numeric': sf[0],
                      'value_unit': 'sqft', 'confidence': 0.85,
                      'section_ref': f'p{sf[1]}', 'page_number': sf[1]})

    # ── deterministic fields (2026-09-28 tie-out) ──
    # The demo-portfolio harness showed these were either missing or
    # supplied WRONG by the legacy whole-document rule layer (tenant and
    # landlord swapped, commencement = expiration). Read them from the
    # text the way a person would; the LLM is a fallback, not the source.
    terms.extend(_deterministic_lease_fields(pages, tgt,
                                             {t['term_type'] for t in terms},
                                             sf=sf[0] if sf else None))
    have = {t['term_type'] for t in terms}

    # rent (informational) — LLM only when the schedule parse found nothing
    if 'base_rent' not in have:
        for s in tgt['rent']:
            r = _ask(llm, s['text'],
                     '{"base_monthly_rent": ..., "annual_rent": ...}',
                     f'rent p{s["page_start"]}')
            v = r.get('base_monthly_rent') or r.get('annual_rent')
            if v:
                terms.append({'term_type': 'base_rent',
                              'term_label': 'Base rent (segmented)',
                              'value_raw': str(v), 'confidence': 0.7,
                              'section_ref': f'p{s["page_start"]}',
                              'page_number': s['page_start']})
            break

    return terms


_MONTHS = ('January|February|March|April|May|June|July|August|September|'
           'October|November|December')
_LONG_DATE = rf'(?:{_MONTHS})\s+\d{{1,2}},\s+\d{{4}}'


def _page_of(pages, needle):
    for pg, t in pages:
        if needle and needle in re.sub(r'\s+', ' ', t or ''):
            return pg
    return None


def _sf_num(tok):
    """OCR digit confusions: I/l -> 1, O -> 0."""
    t = tok.translate(str.maketrans('IlO', '110')).replace(',', '')
    try:
        return float(t)
    except ValueError:
        return None


# "4,220 square feet", "(4,220) square feet", "3,799 total rentable square
# feet", "an approximate 29,938 square foot unit"
_SF_RX = re.compile(r'([\dIl][\dIlO,]{2,7})\)?\s*(?:total\s+)?(?:net\s+)?'
                    r'(?:rentable\s+|usable\s+|leasable\s+)?'
                    r'(?:square\s+(?:fe+t|foot)|sq\.?\s*ft\.?|s\.\s?f\.)', re.I)
# label-first forms: "Approximate leasable area of premises: 7,139"
_SF_LABEL_RX = re.compile(r'(?:leasable|rentable|floor)\s+area(?:\s+of\s+(?:the\s+)?premises)?'
                          r'\s*[:\-]\s*(?:approximately\s+)?([\d,]{3,8})\b', re.I)


# an amendment RESTATING the premises after expansion / relocation / surrender:
# "the total rentable square feet of the New Premises is approximately N",
# "shall thereafter contain approximately N", "(from 3,071 square feet to N",
# "a total of approximately N square feet (the Premises)"
_SF_RESTATE_RX = re.compile(
    r'new\s+premises|thereafter\s+contain|combined\s+premises|enlarg\w*\s+the\s+total|'
    r'square\s+f(?:ee|oo)t\s+to\s*$|\bto\s+a\s+total\s+of|containing\s+a\s+total\s+of|'
    r'leased\s+premises\s+shall\s+(?:now|thereafter)', re.I)
# a PART of the premises or a cap, never the premises: "increase by 420 square
# feet", "adding thereto approximately N", "Additional Premises", "In no event
# ... more than N square feet"
_SF_PART_RX = re.compile(
    r'increase\w*\s+by\s*$|decrease\w*\s+by\s*$|adding\s+(?:thereto\s+)?(?:approximately\s+)?$|'
    r'additional\s+premises|expansion\s+(?:area|space)\s+(?:consisting|containing)|'
    r'surrender\w*\s+premises|in\s+no\s+event|not\s+(?:to\s+)?exceed|more\s+than\s*$|'
    r'less\s+than\s*$|\bfrom\s*$', re.I)


def _sf_scan(text, base, pg, cands, restate_only=False):
    """Score every premises-size mention: the operative statement ('Premises
    ... containing approximately N') beats boilerplate (patio, parking
    ratio, radius, building total, storage); a later restatement of the
    premises (new / combined / thereafter-contain) beats the original.
    restate_only: keep only restatement candidates (used on amendments)."""
    hits = [(m, 0) for m in _SF_RX.finditer(text)] + \
           [(m, 3) for m in _SF_LABEL_RX.finditer(text)]
    for m, bonus in hits:
        ctx_raw = re.sub(r'\s+', ' ', text[max(0, m.start() - 160):m.start()])
        restated = bool(_SF_RESTATE_RX.search(ctx_raw[-160:]))
        if restate_only and not restated:
            continue
        v = _sf_num(m.group(1))
        if v is None or not (100 < v < 100000):
            continue
        # whitespace-normalised: page text breaks phrases across lines
        ctx = re.sub(r'\s+', ' ', text[max(0, m.start() - 100):m.start()]).lower()[-80:]
        score = base + bonus
        if re.search(r'approximat|containing|consisting of|consists of|'
                     r'leases?\s+from|rentable', ctx):
            score += 2
        if re.search(r'premises', ctx):
            score += 2     # the tenant's own space, not a building/center figure
        after60 = re.sub(r'\s+', ' ', text[m.end():m.end() + 80]).lower()[:60]
        if re.search(r'buildings?\s+totaling|restaurant\s+buildings?|mixed[- ]use|development|'
                     r'two[- ]story\s+building|free\s*standing', ctx) or \
                re.search(r'^\W{0,3}(?:rentable\s+)?(?:square\s+feet\s+)?of\s+(?:retail|office)\s+space\s+on\s+the',
                          after60):
            score -= 6     # recital describing the whole development
        if re.search(r'combined\s+total|totaling|combined\s+premises', ctx) or \
                re.search(r'\btotal\b', ctx[-25:] + ' ' + m.group(0).lower()):
            score += 3     # post-expansion / whole-premises total
        if re.search(r'storage|basement|shared|common|patio|mezzanine', ctx):
            score -= 3
        if re.search(r'radius|within|miles|parking|ratio|per\s+\d|building is|'
                     r'of the building|area of the building|shopping center contains|'
                     r'gross leasable|\bgla\b|site plan|example|illustrat|relates only|'
                     r'invoice|\bsaid\s*$|numerator|denominator|fraction|pro\s*rata',
                     ctx):
            score -= 4
        after = re.sub(r'\s+', ' ', text[m.end():m.end() + 40]).lower()[:30]
        if re.search(r'^\W{0,3}\(\s*["“]?premises|^\)?\s*of\s+(?:floor|rentable|leasable)', after):
            score += 1     # the operative definition: '... square feet ("Premises")'
        if restated:
            score += 6     # the premises as restated by a later instrument
        if _SF_PART_RX.search(ctx_raw[-60:]):
            score -= 6     # an increment, add-on, cap or prior size — not the premises
        cands.append((score, v, pg))


_ROLE_ALIASES = {'Landlord': '(?:Landlord|Lessor)', 'Tenant': '(?:Tenant|Lessee)'}
_OTHER_ROLE = {'Landlord': 'Tenant', 'Tenant': 'Landlord'}


def _looks_like_party(name):
    """Reject captures that are an address or boilerplate, not a party."""
    if not name:
        return False
    if re.search(r'\b\d{5}(?:-\d{4})?\b', name):            # ZIP code
        return False
    if re.match(r'\s*\d', name):                              # street number
        return False
    if re.search(r'\b(?:Avenue|Street|Boulevard|Road|Suite|Drive|Floor)\b', name, re.I) \
            and not re.search(r'\b(?:LLC|L\.L\.C|Inc|Corp|LP|L\.P|Ltd|Company)\b', name, re.I):
        return False
    return True


def _party(flat, role):
    """Name defined as ("Landlord"/"Tenant") — or ("Lessor"/"Lessee"), as on
    AIR forms. Handles the label-AFTER convention (X, a Minnesota LLC
    ("Landlord")), "hereinafter referred to as LANDLORD", "as Landlord", and
    cover pages ("BETWEEN X ... Landlord AND Y ... Tenant")."""
    r_role = _ROLE_ALIASES[role]
    # Anchor on the connective that introduces the party ("between X" /
    # "and Y") so a mixed-case name can't collapse to its suffix — the
    # first version returned "LLC" as the tenant on real pilot leases.
    conn = r'(?i:\bbetween|\band)'
    patterns = (
        # ("Landlord") label-after convention, incl. "(herein called “Landlord”)"
        conn + r'\s+(?:the\s+)?([A-Z][^()"“]{2,160}?)\s*'
        r'\(\s*(?:(?i:herein(?:after)?)\s+(?i:called|referred\s+to\s+as)\s+)?'
        rf'(?:the\s+)?["“]\s*(?i:{r_role})\s*["”]',
        # "..., hereinafter referred to as “LANDLORD”" (no parentheses; the
        # party's address can sit in between)
        conn + r'\s+(?:the\s+)?([A-Z][^()"“;]{2,300}?),?\s+'
        rf'(?i:herein(?:after)?\s+(?:called|referred\s+to\s+as))\s+(?:the\s+)?["“]?(?i:{r_role})\b',
        # older/institutional forms: "… between ELM RIDGE PROPERTIES, A
        # LIMITED PARTNERSHIP, as Landlord, and NORTHFIELD GROCERS, INC.,
        # an Iowa corporation, as Tenant."; also AS "LANDLORD"
        conn + r'\s+(?:the\s+)?([A-Z][^()"“;]{2,160}?),?\s+'
        rf'(?i:as)\s+(?:the\s+)?["“]?(?i:{r_role})\b',
        # summary label-first: "1.2 Landlord: The Realty Associates Fund VIII,
        # L.P., a Delaware ..."; "1.1 LANDLORD: CIM/H&H RETAIL, L.P., a ..."
        rf'(?<![A-Za-z’\'])(?i:{r_role[3:-1]})\s*:\s*'
        r'([A-Z][^:;]{2,120}?)(?=,?\s+(?:an?|A|AN)\s+[A-Za-z]+\s+(?i:limited|corporation|general|'
        r'company|LLC|liability|partnership)|\s+\d{1,2}\.\d{1,2}\s|\s+[A-Z]\.\s)',
    )
    if role == 'Landlord':
        # cover page: "LEASE BETWEEN X, LLC A Nevada LLC Landlord And Y Tenant"
        patterns += (r'(?i:\bbetween)\s+([A-Z][^()"“;]{2,120}?)\s+(?:Landlord|Lessor)\s+'
                     r'(?i:and)\s+[A-Z][^()"“;]{2,120}?\s+(?:Tenant|Lessee)\b',)
    else:
        patterns += (r'(?i:\bbetween)\s+[A-Z][^()"“;]{2,120}?\s+(?:Landlord|Lessor)\s+'
                     r'(?i:and)\s+([A-Z][^()"“;]{2,120}?)\s+(?:Tenant|Lessee)\b',)
    # A capture may never contain ANOTHER party's role clause — the first
    # cut of the "as Tenant" form ran from "between ELM RIDGE … as Landlord,
    # and NORTHFIELD" and returned the landlord as the tenant.
    role_clause = re.compile(r'\bas\s+(?:the\s+)?(?:Landlord|Tenant|Lessor|Lessee)\b'
                             r'|["“](?:Landlord|Tenant)["”]', re.I)
    name = None
    for rx in patterns:
        for m in re.finditer(rx, flat):
            cand = m.group(1)
            if role_clause.search(cand):
                # keep only the segment after the last connective
                tail = re.split(r',?\s+\band\s+', cand)[-1]
                if role_clause.search(tail):
                    continue
                cand = tail
            name = cand
            break
        if name:
            break
    if not name:
        return None
    # cut the entity description: ", a Minnesota limited liability company",
    # ", whose address is ...", "LLC A Nevada Limited Liability Company"
    name = re.split(r',\s*(?:an?|as|its|whose|with|having)\s', name, maxsplit=1)[0]
    name = re.split(r'\s+(?:A|AN|An)\s+(?=[A-Z][a-z]+\s+(?:Limited|Corporation|Company|'
                    r'corporation|limited|general|LLC))', name, maxsplit=1)[0]
    name = re.sub(r'\s+(?:d/?b/?a|doing business as)\s.*$', '', name, flags=re.I)
    name = re.sub(r'^(?:between|and)\s+', '', name.strip(), flags=re.I)   # "BY AND BETWEEN X"
    name = re.sub(r'\s+', ' ', name).strip(' ,.;:')
    if not _looks_like_party(name):
        return None
    suffixes = {'llc', 'inc', 'co', 'corp', 'ltd', 'lp', 'llp', 'pa', 'pc',
                'company', 'corporation', 'partnership', 'limited', 'the'}
    words = [w for w in re.findall(r"[A-Za-z0-9&'’]+", name)
             if w.lower().rstrip('.') not in suffixes]
    if not words or len(name) < 3 or len(name) > 90:
        return None
    return name


def _money(s):
    try:
        return float(s.replace(',', '').replace(' ', ''))
    except ValueError:
        return None


_RENT_WORD = re.compile(r'\brent(?:al)?\b|\bGMMR\b', re.I)


def _rent_ctx_ok(src, pos, span=220):
    ctx = src[max(0, pos - span):pos]
    low = ctx.lower()
    return (_RENT_WORD.search(ctx) is not None and 'sublease' not in low
            and 'subtenant' not in low and 'deposit' not in low[-60:])


def _option_ctx(src, pos, span=300):
    """Rent quoted for an option / renewal / extension term is not Year 1."""
    return re.search(r'option\s+(?:period|term|year)s?|renewal\s+(?:period|term)s?|'
                     r'extension\s+(?:period|term)s?|extended\s+term|during\s+the\s+option',
                     src[max(0, pos - span):pos], re.I) is not None


def _year1_monthly_rent(rent_txt, flat, sf=None):
    """(monthly, matched text, label) for the first-year base rent, or None.
    Tried in priority order on the rent segment, then the whole lease:
      1. explicit monthly statements ("Year 1 ... $X per month", "Base Rent:
         $X per month", "Monthly base rent: ... $X")
      2. a self-verifying annual/monthly pair (either order)
      3. a per-SF rate x the premises SF that equals a nearby amount
      4. an annual amount ("$X per annum", "Annual Rent: ... $X") / 12
    """
    srcs = [rent_txt] + ([flat] if flat is not rent_txt and flat != rent_txt else [])
    amt = r'\$\s*([\d,]+\.\d{2})'

    def first(rx, label, flags=re.I):
        for src in srcs:
            for m in re.finditer(rx, src, flags):
                v = _money(m.group(1))
                if v and v >= 100 and not _option_ctx(src, m.start()):
                    return v, m.group(1), label
        return None

    # 1. "Year 1 ... $X per month"
    hit = first(r'(?:Lease\s+)?Year\s*1\b[^$]{0,60}?(?:\$[\d,]+\.\d{2}\s*per\s*sq[^$]{0,20})?'
                + amt + r'\s*per\s*month', 'Base rent, Year 1 monthly')
    if hit:
        return hit
    # 2. "$243,520.00 $20,293.33" (annual then monthly) or the reverse —
    #    the arithmetic proves the pair; "rent" nearby proves it is RENT.
    #    Ahead of the wording rules: a table header "ANNUAL RENT MONTHLY RENT"
    #    puts the ANNUAL figure first after the word "monthly".
    for src in srcs:
        for pm in re.finditer(amt + r'\s*[;,|/]?\s*' + amt, src):
            a, b = _money(pm.group(1)), _money(pm.group(2))
            if (not a or not b or not _rent_ctx_ok(src, pm.start())
                    or _option_ctx(src, pm.start())):
                continue
            if a >= 1200 and abs(a / 12 - b) <= 0.02:
                return b, pm.group(2), 'Base rent, first scheduled monthly'
            if b >= 1200 and abs(b / 12 - a) <= 0.02:
                return a, pm.group(1), 'Base rent, first scheduled monthly'
    # 1b. stated monthly: "Base Rent: $X per month", "Monthly base rent: ... $X"
    hit = first(r'(?:monthly\s+(?:base\s+|minimum\s+)?rent(?:al)?|base\s+rent|minimum\s+rent)[^$]{0,80}'
                + amt + r'\s*(?:per\s+month|/\s*mo\b|monthly|a\s+month|each\s+month)',
                'Base rent, monthly') or \
        first(r'monthly\s+(?:base\s+|minimum\s+)?rent(?:al)?\b[^$]{0,120}?' + amt,
              'Base rent, monthly (labeled)')
    if hit:
        return hit
    # 3. rate x SF: "$1.91 ... $110,002.63" (monthly per SF), "$23.25 per
    #    square foot ... $268,584.00 per annum", "$2,429.63/$15.50PSF"
    if sf:
        rate_rx = r'\$\s*(\d{1,3}\.\d{2,4})(?!\d)'
        for src in srcs:
            for rm in re.finditer(rate_rx, src):
                r = _money(rm.group(1))
                if (not r or not _rent_ctx_ok(src, rm.start(), 400)
                        or _option_ctx(src, rm.start())):
                    continue
                win_lo, win_hi = max(0, rm.start() - 60), rm.end() + 120
                for am in re.finditer(amt, src[win_lo:win_hi]):
                    a = _money(am.group(1))
                    if not a or a < 100:
                        continue
                    if abs(r * sf - a) <= 1.0:
                        # monthly per-SF rates are small; annual ones aren't
                        if r < 5:
                            return a, am.group(1), 'Base rent, rate x SF (monthly)'
                        return round(a / 12, 2), am.group(1), 'Base rent, rate x SF (annual / 12)'
                    if abs(r * sf / 12 - a) <= 1.0:
                        return a, am.group(1), 'Base rent, rate x SF / 12'
    # 4. annual amounts
    annual = (
        r'\$\s*([\d,]+(?:\.\d{2})?)\s*\)?\s*(?:per\s+annum|per\s+year|annually|a\s+year)',
        r'annual\s+(?:minimum\s+|base\s+|fixed\s+)*rent(?:al)?\b[^$]{0,250}?\$\s*([\d,]+(?:\.\d{2})?)',
    )
    for i, rx in enumerate(annual):
        for src in srcs:
            for m in re.finditer(rx, src, re.I):
                if (i == 0 and not _rent_ctx_ok(src, m.start())) or _option_ctx(src, m.start()):
                    continue
                a = _money(m.group(1))
                if a and a >= 1200:
                    return round(a / 12, 2), m.group(1), 'Base rent, annual / 12'
    return None


def _security_deposit(flat):
    """(amount, matched text) for the security deposit, or None when the
    lease states none / doesn't state an amount. Only reads constructions
    that NAME the deposit — the old nearest-dollar guess took the SF, a CAM
    estimate or a rental tax for the deposit on 5 of 14 blind-test leases."""
    amt = r'\$\s*([\d,]+\.\d{2})'
    if re.search(r'security\s+deposit\s*["”)]*\s*:?\s*(?:none\b|n/?a\b|not\s+applicable|waived|'
                 r'\$\s*0(?:\.00)?\b|\$\s*-0-)', flat, re.I):
        return None
    pats = (
        r'total\s+security\s+deposit[^$]{0,30}?' + amt,
        r'security\s+deposit\s*(?:\(\w\))?\s*:\s*(?:[A-Za-z0-9/\- ,]{0,160}?dollars\s*\(\s*)?' + amt,
        amt + r'\s*\)?\s*(?:in\s+the\s+form\s+of\s+a\s+|as\s+(?:a|the)\s+)["“]?security\s+deposit',
        # "... (the first) installment of the Security Deposit in the amount of"
        # is a PART of the deposit, never the deposit
        r'(?<!installment of the )(?<!installment of )security\s+deposit[^$.;]{0,60}?'
        r'(?:of|in\s+the\s+(?:amount|sum)\s+of|the\s+sum\s+of|'
        r'equal\s+to)\s+(?:[A-Za-z0-9/\- ,]{0,160}?dollars\s*\(\s*)?' + amt,
        # "Tenant shall deposit with Landlord the sum of $9,800.00 as security"
        r'deposit\s+with\s+(?:the\s+)?(?:landlord|lessor)\s+(?:the\s+(?:sum|amount)\s+of\s+)?'
        + amt + r'\s*\)?\s+as\s+(?:a\s+|the\s+)?security\b',
    )
    for rx in pats:
        m = re.search(rx, flat, re.I)
        if m:
            v = _money(m.group(1))
            if v and v >= 100:
                return v, m.group(1)
    return None


def _deterministic_lease_fields(pages, tgt, have, sf=None):
    flat = re.sub(r'\s+', ' ', '\n'.join(t for _pg, t in pages))
    head = flat[:20000]
    # Real files are PACKAGES (lease + guaranty + assignments + estoppels).
    # Parties come from the PRIMARY lease instrument's own preamble first —
    # a 2023 assignment in the same PDF names a different landlord.
    lease_head = re.sub(r'\s+', ' ', ' '.join(
        s['text'] for s in tgt.get('identity', [])[:2]))
    out = []

    def add(tt, label, raw, needle, conf=0.9, num=None, unit=None):
        pg = _page_of(pages, needle)
        d = {'term_type': tt, 'term_label': f'{label} (deterministic)',
             'value_raw': raw, 'confidence': conf,
             'section_ref': f'p{pg}' if pg else 'body scan', 'page_number': pg}
        if num is not None:
            d['value_numeric'] = num
        if unit:
            d['value_unit'] = unit
        out.append(d)

    ll = (lease_head and _party(lease_head, 'Landlord')) or _party(head, 'Landlord')
    if ll and 'landlord_name' not in have:
        add('landlord_name', 'Landlord', ll, ll)
    tn = (lease_head and _party(lease_head, 'Tenant')) or _party(head, 'Tenant')
    if tn and 'tenant_identity' not in have:
        add('tenant_identity', 'Tenant', tn, tn, conf=0.85)

    if 'lease_commencement' not in have:
        # all candidate dates; the EARLIEST wins — renewal/option periods
        # also "commence", always later than the original term
        cands = []
        for rx in (rf'commenc\w*\s+(?:on\s+)?({_LONG_DATE})',
                   rf'["“]Commencement Date["”][^.]{{0,60}}?({_LONG_DATE})',
                   rf'({_LONG_DATE})\s*\(\s*(?:the\s+)?["“]Commencement Date'):
            for mm in re.finditer(rx, flat, re.I):
                try:
                    cands.append((datetime.datetime.strptime(
                        re.sub(r'\s+', ' ', mm.group(1)), '%B %d, %Y'), mm.group(1)))
                except ValueError:
                    pass
        if cands:
            d, raw = min(cands)
            add('lease_commencement', 'Commencement', f'{d.month}/{d.day}/{d.year}',
                raw, conf=0.8)

    # Year-1 base rent, monthly — candidates in priority order, each read the
    # way it is stated (blind test 2026-10-02: 0/14 with the old two patterns)
    rent_txt = re.sub(r'\s+', ' ', ' '.join(s['text'] for s in tgt.get('rent', []))) or flat
    if sf is None:
        cands = []
        for pg, t in pages[:12]:
            _sf_scan(t or '', 0, pg, cands)
        best = max(cands, key=lambda c: c[0]) if cands else None
        if best and best[0] >= 2:
            sf = best[1]
            if 'square_footage' not in have:
                add('square_footage', 'Premises SF', f'{sf:.0f} SF', None, conf=0.8,
                    num=sf, unit='sqft')
    rent = _year1_monthly_rent(rent_txt, flat, sf)
    if rent:
        v, needle, label = rent
        add('base_rent', label, f'${v:,.2f}', needle, num=v, unit='monthly')

    dep = _security_deposit(flat)
    if dep and 'security_deposit' not in have:
        v, needle = dep
        add('security_deposit', 'Security deposit', f'${v:,.2f}', needle, num=v, unit='USD')

    m = re.search(r'(?:increase|escalat)\w*[^.]{0,80}?by\s+([\d.]+)\s*percent', flat, re.I) or \
        re.search(r'(?:increase|escalat)\w*[^.]{0,80}?\(\s*([\d.]+)\s*%\s*\)', flat, re.I)
    if m:
        v = float(m.group(1).rstrip('.'))
        add('escalation_rate', 'Annual escalation', f'{v:g}%', m.group(0)[:40],
            num=v, unit='percent')
    return out


def summarize(instruments):
    """Log-friendly one-liner about what segmentation recovered."""
    kinds = Counter(i['kind'] for i in instruments)
    nsegs = sum(len(i.get('segments', [])) for i in instruments)
    chain = ' -> '.join(f"{i['kind']}[p{i['page_start']}-{i['page_end']}]"
                        for i in instruments)
    return f'{nsegs} segments across {dict(kinds)}; chain: {chain}'
