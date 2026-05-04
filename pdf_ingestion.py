"""
PDF Ingestion Pipeline for Real Estate Document Extractor.

Handles both digital (text-based) PDFs and scanned PDFs requiring OCR.
Auto-detects document type and routes through the appropriate pipeline.
All processing is fully local — no data leaves the device.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    """Represents extracted content from a single PDF page."""
    page_number: int
    text: str
    tables: List[List[List[str]]] = field(default_factory=list)  # list of tables, each table = list of rows
    is_scanned: bool = False
    ocr_confidence: Optional[float] = None


@dataclass
class DocumentContent:
    """Represents all extracted content from a PDF."""
    filepath: str
    filename: str
    pages: List[PageContent] = field(default_factory=list)
    page_count: int = 0
    is_scanned: bool = False
    avg_ocr_confidence: Optional[float] = None
    file_hash: str = ""

    @property
    def full_text(self) -> str:
        """Concatenate all page text."""
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def all_tables(self) -> List[Tuple[int, List[List[str]]]]:
        """Get all tables with their page numbers."""
        result = []
        for page in self.pages:
            for table in page.tables:
                result.append((page.page_number, table))
        return result


def compute_file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of a file for duplicate detection."""
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def is_scanned_pdf(filepath: str, sample_pages: int = 3) -> bool:
    """
    Detect whether a PDF is scanned (image-based) or digital (text-based).
    Checks the first few pages for extractable text. If text is minimal,
    it's likely a scanned document.
    """
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            pages_to_check = min(sample_pages, len(pdf.pages))
            total_chars = 0
            for i in range(pages_to_check):
                text = pdf.pages[i].extract_text() or ""
                total_chars += len(text.strip())

            # If average chars per page is very low, likely scanned
            avg_chars = total_chars / max(pages_to_check, 1)
            return avg_chars < 50  # threshold: less than 50 chars/page = scanned
    except Exception as e:
        logger.warning(f"Error detecting PDF type: {e}")
        return False


def preprocess_image_for_ocr(image):
    """
    Apply image preprocessing to improve OCR accuracy on dirty scans.
    Uses OpenCV for deskewing, thresholding, and noise removal.

    Args:
        image: PIL Image object

    Returns:
        Preprocessed PIL Image
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image

        # Convert PIL to OpenCV format
        img_array = np.array(image)

        # Convert to grayscale if needed
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array

        # Noise removal with bilateral filter (preserves edges)
        denoised = cv2.bilateralFilter(gray, 9, 75, 75)

        # Adaptive thresholding for varying lighting conditions
        thresh = cv2.adaptiveThreshold(
            denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # Deskew detection
        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) > 100:
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = -(90 + angle)
            else:
                angle = -angle

            # Only deskew if angle is significant but not extreme
            if 0.5 < abs(angle) < 15:
                (h, w) = thresh.shape[:2]
                center = (w // 2, h // 2)
                M = cv2.getRotationMatrix2D(center, angle, 1.0)
                thresh = cv2.warpAffine(
                    thresh, M, (w, h),
                    flags=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REPLICATE
                )

        # Morphological operations to clean up
        kernel = np.ones((1, 1), np.uint8)
        thresh = cv2.dilate(thresh, kernel, iterations=1)
        thresh = cv2.erode(thresh, kernel, iterations=1)

        return Image.fromarray(thresh)

    except ImportError:
        logger.warning("OpenCV not installed. Skipping image preprocessing.")
        return image


def extract_text_digital(filepath: str) -> DocumentContent:
    """
    Extract text and tables from a digital (text-based) PDF using pdfplumber.
    """
    import pdfplumber

    doc = DocumentContent(
        filepath=filepath,
        filename=os.path.basename(filepath),
        file_hash=compute_file_hash(filepath),
        is_scanned=False
    )

    with pdfplumber.open(filepath) as pdf:
        doc.page_count = len(pdf.pages)

        for i, page in enumerate(pdf.pages):
            # Extract text
            text = page.extract_text() or ""

            # Extract tables
            tables = []
            try:
                page_tables = page.extract_tables()
                if page_tables:
                    for table in page_tables:
                        # Clean up table cells
                        cleaned = []
                        for row in table:
                            cleaned_row = [
                                (cell.strip() if cell else "") for cell in row
                            ]
                            cleaned.append(cleaned_row)
                        tables.append(cleaned)
            except Exception as e:
                logger.warning(f"Table extraction failed on page {i+1}: {e}")

            doc.pages.append(PageContent(
                page_number=i + 1,
                text=text,
                tables=tables,
                is_scanned=False
            ))

    return doc


def extract_text_ocr(filepath: str, preprocess: bool = True,
                     language: str = 'eng') -> DocumentContent:
    """
    Extract text from a scanned PDF using Tesseract OCR.

    Pipeline:
    1. Convert PDF pages to images (pdf2image)
    2. Optionally preprocess images (OpenCV)
    3. Run OCR (pytesseract)
    """
    from pdf2image import convert_from_path
    import pytesseract
    from PIL import Image

    doc = DocumentContent(
        filepath=filepath,
        filename=os.path.basename(filepath),
        file_hash=compute_file_hash(filepath),
        is_scanned=True
    )

    # Convert PDF to images (300 DPI for good OCR accuracy)
    logger.info(f"Converting PDF to images: {filepath}")
    images = convert_from_path(filepath, dpi=300)
    doc.page_count = len(images)

    confidences = []

    for i, image in enumerate(images):
        logger.info(f"OCR processing page {i+1}/{len(images)}")

        # Preprocess if enabled
        if preprocess:
            processed = preprocess_image_for_ocr(image)
        else:
            processed = image

        # Run OCR with confidence data
        ocr_data = pytesseract.image_to_data(
            processed, lang=language, output_type=pytesseract.Output.DICT
        )

        # Extract text
        text = pytesseract.image_to_string(processed, lang=language)

        # Calculate average confidence for this page
        page_confidences = [
            int(c) for c in ocr_data['conf'] if str(c).isdigit() and int(c) > 0
        ]
        avg_conf = sum(page_confidences) / len(page_confidences) if page_confidences else 0
        confidences.append(avg_conf)

        # Attempt table detection via OCR'd text structure
        # (Basic approach — tables from scanned docs are harder)
        tables = _detect_tables_from_ocr_text(text)

        doc.pages.append(PageContent(
            page_number=i + 1,
            text=text,
            tables=tables,
            is_scanned=True,
            ocr_confidence=avg_conf
        ))

    doc.avg_ocr_confidence = sum(confidences) / len(confidences) if confidences else 0
    return doc


def _detect_tables_from_ocr_text(text: str) -> List[List[List[str]]]:
    """
    Basic table detection from OCR'd text.
    Looks for lines with consistent column-like spacing.
    This is a heuristic approach — works for simple tables.
    """
    lines = text.strip().split('\n')
    tables = []
    current_table = []

    for line in lines:
        # Check if line looks like a table row (multiple whitespace-separated columns)
        parts = line.split()
        if len(parts) >= 3 and any(c.isdigit() for c in line):
            # Likely a data row with numbers
            current_table.append(parts)
        else:
            if len(current_table) >= 2:
                tables.append(current_table)
            current_table = []

    if len(current_table) >= 2:
        tables.append(current_table)

    return tables


def ingest_pdf(filepath: str, force_ocr: bool = False,
               preprocess: bool = True) -> DocumentContent:
    """
    Main entry point for PDF ingestion.

    Auto-detects whether the PDF is digital or scanned, and routes
    through the appropriate extraction pipeline.

    Args:
        filepath: Path to the PDF file
        force_ocr: If True, always use OCR even for digital PDFs
        preprocess: If True, apply image preprocessing before OCR

    Returns:
        DocumentContent with all extracted text and tables
    """
    filepath = os.path.abspath(filepath)

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"PDF not found: {filepath}")

    if not filepath.lower().endswith('.pdf'):
        raise ValueError(f"Not a PDF file: {filepath}")

    logger.info(f"Ingesting PDF: {filepath}")

    # Detect if scanned
    scanned = force_ocr or is_scanned_pdf(filepath)

    if scanned:
        logger.info("Detected scanned PDF — using OCR pipeline")
        doc = extract_text_ocr(filepath, preprocess=preprocess)
    else:
        logger.info("Detected digital PDF — using text extraction pipeline")
        doc = extract_text_digital(filepath)

    # Log summary
    total_chars = sum(len(p.text) for p in doc.pages)
    total_tables = sum(len(p.tables) for p in doc.pages)
    logger.info(
        f"Extracted {total_chars} chars, {total_tables} tables "
        f"from {doc.page_count} pages"
    )

    return doc
