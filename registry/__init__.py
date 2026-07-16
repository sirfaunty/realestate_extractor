"""
Shared entity registry: the mutable tree of funds / sub-funds / portfolios /
property-deals that modules select against, plus editable per-deal config.

Usage:
    from registry import get_registry, DEFAULT_DEAL
    reg = get_registry()
    deal = reg.get_entity(DEFAULT_DEAL)
    tif_cfg = reg.get_deal_config(deal_id, 'tif')   # None -> engine defaults

The store is process-wide and thread-safe for reads via short-lived cursors;
callers that mutate should do so from a single request handler.
"""

import json
import os
import threading

from .store import RegistryStore, RegistryError  # noqa: F401

# The deal shown when no ?deal= is supplied — preserves current behavior.
DEFAULT_DEAL = os.environ.get("CAPACTIVE_DEFAULT_DEAL", "chamberlain")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEED_PATH = os.path.join(_HERE, "seed.json")
_DB_PATH = os.environ.get(
    "CAPACTIVE_REGISTRY_DB",
    os.path.join(os.path.dirname(_HERE), "data", "registry.db"),
)

_lock = threading.Lock()
_store: RegistryStore | None = None


def _load_seed_entities() -> list[dict]:
    try:
        with open(_SEED_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("entities", [])
    except Exception:
        return []


def get_registry() -> RegistryStore:
    """Return the process-wide registry store, seeding it on first use."""
    global _store
    with _lock:
        if _store is None:
            store = RegistryStore(_DB_PATH)
            store.connect()
            if store.is_empty():
                store.seed_from(_load_seed_entities())
            _store = store
        return _store


def resolve_deal(deal_id: str | None) -> dict:
    """Resolve a ?deal= value to a registry deal entity, falling back to the
    default. Always returns a valid deal dict so callers never crash on a bad id."""
    reg = get_registry()
    ent = reg.get_entity(deal_id) if deal_id else None
    if ent is None or ent.get("type") != "deal":
        ent = reg.get_entity(DEFAULT_DEAL)
    return ent or {"id": DEFAULT_DEAL, "type": "deal",
                   "label": DEFAULT_DEAL, "warehouse_deal_id": DEFAULT_DEAL,
                   "modules": [], "meta": {}}
