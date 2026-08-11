#!/usr/bin/env python3
"""
#77 Re-import campaign — Phase 1 ingest runner (property-agnostic).

Ingests a folder of lease PDFs into an isolated pilot DB via the standard
BatchProcessor (text layer + OCR fallback, no LLM), non-recursive so
subfolders/zips in Riley's source folders are ignored.

Run natively:
    venv/Scripts/python portfolio_ownership/re_import/ingest_property.py \
        --sources "portfolio_ownership/inbox/Lease Files/Maplewood Square I" \
        --db data/pilot_maplewood.db \
        --property "Maplewood Square I"

Then Phase 2:  venv/Scripts/python run_analysis.py --db <db> --property-id 1
Then tie-out:  venv/Scripts/python portfolio_ownership/re_import/tie_out_property.py ...
"""

import argparse
import logging
import os
import sys
import time

# Surface the batch processor's per-file/per-page progress on the console —
# OCR on large scanned leases is slow and silence looks like a hang.
logging.basicConfig(level=logging.INFO, format='%(message)s')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.dirname(ROOT))

from realestate_extractor.database import Database                    # noqa: E402
from realestate_extractor.batch_processor import BatchProcessor       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sources', required=True, help='folder of lease PDFs')
    ap.add_argument('--db', required=True, help='pilot DB path (isolated)')
    ap.add_argument('--property', required=True, help='property display name')
    ap.add_argument('--type', default='lease', help='document type override')
    ap.add_argument('--recursive', action='store_true')
    args = ap.parse_args()

    if not os.path.isdir(args.sources):
        sys.exit(f'sources folder not found: {args.sources}')

    db = Database(args.db)
    db.connect()   # opens the connection and creates the schema
    proc = BatchProcessor(db)
    t0 = time.time()
    results = proc.process_folder(
        args.sources, document_type=args.type,
        property_name=args.property, recursive=args.recursive)
    ok = [r for r in results if r.success]
    bad = [r for r in results if not r.success]
    print('\n' + '=' * 60)
    print(f'INGEST COMPLETE — {len(ok)}/{len(results)} OK '
          f'({time.time() - t0:.1f}s) -> {args.db}')
    for r in ok:
        print(f'  ok: {r.filename} ({r.page_count}pp, type={r.document_type})')
    for r in bad:
        print(f'  FAILED: {r.filename}: {r.error}')
    db.close()


if __name__ == '__main__':
    main()
