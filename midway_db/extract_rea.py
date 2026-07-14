"""
Midway P3 — REA prohibited-use extraction (local model, on-device).

The REA (Reciprocal Easement Agreement) governs the shopping center. Its Exhibit F is
the SHOPPING CENTER PROHIBITED USES schedule (~28-29 numbered items) — the diligence-
critical list for the buyer's Asian-grocery + food-hall plan (use/exclusive conflicts).

The REA is a large, poorly-scanned document with a noisy text layer; re-OCR is no
better, so we use the embedded text of the prohibited-use pages and let the local model
extract + lightly clean the numbered list into `rea_prohibited_use`.

Scope: the prohibited-use schedule (concrete, list-structured). The broader cross-
parcel / no-change-area analysis is diffuse legal synthesis and stays human diligence.

Usage:
    python extract_rea.py                     # locate Exhibit F, extract prohibited uses
"""
import argparse
import glob
import json
import os
import re
import sqlite3

OLLAMA_URL = os.environ.get("CAPACTIVE_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("CAPACTIVE_OLLAMA_MODEL", "llama3.1:8b")
NUM_CTX = int(os.environ.get("MIDWAY_NUM_CTX", "8192"))
TIMEOUT = int(os.environ.get("MIDWAY_TIMEOUT", "300"))

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_HERE, "data", "midway.db")
DEFAULT_SRC = os.path.join(_HERE, "source_docs", "psa")

SYSTEM_PROMPT = (
    "You are a real-estate analyst extracting a PROHIBITED USES schedule from a "
    "Reciprocal Easement Agreement exhibit. The source text is OCR'd and noisy. Rules:\n"
    "1. Return every numbered prohibited use you can identify.\n"
    "2. Lightly correct obvious OCR errors (e.g. 'Vidco arcade' -> 'Video arcade') but "
    "do NOT invent uses that aren't there.\n"
    "3. Keep each item's number. Put any stated exception in 'exceptions'.\n"
    "4. Use only the requested JSON structure."
)

_EXAMPLE = (
    '{"prohibited_uses": [\n'
    '  {"item_no": "1", "prohibited_use": "<the prohibited use>", "exceptions": "<any exception, or empty>"},\n'
    '  {"item_no": "2", "prohibited_use": "<...>", "exceptions": ""}\n'
    ']}'
)


def _generate(prompt, system):
    import requests
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "system": system, "stream": False,
               "format": "json",
               "options": {"temperature": 0.1, "num_ctx": NUM_CTX, "num_predict": 2048}}
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("response", "")


_GENERATE = _generate


_LIST_LINE = re.compile(r"(?m)^\s*\d{1,2}\s*[.)]\s")


def _find_prohibited_text(pdf_path, max_pages=3):
    """Locate the prohibited-use exhibit (the page with the most numbered list-items
    among pages mentioning 'prohibit' — avoids the table of contents) and return its
    embedded text plus the following pages."""
    import fitz
    doc = fitz.open(pdf_path)
    best, best_n = None, 0
    for i in range(doc.page_count):
        t = doc[i].get_text()
        if "prohibit" in t.lower():
            n = len(_LIST_LINE.findall(t))
            if n > best_n:
                best, best_n = i, n
    if best is None:
        doc.close()
        return None
    text = "\n".join(doc[i].get_text() for i in range(best, min(best + max_pages, doc.page_count)))
    doc.close()
    return text


def _prompt(text):
    return (
        "Extract the SHOPPING CENTER PROHIBITED USES schedule from the noisy OCR text "
        "below. Return ONLY a JSON object: {\"prohibited_uses\": [{item_no, "
        "prohibited_use, exceptions}, ...]}. Include every numbered item; clean obvious "
        "OCR typos but do not invent items.\n\n"
        f"JSON shape:\n{_EXAMPLE}\n\n"
        "REA EXHIBIT TEXT:\n\"\"\"\n" + text[:16000] + "\n\"\"\""
    )


def run(db_path=DEFAULT_DB, src=DEFAULT_SRC):
    reas = sorted(glob.glob(os.path.join(src, "*REA*.pdf")))
    if not reas:
        print("No REA PDF found in", src)
        return
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM rea_prohibited_use")
    con.commit()
    total = 0
    for rea in reas:
        print(f"  {os.path.basename(rea)} — locating Exhibit F…", flush=True)
        text = _find_prohibited_text(rea)
        if not text:
            print("    ! prohibited-use section not found")
            continue
        raw = _GENERATE(_prompt(text), SYSTEM_PROMPT)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("    ! unparseable JSON")
            continue
        items = data.get("prohibited_uses") or []
        for it in items:
            if not isinstance(it, dict) or not it.get("prohibited_use"):
                continue
            con.execute(
                "INSERT INTO rea_prohibited_use(item_no, prohibited_use, exceptions) "
                "VALUES(?,?,?)",
                (str(it.get("item_no") or ""), it["prohibited_use"].strip(),
                 (it.get("exceptions") or "").strip()))
            total += 1
        con.commit()
        print(f"    extracted {len(items)} prohibited uses")
    con.close()
    print(f"\nRecorded {total} prohibited uses.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--src", default=DEFAULT_SRC)
    args = ap.parse_args()
    print(f"REA extraction: {OLLAMA_MODEL} @ {OLLAMA_URL}")
    run(args.db, args.src)
