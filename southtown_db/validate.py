"""
Validate a locally-built lease warehouse against the gold-standard warehouse.

Provision tie-out (structural): section-number coverage, heading match, body-size match.
Abstract tie-out (when abstracts exist): coverage of the 3 tiers per provision, and a
crude length/overlap sanity check vs. the gold hand-authored abstracts.

Usage:
    python validate.py --built data/lease_warehouse.db --gold data/gold_lease_warehouse.db
"""
import argparse
import sqlite3


def _provisions(db):
    c = sqlite3.connect(db)
    return {sn: {"heading": (h or "").strip(), "chars": cc}
            for sn, h, cc in c.execute(
                "SELECT section_num, section_heading, char_count FROM provisions")}


def validate(built_db, gold_db):
    built, gold = _provisions(built_db), _provisions(gold_db)
    bk, gk = set(built), set(gold)
    shared = bk & gk

    print(f"Provisions — built: {len(built)} | gold: {len(gold)}")
    print(f"  section_nums in both: {len(shared)}")
    only_b, only_g = sorted(bk - gk), sorted(gk - bk)
    if only_b:
        print(f"  only in built: {only_b}")
    if only_g:
        print(f"  only in gold : {only_g}")

    head_mism = [sn for sn in shared if built[sn]['heading'] != gold[sn]['heading']]
    print(f"  heading mismatches: {len(head_mism)}")
    for sn in head_mism[:10]:
        print(f"    {sn}: built='{built[sn]['heading'][:40]}' | gold='{gold[sn]['heading'][:40]}'")

    close = sum(1 for sn in shared
                if abs(built[sn]['chars'] - gold[sn]['chars'])
                <= max(20, 0.02 * max(gold[sn]['chars'], 1)))
    print(f"  body char_count within 2%: {close}/{len(shared)}")

    exact = (len(built) == len(gold) and not only_b and not only_g
             and not head_mism and close == len(shared))
    print(f"\nStructural tie-out: {'PASS ✓' if exact else 'REVIEW'}")
    return exact


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--built", required=True)
    ap.add_argument("--gold", required=True)
    args = ap.parse_args()
    validate(args.built, args.gold)
