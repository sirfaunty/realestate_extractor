#!/usr/bin/env python3
"""
Run Phase 2 analysis on a property.

Usage:
    cd ~/Desktop/realestate_extractor
    python3 run_analysis.py --property-id 1           # full re-run (clears + re-extracts all)
    python3 run_analysis.py --property-id 1 --smart   # only re-process docs that need it
    python3 run_analysis.py --property-id 1 --assess  # show extraction health report (no changes)
    python3 run_analysis.py --property-id 1 --new-only  # only process never-analyzed docs
    python3 run_analysis.py --property-id 1 --doc-type closing loan  # filter to specific types
    python3 run_analysis.py --all                      # full re-run on all properties
    python3 run_analysis.py --property-id 1 --reconcile  # cross-document term reconciliation

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
from realestate_extractor.extractors.extraction_engine import DocumentClassifier
from realestate_extractor.templates.document_templates import TEMPLATES

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'org_dev.db')

# Doc types that should produce extraction results
EXTRACTABLE_TYPES = DocumentClassifier.EXTRACTABLE_TYPES
OS_TYPES = {'operating_statement'}
# Types that are expected to produce no structured extraction
NO_EXTRACT_TYPES = {
    'organizational', 'reference',
    'correspondence', 'due_diligence', 'unknown',
}


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


def assess_doc(db, doc):
    """
    Assess a single document's extraction health.

    Returns (status, reason) where status is one of:
        'good'       — has extraction results, no issues detected
        'needs_rerun' — should be re-processed (with reason)
        'new'        — never analyzed
        'skip'       — no extraction expected for this type
    """
    doc_id = doc['id']
    doc_type = doc['document_type']
    analysis_status = doc['analysis_status']
    filename = doc['filename']

    # Parse classification confidence
    conf = 1.0
    if doc.get('metadata'):
        try:
            meta = json.loads(doc['metadata']) if isinstance(doc['metadata'], str) else doc['metadata']
            conf = meta.get('classification_confidence', 1.0)
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Never analyzed ──
    if analysis_status != 'analyzed':
        return 'new', 'Not yet analyzed'

    # ── Type with no extraction expected ──
    if doc_type in NO_EXTRACT_TYPES:
        return 'skip', f'No extraction for {doc_type}'

    # ── Check extraction results ──
    term_count = db.conn.execute(
        "SELECT COUNT(*) FROM financial_terms WHERE document_id = ?",
        (doc_id,)
    ).fetchone()[0]
    clause_count = db.conn.execute(
        "SELECT COUNT(*) FROM clauses WHERE document_id = ?",
        (doc_id,)
    ).fetchone()[0]
    os_count = db.conn.execute(
        "SELECT COUNT(*) FROM operating_statement_items WHERE document_id = ?",
        (doc_id,)
    ).fetchone()[0]
    rr_count = db.conn.execute(
        "SELECT COUNT(*) FROM rent_roll_entries WHERE document_id = ?",
        (doc_id,)
    ).fetchone()[0]
    total_extracted = term_count + clause_count + os_count + rr_count

    # ── Operating statements should have OS items ──
    if doc_type in OS_TYPES:
        if os_count > 0:
            return 'good', f'{os_count} OS items'
        if conf < 0.5:
            return 'skip', f'Low-confidence OS ({conf:.0%}), likely misclassified'
        return 'needs_rerun', 'Operating statement with 0 items'

    # ── Rent roll should have entries ──
    if doc_type == 'rent_roll':
        if rr_count > 0:
            return 'good', f'{rr_count} rent roll entries'
        if conf < 0.5:
            return 'skip', f'Low-confidence rent roll ({conf:.0%}), likely misclassified'
        return 'needs_rerun', 'Rent roll with 0 entries'

    # ── General ledger ──
    if doc_type == 'general_ledger':
        gl_count = db.conn.execute(
            "SELECT COUNT(*) FROM gl_entries WHERE document_id = ?",
            (doc_id,)
        ).fetchone()[0]
        if gl_count > 0:
            return 'good', f'{gl_count} GL entries'
        if conf < 0.5:
            return 'skip', f'Low-confidence GL ({conf:.0%}), likely misclassified'
        return 'needs_rerun', 'General ledger with 0 entries'

    # ── Extractable types (loan, closing, hud_form, proforma, etc.) ──
    if doc_type in EXTRACTABLE_TYPES:
        if total_extracted > 0:
            parts = []
            if term_count: parts.append(f'{term_count} terms')
            if clause_count: parts.append(f'{clause_count} clauses')
            return 'good', ', '.join(parts)
        # Already analyzed with 0 results + low confidence → skip
        # BUT: if a dedicated template exists, always re-run — we know
        # how to extract from this type regardless of classifier confidence.
        has_template = doc_type in TEMPLATES
        if conf < 0.5 and not has_template:
            return 'skip', f'Low-confidence {doc_type} ({conf:.0%}), likely misclassified'
        return 'needs_rerun', f'{doc_type} with 0 extraction results'

    # ── Fallback: unknown type ──
    if total_extracted > 0:
        return 'good', f'{total_extracted} total extracted'
    return 'skip', f'No extraction template for {doc_type}'


def print_assessment(db, property_id):
    """Print a detailed extraction health report for a property."""
    prop = db.conn.execute(
        "SELECT id, name FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    if not prop:
        print(f"Error: Property ID {property_id} not found.")
        return

    prop_name = prop[1] or f"Property #{prop[0]}"
    docs = db.conn.execute(
        "SELECT * FROM documents WHERE property_id = ? ORDER BY document_type, id",
        (property_id,)
    ).fetchall()
    docs = [dict(d) for d in docs]

    if not docs:
        print(f"No documents for {prop_name}.")
        return

    # Assess each doc
    results = []
    for doc in docs:
        status, reason = assess_doc(db, doc)
        results.append((doc, status, reason))

    # Count by status
    counts = {}
    for _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1

    print(f"\n{'='*80}")
    print(f"EXTRACTION HEALTH REPORT — {prop_name} ({len(docs)} documents)")
    print(f"{'='*80}")
    print(f"  Good:        {counts.get('good', 0):3d}  (extraction results look solid)")
    print(f"  Needs rerun: {counts.get('needs_rerun', 0):3d}  (should be re-processed)")
    print(f"  New:         {counts.get('new', 0):3d}  (never analyzed)")
    print(f"  Skip:        {counts.get('skip', 0):3d}  (no extraction expected)")
    print()

    # Show details grouped by status
    for show_status, header, color in [
        ('new', 'NEW — Never Analyzed', ''),
        ('needs_rerun', 'NEEDS RE-RUN', ''),
        ('good', 'GOOD — Has Results', ''),
        ('skip', 'SKIP — No Extraction Expected', ''),
    ]:
        group = [(d, r) for d, s, r in results if s == show_status]
        if not group:
            continue
        print(f"  [{header}] ({len(group)} docs)")
        for doc, reason in group:
            conf_str = ''
            if doc.get('metadata'):
                try:
                    meta = json.loads(doc['metadata']) if isinstance(doc['metadata'], str) else doc['metadata']
                    c = meta.get('classification_confidence')
                    if c is not None:
                        conf_str = f' @{c:.0%}'
                except:
                    pass
            print(f"    {doc['id']:4d}  {doc['document_type']:22s}{conf_str:>5s}  {reason:40s}  {doc['filename'][:45]}")
        print()

    # Summary recommendation
    rerun_count = counts.get('needs_rerun', 0) + counts.get('new', 0)
    skip_count = counts.get('good', 0) + counts.get('skip', 0)
    if rerun_count == 0:
        print("  ✓ All documents have good extraction results. No re-run needed.")
    else:
        print(f"  → {rerun_count} docs need processing, {skip_count} can be skipped.")
        print(f"  → Run with --smart to process only the {rerun_count} that need it.")
        if skip_count > 0:
            est_savings = skip_count / len(docs) * 100
            print(f"  → Estimated time savings: ~{est_savings:.0f}% of a full run.")


def get_docs_to_process(db, property_id, mode='full', doc_types=None):
    """
    Get list of doc IDs to process based on mode.

    Modes:
        'full'     — all docs (default, clears existing data)
        'smart'    — only docs assessed as 'new' or 'needs_rerun'
        'new_only' — only docs with analysis_status != 'analyzed'

    Returns (doc_ids_to_process, doc_ids_to_skip) tuples.
    """
    docs = db.conn.execute(
        "SELECT * FROM documents WHERE property_id = ? ORDER BY document_type, id",
        (property_id,)
    ).fetchall()
    docs = [dict(d) for d in docs]

    if doc_types:
        docs = [d for d in docs if d['document_type'] in doc_types]

    if mode == 'full':
        return [d['id'] for d in docs], []

    process_ids = []
    skip_ids = []

    for doc in docs:
        if mode == 'new_only':
            if doc['analysis_status'] != 'analyzed':
                process_ids.append(doc['id'])
            else:
                skip_ids.append(doc['id'])
        elif mode == 'smart':
            status, reason = assess_doc(db, doc)
            if status in ('new', 'needs_rerun'):
                process_ids.append(doc['id'])
            else:
                skip_ids.append(doc['id'])

    return process_ids, skip_ids


def analyze_property(db, property_id, mode='full', doc_types=None):
    """Run Phase 2 analysis on a single property."""
    prop = db.conn.execute(
        "SELECT id, name FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    if not prop:
        print(f"Error: Property ID {property_id} not found.")
        return

    prop_name = prop[1] or f"Property #{prop[0]}"

    # Determine which docs to process
    process_ids, skip_ids = get_docs_to_process(db, property_id, mode, doc_types)

    if not process_ids:
        print(f"No documents to process for {prop_name}.")
        if skip_ids:
            print(f"  ({len(skip_ids)} docs skipped — already have good extraction results)")
        return

    if mode == 'full':
        # Full mode: clear ALL extraction data for this property
        print(f"Clearing previous extraction data for {prop_name}...")
        all_doc_ids = process_ids + skip_ids
        if all_doc_ids:
            placeholders = ','.join('?' * len(all_doc_ids))
            for table in ['clauses', 'financial_terms', 'rent_roll_entries',
                           'operating_statement_items', 'gl_entries']:
                db.conn.execute(
                    f"DELETE FROM {table} WHERE document_id IN ({placeholders})",
                    all_doc_ids
                )
            db.conn.commit()
        print("Done.\n")
    else:
        # Smart/new-only: only clear data for docs being re-processed
        print(f"Clearing extraction data for {len(process_ids)} docs to re-process...")
        if process_ids:
            placeholders = ','.join('?' * len(process_ids))
            for table in ['clauses', 'financial_terms', 'rent_roll_entries',
                           'operating_statement_items', 'gl_entries']:
                db.conn.execute(
                    f"DELETE FROM {table} WHERE document_id IN ({placeholders})",
                    process_ids
                )
            db.conn.commit()
        if skip_ids:
            print(f"Skipping {len(skip_ids)} docs with good extraction results.\n")

    analyzer = PropertyAnalyzer(db)

    # Progress callback
    def on_step(event, detail):
        ts = time.strftime('%H:%M:%S')
        print(f"  [{ts}] [{event}] {detail}")

    analyzer._on_step = on_step

    total_all = db.conn.execute(
        "SELECT COUNT(*) FROM documents WHERE property_id = ?",
        (property_id,)
    ).fetchone()[0]

    mode_label = {
        'full': 'full re-run',
        'smart': 'smart (re-run needed only)',
        'new_only': 'new docs only',
    }.get(mode, mode)

    print(f"Starting Phase 2 analysis on {prop_name} — {len(process_ids)}/{total_all} documents ({mode_label})")
    if doc_types:
        print(f"  Filtered to types: {', '.join(doc_types)}")
    print("=" * 60)

    start = time.time()

    # Use the analyzer but only for selected docs
    if mode == 'full' and not doc_types:
        # Full run — use existing analyze_property method
        summary = analyzer.analyze_property(property_id)
    else:
        # Selective run — process only specific docs
        summary = analyzer.analyze_documents(property_id, process_ids)

    elapsed = time.time() - start

    # Add skip info to summary
    summary['skipped'] = len(skip_ids)
    summary['mode'] = mode_label

    print(f"\n{'=' * 60}")
    print(f"ANALYSIS COMPLETE — {prop_name} — {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"{'=' * 60}")
    print(json.dumps(summary, indent=2, default=str))

    # Quick stats for this property (all docs, not just processed)
    all_doc_ids = [r[0] for r in db.conn.execute(
        "SELECT id FROM documents WHERE property_id = ?", (property_id,)
    ).fetchall()]
    if all_doc_ids:
        placeholders = ','.join('?' * len(all_doc_ids))
        stats = {}
        for table in ['clauses', 'financial_terms', 'rent_roll_entries',
                       'operating_statement_items', 'gl_entries']:
            count = db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE document_id IN ({placeholders})",
                all_doc_ids
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
    parser.add_argument('--smart', action='store_true',
                        help='Only re-process docs that need it (new, failed, empty)')
    parser.add_argument('--new-only', action='store_true',
                        help='Only process never-analyzed docs')
    parser.add_argument('--assess', action='store_true',
                        help='Show extraction health report (no changes)')
    parser.add_argument('--doc-type', nargs='+',
                        help='Filter to specific document type(s)')
    parser.add_argument('--reconcile', action='store_true',
                        help='Run cross-document term reconciliation')

    args = parser.parse_args()

    db = Database(DB_PATH)
    db.connect()

    # Determine run mode
    if args.smart:
        mode = 'smart'
    elif args.new_only:
        mode = 'new_only'
    else:
        mode = 'full'

    if args.assess:
        if args.property_id:
            print_assessment(db, args.property_id)
        elif args.all:
            rows = db.conn.execute(
                "SELECT id FROM properties ORDER BY id"
            ).fetchall()
            for (pid,) in rows:
                print_assessment(db, pid)
        else:
            print("Specify --property-id or --all with --assess")
        db.close()
        return

    if args.reconcile:
        from realestate_extractor.reconciliation import reconcile_terms, print_reconciliation
        if args.property_id:
            result = reconcile_terms(db.conn, args.property_id)
            print_reconciliation(result)
        elif args.all:
            rows = db.conn.execute(
                "SELECT id FROM properties ORDER BY id"
            ).fetchall()
            for (pid,) in rows:
                result = reconcile_terms(db.conn, pid)
                print_reconciliation(result)
        else:
            print("Specify --property-id or --all with --reconcile")
        db.close()
        return

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
            analyze_property(db, prop_id, mode=mode, doc_types=args.doc_type)
            print()
    elif args.property_id:
        analyze_property(db, args.property_id, mode=mode, doc_types=args.doc_type)
    else:
        # No arguments — list properties and prompt
        print("No property specified. Available properties:")
        rows = list_properties(db)
        if rows:
            print(f"\nUsage:")
            print(f"  python3 run_analysis.py --property-id <ID>              # full re-run")
            print(f"  python3 run_analysis.py --property-id <ID> --smart      # smart (skip good docs)")
            print(f"  python3 run_analysis.py --property-id <ID> --new-only   # new docs only")
            print(f"  python3 run_analysis.py --property-id <ID> --assess     # health report")
            print(f"  python3 run_analysis.py --all")

    db.close()


if __name__ == '__main__':
    main()
