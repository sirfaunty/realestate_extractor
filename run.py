#!/usr/bin/env python3
"""
Launch the Real Estate Document Extractor web interface.

Usage:
    python run.py
    python run.py --port 8080

Then open http://localhost:5000 in your browser.
All processing happens locally on your device.
"""

import argparse
import sys
import os

# Add parent directory to path so the package can be found
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realestate_extractor.webapp import run_webapp


def main():
    parser = argparse.ArgumentParser(description='Launch RE Extractor web interface')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=5000, help='Port to run on (default: 5000)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()

    run_webapp(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
