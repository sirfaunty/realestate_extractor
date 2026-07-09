"""
Build the Southtown lease-abstraction warehouse locally from source documents.

Pipeline (all on-device):
  1. Segment the lease .docx into provisions (segment_lease.segment_docx) — deterministic.
  2. Create the warehouse DB from schema.sql.
  3. Insert the document, provisions, and the full-text-search index.

Abstraction (the 3-tier provision summaries) is a separate step handled by
abstract_lease.py, which runs each provision through the local Ollama engine.

Usage:
    python build_warehouse.py \
        --lease "source_docs/lease_and_exhibits/DHOS Lease 122225.docx" \
        --db data/lease_warehouse.db
"""
import argparse
import datetime
import hashlib
import os
import sqlite3

from segment_lease import segment_docx

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def build(lease_path, db_path, doc_code="DHOS-LEASE",
          doc_title="Dick's House of Sport Lease"):
    provisions = segment_docx(lease_path)

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    with open(os.path.join(_HERE, "schema.sql"), encoding="utf-8") as f:
        cur.executescript(f.read())

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cur.execute(
        "INSERT INTO source_files(filename, sha256, byte_size, doc_type, ingested_at) "
        "VALUES(?,?,?,?,?)",
        (os.path.basename(lease_path), _sha256(lease_path),
         os.path.getsize(lease_path), "lease_body", now))
    source_file_id = cur.lastrowid

    total_chars = sum(p['char_count'] for p in provisions)
    cur.execute(
        "INSERT INTO documents(doc_code, title, doc_class, source_file_id, char_count) "
        "VALUES(?,?,?,?,?)",
        (doc_code, doc_title, "lease_body", source_file_id, total_chars))
    document_id = cur.lastrowid

    for p in provisions:
        cur.execute(
            """INSERT INTO provisions(document_id, seq, article_num, article_roman,
                 article_title, section_num, section_heading, body, char_count, start_para_idx)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (document_id, p['seq'], p['article_num'], p['article_roman'],
             p.get('article_title'), p['section_num'], p['section_heading'],
             p['body'], p['char_count'], p.get('start_idx')))
        pid = cur.lastrowid
        cur.execute(
            "INSERT INTO provisions_fts(rowid, section_num, section_heading, body) "
            "VALUES(?,?,?,?)",
            (pid, p['section_num'], p['section_heading'], p['body']))

    con.commit()
    n = cur.execute("SELECT COUNT(*) FROM provisions").fetchone()[0]
    con.close()
    return {"db": db_path, "provisions": n, "body_chars": total_chars}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lease", required=True, help="Path to the lease .docx")
    ap.add_argument("--db", default=os.path.join(_HERE, "data", "lease_warehouse.db"))
    args = ap.parse_args()
    report = build(args.lease, args.db)
    print(f"Built {report['db']}: {report['provisions']} provisions, "
          f"{report['body_chars']:,} body chars")
