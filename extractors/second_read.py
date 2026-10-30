"""
Second read: an independent OCR of a scanned page, so no figure rests on a
single OCR pass.

The first pass (pdf_ingestion.extract_text_ocr) renders with pdf2image at
250 dpi, cleans the image with OpenCV and lets Tesseract find the layout.
The second read changes all three: PyMuPDF renders at 300 dpi, plain
grayscale, and Tesseract reads in a different page-segmentation mode. Two
reads that print the same words are corroborated; a value whose printed form
is in both reads is corroborated; one printed in only one read is not, and
says so.

Reads are stored per page in `page_reads` (created on demand in whatever
SQLite file the caller passes, so a read can live beside an org DB without
touching it):
    page_reads(document_id, page_number, method, text, confidence, agreement, read_at)

    text, conf = ocr_page(pdf_bytes, 7)
    agreement(first_text, text)          # 0..1, word-sequence similarity
    in_read(quote, text)                 # the quote re-finds in that read
"""
from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from datetime import datetime

SECOND_DPI = 300
SECOND_PSM = int(__import__('os').environ.get('CAPACTIVE_SECOND_PSM', 4))   # 4 = one column of varying lines (measured best of 4 / 6)
METHOD = 'ocr_second_read'
LAYER = 'text_layer_read'   # a scanned PDF's own text layer (the scanner's OCR): an independent read too
LADDER = 'ocr_ladder_read'  # a third read at higher resolution, made for a form whose first two reads do not tie
CORROBORATED = 0.80         # page agreement at or above this = the two reads agree

_Q = str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"', '–': '-',
                    '—': '-', ' ': ' ', '­': ''})


def _norm(t: str) -> str:
    t = unicodedata.normalize('NFKC', t or '').translate(_Q).lower()
    t = re.sub(r'-\s*\n\s*', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def _loose(t: str) -> str:
    return re.sub(r'[^a-z0-9]', '', _norm(t))


def ocr_page(pdf, page_no: int, dpi: int = SECOND_DPI, psm: int = SECOND_PSM,
             timeout: int = 120) -> tuple[str, float]:
    """OCR one page (1-based) of a PDF given as a path or bytes. Returns
    (text, mean word confidence)."""
    import fitz
    import pytesseract
    from PIL import Image

    doc = fitz.open(pdf) if isinstance(pdf, str) else fitz.open(stream=pdf, filetype='pdf')
    try:
        pix = doc[page_no - 1].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        img = Image.frombytes('L', (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    data = pytesseract.image_to_data(img, config=f'--psm {psm}',
                                     output_type=pytesseract.Output.DICT, timeout=timeout)
    lines, confs, cur = [], [], None
    for j, w in enumerate(data['text']):
        w = (w or '').strip()
        if not w:
            continue
        key = (data['block_num'][j], data['par_num'][j], data['line_num'][j])
        if key != cur:
            cur = key
            lines.append([])
        lines[-1].append(w)
        c = data['conf'][j]
        try:
            if float(c) > 0:
                confs.append(float(c))
        except (TypeError, ValueError):
            pass
    return '\n'.join(' '.join(x) for x in lines), (sum(confs) / len(confs) if confs else 0.0)


def text_layer(pdf, page_no: int, min_chars: int = 40) -> str:
    """The page's own text layer when it has a real one ('' when not). On a
    scanned lease that layer is the scanner's OCR: a read independent of ours."""
    import fitz
    doc = fitz.open(pdf) if isinstance(pdf, str) else fitz.open(stream=pdf, filetype='pdf')
    try:
        t = doc[page_no - 1].get_text() or ''
        return t if len(t.strip()) >= min_chars and clean_text(t) else ''
    finally:
        doc.close()


def clean_text(t: str) -> bool:
    """A text layer that reads as language, not a broken font encoding: mostly
    printable ASCII, no '(cid:' glyph codes, and most tokens look like words."""
    s = (t or '').strip()
    if not s or '(cid:' in s:
        return False
    printable = sum(1 for ch in s if 32 <= ord(ch) < 127 or ch in '\n\t') / len(s)
    toks = re.findall(r'\S+', s)
    wordy = sum(1 for w in toks if re.fullmatch(r"[A-Za-z][a-z'\-]*[.,;:)]?|\$?[\d,.]+[.,;:)]?|\(?[a-zA-Z0-9]\)", w))
    return printable >= 0.85 and wordy / max(1, len(toks)) >= 0.6


def has_text_layer(pdf, page_no: int, min_chars: int = 40) -> bool:
    return bool(text_layer(pdf, page_no, min_chars))


def agreement(a: str, b: str) -> float:
    """How far two reads of one page print the same words, in order (0..1)."""
    wa, wb = _norm(a).split(), _norm(b).split()
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def in_read(quote: str, text: str) -> bool:
    """The quote re-finds in a read: verbatim, or equal once spacing and
    punctuation are set aside (OCR spacing)."""
    q = _norm(quote)
    if not q or not text:
        return False
    if q in _norm(text):
        return True
    lq = _loose(quote)
    return len(lq) >= 3 and lq in _loose(text)


def ensure_table(conn: sqlite3.Connection):
    conn.execute('CREATE TABLE IF NOT EXISTS page_reads (document_id INTEGER, page_number INTEGER, '
                 'method TEXT, text TEXT, confidence REAL, agreement REAL, read_at TEXT, '
                 'PRIMARY KEY (document_id, page_number, method))')


def store_read(conn: sqlite3.Connection, doc_id: int, page_no: int, text: str, conf: float,
               agree: float | None, method: str = METHOD):
    ensure_table(conn)
    conn.execute('INSERT OR REPLACE INTO page_reads VALUES (?,?,?,?,?,?,?)',
                 (doc_id, page_no, method, text, conf, agree, datetime.now().isoformat(timespec='seconds')))


def load_reads(path_or_conn, methods=(LAYER, METHOD)) -> dict:
    """{(document_id, page_number): text} of the second reads; {} when none.
    With both kinds on a page the OCR read wins (later in `methods`)."""
    try:
        conn = path_or_conn if isinstance(path_or_conn, sqlite3.Connection) else \
            sqlite3.connect(f'file:{path_or_conn}?mode=ro', uri=True)
        out = {}
        for m in methods:
            for d, p, t in conn.execute('SELECT document_id, page_number, text FROM page_reads WHERE method=?',
                                        (m,)):
                out[(int(d), int(p))] = t or ''
        return out
    except sqlite3.Error:
        return {}
