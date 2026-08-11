"""
Segment-first lease extraction — the validated Hadley pilot pattern
(portfolio_ownership/pilot_hadley/, 7% -> 71% tie-out vs the KA master;
100% of document-derivable fields) generalized for the Capactive engine.

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

# ─── Heading + instrument grammar (validated 90% vs KA master) ───────────

ROMAN = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7,
         'viii': 8, 'ix': 9, 'x': 10, 'xi': 11, 'xii': 12, 'xiii': 13,
         'xiv': 14, 'xv': 15, 'xvi': 16, 'xvii': 17, 'xviii': 18, 'xix': 19,
         'xx': 20, 'xxi': 21, 'xxii': 22, 'xxiii': 23, 'xxiv': 24, 'xxv': 25,
         'xxvi': 26, 'xxvii': 27, 'xxviii': 28, 'xxix': 29, 'xxx': 30}

ORDINALS = ('FIRST', 'SECOND', 'THIRD', 'FOURTH', 'FIFTH', 'SIXTH', 'SEVENTH',
            'EIGHTH', 'NINTH', 'TENTH')

INSTRUMENT_PATTERNS = [
    # a fresh lease agreement mid-package (renewals shipped as complete
    # leases — the Great Clips pattern) starts its own instrument
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

    Osborne/ENGELS form: 'Section N.' lines inside each ARTICLE.
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
            # both notations occur under ARTICLE headings: Osborne/ENGELS
            # 'Section N.' lines AND decimal '2.5. Percentage Rent.' (GNC form)
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
RE_EXPIRE_STMT = re.compile(
    r'(?i)(?:expire|expiring|ending|terminat\w+)[^.]{0,140}?on\s+'
    r'([A-Z][a-z]+\.?\s+\d{1,2}\s*,?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})')


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
                    dt = parse(mm.group(1))
                    if dt:
                        stated.append((dt, f'stated p{pg}'))
            for mm in RE_DATE_RANGE.finditer(text):
                dt = parse(mm.group(2))
                if dt:
                    ranges.append((dt, f'rent-schedule end p{pg}'))
    if stated:
        best = max(stated)
        return f'{best[0].month}/{best[0].day}/{best[0].year}', best[1]
    if len(ranges) >= 2:      # a lone range is too weak a signal
        best = max(ranges)
        return f'{best[0].month}/{best[0].day}/{best[0].year}', best[1]
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
    return out if isinstance(out, dict) else {}


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
            if confused or re.search(rf'(?i){lo}[^.\n]{{0,80}}[("“]+\s*Landlord',
                                     body_head):
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
    if ident_names is None and net:
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
        def _num(tok):
            """OCR digit confusions: I/l -> 1, O -> 0."""
            t = tok.translate(str.maketrans('IlO', '110')).replace(',', '')
            try:
                return float(t)
            except ValueError:
                return None

        RX = re.compile(r'([\dIl][\dIlO,]{2,7})\s*(?:rentable\s+)?'
                        r'square\s+fe*t', re.I)

        def _scan(text, base, pg, cands):
            for m in RX.finditer(text):
                v = _num(m.group(1))
                if v is None or not (100 < v < 100000):
                    continue
                ctx = text[max(0, m.start() - 80):m.start()].lower()
                score = base
                if re.search(r'approximat|containing|consisting of|'
                             r'leases?\s+from|rentable', ctx):
                    score += 2
                if re.search(r'combined\s+total|totaling|combined\s+premises',
                             ctx):
                    score += 3     # post-expansion governing total
                if re.search(r'storage|basement|shared|common', ctx):
                    score -= 3
                cands.append((score, v, pg))

        cands = []
        for s in tgt['sf'] + tgt['term']:
            base = 2 if s['num'] != 0 else 0     # real article beats preamble
            text = s['text']
            if 'square' not in text.lower():
                text = _despace(text)
            _scan(text, base, s['page_start'], cands)
        if not any(c[0] >= 2 for c in cands):
            # last resort: amendments/exhibits (expansion riders live there)
            for inst in instruments:
                if inst['kind'] in ('amendment', 'exhibit'):
                    _scan('\n'.join(t for _pg, t in inst['pages']), -1,
                          inst['page_start'], cands)
        if cands:
            best = max(cands, key=lambda c: c[0])
            if best[0] >= 0 or all(c[0] < 0 for c in cands):
                sf = (best[1], best[2])
    if sf:
        terms.append({'term_type': 'square_footage',
                      'term_label': 'Premises SF (segmented)',
                      'value_raw': f'{sf[0]:.0f} SF', 'value_numeric': sf[0],
                      'value_unit': 'sqft', 'confidence': 0.85,
                      'section_ref': f'p{sf[1]}', 'page_number': sf[1]})

    # rent (informational)
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


def summarize(instruments):
    """Log-friendly one-liner about what segmentation recovered."""
    kinds = Counter(i['kind'] for i in instruments)
    nsegs = sum(len(i.get('segments', [])) for i in instruments)
    chain = ' -> '.join(f"{i['kind']}[p{i['page_start']}-{i['page_end']}]"
                        for i in instruments)
    return f'{nsegs} segments across {dict(kinds)}; chain: {chain}'
