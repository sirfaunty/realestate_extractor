"""
Batch Processor and Folder Watcher for Real Estate Document Extractor.

Phase 1 — Ingest-only pipeline:
- Extract text + tables from documents (PDF, XLSX, DOCX, MSG, etc.)
- Light keyword-based document type tagging (no LLM)
- Store raw content in the database for later analysis

Phase 2 analysis is handled separately by the PropertyAnalyzer.
"""

import os
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable
from datetime import datetime

from .pdf_ingestion import compute_file_hash, DocumentContent
from .document_ingestion import ingest_document, SUPPORTED_EXTENSIONS
from .database import Database
from .extractors.extraction_engine import DocumentClassifier
from .extractors.llm_client import LocalLLMClient

logger = logging.getLogger(__name__)


class ProcessingResult:
    """Result of processing a single document."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.success = False
        self.document_id: Optional[int] = None
        self.document_type: Optional[str] = None
        self.page_count: int = 0
        self.tables_stored: int = 0
        self.error: Optional[str] = None
        self.processing_time: float = 0

    def __repr__(self):
        status = "OK" if self.success else f"FAILED: {self.error}"
        return (
            f"ProcessingResult({self.filename}: {status}, "
            f"type={self.document_type}, "
            f"pages={self.page_count}, "
            f"tables={self.tables_stored}, "
            f"time={self.processing_time:.1f}s)"
        )


class BatchProcessor:
    """Ingest PDFs: extract text + tables, tag type, store raw content."""

    def __init__(self, db: Database, llm_client: Optional[LocalLLMClient] = None,
                 force_ocr: bool = False, preprocess_ocr: bool = True):
        self.db = db
        self.llm = llm_client or LocalLLMClient()
        self.classifier = DocumentClassifier(self.llm)
        self.force_ocr = force_ocr
        self.preprocess_ocr = preprocess_ocr
        self._on_step: Optional[Callable] = None  # callback(step, detail)

    def _emit_step(self, step: str, detail: str = ''):
        """Notify the caller about pipeline progress."""
        if self._on_step:
            try:
                self._on_step(step, detail)
            except Exception:
                pass  # never let callback errors break processing

    def process_single(self, filepath: str,
                       document_type: str = None,
                       property_name: str = None) -> ProcessingResult:
        """
        Ingest a single PDF — extract text + tables, classify by keywords,
        and store everything in the database. No LLM calls, no structured
        extraction. That happens in Phase 2 (property analysis).

        Args:
            filepath: Path to the PDF
            document_type: Override auto-detection (lease, loan, etc.)
            property_name: Associate with a property name

        Returns:
            ProcessingResult with ingest details
        """
        result = ProcessingResult(filepath)
        start_time = time.time()

        try:
            # Step 1: Check for duplicates
            self._emit_step('ingesting', f'Reading {result.filename}...')
            file_hash = compute_file_hash(filepath)
            if self.db.document_exists(file_hash):
                result.error = "Duplicate document (already processed)"
                logger.info(f"Skipping duplicate: {filepath}")
                return result

            # Step 2: Ingest document (PDF, XLSX, DOCX, MSG, etc.)
            logger.info(f"Ingesting: {filepath}")
            doc = ingest_document(filepath, force_ocr=self.force_ocr,
                                  preprocess=self.preprocess_ocr)
            result.page_count = doc.page_count
            ext = os.path.splitext(filepath)[1].lower().lstrip('.')
            page_label = 'sheets' if ext in ('xlsx', 'xls') else 'pages'
            self._emit_step('ingesting', f'{doc.page_count} {page_label} extracted')

            # Step 3: Classify document type (keyword-based, no LLM)
            self._emit_step('classifying', 'Detecting document type...')
            if document_type:
                doc_type = document_type
                classification_confidence = 1.0
            else:
                doc_type, classification_confidence = self.classifier.classify(
                    doc, use_llm=False
                )
                logger.info(
                    f"Tagged as '{doc_type}' "
                    f"(confidence: {classification_confidence:.2f})"
                )
            self._emit_step('classifying', f'Tagged as {doc_type}')

            result.document_type = doc_type

            # Step 4: Store document record + raw content
            self._emit_step('storing', 'Saving to database...')
            doc_id = self._store_document_record(
                doc, doc_type, property_name, file_hash,
                classification_confidence=classification_confidence
            )
            result.document_id = doc_id

            # Store full text for search
            self._store_fulltext(doc_id, doc)

            # Store raw tables (for later analysis)
            tables_count = self._store_raw_tables(doc_id, doc)
            result.tables_stored = tables_count

            self._emit_step('storing', f'Stored {doc.page_count} pages, {tables_count} tables')

            result.success = True

        except Exception as e:
            result.error = str(e)
            logger.error(f"Failed to ingest {filepath}: {e}", exc_info=True)

        result.processing_time = time.time() - start_time
        logger.info(str(result))
        return result

    def process_folder(self, folder_path: str,
                       document_type: str = None,
                       property_name: str = None,
                       recursive: bool = False,
                       on_progress: Optional[Callable] = None) -> List[ProcessingResult]:
        """
        Process all PDFs in a folder.

        Args:
            folder_path: Path to the folder containing PDFs
            document_type: Override auto-detection for all files
            property_name: Associate all files with this property
            recursive: If True, search subdirectories too
            on_progress: Callback function(current, total, result) for progress updates

        Returns:
            List of ProcessingResult for each file
        """
        folder = Path(folder_path)
        if not folder.is_dir():
            raise ValueError(f"Not a directory: {folder_path}")

        # Find all supported files
        all_files = set()
        for ext in SUPPORTED_EXTENSIONS:
            if recursive:
                all_files.update(folder.rglob(f"*.{ext}"))
                all_files.update(folder.rglob(f"*.{ext.upper()}"))
            else:
                all_files.update(folder.glob(f"*.{ext}"))
                all_files.update(folder.glob(f"*.{ext.upper()}"))

        pdf_files = sorted(all_files)

        total = len(pdf_files)
        logger.info(f"Found {total} document files in {folder_path}")

        if total == 0:
            logger.warning("No PDF files found in the specified folder.")
            return []

        results = []
        for i, pdf_path in enumerate(pdf_files):
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing file {i+1}/{total}: {pdf_path.name}")
            logger.info(f"{'='*60}")

            result = self.process_single(
                str(pdf_path),
                document_type=document_type,
                property_name=property_name
            )
            results.append(result)

            if on_progress:
                on_progress(i + 1, total, result)

        # Print summary
        self._print_batch_summary(results)
        return results

    def watch_folder(self, folder_path: str,
                     document_type: str = None,
                     property_name: str = None,
                     poll_interval: int = 10):
        """
        Watch a folder for new PDF files and process them automatically.

        Args:
            folder_path: Path to the folder to watch
            document_type: Override auto-detection
            property_name: Associate with this property
            poll_interval: Seconds between checks for new files
        """
        folder = Path(folder_path)
        if not folder.is_dir():
            raise ValueError(f"Not a directory: {folder_path}")

        processed_files = set()

        # Track already-existing files
        for ext in SUPPORTED_EXTENSIONS:
            for f in folder.glob(f"*.{ext}"):
                file_hash = compute_file_hash(str(f))
                if self.db.document_exists(file_hash):
                    processed_files.add(str(f))

        logger.info(f"Watching folder: {folder_path}")
        logger.info(f"Already processed: {len(processed_files)} files")
        logger.info(f"Poll interval: {poll_interval}s")
        logger.info("Press Ctrl+C to stop watching.\n")

        try:
            while True:
                current_files = set()
                for ext in SUPPORTED_EXTENSIONS:
                    current_files |= set(str(p) for p in folder.glob(f"*.{ext}"))
                    current_files |= set(str(p) for p in folder.glob(f"*.{ext.upper()}"))
                new_files = current_files - processed_files

                for pdf_path in sorted(new_files):
                    logger.info(f"New file detected: {os.path.basename(pdf_path)}")
                    result = self.process_single(
                        pdf_path,
                        document_type=document_type,
                        property_name=property_name
                    )
                    processed_files.add(pdf_path)

                    if result.success:
                        logger.info(f"Successfully processed: {result.filename}")
                    else:
                        logger.error(f"Failed: {result.filename} — {result.error}")

                time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("\nStopped watching folder.")

    # ─── Internal Helpers ────────────────────────────────────────────

    def _store_document_record(self, doc: DocumentContent, doc_type: str,
                                property_name: str = None, file_hash: str = None,
                                classification_confidence: float = None) -> int:
        """Create a document record in the database."""
        return self.db.insert_document(
            filename=doc.filename,
            filepath=doc.filepath,
            document_type=doc_type,
            property_name=property_name,
            page_count=doc.page_count,
            is_scanned=doc.is_scanned,
            ocr_confidence=doc.avg_ocr_confidence,
            file_hash=file_hash or doc.file_hash,
            metadata={"classification_confidence": classification_confidence}
        )

    def _store_fulltext(self, doc_id: int, doc: DocumentContent):
        """Store full text content for search."""
        for page in doc.pages:
            if page.text.strip():
                self.db.insert_fulltext(doc_id, page.page_number, page.text)

    def _store_raw_tables(self, doc_id: int, doc: DocumentContent) -> int:
        """Store raw table data extracted by pdfplumber for later analysis."""
        count = 0
        for page in doc.pages:
            for ti, table in enumerate(page.tables):
                if table and len(table) >= 1:
                    self.db.insert_document_table(
                        doc_id, page.page_number, ti, table
                    )
                    count += 1
        if count:
            self.db.conn.commit()
        return count

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """Safely convert a value to float."""
        if value is None:
            return None
        try:
            if isinstance(value, str):
                value = value.replace(',', '').replace('$', '').strip()
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _print_batch_summary(results: List[ProcessingResult]):
        """Print a summary of batch ingest results."""
        total = len(results)
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        total_time = sum(r.processing_time for r in results)

        print(f"\n{'='*60}")
        print(f"BATCH INGEST COMPLETE")
        print(f"{'='*60}")
        print(f"Total files:      {total}")
        print(f"Ingested:         {succeeded}")
        print(f"Failed:           {failed}")
        print(f"Total time:       {total_time:.1f}s")
        if total > 0:
            print(f"Avg time/file:    {total_time/total:.1f}s")
        print(f"\nPages processed:  {sum(r.page_count for r in results)}")
        print(f"Tables stored:    {sum(r.tables_stored for r in results)}")

        if failed:
            print(f"\nFailed files:")
            for r in results:
                if not r.success:
                    print(f"  - {r.filename}: {r.error}")
        print(f"\nRun 'Analyze' on the property dashboard to extract structured data.")
        print(f"{'='*60}\n")
