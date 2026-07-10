"""
Midway P3 — missing-document detection (deterministic, local).

The core diligence gap at Midway: every tenant package received is the CERTIFICATION
layer (estoppel, SNDA, NERS/checklist, correspondence) but NOT the executed lease and
amendments. This scans the warehouse and flags, per tenant, the executed instrument(s)
still owed — the auto-detectable half of the partner's missing-document tracker.

The analytical confirmations in the gold tracker ("confirm Shopko co-tenancy",
"reconcile renewal count") are human-judgment items and are out of scope here.

Writes rows into `missing_document`. Usage:
    python missing_docs.py                    # scan data/midway.db, print + record gaps
"""
import argparse
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_HERE, "data", "midway.db")

# Roles that certify a lease exists but are not the executed instrument itself.
CERTIFICATION_ROLES = {"estoppel", "snda", "checklist_ner", "tenant_approval_sos",
                       "sale_letter", "correspondence", "vendor_setup_packet"}
# The operative executed instrument for a tenant (a lease bundle, or, for a parking
# operator, the executed services agreement itself).
EXECUTED_ROLES = {"lease_bundle", "parking_services_agreement"}


def _instrument(con, tenant_id):
    r = con.execute(
        "SELECT value FROM lease_abstract WHERE tenant_id=? AND field='instrument_type' "
        "ORDER BY abstract_id LIMIT 1", (tenant_id,)).fetchone()
    return (r[0] if r else "Lease").strip() or "Lease"


def run(db_path=DEFAULT_DB):
    con = sqlite3.connect(db_path)
    tenants = con.execute("SELECT tenant_id, name FROM lease_tenant ORDER BY name").fetchall()
    flagged = []
    for tid, name in tenants:
        roles = {r[0] for r in con.execute(
            "SELECT DISTINCT doc_role FROM lease_document_file WHERE tenant_id=?", (tid,))}
        n_extracted = con.execute(
            "SELECT COUNT(*) FROM lease_document_file WHERE tenant_id=? AND extracted=1",
            (tid,)).fetchone()[0]
        has_cert = bool(roles & CERTIFICATION_ROLES)
        has_executed = bool(roles & EXECUTED_ROLES)

        if not roles or n_extracted == 0:
            flagged.append((f"{name} — no readable documents provided yet", name,
                            "No tenant documents present/extracted; request the file.",
                            "auto: gap analysis", "high"))
        elif has_cert and not has_executed:
            inst = _instrument(con, tid)
            flagged.append((
                f"{name} — executed {inst} + all amendments",
                name,
                "Abstracts rest on the certification layer (estoppel/SNDA/checklist) "
                "only; the executed instrument + amendments are needed for gold-standard "
                "terms and the current post-amendment state.",
                "auto: gap analysis (has certification, no executed lease_bundle)",
                "high"))

    now_cols = [c[1] for c in con.execute("PRAGMA table_info(missing_document)")]
    con.execute("DELETE FROM missing_document WHERE source_or_where LIKE 'auto:%'")
    for item, related, why, src, prio in flagged:
        con.execute(
            "INSERT INTO missing_document(item, related_to, why_needed, source_or_where, "
            "priority, status) VALUES(?,?,?,?,?,'open')", (item, related, why, src, prio))
    con.commit()

    print(f"Missing-document gaps detected: {len(flagged)}")
    for item, related, _why, _src, prio in flagged:
        print(f"  [{prio:6}] {item}")
    con.close()
    return flagged


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    run(args.db)
