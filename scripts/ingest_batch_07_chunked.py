#!/usr/bin/env python3
"""
Chunked ingestion for batch 07 — processes N files at a time,
printing progress to stdout. Designed to be run repeatedly
(skips duplicates automatically).
"""

import os
import sys
import json
import time
import logging

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(parent_dir))
sys.path.insert(0, parent_dir)

try:
    from realestate_extractor.database import Database
    from realestate_extractor.batch_processor import BatchProcessor
    from realestate_extractor.document_ingestion import SUPPORTED_EXTENSIONS
except ImportError:
    from database import Database
    from batch_processor import BatchProcessor
    from document_ingestion import SUPPORTED_EXTENSIONS

logging.basicConfig(level=logging.WARNING, format='%(message)s')

BATCH_DIR = os.path.join(parent_dir, 'uploads', 'batch_07_vg_weekly')
DB_PATH = os.path.join(parent_dir, 'data', 'org_dev.db')
PROPERTY_NAME = 'Chamberlain'

# Parse args
offset = int(sys.argv[1]) if len(sys.argv) > 1 else 0
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30


def main():
    # Collect all files
    all_files = []
    for root, dirs, files in os.walk(BATCH_DIR):
        for f in sorted(files):
            ext = f.rsplit('.', 1)[-1].lower() if '.' in f else ''
            if ext in SUPPORTED_EXTENSIONS:
                all_files.append(os.path.join(root, f))
    all_files.sort()

    total = len(all_files)
    chunk = all_files[offset:offset + limit]

    if not chunk:
        print(f"No files in range [{offset}:{offset+limit}] (total={total})")
        return

    print(f"Processing files {offset+1}-{offset+len(chunk)} of {total}")

    db = Database(DB_PATH)
    db.connect()
    processor = BatchProcessor(db)

    ok = 0
    fail = 0
    dupes = 0
    start = time.time()

    for i, filepath in enumerate(chunk):
        fname = os.path.basename(filepath)
        result = processor.process_single(filepath, property_name=PROPERTY_NAME)

        if result.success:
            ok += 1
            print(f"  [{offset+i+1}/{total}] OK  {fname} -> {result.document_type} ({result.processing_time:.1f}s)")
        elif result.error and "Duplicate" in result.error:
            dupes += 1
            print(f"  [{offset+i+1}/{total}] DUP {fname}")
        else:
            fail += 1
            print(f"  [{offset+i+1}/{total}] ERR {fname}: {result.error}")

    elapsed = time.time() - start
    print(f"\nChunk done: {ok} ok, {dupes} dupes, {fail} failed ({elapsed:.0f}s)")
    print(f"Next: python3 scripts/ingest_batch_07_chunked.py {offset + limit} {limit}")

    db.close()


if __name__ == '__main__':
    main()
