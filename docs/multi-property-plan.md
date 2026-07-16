# Multi-Property / Multi-Deal Plan

_Audit + design for making every Capactive module selectable across properties, portfolios, and (new) funds and sub-funds. Framework-first: nothing about Chamberlain's current output changes until real second-deal data lands._

## 1. Where things stand today

Capactive has 15 modules that fall into three groups, and each group has a very different relationship to "which property am I looking at."

**Retail modules — already deal-aware.** Barrington (Portfolio Cash Flow), Southtown (Lease Abstraction), and Midway (Disposition Diligence) already read from the shared `properties.json` registry and show a per-module selector, with each deal's inputs living in its own `<engine>/data/<slug>/` and `source_docs/<slug>/` folder. These are the template for everything else.

**Market-analytics modules — already multi-property by nature.** Inventory, Sales Comps, Scorecard, Lease Analysis, Market Intel, and Office range over the whole ~189K-property warehouse. "Selection" there is search and filter, not a single-deal picker, so they need no change for this effort.

**Deal-analytics modules — the actual gap.** TIF, Partnership, Debt, Distribution, Proforma, and Closing Books are all wired to the single Chamberlain deal. This is where "one property per module" bites, and it's the scope of this plan.

## 2. The good news: the data layer is already multi-deal

The analytical warehouse was built deal-aware from the start. Every deal-analytics fact table carries a `deal_id` column — `fact_deal_summary`, `fact_proforma_annual`, `fact_distribution_annual`, `fact_debt_annual`, `fact_tif_annual`, `fact_tif_comparison`, and `dim_partner`. And every writer in `warehouse/deal_analytics.py` already takes `deal_id` as its first argument (`persist_proforma(deal_id, …)`, `persist_debt(deal_id, …)`, etc.), keying each row by `deal_id` + `tif_scenario`.

In other words, two deals can already coexist in the warehouse side by side. What's missing is upstream: the modules hardcode Chamberlain assumptions and never pass a real `deal_id` through. The single line `DEAL_ID = 'chamberlain'` in `warehouse/deal_analytics.py` even flags itself: _"default deal; parameterize when multi-deal."_

There's also a working precedent for cross-module state. The base template already threads a `?tif=<scenario>` query parameter across every module via `capactive.getTif()` and `capactive.moduleLink()`. A `?deal=<id>` parameter can follow the exact same mechanism, which keeps the change small and consistent with what's already there.

## 3. What's actually hardcoded, module by module

The blockers are concentrated in three engines that embed Chamberlain constants, plus a couple of smaller items.

**TIF Analysis** — `modules/tif_analysis/engine.py` bakes the assumptions into `TIFAssumptions` defaults and the module-level `CHAMBERLAIN_SCENARIOS` dict (the four TMV values). The engine is a singleton (`_get_engine()` takes no deal).

**Distribution** — `modules/distribution/engine.py` hardcodes `CHAMBERLAIN_PARTNERS`, total equity, and the KA 75% / IDP 25% split. The route persists to `'chamberlain'` as a string literal.

**Debt** — `modules/debt_analysis/engine.py` hardcodes `CHAMBERLAIN_LOAN` (lender, principal, rate, term) plus MIP / capex / surplus-note structures. The route also persists to the literal `'chamberlain'`.

**Proforma** and **Partnership Dashboard** are nearly deal-agnostic already. Proforma reads property-scoped extracted data and takes a `property_id`; Partnership aggregates the other engines and only needs `deal_id` threaded through its sub-engine calls and a dynamic report filename (today it's `Chamberlain_Investor_Report_*.docx`).

**Closing Books** is the heaviest single item: it points at a hardcoded SQLite file, `data/chamberlain_warehouse_v3.sqlite`. Multi-deal here means a database (or a deal column) per deal, so this one is best deferred.

The recurring pattern across all of them is the same three-step fix: (1) move the hardcoded constants into a per-deal config file, (2) read `deal_id` from the request and load that deal's config, and (3) pass `deal_id` into the warehouse writer that already accepts it.

## 4. Proposed model: one registry, four levels above the fact tables

You flagged that fund and sub-fund layers are coming. Designing those in now — rather than retrofitting later — is the right call, because the warehouse's `deal_id` becomes the natural leaf that everything rolls up from.

The proposal is a single shared registry describing an entity **tree**, using a flat adjacency list (each node names its `parent`) so depth is arbitrary and new levels never require a schema change:

```
Fund
└── Sub-fund
    └── Portfolio
        └── Property / Deal   ← this id == warehouse deal_id
```

A unified `registry.json` (superseding `properties.json`, which folds in as the portfolio/property entries):

```jsonc
{
  "entities": [
    { "id": "fund_ka",        "type": "fund",      "parent": null,          "label": "Kraus-Anderson Fund" },
    { "id": "subfund_retail", "type": "subfund",   "parent": "fund_ka",     "label": "Retail Sleeve" },

    { "id": "chamberlain",    "type": "deal",      "parent": "subfund_retail",
      "label": "Chamberlain",
      "modules": ["tif","distribution","debt","partnership","proforma"],
      "config": "config/deals/chamberlain.yaml",
      "warehouse_deal_id": "chamberlain" },

    { "id": "barrington_portfolio", "type": "portfolio", "parent": "subfund_retail",
      "label": "Barrington Portfolio", "module": "barrington" },
    { "id": "southtown_dhos", "type": "lease", "parent": "subfund_retail",
      "label": "Southtown / Dick's House of Sport", "module": "southtown" },
    { "id": "midway_marketplace", "type": "disposition", "parent": "subfund_retail",
      "label": "Midway Marketplace", "module": "midway" }
  ]
}
```

Three design points make this work cleanly:

The entity `id` **is** the warehouse `deal_id`. There's no separate mapping table — the registry is simply the human-readable tree over the ids the fact tables already use.

A deal node isn't bound to one module. Unlike retail entities (each tied to a single module), a deal-analytics entity lists the modules it participates in, and those modules all read the same warehouse rows and the same per-deal config. That mirrors how TIF/Debt/Distribution/Partnership already share the Chamberlain deal today.

Per-deal assumptions live in an **editable, DB-backed store** (not static files). This is where the ~200 lines of constants currently hardcoded in the three engines move to. Engines load config by `deal_id`; the default deal reproduces today's numbers exactly. Storing config in the database (rather than YAML on disk) is a deliberate choice driven by two requirements below: config must be editable, and entities must be re-parentable at runtime.

**Editability and re-parenting are first-class.** The registry is a mutable store, not a fixed file: an entity's `parent` can change at any time. Standing up a deal or portfolio first and attaching it to a fund later, or moving an asset from one portfolio into another, is a single `parent` update — no data migration, because the fact tables are keyed by the entity's own `deal_id`, which never changes when it moves. A static `registry.json` still ships as the initial **seed**, but the runtime source of truth is the mutable store so these edits (eventually from the UI) are safe and transactional.

The rollup payoff: because every fact row is keyed by `deal_id`, and the registry maps `deal_id → portfolio → sub-fund → fund`, fund- and sub-fund-level reporting becomes an aggregation across the leaf `deal_id`s under a node. That's the whole reason to put the hierarchy in now.

## 5. Selector UX

Reuse the `?tif` pattern. Add `capactive.getDeal()` to the base template, have `capactive.moduleLink()` carry `?deal=<id>` alongside `?tif`, and add one deal picker to the nav. Deal-analytics routes read `deal_id = request.args.get('deal', <default>)`. Because the default stays `chamberlain`, the app looks and behaves identically until someone actively picks another deal.

The picker itself can start as a flat dropdown grouped by fund → sub-fund (populated from the registry tree) and grow into a proper cascading selector when there are enough entities to warrant it. Retail modules keep their existing in-page selector but source it from the same unified registry, filtered to that module.

## 6. Phased rollout

**Phase 0 — Framework, zero behavior change (do now).** Introduce `registry.json` with Chamberlain as the sole deal under a default fund/sub-fund and the retail entities migrated in. Move the three engines' constants into `config/deals/chamberlain.yaml` and have engines load config by `deal_id`. Add `?deal` routing, `capactive.getDeal()`, and the nav picker (defaulting to Chamberlain). Thread `deal_id` through the six routes → engines → warehouse writers. Net effect: byte-for-byte the same output today, but every layer is deal-aware. **Guardrail:** a golden-value regression test asserting the config-loaded engines reproduce the current Chamberlain figures exactly (the TIF math is already verified; this locks it).

**Phase 1 — Second deal (when data lands, ~1 week out).** Add the new deal(s) to the registry and a `config/deals/<id>.yaml`, load their warehouse rows, and validate the picker with two real deals end to end.

**Phase 2 — Fund / sub-fund reporting.** Build rollup views that aggregate the fact tables across all `deal_id`s under a fund or sub-fund, and a fund-level dashboard. This is the layer you flagged for "cleaner reporting."

**Phase 3 — Cleanup and the heavy item.** Dynamic report filenames, remove the `DEAL_ID` default, and give Closing Books a per-deal database (or deal-scoped rows). Deferred because it's the highest-effort, lowest-frequency piece.

## 7. Effort and risk

Phases 0–1 are the bulk of the value and are low-risk, because the warehouse is already multi-deal and the `?tif` precedent proves the cross-module wiring. The main risk is regression on Chamberlain's numbers when constants move to config — fully mitigated by the golden-value test in Phase 0. Closing Books is the only genuinely large refactor and is intentionally last. Rough order of magnitude: Phase 0 a few focused days, Phase 1 short once data exists, Phase 2 moderate, Phase 3 as-needed.

## 8. Decisions (resolved)

**Sub-fund is optional.** A fund may hold portfolios and property/deals directly, with no sub-fund in between. The adjacency-list model already supports this — a node's `parent` can be a fund, a sub-fund, or a portfolio — so no structural change is needed; the picker simply skips the sub-fund grouping level when there isn't one.

**Config and hierarchy are mutable and DB-backed.** Both the entity tree and per-deal config live in an editable store, not static files, so entities can be re-parented (deal → fund later, asset moved between portfolios) and assumptions edited without a migration. See Section 4.

**Closing Books stays as-is for now**, deferred to Phase 3. Noted for when new data arrives: the end goal is that **every** module is usable across every layer of the hierarchy — the macro fund/portfolio view and the micro per-deal/asset view from the same tool. That cross-layer reach is the core value, so Phase 2 (rollup reporting) and eventually bringing Closing Books and all modules fully into the hierarchy are what unlock it.
