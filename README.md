# Real Estate Document Extractor

A fully local PDF extraction tool for real estate documents. All processing happens on your device — **no data ever leaves your machine**.

## What It Does

Extracts structured data from real estate PDFs and stores it in a local SQLite database for searching, querying, and analysis.

**Supported document types:**

| Type | Extraction Mode | What's Extracted |
|------|----------------|------------------|
| Lease Agreements | Dual (legal + financial) | Clause text preserved verbatim + structured financial terms (rent, escalations, TI, etc.) |
| Loan Documents | Dual (legal + financial) | Covenants, default provisions + rate, term, amortization, LTV, DSCR |
| Purchase/Closing Docs | Dual (legal + financial) | Reps & warranties, conditions + price, cap rate, earnest money |
| Guarantee Agreements | Dual (legal + financial) | Scope, waivers, subrogation + guarantee amount, burn-off, covenants |
| Rent Rolls | Tabular | Unit-level tenant, SF, rent, lease dates, status |
| Operating Statements | Tabular | Line-item revenue, expenses, NOI with categorization |
| General Ledger | Tabular | Account-level transactions with dates, amounts, vendors |

## Prerequisites

### 1. Python 3.9+

### 2. Tesseract OCR (for scanned PDFs)

**macOS:**
```bash
brew install tesseract
```

**Ubuntu/Debian:**
```bash
sudo apt-get install tesseract-ocr
```

**Windows:**
Download from: https://github.com/UB-Mannheim/tesseract/wiki

### 3. Poppler (for PDF-to-image conversion)

**macOS:**
```bash
brew install poppler
```

**Ubuntu/Debian:**
```bash
sudo apt-get install poppler-utils
```

### 4. Ollama (for intelligent extraction — recommended)

Install from https://ollama.ai, then pull a model:

```bash
ollama pull llama3.1:8b
```

The tool works without Ollama (using rule-based extraction), but results are significantly better with it.

## Installation

```bash
cd realestate_extractor
pip install -r requirements.txt
```

## Quick Start

### Process a single file
```bash
python -m realestate_extractor process lease_agreement.pdf --type lease --property "123 Main St"
```

### Process a folder of documents
```bash
python -m realestate_extractor batch /path/to/pdfs --property "Portfolio A"
```

### Watch a folder for new files
```bash
python -m realestate_extractor watch /path/to/inbox --interval 10
```

### Query the database
```bash
# Portfolio summary
python -m realestate_extractor query --summary

# All financial terms from leases
python -m realestate_extractor query --terms --type lease

# Search for specific clauses
python -m realestate_extractor query --clauses assignment_subletting

# Rent roll data
python -m realestate_extractor query --rent-roll --property "123 Main"

# Full-text search
python -m realestate_extractor search "assignment clause"

# Export to CSV
python -m realestate_extractor export financial_terms financial_terms.csv
python -m realestate_extractor export rent_roll_entries rent_roll.csv
```

### Check system status
```bash
python -m realestate_extractor status
```

## How It Works

### For narrative documents (leases, loans, guarantees):

1. **PDF Ingestion** — Extracts text using pdfplumber (digital) or Tesseract OCR (scanned). Dirty scans get preprocessed with OpenCV for deskewing, denoising, and contrast enhancement.

2. **Document Classification** — Auto-detects document type using keyword scoring, with LLM fallback for ambiguous cases.

3. **Dual-Mode Extraction:**
   - **Legal Mode** — Identifies and extracts clauses by type (assignment, default, insurance, etc.), preserving the complete original language with section references.
   - **Financial Mode** — Pulls structured terms (rent, rates, dates, amounts) as normalized key-value pairs ready for analysis.

4. **Storage** — Everything goes into SQLite with full-text search indexing.

### For tabular documents (rent rolls, operating statements, GL):

1. **Table Detection** — pdfplumber identifies table structures in the PDF.
2. **Column Mapping** — Headers are matched to standardized field names using fuzzy matching against known aliases.
3. **LLM Fallback** — If tables can't be detected structurally (common with scanned docs), the LLM parses the text into rows.

## Database Schema

The SQLite database contains these tables:

- `documents` — Master record for each processed PDF
- `document_fulltext` — Page-level full-text search (FTS5)
- `clauses` — Extracted legal clauses with full text and summaries
- `financial_terms` — Structured financial data points
- `rent_roll_entries` — Unit-level rent roll data
- `operating_statement_items` — Categorized income/expense line items
- `gl_entries` — General ledger transactions

All tables are cross-referenced by `document_id` for portfolio-level queries.

## Configuration

### Using a different LLM model

```bash
python -m realestate_extractor --model mistral:7b process document.pdf
```

### Custom Ollama URL

```bash
python -m realestate_extractor --ollama-url http://192.168.1.100:11434 process document.pdf
```

### Custom database path

```bash
python -m realestate_extractor --db /path/to/my_database.db process document.pdf
```

## Architecture

```
realestate_extractor/
├── __init__.py
├── __main__.py              # Entry point
├── cli.py                   # Command-line interface
├── database.py              # SQLite schema and operations
├── pdf_ingestion.py         # PDF text extraction + OCR pipeline
├── batch_processor.py       # Folder processing + watch mode
├── requirements.txt
├── extractors/
│   ├── __init__.py
│   ├── llm_client.py        # Local LLM client (Ollama)
│   └── extraction_engine.py # Legal, financial, and tabular extraction
└── templates/
    ├── __init__.py
    └── document_templates.py # Per-document-type extraction rules
```

## Privacy

This tool is designed from the ground up for data privacy:

- **No cloud services.** All PDF processing, OCR, and text extraction happen locally.
- **No external APIs.** The LLM runs on your machine via Ollama.
- **No telemetry.** The tool makes zero network requests (except to localhost for Ollama).
- **Local database.** All extracted data stays in a SQLite file on your disk.
- **No training data.** Your documents are never used to train any model.
