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

_HERE = os.path.dirname(os.path.abspath(__file__))
# A deployment's real entities live in a gitignored local seed; the repo copy
# is a neutral example.
_LOCAL_SEED_PATH = os.path.join(os.path.dirname(_HERE), "data", "deal_seeds",
                                "registry_seed.json")
_SEED_PATH = (_LOCAL_SEED_PATH if os.path.exists(_LOCAL_SEED_PATH)
              else os.path.join(_HERE, "seed.json"))


def _default_deal_id() -> str:
    """Deal shown when no ?deal= is supplied: env override, else the deal
    seed's deal_id, else the first deal in the registry seed."""
    env = os.environ.get("CAPACTIVE_DEFAULT_DEAL")
    if env:
        return env
    try:
        from modules import deal_seed
        if deal_seed.load().get("deal_id"):
            return deal_seed.load()["deal_id"]
    except Exception:
        pass
    try:
        with open(_SEED_PATH, "r", encoding="utf-8") as fh:
            for e in json.load(fh).get("entities", []):
                if e.get("type") == "deal":
                    return e["id"]
    except Exception:
        pass
    return "example_deal"


DEFAULT_DEAL = _default_deal_id()
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
            # The local registry seed holds a deployment's real deal
            # entities. A packaged instance must start EMPTY —
            # seeding is opt-in: on in dev mode, or CAPACTIVE_SEED_REGISTRY=1.
            seed_ok = (os.environ.get('CAPACTIVE_SEED_REGISTRY',
                       '1' if os.environ.get('CAPACTIVE_DEV_MODE') == '1' else '0') == '1')
            if store.is_empty() and seed_ok:
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
