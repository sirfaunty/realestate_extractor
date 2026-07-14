"""
Midway P3 — PSA extraction (local model, on-device).

OCRs each Purchase & Sale Agreement (scanned, executed via DocuSign) and extracts the
deal economics into structured tables: `agreement` (metadata), `financial_term`
(purchase price, earnest money, broker commissions, thresholds), `key_date_deadline`
(inspection period, closing, survival), and `broker` (seller's/buyer's + commission).

Core, cleanly-extractable PSA facts. Nuanced contingencies / cost allocations and
any cross-deal judgment remain the human diligence layer.

Reuses ingest.extract_pdf for OCR. Same local model as the platform.

Usage:
    python extract_psa.py                     # process every PDF in source_docs/psa
"""
import argparse
import glob
import json
import os
import sqlite3

import ingest  # reuse per-page text + OCR extraction

OLLAMA_URL = os.environ.get("CAPACTIVE_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("CAPACTIVE_OLLAMA_MODEL", "llama3.1:8b")
NUM_CTX = int(os.environ.get("MIDWAY_NUM_CTX", "8192"))
TIMEOUT = int(os.environ.get("MIDWAY_TIMEOUT", "300"))
MAX_TEXT_CHARS = 22000    # ~5.5k tokens; covers the price + most deal terms within 8k ctx

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_HERE, "data", "midway.db")
DEFAULT_SRC = os.path.join(_HERE, "source_docs", "psa")

SYSTEM_PROMPT = (
    "You are a real-estate transactions analyst extracting the key terms of a Purchase "
    "and Sale Agreement (PSA). Rules:\n"
    "1. Extract ONLY facts stated in the text; do not infer. The text may be OCR'd.\n"
    "2. Quote dollar amounts, percentages, and day-counts exactly.\n"
    "3. Use only the JSON structure requested — do not invent keys.\n"
    "4. Be concise; the value is the fact itself."
)

# Placeholder example — shows STRUCTURE only. Values are angle-bracket placeholders so
# the model cannot copy them as data (it must read the actual PSA text).
_EXAMPLE = (
    '{\n'
    '  "title": "<agreement title>",\n'
    '  "purchase_price": "<the exact purchase price stated in THIS document>",\n'
    '  "effective": "<Month Year>",\n'
    '  "buyer": "<buyer name>",\n'
    '  "seller": "<seller name>",\n'
    '  "property": "<property description>",\n'
    '  "financial_terms": [\n'
    '    {"item": "Earnest Money", "value": "<amount>", "notes": "<who holds it>"},\n'
    '    {"item": "Buyer\'s Broker Commission", "value": "<% or $>", "notes": ""}\n'
    '  ],\n'
    '  "key_dates": [\n'
    '    {"item": "Inspection Period", "trigger": "<trigger event>", "duration": "<N days>"},\n'
    '    {"item": "Closing Date", "trigger": "<trigger event>", "duration": "<N days>"}\n'
    '  ],\n'
    '  "brokers": [\n'
    '    {"side": "Seller", "name": "<broker name>", "commission_pct": 0.0}\n'
    '  ]\n'
    '}'
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


def _prompt(text):
    return (
        "Extract the key terms of this Purchase and Sale Agreement. Read the actual "
        "text below and report ITS values — the example shows only the JSON shape, its "
        "placeholder values are NOT real data.\n\n"
        "Return ONLY a JSON object with these keys: title, purchase_price, effective, "
        "buyer, seller, property, financial_terms (list of {item, value, notes}), "
        "key_dates (list of {item, trigger, duration}), brokers (list of "
        "{side, name, commission_pct}). List EVERY earnest-money, commission, threshold, "
        "deadline, and broker you find. Include only facts stated in the text.\n\n"
        f"JSON shape (placeholders, not data):\n{_EXAMPLE}\n\n"
        "PSA TEXT:\n\"\"\"\n" + text[:MAX_TEXT_CHARS] + "\n\"\"\""
    )


def extract_one(con, pdf_path):
    text, pages, _ht, _oc, _no = ingest.extract_pdf(pdf_path)
    raw = _GENERATE(_prompt(text), SYSTEM_PROMPT)
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return None
    fname = os.path.basename(pdf_path)
    cur = con.execute(
        "INSERT INTO agreement(label, source_file, title, effective_month, "
        "effective_year, governing_law, page_count) VALUES(?,?,?,?,?,?,?)",
        (d.get("property") or fname, fname, d.get("title"),
         (d.get("effective") or "").split()[0] if d.get("effective") else None,
         (d.get("effective") or "").split()[-1] if d.get("effective") else None,
         "Minnesota", pages))
    aid = cur.lastrowid
    if d.get("purchase_price"):
        con.execute("INSERT INTO financial_term(agreement_id,item,value,notes) VALUES(?,?,?,?)",
                    (aid, "Purchase Price", d["purchase_price"], f"buyer {d.get('buyer')}"))
    for ft in d.get("financial_terms", []) or []:
        if isinstance(ft, dict) and ft.get("item"):
            con.execute("INSERT INTO financial_term(agreement_id,item,value,notes) VALUES(?,?,?,?)",
                        (aid, ft["item"], ft.get("value"), ft.get("notes")))
    for kd in d.get("key_dates", []) or []:
        if isinstance(kd, dict) and kd.get("item"):
            con.execute("INSERT INTO key_date_deadline(agreement_id,item,trigger,duration) VALUES(?,?,?,?)",
                        (aid, kd["item"], kd.get("trigger"), kd.get("duration")))
    for b in d.get("brokers", []) or []:
        if isinstance(b, dict) and b.get("name"):
            pct = b.get("commission_pct")
            try:
                pct = float(pct)
            except (TypeError, ValueError):
                pct = None
            con.execute("INSERT INTO broker(agreement_id,side,name,commission_pct) VALUES(?,?,?,?)",
                        (aid, b.get("side"), b["name"], pct))
    con.commit()
    n = (1 if d.get("purchase_price") else 0) + len(d.get("financial_terms") or []) \
        + len(d.get("key_dates") or []) + len(d.get("brokers") or [])
    return {"agreement_id": aid, "title": d.get("title"),
            "price": d.get("purchase_price"), "facts": n}


def run(db_path=DEFAULT_DB, src=DEFAULT_SRC):
    con = sqlite3.connect(db_path)
    con.execute("DELETE FROM agreement")
    con.execute("DELETE FROM financial_term")
    con.execute("DELETE FROM key_date_deadline")
    con.execute("DELETE FROM broker")
    con.commit()
    pdfs = sorted(glob.glob(os.path.join(src, "*PSA*.pdf")))
    print(f"Extracting {len(pdfs)} PSAs with {OLLAMA_MODEL}")
    for p in pdfs:
        print(f"  {os.path.basename(p)} — OCR + extract…", flush=True)
        try:
            r = extract_one(con, p)
        except Exception as e:
            print(f"    ! failed: {str(e)[:70]}")
            continue
        if r:
            print(f"    price {r['price']}  ({r['facts']} facts)")
        else:
            print("    ! model returned unparseable JSON")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--src", default=DEFAULT_SRC)
    args = ap.parse_args()
    print(f"PSA extraction: {OLLAMA_MODEL} @ {OLLAMA_URL}")
    run(args.db, args.src)
