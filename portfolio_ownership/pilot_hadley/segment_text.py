#!/usr/bin/env python3
"""
#83 Text-based lease segmenter — flat-PDF generalization of
southtown_db/segment_lease.py (which read .docx heading styles).

Input: a Capactive extraction DB (document_fulltext, one row per page).
Output: per document —
  1. instrument chain (original lease, amendments, assignments, estoppel,
     SNDA, exhibits) with page ranges, in document order;
  2. within each lease-body instrument, article/section segments with
     heading number, title, page span, and text — small enough for
     per-segment LLM calls that never hit the timeout ladder.

Validation mode (--validate) compares recovered structure against the KA
master's page-cited provision categories for the mapped tenant
("Article N: TITLE" / "Section N: Title" forms) — Riley's verified layer
is the source of truth for what structure each lease actually has.

Read-only on both databases.
    python portfolio_ownership/pilot_hadley/segment_text.py [--validate]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
PILOT_DB = os.path.join(ROOT, 'data', 'pilot_hadley.db')
MASTER = os.path.join(ROOT, 'portfolio_ownership',
                      'KA_OWNERSHIP_MODULE_MASTER_20260724', 'portfolio_warehouse.db')

DOC_TENANT = {
    'cbd + vape': 'eastgate_tobacco',
    'subway': 'subway',
    "lee's liquor": 'lees_liquor',
    'ilove nails': 'ilove_nails',
    'great clips': 'great_clips',
    'maple leaf': 'maple_leaf_massage',
}

ROMAN = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7,
         'viii': 8, 'ix': 9, 'x': 10, 'xi': 11, 'xii': 12, 'xiii': 13,
         'xiv': 14, 'xv': 15, 'xvi': 16, 'xvii': 17, 'xviii': 18, 'xix': 19,
         'xx': 20, 'xxi': 21, 'xxii': 22, 'xxiii': 23, 'xxiv': 24, 'xxv': 25,
         'xxvi': 26, 'xxvii': 27, 'xxviii': 28, 'xxix': 29, 'xxx': 30}

ORDINALS = ('FIRST', 'SECOND', 'THIRD', 'FOURTH', 'FIFTH', 'SIXTH', 'SEVENTH',
            'EIGHTH', 'NINTH', 'TENTH')

# Instrument-start patterns. Checked only near the top of a page (scanned
# chains start each instrument on a fresh page); mid-text references to
# "any amendment" therefore don't split.
INSTRUMENT_PATTERNS = [
    (r'^(?:' + '|'.join(ORDINALS) + r')\s+AMENDMENT\s+(?:TO|OF)\s+LEASE', 'amendment'),
    (r'^AMENDMENT\s+(?:TO|OF)\s+LEASE', 'amendment'),
    (r'^AMENDMENT\s*$', 'amendment'),
    (r'^LEASE\s+AMENDMENT', 'amendment'),
    (r'^ASSIGNMENT\s+(?:AND\s+ASSUMPTION\s+)?OF\s+LEASE', 'assignment'),
    (r'^LEASE\s+TERMINATION', 'termination'),
    (r'^TERMINATION\s+(?:OF|AGREEMENT)', 'termination'),
    (r'^SUBORDINATION[,\s]+NON.?DISTURBANCE', 'snda'),
    (r'^ESTOPPEL\s+CERTIFICATE', 'estoppel'),
    (r'(?i)^estoppel\s+certificate\s*$', 'estoppel'),
    (r'^GUARANTY(?:\s+OF\s+LEASE)?\s*$', 'guaranty'),
    (r'^LICENSE\s+AGREEMENT', 'license'),
    (r'^EXHIBIT\s*"?([A-Z])"?\s*$', 'exhibit'),
    (r'^EXHIBIT([A-Z])\s*$', 'exhibit'),          # OCR: "EXHIBITB"
]

RE_ARTICLE = re.compile(r'^ARTICLE\s+(\d{1,2}|[IVXL]+)\s*[:.]?\s*(.*)$', re.I)
# Body numbered heading: ALL-CAPS title ending in ':' or '.', text may follow
# on the same line ("1. PREMISES: The Premises consists of ...").
RE_NUMPARA_CAPS = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Z &/,'’\-]{2,60}?)\s*(?:[:.~]|$)")
# OCR paren form: "12.) INSURANCE."  "15.) ASSIGNMENT OR SUBLETTING~"
RE_NUMPARA_PAREN = re.compile(r"^(\d{1,2})\s*[.,]?\)\s*([A-Z][A-Z &/,'’\-]{2,60}?)\s*(?:[:.~]|$)")
# Title-case numbered line — the TOC form ("22. Short Form Lease ...").
RE_NUMPARA_TITLE = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Za-z ,&/'’-]{2,60}\.?)\s*$")
RE_SECTION = re.compile(r'^SECTION\s+(\d{1,2})\s*[:.]?\s*(.*)$', re.I)


def art_num(tok):
    tok = tok.strip().lower()
    if tok.isdigit():
        return int(tok)
    return ROMAN.get(tok)


def load_doc(conn, doc_id):
    """Return list of (page_number:int, [lines])."""
    rows = conn.execute(
        "SELECT CAST(page_number AS INTEGER), content FROM document_fulltext "
        "WHERE document_id=? ORDER BY 1", (str(doc_id),)).fetchall()
    return [(pg, content.split('\n')) for pg, content in rows]


def find_instruments(pages):
    """Split the page stream into instruments by top-of-page headers."""
    bounds = []  # (page_idx, kind, header_line)
    for idx, (pg, lines) in enumerate(pages):
        top = [l.strip() for l in lines if l.strip()][:6]
        for line in top:
            for pat, kind in INSTRUMENT_PATTERNS:
                if re.match(pat, line):
                    bounds.append((idx, kind, line[:80]))
                    break
            else:
                continue
            break
    instruments = []
    # Everything before the first boundary is the primary instrument.
    starts = [0] + [b[0] for b in bounds if b[0] != 0]
    kinds = ['lease'] + [b[1] for b in bounds if b[0] != 0]
    heads = ['(primary instrument)'] + [b[2] for b in bounds if b[0] != 0]
    if bounds and bounds[0][0] == 0:
        kinds[0] = bounds[0][1]
        heads[0] = bounds[0][2]
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(pages)
        instruments.append({
            'kind': kinds[i], 'header': heads[i],
            'page_start': pages[s][0], 'page_end': pages[e - 1][0],
            'pages': pages[s:e],
        })
    return instruments


def toc_pages(pages):
    """Pages that are a table of contents / index — skip for heading detection.

    Two signals: an explicit TOC banner, or a page dominated by numbered
    title-case lines with little body text between them (the index form of
    'N. Title' leases — body headings are ALL-CAPS with a ':' or '.').
    """
    out = set()
    density = {}
    for pg, lines in pages:
        joined = ' '.join(lines[:6]).upper()
        nonblank = [l.strip() for l in lines if l.strip()]
        toc_like = sum(1 for l in nonblank
                       if RE_NUMPARA_TITLE.match(l) or re.search(r'\.{4,}\s*\d*\s*$', l))
        density[pg] = toc_like
        if ('TABLE OF CONTENTS' in joined or 'INDEX TO LEASE' in joined or
                ('ARTICLE' in joined and 'DESCRIPTION' in joined and 'PAGE' in joined)):
            out.add(pg)
            continue
        if len(nonblank) >= 4 and toc_like >= 4 and toc_like / len(nonblank) >= 0.4:
            out.add(pg)
    # Expand: TOCs run over consecutive pages; a neighbor with a couple of
    # index-form lines belongs to the same TOC block.
    changed = True
    while changed:
        changed = False
        for pg in list(out):
            for nb in (pg - 1, pg + 1):
                if nb not in out and density.get(nb, 0) >= 2:
                    out.add(nb)
                    changed = True
    return out


def segment_instrument(inst, skip_pages=frozenset()):
    """Segment a lease-body instrument into article/section chunks."""
    # Collect candidate headings with provenance.
    cands_article, cands_numpara = [], []
    flat = []  # (page, line_idx_global, text)
    gi = 0
    for pg, lines in inst['pages']:
        for l in lines:
            flat.append((pg, gi, l))
            gi += 1
    for k, (pg, gi_, l) in enumerate(flat):
        if pg in skip_pages:
            continue
        s = l.strip()
        m = RE_ARTICLE.match(s)
        if m and len(s) < 70:
            n = art_num(m.group(1))
            title = m.group(2).strip()
            if not title:  # title on next non-blank line
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

    # Pick the dominant style; enforce a mostly-ascending sequence so numbered
    # sub-lists ("1. ... 2. ..." inside a paragraph) don't fragment segments.
    def ascending_filter(cands):
        out, last = [], 0
        for k, pg, n, title in cands:
            if n >= last or (n == 1 and last >= 3 and len(out) > 2):
                # allow restart at 1 only if we treat it as noise -> reject
                if n < last:
                    continue
                out.append((k, pg, n, title))
                last = n
        return out

    arts = ascending_filter(cands_article)
    nums = ascending_filter(cands_numpara)
    heads = arts if len(arts) >= len(nums) else nums
    style = 'article' if heads is arts else 'numbered'
    if not heads:
        return style, []

    segments = []
    # Preamble: cover page / parties / summary block before the first heading.
    # Identity and fundamental-provision data live here — never drop it.
    k0 = heads[0][0]
    if k0 > 0:
        pre = '\n'.join(t for (_, _, t) in flat[:k0]).strip()
        if pre:
            segments.append({
                'num': 0, 'title': '(preamble / cover / fundamental provisions)',
                'page_start': flat[0][0], 'page_end': flat[k0 - 1][0],
                'chars': len(pre), 'text': pre,
            })
    for j, (k, pg, n, title) in enumerate(heads):
        k_end = heads[j + 1][0] if j + 1 < len(heads) else len(flat)
        body_lines = [t for (_, gidx, t) in flat[k:k_end]]
        pg_end = flat[k_end - 1][0] if k_end > k else pg
        text = '\n'.join(body_lines).strip()
        segments.append({
            'num': n, 'title': title, 'page_start': pg, 'page_end': pg_end,
            'chars': len(text), 'text': text,
        })
    return style, segments


def norm_title(s):
    return re.sub(r'[^a-z]', '', (s or '').lower())


def master_targets(mconn, tenant_key, style):
    """Master's categories -> {num: title}, in the matching namespace.

    Article-style leases validate against 'Article N:' categories; numbered
    leases against 'Section N:' — the master keeps both namespaces and mixing
    them inflates the denominator with the other lease family's numbers.
    """
    want = 'Article' if style == 'article' else 'Section'
    out = {}
    for (cat,) in mconn.execute(
            "SELECT DISTINCT category FROM lease_provision "
            "WHERE property_id='OSBORN-3007' AND tenant_key=?", (tenant_key,)):
        m = re.match(r'^(Article|Section)\s+(\d{1,2})\s*:\s*(.+)$', cat)
        if m and m.group(1) == want and int(m.group(2)) > 0:
            out.setdefault(int(m.group(2)), m.group(3).strip())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=PILOT_DB)
    ap.add_argument('--validate', action='store_true',
                    help='score recovered structure against the KA master')
    ap.add_argument('--json', help='write full segmentation to this JSON file')
    args = ap.parse_args()

    p = sqlite3.connect(f'file:{args.db.replace(os.sep, "/")}?mode=ro', uri=True)
    m = None
    if args.validate:
        m = sqlite3.connect(f'file:{MASTER.replace(os.sep, "/")}?mode=ro', uri=True)

    dump = {}
    grand = Counter()
    for did, fname in p.execute("SELECT id, filename FROM documents ORDER BY id"):
        pages = load_doc(p, did)
        if not pages:
            print(f'doc {did}: no page text — skipped')
            continue
        skip = toc_pages(pages)
        instruments = find_instruments(pages)
        print(f'\n=== doc {did}: {fname} ({len(pages)} pp) ===')
        print('  chain: ' + ' -> '.join(
            f"{i['kind']}[p{i['page_start']}-{i['page_end']}]" for i in instruments))

        doc_out = {'filename': fname, 'instruments': []}
        for inst in instruments:
            entry = {'kind': inst['kind'], 'header': inst['header'],
                     'page_start': inst['page_start'], 'page_end': inst['page_end']}
            if inst['kind'] in ('lease', 'amendment', 'assignment', 'license'):
                style, segs = segment_instrument(inst, skip)
                entry['style'] = style
                entry['segments'] = segs
                if segs:
                    sizes = [s['chars'] for s in segs]
                    print(f"    {inst['kind']} p{inst['page_start']}-{inst['page_end']}: "
                          f"{len(segs)} segments ({style} style), "
                          f"{min(sizes)}-{max(sizes)} chars "
                          f"(median {sorted(sizes)[len(sizes)//2]})")
                    grand['segments'] += len(segs)
                    grand[f'segments_over_10k'] += sum(1 for c in sizes if c > 10000)
            doc_out['instruments'].append(entry)
        dump[str(did)] = doc_out

        if args.validate:
            tk = next((v for k, v in DOC_TENANT.items() if k in fname.lower()), None)
            # style of the primary lease instrument
            doc_style = next((i.get('style') for i in doc_out['instruments']
                              if i.get('segments')), 'article')
            targets = master_targets(m, tk, doc_style) if tk else {}
            if not targets:
                print('    validation: no Article/Section-form targets in master')
                continue
            # Recovered headings across lease-body instruments.
            got = {}
            for inst in doc_out['instruments']:
                for s in inst.get('segments', []):
                    got.setdefault(s['num'], s['title'])
            hit_n = sum(1 for n in targets if n in got)
            title_hits = 0
            for n, t in targets.items():
                if n in got:
                    a, b = norm_title(t), norm_title(got[n])
                    if a and b and (a[:8] in b or b[:8] in a):
                        title_hits += 1
            print(f'    validation vs master ({tk}): '
                  f'{hit_n}/{len(targets)} heading numbers recovered, '
                  f'{title_hits} title matches')
            missing = sorted(set(targets) - set(got))
            if missing:
                print(f'    missing numbers: {missing}')
            grand['target_headings'] += len(targets)
            grand['recovered_headings'] += hit_n
            grand['title_matches'] += title_hits

    print('\n' + '=' * 70)
    print(f"TOTAL: {grand['segments']} segments; "
          f"{grand['segments_over_10k']} exceed 10K chars (would hit 90s LLM tier)")
    if args.validate and grand['target_headings']:
        pct = 100.0 * grand['recovered_headings'] / grand['target_headings']
        print(f"STRUCTURE RECOVERY vs MASTER: "
              f"{grand['recovered_headings']}/{grand['target_headings']} ({pct:.0f}%) "
              f"heading numbers; {grand['title_matches']} title matches")
    if args.json:
        slim = json.loads(json.dumps(dump))
        for d in slim.values():
            for inst in d['instruments']:
                for s in inst.get('segments', []):
                    s['text'] = s['text'][:200] + '...' if len(s['text']) > 200 else s['text']
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(slim, f, indent=1)
        print(f'wrote {args.json}')


if __name__ == '__main__':
    main()
