"""
Midway P1 — document ingestion + OCR (local, on-device).

Walks a tenant-packages tree, registers each PDF in `lease_document_file`, and
extracts text — the digital text layer where present (PyMuPDF), OCR where the page
is scanned (RapidOCR, ONNX — no system Tesseract/poppler needed). Extracted text is
written per file and the row is marked extracted=1.

Pip-only: pymupdf, rapidocr-onnxruntime. Nothing leaves the device.

Usage:
    python ingest.py                      # defaults: source_docs/tenant_packages -> data/midway.db
    python ingest.py --limit-pages 4      # cap OCR pages per file (faster smoke test)
"""
import argparse
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(_HERE, "source_docs", "tenant_packages")
DEFAULT_DB = os.path.join(_HERE, "data", "midway.db")
DEFAULT_TEXT = os.path.join(_HERE, "data", "ocr_text")

# doc_role inferred from filename (matches the partner's taxonomy).
_ROLE_RULES = [
    ("parking services", "parking_services_agreement"),
    ("sale letter", "sale_letter"), ("sale_letter", "sale_letter"),
    ("estoppel", "estoppel"),
    ("snda", "snda"), ("snds", "snda"),
    ("checklist", "checklist_ner"), ("ners", "checklist_ner"),
    ("tenant approval", "tenant_approval_sos"), ("approval", "tenant_approval_sos"),
    ("tenant documents", "vendor_setup_packet"), ("vendor", "vendor_setup_packet"),
    ("signage", "signage"),
    ("correspondence", "correspondence"),
]

_CATEGORY = {
    "LA Fitness": "fitness", "Dollar Tree": "retail", "JP Morgan Chase": "bank",
    "Mother Nature": "retail", "Midway Tobacco": "retail", "Cornerstone Parking": "parking",
    "Clear Channel": "media", "Comcast": "telecom", "CenturyLink": "telecom",
}
_TEXT_LAYER_MIN = 100   # chars over first pages => has a real text layer

_OCR = None


def _classify(fname):
    low = fname.lower()
    for kw, role in _ROLE_RULES:
        if kw in low:
            return role
    return "other"


def _tenant_name(folder):
    return folder.replace("_", " ").strip()


def _get_ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def _ocr_page(page):
    import numpy as np
    pix = page.get_pixmap(dpi=200)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:  # drop alpha
        img = img[:, :, :3]
    res, _ = _get_ocr()(img)
    return "\n".join(line[1] for line in (res or []))


def extract_pdf(path, limit_pages=None, ocr=True):
    """Per-page extraction: use the digital text layer where a page has one, OCR only
    the pages that don't. Returns (text, pages, has_text_layer, used_ocr, needs_ocr).
    With ocr=False, text-less pages are skipped and the file is flagged needs_ocr."""
    import fitz
    doc = fitz.open(path)
    pages = doc.page_count
    n = pages if limit_pages is None else min(pages, limit_pages)
    out, any_text, any_ocr, pending = [], False, False, False
    for i in range(n):
        page = doc[i]
        t = page.get_text().strip()
        if len(t) >= 20:
            out.append(t); any_text = True
        elif ocr:
            out.append(_ocr_page(page)); any_ocr = True
        else:
            pending = True
    doc.close()
    return "\n".join(out), pages, any_text, any_ocr, (any_ocr or pending)


def run(src=DEFAULT_SRC, db_path=DEFAULT_DB, text_dir=DEFAULT_TEXT, limit_pages=None, ocr=True):
    import glob
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    os.makedirs(text_dir, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    with open(os.path.join(_HERE, "schema.sql"), encoding="utf-8") as f:
        con.executescript(f.read())

    tenants = {}   # name -> tenant_id
    pdfs = sorted(glob.glob(os.path.join(src, "**", "*.pdf"), recursive=True))
    print(f"Ingesting {len(pdfs)} PDFs from {src}")
    n_ocr = n_digital = 0
    for path in pdfs:
        rel = os.path.relpath(path, src)
        folder = rel.split(os.sep)[0]
        tname = _tenant_name(folder)
        if tname not in tenants:
            cur = con.execute(
                "INSERT INTO lease_tenant(name, folder, category, status) VALUES(?,?,?,?)",
                (tname, folder, _CATEGORY.get(tname, "unknown"), "active"))
            tenants[tname] = cur.lastrowid
        tid = tenants[tname]
        role = _classify(os.path.basename(path))
        try:
            text, pages, has_text, used_ocr, needs_ocr = extract_pdf(path, limit_pages, ocr=ocr)
        except Exception as e:
            print(f"  ! {rel[:55]}: {str(e)[:50]}")
            continue
        extracted = int(bool(text))
        text_path = None
        if text:
            tp = os.path.join(text_dir, folder, os.path.basename(path) + ".txt")
            os.makedirs(os.path.dirname(tp), exist_ok=True)
            with open(tp, "w", encoding="utf-8") as f:
                f.write(text)
            text_path = os.path.relpath(tp, _HERE)
        con.execute(
            """INSERT INTO lease_document_file
                 (tenant_id, rel_path, doc_role, size_bytes, pages, has_text_layer,
                  needs_ocr, extracted, text_path)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (tid, rel, role, os.path.getsize(path), pages, int(has_text),
             int(needs_ocr), extracted, text_path))
        n_ocr += used_ocr
        n_digital += (has_text and not used_ocr)
        status = "mix " if (has_text and used_ocr) else ("OCR " if used_ocr
                 else ("text" if has_text else "defer"))
        print(f"  {status}  {role:26} {len(text):>6}c  {rel[:48]}", flush=True)
    con.commit()
    con.close()
    print(f"\nDone: {len(pdfs)} files · {len(tenants)} tenants · "
          f"{n_digital} digital, {n_ocr} OCR'd · DB {db_path}")
    return {"files": len(pdfs), "tenants": len(tenants), "digital": n_digital, "ocr": n_ocr}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--text-dir", default=DEFAULT_TEXT)
    ap.add_argument("--limit-pages", type=int, default=None)
    ap.add_argument("--no-ocr", action="store_true",
                    help="Digital text only; register scanned files as needs_ocr (fast)")
    args = ap.parse_args()
    run(args.src, args.db, args.text_dir, args.limit_pages, ocr=not args.no_ocr)
# end
