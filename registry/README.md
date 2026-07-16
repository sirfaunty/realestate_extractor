# Registry — shared entity tree + per-deal config

The registry is the single source of truth for **which entities exist** (funds,
sub-funds, portfolios, property/deals) and **their editable settings**. It backs
the deal picker and makes every deal-analytics module (TIF, Distribution, Debt,
Partnership, Proforma) selectable across deals. See `docs/multi-property-plan.md`
for the design rationale.

## What's here

- `store.py` — `RegistryStore`, a SQLite-backed store (`data/registry.db`).
  - `registry_entity`: a flat adjacency list (`id`, `type`, `parent_id`, `label`,
    `module`/`modules`, `warehouse_deal_id`, `meta`). Arbitrary depth; sub-funds
    optional. Re-parenting is a single `parent_id` update — it never touches the
    warehouse, because fact rows are keyed by the entity's own id.
  - `deal_config`: editable per-deal, per-module assumptions (`deal_id`, `module`,
    JSON `config`). This is where the TIF / Distribution / Debt constants live.
- `__init__.py` — process-wide singleton (`get_registry()`), `DEFAULT_DEAL`, and
  `resolve_deal()` (always returns a valid deal, falling back to the default).
- `seed.json` — initial entities, loaded once when the entity table is empty.
- `deal_config_seed.py` — seeds Chamberlain's config by introspecting the engine
  defaults (so config == current behavior; guarded by `tests/test_deal_config_golden.py`).
- `deal_context.py` — request helpers every deal module uses (see below).
- `routes.py` — read API: `/api/registry/deals`, `/api/registry/tree`,
  `/api/registry/resolve`.

## How a module becomes deal-aware

Routes never parse `?deal=` themselves; they use `deal_context.py`:

```python
from registry.deal_context import (
    deal_id_from_request as _deal_id,     # ?deal= or DEFAULT_DEAL
    warehouse_deal_id as _warehouse_deal_id,   # key for warehouse writes
    deal_config as _deal_config,          # editable config, or None -> engine defaults
)

deal_id = _deal_id()
engine = build_engine_from(_deal_config(deal_id, 'tif'))   # None -> defaults
persist_x(_warehouse_deal_id(deal_id), result)
```

Every helper is defensive: if the registry is unavailable it falls back to the
default deal / `None` config, so a module never breaks on a registry hiccup, and
the default deal is byte-for-byte identical to the pre-registry behavior.

## Hierarchy & re-parenting

`type` ∈ `fund | subfund | portfolio | deal | property | lease | disposition`.
A `deal` node lists the `modules` it participates in and shares one warehouse
`deal_id`; retail entities bind to a single `module`. To restructure:

```python
reg = get_registry()
reg.upsert_entity('fund_1', 'fund', 'Flagship Fund')
reg.move_entity('chamberlain', 'fund_1')   # attach deal to a fund later
```

`move_entity` rejects cycles. Reporting rollups walk `ancestors()` to aggregate
warehouse facts under a fund or sub-fund.

## Adding a deal (Phase 1)

1. `upsert_entity(<id>, 'deal', '<label>', parent_id=<fund/subfund>, modules=[...])`
   (or add it to `seed.json` before first boot).
2. `set_deal_config(<id>, 'tif'|'distribution'|'debt', {...})` for its assumptions.
3. Load its warehouse rows. It now appears in the deal picker automatically.
