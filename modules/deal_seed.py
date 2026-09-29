"""Per-deployment deal assumptions, kept OUT of the code.

The deal-analytics engines (TIF, distribution, debt) ship with neutral example
defaults. A deployment that has a real deal supplies its numbers in a
gitignored JSON seed:

    data/deal_seeds/deal_defaults.json      (override: CAPACTIVE_DEAL_SEED)

Shape:
    {"deal_id": "...", "entity_name": "...",
     "tif":          {"assumptions": {...}, "scenarios": {name: tmv}},
     "distribution": {"assumptions": {...}, "default_cf": {"1": cf, ...}},
     "debt":         {"loan": {...}, "mip": {...}, "capex": {...},
                      "surplus": {...}, "property": {...}}}

Missing file / missing section → the engine's neutral example applies.
"""

from __future__ import annotations

import copy
import json
import logging
import os

logger = logging.getLogger(__name__)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cache: dict | None = None


def seed_path() -> str:
    return os.environ.get('CAPACTIVE_DEAL_SEED') or os.path.join(
        _REPO, 'data', 'deal_seeds', 'deal_defaults.json')


def load() -> dict:
    """The whole seed ({} when absent or unreadable). Cached per process."""
    global _cache
    if _cache is None:
        try:
            with open(seed_path(), 'r', encoding='utf-8') as fh:
                _cache = json.load(fh)
        except FileNotFoundError:
            _cache = {}
        except Exception as e:  # malformed seed must not take the app down
            logger.warning(f'deal seed unreadable ({seed_path()}): {e}')
            _cache = {}
    return _cache


def present() -> bool:
    return bool(load())


def section(name: str) -> dict:
    """A deep copy of one section ({} when absent) — callers may mutate it."""
    return copy.deepcopy(load().get(name) or {})


def reset_cache() -> None:
    """For tests: re-read the seed on next access."""
    global _cache
    _cache = None
