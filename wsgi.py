"""
Production WSGI entry point (docs/DEPLOY.md).

The app runs as the package `realestate_extractor.webapp`, so the repo's
parent directory must be importable — same trick run.py uses.

Serve with waitress (bundled in the container image):
    waitress-serve --host 0.0.0.0 --port 5000 --threads 8 wsgi:app
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realestate_extractor.webapp import app  # noqa: E402

if __name__ == '__main__':
    # fallback for quick local checks without waitress
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)),
            threaded=True)
