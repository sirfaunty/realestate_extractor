# Southtown lease-abstraction warehouse (`southtown_db`)

Local, on-device engine that turns a commercial lease into a structured,
searchable **provision warehouse** with multi-tier **abstracts** — the first
backbone piece of the forthcoming retail-development module (the Barrington-style
no-code module is the eventual goal).

Source: Kraus-Anderson / Dick's House of Sport (DHOS) build-to-suit lease at
Southtown Shopping Center. Ported and generalized from the partner's handoff.

## Two pipelines

1. **Provision segmentation** — `segment_lease.py` / `build_warehouse.py`.
   Deterministic parse of the lease `.docx` heading structure (Heading 1 = article,
   Heading 2 = section) into a flat provision table + FTS index. No model involved,
   so it is exact and repeatable. **Verified to reproduce the partner's gold-standard
   warehouse: 113 provisions, matching section numbers/headings, body sizes within 2%.**

2. **Provision abstraction** — `abstract_lease.py` *(next)*.
   Runs each provision body through the **local Ollama engine** to generate three
   tiers (`detailed`, `detailed_summary`, `abstract_summary`), replacing the
   partner's hand-authoring with a repeatable local pipeline. Validated against the
   339 gold hand-authored abstracts (`data/gold_lease_warehouse.db`) as ground truth.

## Layout

```
southtown_db/
  schema.sql              warehouse schema (provisions, abstracts, exhibits, FTS)
  segment_lease.py        deterministic .docx -> provisions (VERIFIED)
  build_warehouse.py      segment + create DB + FTS index
  abstract_lease.py       local-engine abstraction (Phase 1, in progress)
  validate.py             tie-out vs. gold warehouse
  data/          (gitignored)  built + gold warehouses — never committed
  source_docs/   (gitignored)  lease .docx + exhibits — re-run locally, never leave device
```

## Run locally

```bash
# 1. Segment + build the warehouse (deterministic)
python build_warehouse.py \
    --lease "source_docs/lease_and_exhibits/DHOS Lease 122225.docx" \
    --db data/lease_warehouse.db

# 2. Confirm it ties out to the gold standard
python validate.py --built data/lease_warehouse.db --gold data/gold_lease_warehouse.db
```

## Security posture

Same rule as Barrington: **source documents and any text-laden DB never leave the
device.** `data/` and `source_docs/` are gitignored; only code is committed.
Extraction and abstraction both run locally (segmentation is pure Python;
abstraction uses the local Ollama model — nothing is sent to any external API).
