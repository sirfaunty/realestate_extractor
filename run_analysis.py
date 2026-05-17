#!/usr/bin/env python3
"""
Run Phase 2 analysis on a property.

Usage:
    cd ~/Desktop/realestate_extractor
    python3 run_analysis.py                    # lists available properties
    python3 run_analysis.py --property-id 1    # analyze property 1
    python3 run_analysis.py --all              # analyze all properties

Requires Ollama running with llama3.1:8b for LLM gap-fill.
Rule-based extraction + columnar parser work without Ollama.
"""
import sys
import os
import json
import time
import argparse

# Ensure the package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realestate_extractor.database import Database
from realestate_extractor.property_analyzer import PropertyAnalyzer

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'org_dev.db')


def list_properties(db):
    """List all properties and their document counts."""
    rows = db.conn.execute("""
        SELECT p.id, p.name,
               COUNT(d.id) as doc_count,
               p.address
        FROM properties p
        LEFT JOIN documents d ON d.property_id = p.id
        GROUP BY p.id
        ORDER BY p.id
    """).fetchall()
    if not rows:
        print("No properties found in database.")
        return []
    print(f"\n{'ID':>4}  {'Property Name':<40} {'Docs':>5}  {'Address'}")
    print("-" * 80)
    for r in rows:
        print(f"{r[0]:>4}  {r[1] or '(unnamed)':<40} {r[2]:>5}  {r[3] or ''}")
    return rows


def analyze_property(db, property_id):
    """Run Phase 2 analysis on a single property."""
    # Get property info
    prop = db.conn.execute(
        "SELECT id, name FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    if not prop:
        print(f"Error: Property ID {property_id} not found.")
        return

    prop_name = prop[1] or f"Property #{prop[0]}"

    # Clear previous extraction data for this property
    print(f"Clearing previous extraction data for {prop_name}...")
    doc_ids = [r[0] for r in db.conn.execute(
        "SELECT id FROM documents WHERE property_id = ?", (property_id,)
    ).fetchall()]

    if doc_ids:
        placeholders = ','.join('?' * len(doc_ids))
        for table in ['clauses', 'financial_terms', 'rent_roll_entries',
                       'operating_statement_items', 'gl_entries']:
            db.conn.execute(
                f"DELETE FROM {table} WHERE document_id IN ({placeholders})",
                doc_ids
            )
        db.conn.commit()
    print("Done.\n")

    analyzer = PropertyAnalyzer(db)

    # Progress callback
    def on_step(event, detail):
        ts = time.strftime('%H:%M:%S')
        print(f"  [{ts}] [{event}] {detail}")

    analyzer._on_step = on_step

    total = db.conn.execute(
        "SELECT COUNT(*) FROM documents WHERE property_id = ?",
        (property_id,)
    ).fetchone()[0]
    print(f"Starting Phase 2 analysis on {prop_name} — {total} documents")
    print("=" * 60)

    start = time.time()
    summary = analyzer.analyze_property(property_id)
    elapsed = time.time() - start

    print(f"\n{'=' * 60}")
    print(f"ANALYSIS COMPLETE — {prop_name} — {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"{'=' * 60}")
    print(json.dumps(summary, indent=2, default=str))

    # Quick stats for this property
    if doc_ids:
        placeholders = ','.join('?' * len(doc_ids))
        stats = {}
        for table in ['clauses', 'financial_terms', 'rent_roll_entries',
                       'operating_statement_items', 'gl_entries']:
            count = db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE document_id IN ({placeholders})",
                doc_ids
            ).fetchone()[0]
            stats[table] = count
        print(f"\nExtraction totals for {prop_name}:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Run Phase 2 analysis on property documents"
    )
    parser.add_argument('--property-id', '-p', type=int,
                        help='Property ID to analyze')
    parser.add_argument('--all', action='store_true',
                        help='Analyze all properties')
    args = parser.parse_args()

    db = Database(DB_PATH)
    db.connect()

    if args.all:
        rows = db.conn.execute(
            "SELECT id, name FROM properties ORDER BY id"
        ).fetchall()
        if not rows:
            print("No properties found.")
            db.close()
            return
        print(f"Analyzing {len(rows)} properties...\n")
        for prop_id, prop_name in rows:
            analyze_property(db, prop_id)
            print()
    elif args.property_id:
        analyze_property(db, args.property_id)
    else:
        # No arguments — list properties and prompt
        print("No property specified. Available properties:")
        rows = list_properties(db)
        if rows:
            print(f"\nUsage: python3 run_analysis.py --property-id <ID>")
            print(f"       python3 run_analysis.py --all")

    db.close()


if __name__ == '__main__':
    main()
