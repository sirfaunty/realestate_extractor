"""
Locate the bespoke engines behind the deal modules.

Some modules (Portfolio Cash Flow, Lease Abstraction, Disposition Diligence)
wrap engines built for a specific engagement. Those engines and their deal
registry live OUTSIDE this repo (a private sibling repo). A gitignored
`bespoke_engines.json` at the repo root says where they are, e.g.:

    {
      "registry": "../capactive-sponsor/properties.json",
      "portfolio_cashflow": "../capactive-sponsor/<engine folder>",
      "lease_abstraction": "../capactive-sponsor/<engine folder>",
      "disposition_diligence": "../capactive-sponsor/<engine folder>"
    }

Relative paths are resolved from the repo root. A module whose engine isn't
configured gets a path that doesn't exist, so it shows its empty state (as on
a deploy without the engines) instead of failing.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO_ROOT, 'bespoke_engines.json')
_MISSING = os.path.join(REPO_ROOT, '_engine_not_installed')


def _config() -> dict:
    try:
        with open(CONFIG, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _resolve(key: str) -> str:
    path = _config().get(key)
    if not path:
        return os.path.join(_MISSING, key)
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(REPO_ROOT, path))


def registry_path() -> str:
    """The deal registry (JSON with a 'properties' list)."""
    return _resolve('registry')


def data_path(key: str) -> str:
    """A configured data file or folder (e.g. 'portfolio_warehouse',
    'portfolio_rentroll'); a non-existent path when not configured."""
    return _resolve(key)


def engine_root(module: str) -> str:
    """The engine folder for a module, added (with its parent) to sys.path so
    both its top-level scripts and its package import in-process."""
    root = _resolve(module)
    for p in (os.path.dirname(root), root):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    return root
