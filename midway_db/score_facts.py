"""
Score locally-extracted lease-abstract facts against the partner's gold facts.

The gold uses freeform, deal-specific field names, so we map our CORE fields to
gold synonyms and report, per tenant:
  - coverage : how many core fields we populated
  - fidelity : for the subset where a comparable gold value exists, the overlap of
               hard tokens/figures (entity names, $, dates, SF) between ours and gold

This is a fuzzy, honest read (gold also carries an analytical/flag layer we don't
target). It answers: is the local model pulling the right standard facts?

Usage:
    python score_facts.py --built data/midway.db --gold data/gold_midway_psa.db
"""
import argparse
import re
import sqlite3
from collections import defaultdict

# core field -> gold field-name synonyms
SYNONYMS = {
    "instrument_type": ["instrument_type"],
    "tenant_entity": ["current_tenant", "tenant_entity_verified", "original_predecessors"],
    "landlord": ["landlord_at_estoppel", "current_landlord", "ownership_chain_landlord"],
    "premises_address": ["premises_address", "premises"],
    "premises_sf": ["premises", "scope"],
    "lease_date": ["commencement", "original_term"],
    "term_expiration": ["term_expiration", "base_expiration", "current_term",
                        "second_amendment_term"],
    "base_rent": ["base_rent_current", "base_rent_per_estoppel", "monthly_rent_2019"],
    "renewal_options": ["renewal_options", "current_renewal", "remaining_options"],
    "permitted_use": ["scope", "use"],
    "assignment_status": ["assignment_subletting"],
    "security_deposit": ["security_deposit"],
    "notice_address_tenant": ["notice_address_tenant"],
}

_MONEY = re.compile(r"\$[\d,]+(?:\.\d+)?")
_PCT = re.compile(r"\d+(?:\.\d+)?\s?%")
_SF = re.compile(r"\d[\d,]*\s?(?:sf|sq)")
_DNUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_DNAME = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b")
_STOP = set("the a an and or of to in for on with as is are be by that this at llc inc "
            "shall such any all not no".split())


def _canon_dates(t):
    """Normalize both 9/3/2004 and 'September 3, 2004' to 2004-09-03 so formats match."""
    out = set()
    for mo, d, y in _DNUM.findall(t or ""):
        y = int(y); y = 2000 + y if y < 100 else y
        out.add(f"{y:04d}-{int(mo):02d}-{int(d):02d}")
    for mn, d, y in _DNAME.findall((t or "").lower()):
        out.add(f"{int(y):04d}-{_MONTHS[mn]:02d}-{int(d):02d}")
    return out


def _figs(t):
    t = t or ""
    s = {m.group().lower().replace(" ", "") for r in (_MONEY, _PCT, _SF) for m in r.finditer(t)}
    return s | _canon_dates(t)


def _toks(t):
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower())
            if w not in _STOP and len(w) > 2}


def _overlap(a, b):
    fa, fb = _figs(a), _figs(b)
    ta, tb = _toks(a), _toks(b)
    fig = len(fa & fb) / len(fa) if fa else None
    tok = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return fig, tok


def _norm(n):
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())


def _facts(db, engine_tagged):
    """Return {norm_tenant_name: {field: value}} and {norm_name: display_name}."""
    c = sqlite3.connect(db)
    names = dict(c.execute("SELECT tenant_id, name FROM lease_tenant"))
    out = defaultdict(dict)
    disp = {}
    for tid, field, value, src in c.execute(
            "SELECT tenant_id, field, value, source_page FROM lease_abstract"):
        is_engine = (src or "").startswith("[")
        if engine_tagged and not is_engine:
            continue
        if not engine_tagged and is_engine:
            continue
        nm = _norm(names.get(tid, ""))
        disp[nm] = names.get(tid, nm)
        out[nm].setdefault(field, value)
    return out, disp


def score(built_db, gold_db):
    built, disp = _facts(built_db, engine_tagged=True)
    gold, _ = _facts(gold_db, engine_tagged=False)   # keyed by normalized tenant name

    per_tenant = defaultdict(lambda: {"extracted": 0, "comparable": 0, "figs": [], "toks": []})
    for nm, fields in built.items():
        gold_fields = gold.get(nm, {})
        for field, val in fields.items():
            if field not in SYNONYMS:
                continue
            d = per_tenant[nm]
            d["extracted"] += 1
            gval = next((gold_fields[g] for g in SYNONYMS[field] if g in gold_fields), None)
            if gval:
                d["comparable"] += 1
                fig, tok = _overlap(val, gval)
                if fig is not None:
                    d["figs"].append(fig)
                d["toks"].append(tok)

    print(f"{'tenant':22} {'core facts':>11} {'comparable':>11} {'fig recall':>11} {'tok overlap':>12}")
    tot_fig, tot_tok = [], []
    for nm in sorted(per_tenant, key=lambda n: disp.get(n, n)):
        d = per_tenant[nm]
        fig = sum(d["figs"]) / len(d["figs"]) if d["figs"] else None
        tok = sum(d["toks"]) / len(d["toks"]) if d["toks"] else None
        tot_fig += d["figs"]; tot_tok += d["toks"]
        name = disp.get(nm, nm)[:22]
        figs = f"{fig:.0%}" if fig is not None else "—"
        toks = f"{tok:.0%}" if tok is not None else "—"
        print(f"{name:22} {d['extracted']:>11} {d['comparable']:>11} {figs:>11} {toks:>12}")
    if tot_fig:
        print(f"\nOverall figure recall vs gold: {sum(tot_fig)/len(tot_fig):.0%} "
              f"| token overlap: {sum(tot_tok)/len(tot_tok):.0%}")
    print("\nNote: gold also carries an analytical/flag layer (reconciliations, "
          "discrepancy flags, ownership chains) that this core extraction does not target.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--built", default="data/midway.db")
    ap.add_argument("--gold", default="data/gold_midway_psa.db")
    args = ap.parse_args()
    score(args.built, args.gold)
