# Windows Setup Context

This file provides context for setting up and troubleshooting the Capactive platform on a Windows machine. It supplements CLAUDE.md (which covers the codebase itself) with Windows-specific details.

## Project State (as of June 2026)

- **Branch**: `track-a/extractor-training`
- **~405 documents** ingested across 7 batches
- **Platform**: Flask web app with SQLite (WAL mode), local Ollama LLM for extraction
- **Port**: 5000 (Flask dev server via `python webapp.py`)

## Windows Prerequisites

| Tool | Install From | Verify With |
|------|-------------|-------------|
| Python 3.11+ | python.org/downloads (CHECK "Add to PATH" on first screen) | `python --version` |
| Git for Windows | git-scm.com/download/win | `git --version` |
| Tesseract OCR | github.com/UB-Mannheim/tesseract/wiki | `tesseract --version` |
| Poppler | github.com/oschwartz10612/poppler-windows/releases | `pdftoppm -h` |
| Ollama | ollama.com/download/windows | `ollama list` |

## Windows PATH Setup (Most Common Issue)

Tesseract and Poppler must be added to the system PATH manually:
1. Win+S → "Edit the system environment variables"
2. Click "Environment Variables"
3. Under System variables → select Path → Edit
4. Add these entries:
   - `C:\Program Files\Tesseract-OCR`
   - `C:\poppler\Library\bin` (adjust if extracted to a different path)
5. OK out of all dialogs, then **open a new terminal** (existing ones won't see the change)

## Virtual Environment

```bash
# Git Bash:
python -m venv venv
source venv/Scripts/activate

# PowerShell / CMD:
python -m venv venv
venv\Scripts\activate
```

Always activate before running `pip install` or `python webapp.py`. You should see `(venv)` in your prompt.

## Data Transfer

These folders are gitignored and must be copied manually from the Mac (~37 GB total):

- `data/` (~29 GB) — DuckDB warehouse, SQLite databases, CoStar exports, cache files
- `uploads/` (~8 GB) — original uploaded documents (PDFs, Excel, .msg emails)
- `*.pkl` (< 100 MB) — pickle caches in project root

Transfer via USB drive (exFAT format) or network share.

## Key Differences from Mac

- **No gunicorn** — gunicorn doesn't run on Windows. Use `python webapp.py` directly (Flask dev server). This is fine for local use.
- **Path separators** — the codebase uses `pathlib.Path` and `os.path.join` so paths should work cross-platform. If you see hardcoded `/` paths causing issues, that's a bug to fix.
- **Tesseract path** — if pytesseract can't find Tesseract, you may need to set it explicitly in the code: `pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'`
- **Ollama** — runs as a Windows service (auto-starts). If not running: `ollama serve` in a separate terminal.
- **Line endings** — Git for Windows defaults to "checkout Windows-style, commit Unix-style" which is correct. Don't change this.

## Common Troubleshooting

| Problem | Fix |
|---------|-----|
| `python` not recognized | Reinstall Python with "Add to PATH" checked, or add manually |
| `pip install` fails with permissions | Make sure venv is activated (look for `(venv)` in prompt) |
| `tesseract` not found | Add to PATH (see above), open new terminal |
| `pdftoppm` not found | Add Poppler bin to PATH (see above), open new terminal |
| `ModuleNotFoundError` | Activate venv first: `source venv/Scripts/activate` |
| Server starts but pages error | Check that `data/` folder was copied with all SQLite DBs |
| OCR fails on scanned PDFs | Verify Tesseract: `tesseract --version` in new terminal |
| Ollama connection refused | Run `ollama serve` or check it's in system tray |
| Port 5000 in use | `python webapp.py --port 5001` or kill the other process |

## Environment Variables (All Optional)

```
CAPACTIVE_DEV_MODE=1              # Skip login gate
CAPACTIVE_OLLAMA_URL=http://localhost:11434
CAPACTIVE_OLLAMA_MODEL=llama3.1:8b
CAPACTIVE_DATA_DIR=data
```

Set in PowerShell: `$env:CAPACTIVE_DEV_MODE = "1"`
Set permanently: System Environment Variables dialog (see PATH section above)

## Git Workflow

```bash
git pull                          # Always pull first
git add -A
git commit -m "message"
git push
```

Both machines push to the same branch (`track-a/extractor-training`). Always pull before starting work on either machine to avoid merge conflicts.
