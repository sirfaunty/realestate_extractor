"""
Local provision-abstraction engine.

Runs each provision in the lease warehouse through the local Ollama model to
generate three abstract tiers, replacing the partner's hand-authoring with a
repeatable, on-device pipeline. Nothing leaves the machine.

Same local model/endpoint Capactive uses (llama3.1:8b @ localhost:11434),
overridable via CAPACTIVE_OLLAMA_MODEL / CAPACTIVE_OLLAMA_URL. Uses a direct
Ollama call so we can set a large context window for dense legal text.

The three tiers (matching the gold schema):
  - detailed          : dense restatement; preserves figures, defined terms,
                        cross-references; cites the section number.
  - detailed_summary  : plain-language paragraph (3-5 sentences).
  - abstract_summary  : one sentence.

Interpretive rule (Riley's standing instruction): ONLY the lease language governs.
Cite the section. Do not invent. Flag ambiguity rather than resolving it.

Usage (run locally, with Ollama serving):
    python abstract_lease.py --db data/lease_warehouse.db
    python abstract_lease.py --db data/lease_warehouse.db --limit 5      # test a few
    python abstract_lease.py --db data/lease_warehouse.db --only 1.6     # one provision
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

OLLAMA_URL = os.environ.get("CAPACTIVE_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("CAPACTIVE_OLLAMA_MODEL", "llama3.1:8b")
NUM_CTX = int(os.environ.get("SOUTHTOWN_NUM_CTX", "8192"))
NUM_PREDICT = int(os.environ.get("SOUTHTOWN_NUM_PREDICT", "2048"))  # max output tokens
TIMEOUT = int(os.environ.get("SOUTHTOWN_TIMEOUT", "300"))  # per-call read timeout (s)
MAX_BODY_CHARS = 22000  # keep prompt within context; provisions rarely exceed this

SYSTEM_PROMPT = (
    "You are a commercial real-estate attorney abstracting a single provision of an "
    "executed lease for the landlord's investment team. Strict rules:\n"
    "1. ONLY the lease language provided governs. Do not use outside knowledge or "
    "assume market-standard terms.\n"
    "2. Preserve EVERY hard figure exactly as written — dollar amounts, percentages, "
    "square footage, dates, and notice/cure/time periods — and keep defined terms and "
    "their capitalization. Dropping a figure is the worst possible error.\n"
    "3. Always reference the section number you were given.\n"
    "4. Do not invent facts. If the language is ambiguous or a term is defined "
    "elsewhere, say so explicitly rather than guessing.\n"
    "5. Be COMPLETE: enumerate EVERY option, election, or remedy each party has, "
    "and state the rent or timing consequence of each — in particular, if a party "
    "may open and pay a reduced or substitute rent, or delay/terminate, capture "
    "each such path and its trigger. Do not collapse multiple options into one.\n"
    "6. Capture EVERY termination, cure, or notice right with its exact time period.\n"
    "7. Write like a dense deal memo — no verbatim legalese, no filler — but "
    "COMPLETENESS ALWAYS WINS OVER BREVITY: never drop a figure, defined term, "
    "baseline, carve-out, or option to save space. Lead each tier with the "
    "provision's single most important figure or threshold when one exists."
)

USER_TEMPLATE = """Abstract the following lease provision at three levels of detail.

SECTION: {section_num}  ({article_title})
HEADING: {section_heading}

PROVISION TEXT:
\"\"\"
{body}
\"\"\"

Return ONLY a JSON object with exactly these three string keys:
- "detailed": a dense memo-style restatement that preserves EVERY figure, defined
  term, baseline, carve-out, and cross-reference, cites the section, and enumerates
  each party option/remedy as (i), (ii), ... with its rent and timing consequence.
  Lead with the key figure/threshold. Completeness over brevity; no verbatim legalese.
- "detailed_summary": 2-4 sentences capturing the operative mechanics and every
  option, and stating the ACTUAL key figures and timeframes (the %, the SF baseline,
  the exact month/day periods) — never write vague phrases like "specified timeframes".
- "abstract_summary": ONE sentence (~25 words max) that LEADS WITH the single most
  important figure or threshold (a %, dollar amount, SF, or time period) when one
  exists, then states the essence.

If the provision has no operative content (e.g., a bare heading), say so in each field."""


def ollama_generate(prompt, system, url=OLLAMA_URL, model=OLLAMA_MODEL,
                    num_ctx=NUM_CTX, temperature=0.1, timeout=None, retries=1):
    """Direct Ollama /api/generate call, JSON-formatted response. Overridable for tests.
    Retries once (with a longer timeout) on read-timeout — dense provisions can be slow."""
    import requests
    timeout = timeout or TIMEOUT
    payload = {
        "model": model, "prompt": prompt, "system": system, "stream": False,
        "format": "json",
        "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": NUM_PREDICT},
    }
    last = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(f"{url}/api/generate", json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.exceptions.Timeout as e:
            last = e
            timeout = int(timeout * 1.5)  # give a slow provision more room on retry
    raise last


# Indirection so tests can inject a fake generator.
_GENERATE = ollama_generate

TIERS = ("detailed", "detailed_summary", "abstract_summary")


def parse_tiers(raw):
    """Parse the model's JSON into the 3 tiers. Falls back to a tolerant field
    extraction if the JSON is truncated (e.g., output hit the token cap)."""
    import re
    try:
        data = json.loads(raw)
        return {t: (data.get(t) or "").strip() for t in TIERS}
    except json.JSONDecodeError:
        pass
    out = {}
    for t in TIERS:
        # Grab the field value up to the next closing quote+comma/brace, or to the
        # end of the string if the response was cut off mid-value.
        m = re.search(r'"' + t + r'"\s*:\s*"(.*?)"\s*(?:,|\})', raw, re.S) \
            or re.search(r'"' + t + r'"\s*:\s*"(.*)$', raw, re.S)
        if m:
            val = m.group(1).rstrip().rstrip('"')
            out[t] = val.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t').strip()
    if not out:
        raise ValueError("could not parse any tier from model response")
    for t in TIERS:
        out.setdefault(t, "")
    return out


def _pick_num_ctx(prompt, system):
    """Smallest context window (capped at NUM_CTX) that fits input + max output.
    Most provisions are small, so they run at 4096 and fit fully on GPU — avoiding
    the CPU offload (and the resulting slowdown) that a fixed 8192 causes."""
    est = int((len(prompt) + len(system)) / 3.5) + NUM_PREDICT + 256
    for c in (4096, 8192):
        if c >= NUM_CTX:
            return NUM_CTX
        if est <= c:
            return c
    return NUM_CTX


def abstract_provision(row):
    body = row["body"] or ""
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n[... provision truncated for length ...]"
    prompt = USER_TEMPLATE.format(
        section_num=row["section_num"], article_title=row["article_title"] or "",
        section_heading=row["section_heading"] or "", body=body)
    raw = _GENERATE(prompt, SYSTEM_PROMPT, num_ctx=_pick_num_ctx(prompt, SYSTEM_PROMPT))
    return parse_tiers(raw)


def run(db_path, model=OLLAMA_MODEL, limit=None, only=None, missing=False, progress_cb=None):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    q = "SELECT * FROM provisions"
    params = []
    where = []
    if only:
        where.append("section_num = ?")
        params.append(only)
    if missing:
        # only provisions that don't yet have all 3 abstract tiers for this engine
        where.append("(SELECT COUNT(*) FROM abstracts a "
                     "WHERE a.provision_id = provisions.id AND a.engine = ?) < 3")
        params.append(model)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY seq"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = cur.execute(q, params).fetchall()

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    ok, failed = 0, []
    for i, row in enumerate(rows, 1):
        sn = row["section_num"]
        if progress_cb:
            progress_cb(i, len(rows), sn, row["section_heading"])
        print(f"  [{i}/{len(rows)}] {sn} {row['section_heading'][:48]!r} ...", flush=True)
        try:
            tiers = abstract_provision(row)
        except Exception as e:                       # noqa: BLE001 (log + continue)
            failed.append((sn, str(e)[:80]))
            print(f"      ! failed: {str(e)[:80]}", flush=True)
            continue
        cur.execute("DELETE FROM abstracts WHERE provision_id = ? AND engine = ?",
                    (row["id"], model))
        for t in TIERS:
            cur.execute(
                "INSERT INTO abstracts(provision_id, abstract_type, content, engine, created_at) "
                "VALUES(?,?,?,?,?)", (row["id"], t, tiers[t], model, now))
        con.commit()
        ok += 1

    total = con.execute(
        "SELECT COUNT(DISTINCT provision_id) FROM abstracts WHERE engine = ?",
        (model,)).fetchone()[0]
    con.close()
    print(f"\nAbstracted {ok}/{len(rows)} provisions with {model} "
          f"(warehouse now covers {total}). Failures: {len(failed)}")
    for sn, err in failed:
        print(f"  - {sn}: {err}")
    return {"ok": ok, "attempted": len(rows), "failed": failed, "covered": total}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--model", default=OLLAMA_MODEL)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="Single section_num, e.g. 1.6")
    ap.add_argument("--missing", action="store_true",
                    help="Only abstract provisions still missing abstracts for this model")
    args = ap.parse_args()
    print(f"Abstraction engine: {args.model} @ {OLLAMA_URL} "
          f"(num_ctx={NUM_CTX}, timeout={TIMEOUT}s)")
    res = run(args.db, model=args.model, limit=args.limit, only=args.only,
              missing=args.missing)
    sys.exit(0 if res["ok"] else 1)
