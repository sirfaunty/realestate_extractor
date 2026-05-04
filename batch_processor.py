"""
Batch Processor and Folder Watcher for Real Estate Document Extractor.

Handles:
- Processing all PDFs in a directory
- Watch mode for automatic processing of new files
- Progress tracking and error handling
- Duplicate detection via file hashing
"""

import os
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable
from datetime import datetime

from .pdf_ingestion import ingest_pdf, compute_file_hash, DocumentContent
from .database import Database
from .extractors.extraction_engine import ExtractionEngine, DocumentClassifier
from .extractors.llm_client import LocalLLMClient
from .templates.document_templates import get_template

logger = logging.getLogger(__name__)


class ProcessingResult:
    """Result of processing a single document."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.success = False
        self.document_id: Optional[int] = None
        self.document_type: Optional[str] = None
        self.financial_terms_count = 0
        self.clauses_count = 0
        self.tabular_rows_count = 0
        self.error: Optional[str] = None
        self.processing_time: float = 0

    def __repr__(self):
        status = "OK" if self.success else f"FAILED: {self.error}"
        return (
            f"ProcessingResult({self.filename}: {status}, "
            f"type={self.document_type}, "
            f"terms={self.financial_terms_count}, "
            f"clauses={self.clauses_count}, "
            f"rows={self.tabular_rows_count}, "
            f"time={self.processing_time:.1f}s)"
        )


class BatchProcessor:
    """Process multiple PDFs from a folder with progress tracking."""

    def __init__(self, db: Database, llm_client: Optional[LocalLLMClient] = None,
                 force_ocr: bool = False, preprocess_ocr: bool = True):
        self.db = db
        self.llm = llm_client or LocalLLMClient()
        self.engine = ExtractionEngine(self.llm)
        self.classifier = DocumentClassifier(self.llm)
        self.force_ocr = force_ocr
        self.preprocess_ocr = preprocess_ocr

    def process_single(self, filepath: str,
                       document_type: str = None,
                       property_name: str = None) -> ProcessingResult:
        """
        Process a single PDF file end-to-end.

        Args:
            filepath: Path to the PDF
            document_type: Override auto-detection (lease, loan, etc.)
            property_name: Associate with a property name

        Returns:
            ProcessingResult with extraction details
        """
        result = ProcessingResult(filepath)
        start_time = time.time()

        try:
            # Step 1: Check for duplicates
            file_hash = compute_file_hash(filepath)
            if self.db.document_exists(file_hash):
                result.error = "Duplicate document (already processed)"
                logger.info(f"Skipping duplicate: {filepath}")
                return result

            # Step 2: Ingest PDF (text extraction / OCR)
            logger.info(f"Ingesting: {filepath}")
            doc = ingest_pdf(filepath, force_ocr=self.force_ocr,
                            preprocess=self.preprocess_ocr)

            # Step 3: Classify document type
            if document_type:
                doc_type = document_type
                classification_confidence = 1.0
            else:
                doc_type, classification_confidence = self.classifier.classify(doc)
                logger.info(
                    f"Classified as '{doc_type}' "
                    f"(confidence: {classification_confidence:.2f})"
                )

            result.document_type = doc_type

            # Step 4: Get extraction template
            template = get_template(doc_type)
            if not template:
                result.error = f"No template for document type: {doc_type}"
                logger.warning(result.error)
                # Still store the document and full text
                doc_id = self._store_document_record(doc, doc_type, property_name, file_hash)
                result.document_id = doc_id
                self._store_fulltext(doc_id, doc)
                result.success = True  # partial success
                return result

            # Step 5: Run extraction
            logger.info(f"Running extraction ({', '.join(m.value for m in template.extraction_modes)})")
            extraction = self.engine.extract(doc, template)

            # Step 6: Store everything in database
            doc_id = self._store_document_record(
                doc, doc_type, property_name, file_hash,
                classification_confidence=classification_confidence
            )
            result.document_id = doc_id

            # Store full text for search
            self._store_fulltext(doc_id, doc)

            # Store financial terms
            for term in extraction.get('financial_terms', []):
                try:
                    self.db.insert_financial_term(
                        document_id=doc_id,
                        term_type=term.get('term_type', 'unknown'),
                        value_raw=term.get('value_raw'),
                        value_numeric=self._safe_float(term.get('value_numeric')),
                        value_unit=term.get('value_unit'),
                        term_label=term.get('term_label'),
                        effective_date=term.get('effective_date'),
                        expiration_date=term.get('expiration_date'),
                        escalation_type=term.get('escalation_type'),
                        escalation_detail=term.get('escalation_detail'),
                        section_ref=term.get('section_ref'),
                        page_number=term.get('page_number'),
                        confidence=self._safe_float(term.get('confidence')),
                    )
                    result.financial_terms_count += 1
                except Exception as e:
                    logger.warning(f"Failed to store financial term: {e}")

            # Store clauses
            for clause in extraction.get('clauses', []):
                try:
                    self.db.insert_clause(
                        document_id=doc_id,
                        clause_type=clause.get('clause_type', 'unknown'),
                        full_text=clause.get('full_text', ''),
                        section_ref=clause.get('section_ref'),
                        clause_title=clause.get('clause_title'),
                        summary=clause.get('summary'),
                        page_number=clause.get('page_number'),
                        confidence=self._safe_float(clause.get('confidence')),
                    )
                    result.clauses_count += 1
                except Exception as e:
                    logger.warning(f"Failed to store clause: {e}")

            # Store tabular data based on document type
            for row in extraction.get('tabular_data', []):
                try:
                    self._store_tabular_row(doc_id, doc_type, row, property_name)
                    result.tabular_rows_count += 1
                except Exception as e:
                    logger.warning(f"Failed to store tabular row: {e}")

            result.success = True

        except Exception as e:
            result.error = str(e)
            logger.error(f"Failed to process {filepath}: {e}", exc_info=True)

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

        # Find all PDFs
        if recursive:
            pdf_files = sorted(folder.rglob("*.pdf"))
        else:
            pdf_files = sorted(folder.glob("*.pdf"))

        # Also check for uppercase .PDF extension
        if recursive:
            pdf_files += sorted(folder.rglob("*.PDF"))
        else:
            pdf_files += sorted(folder.glob("*.PDF"))

        # Deduplicate (in case of overlapping patterns)
        pdf_files = sorted(set(pdf_files))

        total = len(pdf_files)
        logger.info(f"Found {total} PDF files in {folder_path}")

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
        for pdf in folder.glob("*.pdf"):
            file_hash = compute_file_hash(str(pdf))
            if self.db.document_exists(file_hash):
                processed_files.add(str(pdf))

        logger.info(f"Watching folder: {folder_path}")
        logger.info(f"Already processed: {len(processed_files)} files")
        logger.info(f"Poll interval: {poll_interval}s")
        logger.info("Press Ctrl+C to stop watching.\n")

        try:
            while True:
                current_pdfs = set(str(p) for p in folder.glob("*.pdf"))
                current_pdfs |= set(str(p) for p in folder.glob("*.PDF"))
                new_files = current_pdfs - processed_files

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

    def _store_tabular_row(self, doc_id: int, doc_type: str,
                            row: Dict, property_name: str = None):
        """Route tabular data to the correct database table."""
        if doc_type == "rent_roll":
            self.db.insert_rent_roll_entry(
                document_id=doc_id,
                property_name=property_name or row.get('property_name'),
                unit_number=row.get('unit_number'),
                tenant_name=row.get('tenant_name'),
                suite=row.get('suite'),
                square_footage=self._safe_float(row.get('square_footage')),
                lease_start=row.get('lease_start'),
                lease_end=row.get('lease_end'),
                monthly_rent=self._safe_float(row.get('monthly_rent')),
                annual_rent=self._safe_float(row.get('annual_rent')),
                rent_psf=self._safe_float(row.get('rent_psf')),
                status=row.get('status'),
                notes=row.get('notes'),
                page_number=row.get('page_number'),
            )
        elif doc_type == "operating_statement":
            self.db.insert_operating_statement_item(
                document_id=doc_id,
                category=row.get('category', 'unknown'),
                line_item=row.get('line_item', ''),
                property_name=property_name or row.get('property_name'),
                period=row.get('period'),
                subcategory=row.get('subcategory'),
                amount=self._safe_float(row.get('amount')),
                amount_psf=self._safe_float(row.get('amount_psf')),
                is_subtotal=row.get('is_subtotal', False),
                is_total=row.get('is_total', False),
                page_number=row.get('page_number'),
            )
        elif doc_type == "general_ledger":
            self.db.insert_gl_entry(
                document_id=doc_id,
                property_name=property_name or row.get('property_name'),
                account_code=row.get('account_code'),
                account_name=row.get('account_name'),
                entry_date=row.get('entry_date'),
                description=row.get('description'),
                debit=self._safe_float(row.get('debit')),
                credit=self._safe_float(row.get('credit')),
                balance=self._safe_float(row.get('balance')),
                period=row.get('period'),
                vendor=row.get('vendor'),
                reference=row.get('reference'),
                page_number=row.get('page_number'),
            )

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
        """Print a summary of batch processing results."""
        total = len(results)
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        total_time = sum(r.processing_time for r in results)

        print(f"\n{'='*60}")
        print(f"BATCH PROCESSING COMPLETE")
        print(f"{'='*60}")
        print(f"Total files:      {total}")
        print(f"Succeeded:        {succeeded}")
        print(f"Failed:           {failed}")
        print(f"Total time:       {total_time:.1f}s")
        if total > 0:
            print(f"Avg time/file:    {total_time/total:.1f}s")
        print(f"\nTerms extracted:  {sum(r.financial_terms_count for r in results)}")
        print(f"Clauses extracted: {sum(r.clauses_count for r in results)}")
        print(f"Table rows:       {sum(r.tabular_rows_count for r in results)}")

        if failed:
            print(f"\nFailed files:")
            for r in results:
                if not r.success:
                    print(f"  - {r.filename}: {r.error}")
        print(f"{'='*60}\n")
