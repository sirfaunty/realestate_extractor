"""
Midway P2 — structured lease-abstract extraction (local model, on-device).

For each tenant, feeds the fact-dense certification text (estoppel + checklist/NERS,
falling back to correspondence / SNDA / services agreement) to the local Ollama model
and extracts a CORE set of standard lease-abstract fields as structured facts, each
with a source citation and confidence. Facts are written to `lease_abstract`.

Scope (deliberate): the recurring, single-instrument-extractable fields below. The
partner's bespoke analytical fields (rentroll_reconciliation, date_discrepancy_flag,
ownership_chain synthesis, the co-tenancy/REA/entity FLAGs) require cross-document
reasoning + deal knowledge and remain the human diligence layer — see README.

Same local model as the rest of the platform (llama3.1:8b @ localhost:11434).

Usage:
    python abstract_facts.py                 # all tenants in data/midway.db
    python abstract_facts.py --only "LA Fitness"
"""
import argparse
import datetime
import json
import os
import sqlite3

OLLAMA_URL = os.environ.get("CAPACTIVE_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("CAPACTIVE_OLLAMA_MODEL", "llama3.1:8b")
NUM_CTX = int(os.environ.get("MIDWAY_NUM_CTX", "8192"))
NUM_PREDICT = int(os.environ.get("MIDWAY_NUM_PREDICT", "1536"))
TIMEOUT = int(os.environ.get("MIDWAY_TIMEOUT", "300"))
MAX_TEXT_CHARS = 16000   # keep the prompt within context

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_HERE, "data", "midway.db")

# Core field schema: field -> guidance shown to the model.
CORE_FIELDS = {
    "instrument_type": "Lease, Ground Lease, License, or Services Agreement",
    "tenant_entity": "the tenant's full legal entity name (with state/type if given)",
    "landlord": "the current landlord named in the estoppel/certification",
    "premises_address": "street address and/or suite of the demised premises",
    "premises_sf": "leased square footage, only if explicitly stated",
    "lease_date": "the lease/agreement dated date (or commencement date)",
    "term_expiration": "the current base-term expiration date",
    "base_rent": "current base/minimum rent with the amount (annual or monthly)",
    "renewal_options": "renewal/extension options — count and length",
    "permitted_use": "the permitted use of the premises",
    "assignment_status": "assignment/sublet status or restriction, if stated",
    "security_deposit": "security deposit amount, or 'None' if stated as none",
    "notice_address_tenant": "the tenant's notice address",
}

# Which doc roles are most fact-dense, in priority order (legal instruments first,
# transmittal correspondence last so it doesn't distract the model).
_ROLE_PRIORITY = ["estoppel", "checklist_ner", "snda", "tenant_approval_sos",
                  "parking_services_agreement", "vendor_setup_packet",
                  "sale_letter", "correspondence"]
_PER_DOC_CHARS = 7000    # cap any single doc so it can't crowd out the others

SYSTEM_PROMPT = (
    "You are a commercial real-estate lease analyst extracting structured facts from a "
    "tenant's certification documents (estoppel, SNDA, checklist) for a disposition. "
    "Rules:\n"
    "1. Your JSON output keys MUST be chosen only from the provided field list — never "
    "invent keys like 'title' or 'subject'. Omit fields not stated in the text.\n"
    "2. Extract ONLY facts stated in the text; do not infer or use outside knowledge.\n"
    "3. Quote figures, dates, and entity names exactly as written.\n"
    "4. The text may be OCR'd/imperfect; if a value is garbled or uncertain, set "
    "confidence to 'low'.\n"
    "5. The value is the fact itself, concise — not commentary."
)

_EXAMPLE = (
    '{\n'
    '  "instrument_type": {"value": "Ground Lease", "source": "estoppel", "confidence": "high"},\n'
    '  "tenant_entity": {"value": "Fitness International, LLC", "source": "estoppel preamble", "confidence": "high"},\n'
    '  "base_rent": {"value": "$425,000.00/yr", "source": "NERS rent block", "confidence": "high"},\n'
    '  "term_expiration": {"value": "9/30/2033", "source": "checklist", "confidence": "high"}\n'
    '}'
)


def _build_prompt(fields_block, text):
    return (
        "Extract lease facts from the tenant documents below.\n\n"
        "Return ONLY a JSON object. Each KEY must be exactly one of these field names "
        "(include a field only if it is stated in the text):\n"
        f"{fields_block}\n\n"
        'Each value is an object: {"value": <string>, "source": <which document>, '
        '"confidence": "high"|"medium"|"low"}.\n\n'
        f"Example of the exact output format:\n{_EXAMPLE}\n\n"
        "TENANT DOCUMENTS:\n\"\"\"\n" + text + "\n\"\"\""
    )


def ollama_generate(prompt, system, num_ctx=None):
    import requests
    payload = {
        "model": OLLAMA_MODEL, "prompt": prompt, "system": system, "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": num_ctx or NUM_CTX,
                    "num_predict": NUM_PREDICT},
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("response", "")


_GENERATE = ollama_generate


def _gather_text(con, tenant_id):
    """Concatenate a tenant's fact-dense extracted text, highest-priority first."""
    rows = con.execute(
        "SELECT doc_role, text_path FROM lease_document_file "
        "WHERE tenant_id=? AND extracted=1 AND text_path IS NOT NULL", (tenant_id,)
    ).fetchall()
    rows.sort(key=lambda r: _ROLE_PRIORITY.index(r[0]) if r[0] in _ROLE_PRIORITY else 99)
    chunks, used = [], 0
    for role, tp in rows:
        p = os.path.join(_HERE, tp)
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8", errors="ignore").read().strip()
        if not t:
            continue
        budget = MAX_TEXT_CHARS - used
        if budget <= 0:
            break
        chunk = f"[{role}]\n{t[:min(budget, _PER_DOC_CHARS)]}"
        chunks.append(chunk)
        used += len(chunk)
    return "\n\n".join(chunks)


def abstract_tenant(con, tenant_id, model=OLLAMA_MODEL):
    text = _gather_text(con, tenant_id)
    if not text:
        return 0
    fields_block = "\n".join(f"- {f}: {desc}" for f, desc in CORE_FIELDS.items())
    raw = _GENERATE(_build_prompt(fields_block, text), SYSTEM_PROMPT)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    con.execute("DELETE FROM lease_abstract WHERE tenant_id=? AND source_page LIKE ?",
                (tenant_id, f"[{model}]%"))
    n = 0
    for field, obj in data.items():
        if field not in CORE_FIELDS or not isinstance(obj, dict):
            continue
        val = (obj.get("value") or "").strip()
        if not val:
            continue
        src = f"[{model}] {(obj.get('source') or '').strip()}"[:120]
        conf = (obj.get("confidence") or "medium").strip().lower()
        con.execute(
            "INSERT INTO lease_abstract(tenant_id, field, value, source_page, confidence) "
            "VALUES(?,?,?,?,?)", (tenant_id, field, val, src, conf))
        n += 1
    con.commit()
    return n


def run(db_path=DEFAULT_DB, only=None, model=OLLAMA_MODEL):
    con = sqlite3.connect(db_path)
    q = "SELECT tenant_id, name FROM lease_tenant"
    params = []
    if only:
        q += " WHERE name LIKE ?"
        params = [f"%{only}%"]
    tenants = con.execute(q + " ORDER BY tenant_id", params).fetchall()
    print(f"Abstracting {len(tenants)} tenant(s) with {model}")
    total = 0
    for tid, name in tenants:
        try:
            n = abstract_tenant(con, tid, model)
        except Exception as e:
            print(f"  ! {name}: {str(e)[:60]}")
            continue
        total += n
        print(f"  {name:24} {n:>2} facts", flush=True)
    con.close()
    print(f"\nExtracted {total} facts across {len(tenants)} tenants.")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--only", default=None)
    ap.add_argument("--model", default=OLLAMA_MODEL)
    args = ap.parse_args()
    print(f"Extraction engine: {args.model} @ {OLLAMA_URL} (num_ctx={NUM_CTX})")
    run(args.db, args.only, args.model)
