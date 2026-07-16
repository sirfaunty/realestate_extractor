"""
Request-scoped deal selection — the single place that answers "which deal is the
user looking at, and what are its settings?" for the deal-analytics modules.

Every deal-analytics route uses these helpers so the ?deal= convention, the
default-deal fallback, and the registry lookups live in exactly one place. All
functions are defensive: if the registry is unavailable they fall back to the
default deal / None config, so a module never breaks on a registry hiccup.
"""

from __future__ import annotations

from flask import request

from . import DEFAULT_DEAL, get_registry, resolve_deal


def deal_id_from_request() -> str:
    """The selected deal from ?deal=, or the default when absent."""
    return request.args.get('deal') or DEFAULT_DEAL


def warehouse_deal_id(deal_id: str | None) -> str:
    """The warehouse key for a deal (defaults to the deal's own id)."""
    try:
        return resolve_deal(deal_id)['warehouse_deal_id']
    except Exception:
        return deal_id or DEFAULT_DEAL


def deal_config(deal_id: str | None, module: str) -> dict | None:
    """Editable per-deal config for a module, or None (engine uses its defaults)."""
    try:
        return get_registry().get_deal_config(warehouse_deal_id(deal_id), module)
    except Exception:
        return None


def deal_label(deal_id: str | None) -> str:
    """Human label for a deal (used e.g. in exported report filenames)."""
    try:
        return resolve_deal(deal_id).get('label') or deal_id or DEFAULT_DEAL
    except Exception:
        return deal_id or DEFAULT_DEAL
