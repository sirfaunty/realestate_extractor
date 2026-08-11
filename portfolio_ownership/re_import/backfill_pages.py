#!/usr/bin/env python3
"""
#77 Re-import campaign — OCR backfill utility (property-agnostic).

Two jobs, both idempotent, run natively (writes to the pilot DB):

1. Backfill: any page in 1..page_count with no document_fulltext row gets
   OCR'd from the source PDF (RapidOCR @200dpi, text layer preferred).
   These are the pages the ingest skipped — on the Osborne/ENGELS lease
   form, page 3 (Articles 1-3: premises, term, rent) is a frequent victim.
2. --reocr DOC:PAGE ...: multi-DPI (200/300/400) re-OCR of specific pages,
   keeping the best candidate by CONTENT (SF figures + dates, then length)
   — a longer OCR pass can still drop the one line that matters.

    venv/Scripts/python portfolio_ownership/re_import/backfill_pages.py \
        --db data/pilot_maplewood.db [--reocr 1:3 5:3]
"""

import argparse
import os
import re
import sqlite3
import sys

MIN_PAGE_TEXT = 40


def _ocr_score(t):
    sf = len(re.findall(r'[\d,]{3,7}\s*(?:rentable\s+)?square\s*feet', t, re.I))
    dates = len(re.findall(r'\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]+ \d{1,2}, \d{4}', t))
    return (sf, dates, len(t))


def _page_ocr(page, ocr, dpi):
    import numpy as np
    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    res, _ = ocr(img)
    return '\n'.join(line[1] for line in (res or []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--reocr', nargs='*', default=[],
                    help='doc:page specs for multi-DPI retry, e.g. 1:3 5:3')
    ap.add_argument('--repair-cid', action='store_true',
                    help='(now the default; flag kept for compatibility)')
    ap.add_argument('--skip-repair', action='store_true',
                    help='skip the (cid:) garbage repair pass')
    args = ap.parse_args()
    args.repair_cid = not args.skip_repair   # repair is on by default

    import fitz
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    con = sqlite3.connect(args.db)

    print('=== backfill: pages with no text row ===')
    added = 0
    for did, path, pcount in con.execute(
            "SELECT id, filepath, page_count FROM documents"):
        if not os.path.exists(path):
            continue
        # trust the SOURCE PDF's page count, not the DB's — the digital
        # pipeline's empty-page early-stop used to record only the pages it
        # extracted (the Ellie case: 46-page scan recorded as 1 page)
        doc = fitz.open(path)
        true_count = doc.page_count
        if true_count != (pcount or 0):
            con.execute("UPDATE documents SET page_count=? WHERE id=?",
                        (true_count, did))
            print(f'  doc {did}: page_count corrected {pcount} -> {true_count}')
        have = {int(r[0]) for r in con.execute(
            "SELECT CAST(page_number AS INTEGER) FROM document_fulltext "
            "WHERE CAST(document_id AS INTEGER)=?", (did,))}
        missing = [pg for pg in range(1, true_count + 1) if pg not in have]
        if not missing:
            doc.close()
            continue
        for pg in missing:
            if pg > doc.page_count:
                continue
            page = doc[pg - 1]
            t = page.get_text().strip()
            if len(t) < MIN_PAGE_TEXT:
                t = _page_ocr(page, ocr, 200)
            if t.strip():
                con.execute(
                    "INSERT INTO document_fulltext (document_id, page_number, "
                    "content) VALUES (?,?,?)", (str(did), str(pg), t))
                added += 1
                print(f'  doc {did} p{pg}: backfilled {len(t)} chars')
            else:
                print(f'  doc {did} p{pg}: still empty after OCR — genuine blank?')
        doc.close()
    con.commit()
    print(f'  {added} pages backfilled')

    if args.repair_cid:
        print('\n=== repair: pages with (cid:) garbage text ===')
        bad = con.execute("""
            SELECT CAST(f.document_id AS INTEGER), CAST(f.page_number AS INTEGER)
            FROM document_fulltext f
            WHERE (length(f.content) - length(replace(f.content, '(cid:', '')))
                  / 5 > 20
            ORDER BY 1, 2""").fetchall()
        by_doc = {}
        for did, pg in bad:
            by_doc.setdefault(did, []).append(pg)
        print(f'  {len(bad)} garbage pages across {len(by_doc)} documents')
        import time as _time
        for did, pages_ in by_doc.items():
            path = con.execute("SELECT filepath FROM documents WHERE id=?",
                               (did,)).fetchone()[0]
            if not os.path.exists(path):
                print(f'  doc {did}: source PDF missing — skipped')
                continue
            doc = fitz.open(path)
            fixed = 0
            # These pages carry garbage vector-text overlays that send the
            # OCR detector into overdrive at 200dpi (minutes/page). 150dpi
            # reads them fine in ~10s; drop to 100 if a page still drags.
            dpi = 150
            for k, pg in enumerate(pages_):
                if pg > doc.page_count:
                    continue
                t0 = _time.time()
                t = _page_ocr(doc[pg - 1], ocr, dpi)
                dt = _time.time() - t0
                if dt > 30 and dpi > 100:
                    dpi = 100
                    print(f'    doc {did} p{pg}: {dt:.0f}s — dropping to '
                          f'{dpi}dpi for the rest of this document')
                if t.strip():
                    con.execute(
                        "DELETE FROM document_fulltext WHERE "
                        "CAST(document_id AS INTEGER)=? AND "
                        "CAST(page_number AS INTEGER)=?", (did, pg))
                    con.execute(
                        "INSERT INTO document_fulltext (document_id, "
                        "page_number, content) VALUES (?,?,?)",
                        (str(did), str(pg), t))
                    fixed += 1
                    con.commit()   # per-page: Ctrl+C-safe, resumable
                print(f'    doc {did} p{pg} ({k + 1}/{len(pages_)}): '
                      f'{len(t)} chars in {dt:.0f}s', flush=True)
            doc.close()
            print(f'  doc {did}: re-OCR {fixed}/{len(pages_)} pages')
        con.commit()

    for spec in args.reocr:
        did, pg = (int(x) for x in spec.split(':'))
        path = con.execute("SELECT filepath FROM documents WHERE id=?",
                           (did,)).fetchone()[0]
        old = con.execute(
            "SELECT content FROM document_fulltext WHERE "
            "CAST(document_id AS INTEGER)=? AND CAST(page_number AS INTEGER)=?",
            (did, pg)).fetchone()
        cands = [('stored', old[0])] if old else []
        doc = fitz.open(path)
        for dpi in (200, 300, 400):
            cands.append((f'{dpi}dpi', _page_ocr(doc[pg - 1], ocr, dpi)))
        doc.close()
        best = max(cands, key=lambda c: _ocr_score(c[1]))
        print(f'  reocr doc {did} p{pg}: '
              f'{ {k: _ocr_score(v) for k, v in cands} } -> {best[0]}')
        if best[0] != 'stored' and best[1].strip():
            con.execute("DELETE FROM document_fulltext WHERE "
                        "CAST(document_id AS INTEGER)=? AND "
                        "CAST(page_number AS INTEGER)=?", (did, pg))
            con.execute("INSERT INTO document_fulltext (document_id, "
                        "page_number, content) VALUES (?,?,?)",
                        (str(did), str(pg), best[1]))
    con.commit()
    con.close()
    print('done')


if __name__ == '__main__':
    main()
