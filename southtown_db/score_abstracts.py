"""
Score locally-generated abstracts against the gold hand-authored abstracts.

Honest, quantitative read on how close the local model gets. Metrics per tier:
  - coverage      : how many provisions got an abstract
  - figure_recall : fraction of hard figures ($, %, SF, dates, defined-term numbers)
                    in the GOLD abstract that also appear in the local one. This is
                    the metric that matters most for legal abstraction — are the
                    numbers preserved?
  - token_jaccard : lexical overlap of content words (rough semantic proxy)
  - len_ratio     : local length / gold length (sanity on verbosity)

Usage:
    python score_abstracts.py --built data/lease_warehouse.db \
        --gold data/gold_lease_warehouse.db --model llama3.1:8b
"""
import argparse
import re
import sqlite3
from collections import defaultdict

TIERS = ("detailed", "detailed_summary", "abstract_summary")

_FIGURE = re.compile(
    r"\$[\d,]+(?:\.\d+)?|"                       # dollars
    r"\d+(?:\.\d+)?\s?%|"                         # percents
    r"\d[\d,]*\s?(?:SF|sf|square feet|sq\.?\s?ft)|"  # square footage
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|"              # dates
    r"\b(?:19|20)\d{2}\b|"                        # years
    r"\b\d+\s?(?:days?|months?|years?)\b"         # periods
)
_STOP = set("the a an and or of to in for on with as is are be by that this shall "
            "such any all not no if then may will each per its it".split())


def _figures(text):
    return {m.group().lower().replace(" ", "") for m in _FIGURE.finditer(text or "")}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOP and len(w) > 2}


def _load(db, model=None):
    """Return {(section_num, tier): content}. If model given, only that engine."""
    c = sqlite3.connect(db)
    has_engine = any(r[1] == "engine" for r in c.execute("PRAGMA table_info(abstracts)"))
    q = ("SELECT p.section_num, a.abstract_type, a.content "
         "FROM abstracts a JOIN provisions p ON p.id = a.provision_id")
    params = []
    if model and has_engine:
        q += " WHERE a.engine = ?"
        params.append(model)
    out = {}
    for sn, atype, content in c.execute(q, params):
        out[(sn, atype)] = content
    return out


def score(built_db, gold_db, model):
    built = _load(built_db, model=model)
    gold = _load(gold_db)  # gold DB has no engine column; take all

    per_tier = defaultdict(lambda: {"n": 0, "fig_r": [], "jac": [], "lr": []})
    for (sn, tier), g in gold.items():
        if tier not in TIERS:
            continue
        b = built.get((sn, tier))
        d = per_tier[tier]
        if not b:
            continue
        d["n"] += 1
        gf, bf = _figures(g), _figures(b)
        d["fig_r"].append(len(gf & bf) / len(gf) if gf else 1.0)
        gt, bt = _tokens(g), _tokens(b)
        d["jac"].append(len(gt & bt) / len(gt | bt) if (gt | bt) else 0.0)
        d["lr"].append(len(b) / max(len(g), 1))

    gold_provs = len({sn for (sn, t) in gold})
    print(f"Model: {model}")
    print(f"Gold provisions: {gold_provs}\n")
    print(f"{'tier':18} {'coverage':>10} {'figure_recall':>14} {'token_jaccard':>14} {'len_ratio':>10}")
    for tier in TIERS:
        d = per_tier[tier]
        if not d["n"]:
            print(f"{tier:18} {'0':>10} {'—':>14} {'—':>14} {'—':>10}")
            continue
        avg = lambda xs: sum(xs) / len(xs)
        print(f"{tier:18} {f'{d[chr(110)]}/{gold_provs}':>10} "
              f"{avg(d['fig_r']):>13.0%} {avg(d['jac']):>13.0%} {avg(d['lr']):>9.2f}x")
    print("\nfigure_recall is the key metric: fraction of gold's hard figures "
          "($, %, SF, dates, periods) preserved in the local abstract.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--built", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--model", default="llama3.1:8b")
    args = ap.parse_args()
    score(args.built, args.gold, args.model)
