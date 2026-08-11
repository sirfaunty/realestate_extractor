#!/usr/bin/env python3
"""
#77 / #83 Per-segment targeted extraction — the Southtown pattern applied to
the Hadley pilot: small segments, focused prompts, chain-awareness.

Run NATIVELY on Windows (needs local Ollama + write access to the pilot DB):
    venv/Scripts/python portfolio_ownership/pilot_hadley/extract_segments.py

Steps:
  1. OCR backfill — pages with no text layer (page 3 of the three
     Osborne-form leases holds Articles 1-3) are OCR'd from the source PDFs
     with the Midway machinery (fitz + RapidOCR) and written into
     document_fulltext. Idempotent: only missing pages.
  2. Segment each document (segment_text.py — validated 90% vs the master).
  3. Targeted per-segment prompts against llama3.1:8b:
       identity   <- preamble/cover segment
       term/exp   <- term & premises segments, then EVERY amendment in
                     document order; the LAST non-null expiration governs
                     (later instruments supersede — the Subway fix)
       SF         <- premises/fundamental-provision segments (+ regex net)
       rent       <- first rent-titled segment (informational)
  4. Write results to financial_terms under run_id='seg_v1' (idempotent:
     the run's prior rows are replaced). tie_out.py scores seg_v1 rows
     strictly when present.

Flag, don't fabricate: every value carries its section_ref + page; nulls
stay null.
"""

import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import segment_text as st  # noqa: E402
from extractors.llm_client import LocalLLMClient  # noqa: E402

PILOT_DB = os.path.join(ROOT, 'data', 'pilot_hadley.db')
RUN_ID = 'seg_v1'
MIN_PAGE_TEXT = 40

SYSTEM = ("You are a commercial lease abstraction assistant. ONLY the lease "
          "language provided governs. Never guess, never use outside "
          "knowledge. If a value is not stated in the text, return null. "
          "Respond with JSON only.")

MONTHS = {m: i + 1 for i, m in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july',
     'august', 'september', 'october', 'november', 'december'])}


def to_mdy(s):
    """Normalize an assortment of date strings to 'M/D/YYYY' or None."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        return f'{int(m.group(2))}/{int(m.group(3))}/{m.group(1)}'
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        return f'{int(m.group(1))}/{int(m.group(2))}/{m.group(3)}'
    m = re.search(r'([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', s)
    if m and m.group(1).lower() in MONTHS:
        return f'{MONTHS[m.group(1).lower()]}/{int(m.group(2))}/{m.group(3)}'
    return None


WORD_NUMS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
             'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'fifteen': 15,
             'twenty': 20, 'twentyfive': 25, 'thirty': 30}

# Data date of Riley's master (KA_OWNERSHIP_MODULE_MASTER_20260724) — the
# as-of date auto-renewing terms are rolled forward to.
AS_OF = (2026, 7, 24)


def term_months(s):
    """'ten (10) years' / '10 years' / '120 months' -> months, else None."""
    if not s or not isinstance(s, str):
        return None
    s = s.lower()
    m = re.search(r'(\d{1,3})\s*\)?\s*(year|month)', s)
    if not m:
        w = re.search(r'\b([a-z]+)\s*(?:\(\d+\))?\s*(year|month)', s)
        if w and w.group(1) in WORD_NUMS:
            m = (WORD_NUMS[w.group(1)], w.group(2))
        else:
            return None
        return m[0] * (12 if m[1] == 'year' else 1)
    return int(m.group(1)) * (12 if m.group(2) == 'year' else 1)


def add_months_minus_day(mdy, months):
    """commencement 'M/D/YYYY' + term months - 1 day -> 'M/D/YYYY'."""
    import datetime
    mth, d, y = (int(x) for x in mdy.split('/'))
    total = (mth - 1) + months
    y2, m2 = y + total // 12, total % 12 + 1
    while True:  # clamp day into the target month
        try:
            end = datetime.date(y2, m2, d)
            break
        except ValueError:
            d -= 1
    end -= datetime.timedelta(days=1)
    return f'{end.month}/{end.day}/{end.year}'


def roll_forward(mdy, period_months, as_of=AS_OF):
    """Auto-renewal: extend by whole periods until expiration >= as-of."""
    import datetime
    mth, d, y = (int(x) for x in mdy.split('/'))
    cur = datetime.date(y, mth, d)
    limit = datetime.date(*as_of)
    rolls = 0
    while cur < limit and rolls < 12:
        # extend: expiration + period (day stays the period-end anchor)
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


def ocr_backfill(con):
    """OCR pages that have no text row; insert into document_fulltext."""
    try:
        import fitz
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        print(f'  OCR backfill skipped (dependency missing: {e})')
        return 0
    ocr = None
    added = 0
    for did, path, pcount in con.execute(
            "SELECT id, filepath, page_count FROM documents"):
        have = {int(r[0]) for r in con.execute(
            "SELECT CAST(page_number AS INTEGER) FROM document_fulltext "
            "WHERE document_id=?", (str(did),))}
        missing = [pg for pg in range(1, (pcount or 0) + 1) if pg not in have]
        if not missing or not os.path.exists(path):
            continue
        doc = fitz.open(path)
        for pg in missing:
            if pg > doc.page_count:
                continue
            page = doc[pg - 1]
            t = page.get_text().strip()
            if len(t) < MIN_PAGE_TEXT:
                if ocr is None:
                    print('  loading RapidOCR ...')
                    ocr = RapidOCR()
                import numpy as np
                pix = page.get_pixmap(dpi=200)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n)
                if pix.n == 4:
                    img = img[:, :, :3]
                res, _ = ocr(img)
                t = '\n'.join(line[1] for line in (res or []))
            if t.strip():
                con.execute(
                    "INSERT INTO document_fulltext (document_id, page_number, content) "
                    "VALUES (?,?,?)", (str(did), str(pg), t))
                added += 1
                print(f'  doc {did} p{pg}: backfilled {len(t)} chars (OCR)')
        doc.close()
    con.commit()
    return added


def ask(llm, seg_text, question_json, label):
    """One focused prompt against one small segment. Returns dict or {}."""
    if len(seg_text) > 12000:            # stay under the 90s tier regardless
        seg_text = seg_text[:12000]
    prompt = (f'LEASE TEXT ({label}):\n"""\n{seg_text}\n"""\n\n'
              f'Extract exactly this JSON (null for anything not stated):\n'
              f'{question_json}\n')
    t0 = time.time()
    out = llm.generate_structured(prompt, SYSTEM)
    dt = time.time() - t0
    ok = isinstance(out, dict)
    print(f'      [{label}] {dt:.0f}s -> {"ok" if ok else "no parse"}')
    return out if ok else {}


def pick_segments(instruments):
    """Route segments to extraction targets."""
    lease_insts = [i for i in instruments if i.get('segments')]
    primary = lease_insts[0] if lease_insts else None
    amendments = [i for i in instruments if i['kind'] == 'amendment']

    tgt = {'identity': [], 'term': [], 'sf': [], 'rent': [], 'amend': []}
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

        # dedup (the preamble can qualify twice: as num==0 AND by title),
        # THEN cap — otherwise a duplicate crowds out a real body segment.
        def dedup(lst):
            seen, out = set(), []
            for s in lst:
                if id(s) not in seen:
                    seen.add(id(s))
                    out.append(s)
            return out
        tgt['term'] = dedup(tgt['term'])[:3]
        tgt['sf'] = dedup(tgt['sf'])[:3]
    for a in amendments:
        text = '\n'.join('\n'.join(lines) for _pg, lines in a.get('pages', []))
        tgt['amend'].append({'title': a['header'], 'page_start': a['page_start'],
                             'page_end': a['page_end'], 'text': text.strip(),
                             'chars': len(text)})
    return tgt


def _ocr_score(t):
    """Content-aware quality score: prefer text that captures the substantive
    quantities (SF figures, dates), tiebreak on length."""
    sf_hits = len(re.findall(r'[\d,]{3,7}\s*(?:rentable\s+)?square\s*feet', t, re.I))
    date_hits = len(re.findall(r'\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]+ \d{1,2}, \d{4}', t))
    return (sf_hits, date_hits, len(t))


def reocr(con, targets):
    """Re-OCR specific pages (targets like ['1:3']) at multiple DPIs and keep
    the best candidate by content, not just length — a longer OCR pass can
    still drop the one line that matters."""
    import fitz
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    for spec in targets:
        did, pg = (int(x) for x in spec.split(':'))
        path = con.execute("SELECT filepath FROM documents WHERE id=?",
                           (did,)).fetchone()[0]
        old = con.execute(
            "SELECT content FROM document_fulltext WHERE document_id=? AND "
            "CAST(page_number AS INTEGER)=?", (str(did), pg)).fetchone()
        cands = [('stored', old[0])] if old else []
        doc = fitz.open(path)
        page = doc[pg - 1]
        for dpi in (200, 300, 400):
            pix = page.get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            res, _ = ocr(img)
            cands.append((f'{dpi}dpi',
                          '\n'.join(line[1] for line in (res or []))))
        doc.close()
        best = max(cands, key=lambda c: _ocr_score(c[1]))
        scores = {k: _ocr_score(v) for k, v in cands}
        print(f'  doc {did} p{pg}: candidates {scores} -> keeping {best[0]}')
        if best[0] != 'stored' and best[1].strip():
            con.execute("DELETE FROM document_fulltext WHERE document_id=? AND "
                        "CAST(page_number AS INTEGER)=?", (str(did), pg))
            con.execute("INSERT INTO document_fulltext (document_id, page_number, "
                        "content) VALUES (?,?,?)", (str(did), str(pg), best[1]))
    con.commit()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--reocr', nargs='*', default=[],
                    help='doc:page specs to re-OCR at high DPI, e.g. 1:3')
    args = ap.parse_args()

    con = sqlite3.connect(PILOT_DB)
    llm = LocalLLMClient()
    if not llm.is_available():
        sys.exit('Ollama is not reachable at localhost:11434 — start it first.')

    print('=== Step 1: OCR backfill of text-less pages ===')
    n = ocr_backfill(con)
    print(f'  {n} pages backfilled')
    if args.reocr:
        reocr(con, args.reocr)
    print()

    print('=== Step 2+3: segment + targeted extraction ===')
    con.execute("DELETE FROM financial_terms WHERE run_id=?", (RUN_ID,))
    ro = sqlite3.connect(f'file:{PILOT_DB.replace(os.sep, "/")}?mode=ro', uri=True)

    for did, fname in con.execute(
            "SELECT id, filename FROM documents ORDER BY id").fetchall():
        print(f'\n--- doc {did}: {fname}')
        pages = st.load_doc(ro, did)
        skip = st.toc_pages(pages)
        instruments = st.find_instruments(pages)
        for inst in instruments:
            if inst['kind'] in ('lease', 'amendment', 'assignment', 'license'):
                style, segs = st.segment_instrument(inst, skip)
                inst['segments'] = segs
        tgt = pick_segments(instruments)
        rows = []

        # identity — preamble first; if that page is scan-sticker/index junk,
        # fall back to the Tenant-definition clause in the lease body.
        ident_names = None
        for s in tgt['identity'][:2]:
            ident = ask(llm, s['text'],
                        '{"tenant_legal_name": ..., '
                        '"trade_name": "the d/b/a or store brand if stated", '
                        '"landlord_name": ...}', f'identity p{s["page_start"]}')
            if ident.get('tenant_legal_name') or ident.get('trade_name'):
                ident_names = (' / '.join(str(v) for v in
                                          (ident.get('tenant_legal_name'),
                                           ident.get('trade_name')) if v),
                               s['page_start'])
                break
        # deterministic net: '<Name> ... ("Tenant")' and 'd/b/a <Name>'
        body = '\n'.join('\n'.join(lines) for _pg, lines in pages)[:20000]
        found = []
        m = re.search(r"([A-Z][A-Za-z0-9 .,&'’-]{2,60}?)\s*[,(]\s*"
                      r'(?:an?\s+[^()\n]{0,60}?)?\(?["“]?Tenant["”]?\)',
                      body)
        if m:
            found.append(m.group(1).strip(' ,'))
        m = re.search(r"d[./]?b[./]?a[./]?\s*([A-Z][A-Za-z0-9 .&'’-]{2,40})",
                      body)
        if m:
            found.append(m.group(1).strip())
        net = ' / '.join(dict.fromkeys(found)) if found else None
        if ident_names is None and net:
            ident_names = (net, None)
        if ident_names:
            rows.append(('tenant_identity', 'Tenant (segmented)',
                         ident_names[0], None, None, None, None,
                         f'p{ident_names[1]}' if ident_names[1] else 'body scan',
                         ident_names[1]))
            if net and net.lower() not in ident_names[0].lower():
                rows.append(('tenant_identity', 'Tenant (definition clause)',
                             net, None, None, None, None, 'body scan', None))

        # term + expiration, chain-aware: primary lease first, then each
        # amendment in document order; last non-null expiration governs.
        # Leases that state commencement + term length (not an end date) get
        # a computed expiration; auto-renewing terms roll forward to the
        # master's as-of date — both flagged as computed, never silent.
        governing_exp, governing_src = None, None
        commencement, tmonths = None, None
        for s in tgt['term']:
            r = ask(llm, s['text'],
                    '{"commencement_date": "MM/DD/YYYY or null", '
                    '"expiration_date": "MM/DD/YYYY or null", '
                    '"term_length": "as stated, e.g. ten (10) years", '
                    '"renewal_options": ...}',
                    f'term p{s["page_start"]}-{s["page_end"]}')
            c, e = to_mdy(r.get('commencement_date')), to_mdy(r.get('expiration_date'))
            commencement = commencement or c
            tmonths = tmonths or term_months(r.get('term_length'))
            if e:
                governing_exp, governing_src = e, f'lease p{s["page_start"]}'
        if not governing_exp and commencement and tmonths:
            governing_exp = add_months_minus_day(commencement, tmonths)
            governing_src = (f'computed: commencement {commencement} '
                             f'+ {tmonths} months')
        auto_period = None
        for a in tgt['amend']:
            if not a['text'].strip():
                continue
            r = ask(llm, a['text'],
                    '{"new_expiration_date": "MM/DD/YYYY or null", '
                    '"expiration_date": "MM/DD/YYYY or null", '
                    '"auto_renewal": "true only if the term automatically '
                    'renews unless notice is given, else false", '
                    '"renewal_period": "length of each renewal period as '
                    'stated, e.g. four (4) years, or null"}',
                    f'amendment p{a["page_start"]}-{a["page_end"]}')
            e = to_mdy(r.get('new_expiration_date')) or to_mdy(r.get('expiration_date'))
            rows.append(('instrument_expiration', a['title'][:60],
                         e or '(no expiration stated)', None, None,
                         None, e, f'p{a["page_start"]}', a['page_start']))
            if e:
                governing_exp, governing_src = e, f'amendment p{a["page_start"]}'
            if str(r.get('auto_renewal')).lower() == 'true':
                auto_period = term_months(r.get('renewal_period')) or auto_period
        if governing_exp and auto_period:
            rolled, n = roll_forward(governing_exp, auto_period)
            if n:
                governing_src += (f'; auto-renewed {n}x{auto_period}mo '
                                  f'-> as of {AS_OF[1]}/{AS_OF[2]}/{AS_OF[0]}')
                governing_exp = rolled
        if governing_exp:
            rows.append(('governing_expiration',
                         f'Expiration (governing: {governing_src})',
                         governing_exp, None, None, commencement,
                         governing_exp, governing_src[:120], None))

        # square footage: LLM on premises segments, regex safety net
        sf = None
        for s in tgt['sf']:
            r = ask(llm, s['text'], '{"square_feet": ...}',
                    f'sf p{s["page_start"]}')
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
            for s in tgt['sf'] + tgt['term']:
                m = re.search(r'([\d,]{3,7})\s*(?:rentable\s+)?square\s+feet',
                              s['text'], re.I)
                if m:
                    v = float(m.group(1).replace(',', ''))
                    if 100 < v < 100000:
                        sf = (v, s['page_start'])
                        break
        if sf:
            rows.append(('square_footage', 'Premises SF (segmented)',
                         f'{sf[0]:.0f} SF', sf[0], 'sqft', None, None,
                         f'p{sf[1]}', sf[1]))

        # rent (informational — master carries no Hadley rent values)
        for s in tgt['rent']:
            r = ask(llm, s['text'],
                    '{"base_monthly_rent": ..., "annual_rent": ...}',
                    f'rent p{s["page_start"]}')
            v = r.get('base_monthly_rent') or r.get('annual_rent')
            if v:
                rows.append(('base_rent', 'Base rent (segmented)', str(v),
                             None, None, None, None, f'p{s["page_start"]}',
                             s['page_start']))
            break

        for (tt, lbl, raw, num, unit, eff, exp, ref, pg) in rows:
            con.execute(
                "INSERT INTO financial_terms (document_id, term_type, term_label, "
                "value_raw, value_numeric, value_unit, effective_date, "
                "expiration_date, section_ref, page_number, confidence, run_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, tt, lbl, raw, num, unit, eff, exp, ref, pg, 0.8, RUN_ID))
        con.commit()
        print(f'    wrote {len(rows)} seg_v1 terms')

    got = con.execute("SELECT count(*) FROM financial_terms WHERE run_id=?",
                      (RUN_ID,)).fetchone()[0]
    print(f'\nDONE — {got} seg_v1 rows. Now run: '
          f'python portfolio_ownership/pilot_hadley/tie_out.py')


if __name__ == '__main__':
    main()
