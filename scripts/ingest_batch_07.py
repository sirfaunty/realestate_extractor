#!/usr/bin/env python3
"""
Ingest VG Weekly Reports as batch 07_VG_WEEKLY_REPORTS.

Three source datasets:
  - Chamberlain Pre August 2024/ (225 PDFs, 2021-2024 weekly leasing/property reports)
  - Portfolio Weekly - Batch 1/ (39 .msg, Aug 2024 - Apr 2026 portfolio weekly emails)
  - Portfolio Weekly - Batch 2/ (48 .msg, Aug 2024 - Dec 2025 portfolio weekly emails)

All assigned to property: Chamberlain (property_id=1)
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime

# Add parent to path so we can import as a package
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
grandparent_dir = os.path.dirname(parent_dir)
sys.path.insert(0, grandparent_dir)
sys.path.insert(0, parent_dir)

# Try both import styles
try:
    from realestate_extractor.database import Database
    from realestate_extractor.batch_processor import BatchProcessor
except ImportError:
    from database import Database
    from batch_processor import BatchProcessor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('data/batch_07_ingest.log')
    ]
)
logger = logging.getLogger(__name__)

BATCH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'uploads', 'batch_07_vg_weekly'
)
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'org_dev.db'
)
PROPERTY_NAME = 'Chamberlain'


def progress_callback(current, total, result):
    status = "OK" if result.success else f"FAIL: {result.error}"
    logger.info(f"[{current}/{total}] {result.filename}: {status} "
                f"(type={result.document_type}, {result.processing_time:.1f}s)")


def main():
    logger.info("=" * 70)
    logger.info("BATCH 07: VG Weekly Reports Ingestion")
    logger.info("=" * 70)

    if not os.path.isdir(BATCH_DIR):
        logger.error(f"Batch directory not found: {BATCH_DIR}")
        sys.exit(1)

    # Count files
    try:
        from realestate_extractor.document_ingestion import SUPPORTED_EXTENSIONS
    except ImportError:
        from document_ingestion import SUPPORTED_EXTENSIONS
    all_files = []
    for root, dirs, files in os.walk(BATCH_DIR):
        for f in files:
            ext = f.rsplit('.', 1)[-1].lower() if '.' in f else ''
            if ext in SUPPORTED_EXTENSIONS:
                all_files.append(os.path.join(root, f))

    logger.info(f"Found {len(all_files)} supported files in {BATCH_DIR}")

    # Group by source
    chamberlain_pre = [f for f in all_files if 'Chamberlain Pre August' in f]
    portfolio_1 = [f for f in all_files if 'Portfolio Weekly - Batch 1' in f]
    portfolio_2 = [f for f in all_files if 'Portfolio Weekly - Batch 2' in f]
    logger.info(f"  Chamberlain Pre Aug 2024: {len(chamberlain_pre)} files")
    logger.info(f"  Portfolio Weekly Batch 1: {len(portfolio_1)} files")
    logger.info(f"  Portfolio Weekly Batch 2: {len(portfolio_2)} files")

    db = Database(DB_PATH)
    db.connect()
    processor = BatchProcessor(db)

    start_time = time.time()
    all_results = []

    # Process recursively — BatchProcessor.process_folder handles this
    logger.info("\nStarting ingestion...")
    results = processor.process_folder(
        BATCH_DIR,
        property_name=PROPERTY_NAME,
        recursive=True,
        on_progress=progress_callback
    )
    all_results.extend(results)

    elapsed = time.time() - start_time
    success = sum(1 for r in all_results if r.success)
    failed = sum(1 for r in all_results if not r.success and r.error != "Duplicate document (already processed)")
    dupes = sum(1 for r in all_results if r.error == "Duplicate document (already processed)")

    logger.info("\n" + "=" * 70)
    logger.info("BATCH 07 INGESTION COMPLETE")
    logger.info(f"  Total files: {len(all_results)}")
    logger.info(f"  Successful:  {success}")
    logger.info(f"  Duplicates:  {dupes}")
    logger.info(f"  Failed:      {failed}")
    logger.info(f"  Time:        {elapsed:.0f}s ({elapsed/60:.1f}m)")
    logger.info("=" * 70)

    # Type distribution
    from collections import Counter
    types = Counter(r.document_type for r in all_results if r.success)
    logger.info("\nDocument type distribution:")
    for doc_type, count in types.most_common():
        logger.info(f"  {doc_type}: {count}")

    # Save summary
    summary = {
        'batch_id': '07_VG_WEEKLY_REPORTS',
        'ingested_at': datetime.now().isoformat(),
        'total_files': len(all_results),
        'successful': success,
        'duplicates': dupes,
        'failed': failed,
        'elapsed_seconds': round(elapsed, 1),
        'type_distribution': dict(types),
        'failures': [
            {'file': r.filename, 'error': r.error}
            for r in all_results
            if not r.success and r.error != "Duplicate document (already processed)"
        ]
    }
    summary_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'batch_07_summary.json'
    )
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    logger.info(f"\nSummary saved to {summary_path}")

    db.close()
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
