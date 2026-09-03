"""
Extraction-client environment check (docs/EXTRACTION_CLIENT_SETUP.md).

Run on the extraction machine after setup, and any time something feels
off. Reports each dependency PASS/FAIL with the fix for each failure.

    venv/Scripts/python client_doctor.py
    venv/Scripts/python client_doctor.py --instance   # also test sync creds
"""

import argparse
import importlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OLLAMA_URL = os.environ.get('CAPACTIVE_OLLAMA_URL', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('CAPACTIVE_OLLAMA_MODEL', 'llama3.1:8b')

results = []


def check(name, ok, fix=''):
    results.append((name, ok, fix))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ('' if ok else f'\n         fix: {fix}'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--instance', action='store_true',
                    help='also test capactive_sync.ini credentials')
    args = ap.parse_args()

    print('Capactive extraction client — environment check\n')

    # Python
    v = sys.version_info
    check(f'Python {v.major}.{v.minor}', v >= (3, 11),
          'install Python 3.11+ and recreate the venv')

    # Python packages
    for mod, pkg in [('flask', 'Flask'), ('pdfplumber', 'pdfplumber'),
                     ('fitz', 'PyMuPDF'), ('pytesseract', 'pytesseract'),
                     ('pdf2image', 'pdf2image'), ('cv2', 'opencv-python-headless'),
                     ('docx', 'python-docx'), ('requests', 'requests'),
                     ('duckdb', 'duckdb')]:
        try:
            importlib.import_module(mod)
            check(f'package {pkg}', True)
        except Exception:
            check(f'package {pkg}', False,
                  'venv/Scripts/pip install -r requirements.txt')

    # Tesseract
    tess = shutil.which('tesseract')
    check('Tesseract on PATH', bool(tess),
          'install from github.com/UB-Mannheim/tesseract/wiki and add its '
          'folder to PATH (or set pytesseract.tesseract_cmd)')
    if tess:
        try:
            out = subprocess.run([tess, '--version'], capture_output=True,
                                 text=True, timeout=10).stdout.splitlines()[0]
            print(f'         {out}')
        except Exception:
            pass

    # Poppler (pdftoppm) for pdf2image
    check('Poppler (pdftoppm) on PATH', bool(shutil.which('pdftoppm')),
          'install poppler for Windows (github.com/oschwartz10612/poppler-windows) '
          'and add its bin\\ folder to PATH')

    # Ollama reachable + model present
    try:
        import requests
        r = requests.get(f'{OLLAMA_URL}/api/tags', timeout=5)
        models = [m.get('name', '') for m in r.json().get('models', [])]
        check(f'Ollama reachable at {OLLAMA_URL}', True)
        have = any(m.split(':')[0] == OLLAMA_MODEL.split(':')[0] for m in models)
        check(f'model {OLLAMA_MODEL} pulled', have,
              f'ollama pull {OLLAMA_MODEL}')
    except Exception:
        check(f'Ollama reachable at {OLLAMA_URL}', False,
              'install from ollama.com, make sure the Ollama service is '
              'running (tray icon), then: ollama pull ' + OLLAMA_MODEL)
        check(f'model {OLLAMA_MODEL} pulled', False, 'see above')

    # Local data dir writable
    data = os.path.join(HERE, 'data')
    try:
        os.makedirs(data, exist_ok=True)
        test = os.path.join(data, '.doctor_write_test')
        open(test, 'w').close()
        os.remove(test)
        check('data/ writable', True)
    except Exception as e:
        check('data/ writable', False, f'fix permissions on {data} ({e})')

    # Sync config
    ini = os.path.join(HERE, 'capactive_sync.ini')
    check('capactive_sync.ini present', os.path.exists(ini),
          'create it with [sync] url= and token= (see setup doc §5)')

    if args.instance and os.path.exists(ini):
        try:
            sys.path.insert(0, os.path.dirname(HERE))
            from realestate_extractor.sync_client import load_config, session_for
            class A: url = None; token = None
            url, token = load_config(A)
            r = session_for(token).get(f'{url}/api/sync/ping', timeout=20)
            ok = r.status_code == 200
            check(f'instance handshake {url}', ok,
                  f'HTTP {r.status_code}: {r.text[:120]} — check token, or '
                  f'revoke + re-register the device if fingerprint changed')
            if ok:
                j = r.json()
                print(f"         device {j['device_name']} → org {j['org_id']}")
        except Exception as e:
            check('instance handshake', False, f'{e}')

    fails = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed"
          + (f" — fix: {', '.join(fails)}" if fails else ' — ready to extract.'))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
