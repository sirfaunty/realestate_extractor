# Capactive org instance (docs/PACKAGING_DESIGN.md §4, docs/DEPLOY.md)
#
# This image is the WEB INSTANCE: receives sync pushes from extraction
# devices, serves dashboards/modules/deliverables to access users.
# It deliberately ships WITHOUT Ollama — LLM extraction runs on extraction
# devices, never here. Tesseract/poppler are included only because the
# ingestion modules import their Python bindings at startup (and a client
# may choose to upload+process directly on the instance later).

FROM python:3.12-slim

# system deps: OCR + PDF rendering + opencv runtime + sqlite CLI (backups)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
        libglib2.0-0 \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# the app imports itself as the package `realestate_extractor`
WORKDIR /app/realestate_extractor

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt waitress

COPY . .

# All persistent state lives under data/ (org DBs, synced PDFs,
# deliverables) plus the central config DB, which we point into data/
# so ONE volume covers everything.
ENV CAPACTIVE_DATA_DIR=/app/realestate_extractor/data \
    CAPACTIVE_CONFIG_DB=/app/realestate_extractor/data/capactive_config.db \
    CAPACTIVE_DEV_MODE=0 \
    PYTHONUNBUFFERED=1

VOLUME ["/app/realestate_extractor/data"]

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/status', timeout=4)" || exit 1

CMD ["waitress-serve", "--host", "0.0.0.0", "--port", "5000", \
     "--threads", "8", "wsgi:app"]
