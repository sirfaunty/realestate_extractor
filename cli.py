"""
Command-Line Interface for Real Estate Document Extractor.

Usage:
    python -m realestate_extractor process <file.pdf> [--type lease] [--property "123 Main St"]
    python -m realestate_extractor batch <folder> [--type lease] [--property "Portfolio A"]
    python -m realestate_extractor watch <folder> [--interval 10]
    python -m realestate_extractor query [--type lease] [--property "123 Main"]
    python -m realestate_extractor search "assignment clause"
    python -m realestate_extractor export <table> <output.csv>
    python -m realestate_extractor templates
    python -m realestate_extractor status
"""

import argparse
import sys
import json
import logging
from pathlib import Path

from .database import Database
from .batch_processor import BatchProcessor
from .extractors.llm_client import LocalLLMClient
from .templates.document_templates import list_templates, TEMPLATES


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="realestate_extractor",
        description="Local PDF extraction tool for real estate documents. "
                    "All processing happens on your device — no data leaves your machine."
    )
    parser.add_argument("--db", default="realestate_extractions.db",
                        help="Path to SQLite database file (default: realestate_extractions.db)")
    parser.add_argument("--model", default="llama3.1:8b",
                        help="Ollama model to use (default: llama3.1:8b)")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama API URL (default: http://localhost:11434)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose/debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ─── process: Single file ────────────────────────────────────────
    p_process = subparsers.add_parser("process", help="Process a single PDF file")
    p_process.add_argument("file", help="Path to PDF file")
    p_process.add_argument("--type", dest="doc_type", choices=list(TEMPLATES.keys()),
                           help="Document type (auto-detected if not specified)")
    p_process.add_argument("--property", dest="property_name",
                           help="Property name to associate with this document")
    p_process.add_argument("--force-ocr", action="store_true",
                           help="Force OCR even for digital PDFs")
    p_process.add_argument("--no-preprocess", action="store_true",
                           help="Skip image preprocessing before OCR")

    # ─── batch: Folder processing ────────────────────────────────────
    p_batch = subparsers.add_parser("batch", help="Process all PDFs in a folder")
    p_batch.add_argument("folder", help="Path to folder containing PDFs")
    p_batch.add_argument("--type", dest="doc_type", choices=list(TEMPLATES.keys()),
                         help="Document type for all files (auto-detected if not specified)")
    p_batch.add_argument("--property", dest="property_name",
                         help="Property name for all documents")
    p_batch.add_argument("--recursive", action="store_true",
                         help="Search subdirectories too")
    p_batch.add_argument("--force-ocr", action="store_true",
                         help="Force OCR for all files")

    # ─── watch: Folder watcher ───────────────────────────────────────
    p_watch = subparsers.add_parser("watch", help="Watch a folder for new PDFs")
    p_watch.add_argument("folder", help="Path to folder to watch")
    p_watch.add_argument("--type", dest="doc_type", choices=list(TEMPLATES.keys()),
                         help="Document type for new files")
    p_watch.add_argument("--property", dest="property_name",
                         help="Property name for new documents")
    p_watch.add_argument("--interval", type=int, default=10,
                         help="Poll interval in seconds (default: 10)")

    # ─── query: Search the database ──────────────────────────────────
    p_query = subparsers.add_parser("query", help="Query extracted data")
    p_query.add_argument("--type", dest="doc_type",
                         help="Filter by document type")
    p_query.add_argument("--property", dest="property_name",
                         help="Filter by property name")
    p_query.add_argument("--terms", action="store_true",
                         help="Show financial terms")
    p_query.add_argument("--clauses", dest="clause_type",
                         help="Show clauses of a specific type")
    p_query.add_argument("--rent-roll", action="store_true",
                         help="Show rent roll data")
    p_query.add_argument("--opstat", action="store_true",
                         help="Show operating statement data")
    p_query.add_argument("--gl", action="store_true",
                         help="Show general ledger entries")
    p_query.add_argument("--summary", action="store_true",
                         help="Show portfolio summary")
    p_query.add_argument("--json", dest="as_json", action="store_true",
                         help="Output as JSON")

    # ─── search: Full-text search ────────────────────────────────────
    p_search = subparsers.add_parser("search", help="Full-text search across documents")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")

    # ─── export: Export to CSV ───────────────────────────────────────
    p_export = subparsers.add_parser("export", help="Export data to CSV")
    p_export.add_argument("table", choices=[
        "documents", "clauses", "financial_terms",
        "rent_roll_entries", "operating_statement_items", "gl_entries"
    ], help="Table to export")
    p_export.add_argument("output", help="Output CSV file path")
    p_export.add_argument("--filter", nargs=2, action="append", metavar=("FIELD", "VALUE"),
                          help="Filter: --filter document_type lease")

    # ─── templates: List available templates ─────────────────────────
    subparsers.add_parser("templates", help="List available document templates")

    # ─── status: System status ───────────────────────────────────────
    subparsers.add_parser("status", help="Check system status and database stats")

    return parser


def cmd_process(args, db: Database, llm: LocalLLMClient):
    """Process a single PDF file."""
    processor = BatchProcessor(
        db, llm,
        force_ocr=args.force_ocr,
        preprocess_ocr=not args.no_preprocess
    )
    result = processor.process_single(
        args.file,
        document_type=args.doc_type,
        property_name=args.property_name
    )

    if result.success:
        print(f"\nSuccessfully processed: {result.filename}")
        print(f"  Document type:    {result.document_type}")
        print(f"  Document ID:      {result.document_id}")
        print(f"  Financial terms:  {result.financial_terms_count}")
        print(f"  Legal clauses:    {result.clauses_count}")
        print(f"  Table rows:       {result.tabular_rows_count}")
        print(f"  Processing time:  {result.processing_time:.1f}s")
    else:
        print(f"\nFailed to process: {result.filename}")
        print(f"  Error: {result.error}")
        sys.exit(1)


def cmd_batch(args, db: Database, llm: LocalLLMClient):
    """Process all PDFs in a folder."""
    processor = BatchProcessor(db, llm, force_ocr=args.force_ocr)

    def progress_callback(current, total, result):
        status = "OK" if result.success else "FAIL"
        print(f"  [{current}/{total}] {result.filename}: {status}")

    results = processor.process_folder(
        args.folder,
        document_type=args.doc_type,
        property_name=args.property_name,
        recursive=args.recursive,
        on_progress=progress_callback
    )


def cmd_watch(args, db: Database, llm: LocalLLMClient):
    """Watch a folder for new PDFs."""
    processor = BatchProcessor(db, llm)
    processor.watch_folder(
        args.folder,
        document_type=args.doc_type,
        property_name=args.property_name,
        poll_interval=args.interval
    )


def cmd_query(args, db: Database):
    """Query extracted data."""
    if args.summary:
        summary = db.get_portfolio_summary(args.property_name)
        print("\nPortfolio Summary:")
        print(f"  Properties: {summary['total_properties']}")
        print(f"  Documents by type:")
        for dtype, count in summary.get('document_counts', {}).items():
            print(f"    {dtype}: {count}")
        rr = summary.get('rent_roll', {})
        if rr.get('units'):
            print(f"  Rent Roll:")
            print(f"    Total units: {rr['units']}")
            print(f"    Total monthly rent: ${rr.get('total_monthly_rent', 0):,.2f}")
            print(f"    Avg rent PSF: ${rr.get('avg_rent_psf', 0):,.2f}")
            print(f"    Total SF: {rr.get('total_sqft', 0):,.0f}")
        return

    if args.terms:
        terms = db.get_financial_terms(term_type=args.doc_type)
        _print_results(terms, args.as_json, "Financial Terms")
        return

    if args.clause_type:
        clauses = db.get_clauses(clause_type=args.clause_type)
        _print_results(clauses, args.as_json, f"Clauses: {args.clause_type}")
        return

    if args.rent_roll:
        data = db.get_rent_roll(property_name=args.property_name)
        _print_results(data, args.as_json, "Rent Roll")
        return

    if args.opstat:
        data = db.get_operating_statement(period=args.doc_type)
        _print_results(data, args.as_json, "Operating Statement")
        return

    if args.gl:
        data = db.get_gl_entries()
        _print_results(data, args.as_json, "General Ledger")
        return

    # Default: list documents
    docs = db.list_documents(document_type=args.doc_type,
                             property_name=args.property_name)
    _print_results(docs, args.as_json, "Documents")


def cmd_search(args, db: Database):
    """Full-text search."""
    results = db.search_fulltext(args.query, limit=args.limit)
    if not results:
        print("No results found.")
        return

    print(f"\nSearch results for '{args.query}' ({len(results)} matches):\n")
    for r in results:
        doc = db.get_document(int(r['document_id']))
        filename = doc['filename'] if doc else 'unknown'
        print(f"  [{filename} p.{r['page_number']}]")
        print(f"  {r['snippet']}\n")


def cmd_export(args, db: Database):
    """Export table to CSV."""
    filters = {}
    if args.filter:
        for field, value in args.filter:
            filters[field] = value

    count = db.export_to_csv(args.table, args.output, filters or None)
    print(f"Exported {count} rows to {args.output}")


def cmd_templates():
    """List available templates."""
    print("\nAvailable Document Templates:\n")
    for t in list_templates():
        print(f"  {t['type']:25s} {t['name']}")
        print(f"  {'':25s} {t['description']}")
        print(f"  {'':25s} Modes: {', '.join(t['modes'])}")
        print()


def cmd_status(args, db: Database, llm: LocalLLMClient):
    """Check system status."""
    print("\nSystem Status:")
    print(f"  Database: {args.db}")

    # Check Ollama
    if llm.is_available():
        models = llm.list_models()
        print(f"  Ollama:   Connected ({len(models)} models available)")
        for m in models:
            marker = " *" if m == args.model else ""
            print(f"            - {m}{marker}")
    else:
        print("  Ollama:   NOT CONNECTED")
        print("            Install: https://ollama.ai")
        print("            Then run: ollama pull llama3.1:8b")

    # Check Python dependencies
    deps = {
        "pdfplumber": "PDF text extraction",
        "pytesseract": "OCR for scanned PDFs",
        "pdf2image": "PDF to image conversion",
        "cv2": "Image preprocessing (OpenCV)",
        "requests": "HTTP client for Ollama",
    }

    print(f"\n  Dependencies:")
    for module, desc in deps.items():
        try:
            __import__(module)
            print(f"    {module:15s} OK    ({desc})")
        except ImportError:
            print(f"    {module:15s} MISSING ({desc})")

    # Database stats
    try:
        docs = db.list_documents()
        print(f"\n  Database Stats:")
        print(f"    Documents:  {len(docs)}")
        types = {}
        for d in docs:
            types[d['document_type']] = types.get(d['document_type'], 0) + 1
        for t, c in types.items():
            print(f"      {t}: {c}")
    except Exception:
        print(f"\n  Database: Empty (no documents processed yet)")

    print()


def _print_results(data: list, as_json: bool, title: str):
    """Print query results in table or JSON format."""
    if not data:
        print(f"\nNo {title.lower()} found.")
        return

    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return

    print(f"\n{title} ({len(data)} results):\n")

    # Auto-format as simple table
    if data:
        keys = [k for k in data[0].keys() if data[0][k] is not None][:8]  # limit columns
        # Print header
        header = " | ".join(f"{k:20s}" for k in keys)
        print(f"  {header}")
        print(f"  {'-' * len(header)}")
        # Print rows
        for row in data[:50]:  # limit to 50 rows
            values = " | ".join(f"{str(row.get(k, '')):20s}" for k in keys)
            print(f"  {values}")

        if len(data) > 50:
            print(f"\n  ... and {len(data) - 50} more rows. Use --json for full output.")

    print()


def main():
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    setup_logging(args.verbose)

    # Initialize database
    db = Database(args.db)
    db.connect()

    # Initialize LLM client
    llm = LocalLLMClient(
        base_url=args.ollama_url,
        model=args.model
    )

    try:
        if args.command == "process":
            cmd_process(args, db, llm)
        elif args.command == "batch":
            cmd_batch(args, db, llm)
        elif args.command == "watch":
            cmd_watch(args, db, llm)
        elif args.command == "query":
            cmd_query(args, db)
        elif args.command == "search":
            cmd_search(args, db)
        elif args.command == "export":
            cmd_export(args, db)
        elif args.command == "templates":
            cmd_templates()
        elif args.command == "status":
            cmd_status(args, db, llm)
    finally:
        db.close()


if __name__ == "__main__":
    main()
