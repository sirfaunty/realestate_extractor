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

2. **Provision abstraction** — `abstract_lease.py` + `score_abstracts.py`.
   Runs each provision body through the **local Ollama engine** to generate three
   tiers (`detailed`, `detailed_summary`, `abstract_summary`), replacing the
   partner's hand-authoring with a repeatable local pipeline. Scored against the
   339 gold hand-authored abstracts (`data/gold_lease_warehouse.db`) as ground truth.

   **Result (llama3.1:8b):** 85-89% figure recall across the three tiers — a solid
   automated **first draft that still needs human review** (roughly 1 in 7 hard
   figures is dropped, and the model does not do gold's cross-provision synthesis).
   Higher fidelity would need a larger local model (e.g. qwen2.5:14b); tracked as a
   future lever. Context window is sized per provision so small ones run fully on
   GPU. Use `--missing` to resume, `--only <section>` to spot-check.

## Layout

```
southtown_db/
  schema.sql              warehouse schema (provisions, abstracts, exhibits, FTS)
  segment_lease.py        deterministic .docx -> provisions (VERIFIED)
  build_warehouse.py      segment + create DB + FTS index
  abstract_lease.py       local-engine abstraction (3 tiers; --missing/--only)
  score_abstracts.py      abstract quality vs. gold (figure recall, overlap, length)
  compendium_docx.py      Lease Abstract Compendium deliverable (Word .docx)
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

# 3. Abstract every provision with the local model (resume timed-out ones with --missing)
python abstract_lease.py --db data/lease_warehouse.db

# 4. Generate the Lease Abstract Compendium (Word deliverable)
python compendium_docx.py --db data/lease_warehouse.db --out Southtown_Lease_Abstract_Compendium.docx
```

## Deliverable — Lease Abstract Compendium

`compendium_docx.py` renders the warehouse into a Word document mirroring the
partner's deliverable #6: KA title block, a "How to Read" legend, then every
provision grouped by Article, each shown as a heading + a three-row table
(Detailed / Detailed Summary / Abstract Summary). Because the abstracts are
machine-generated, the document is labeled an **automated first draft for internal
review — not attorney work product**. Pass `--engine gold` to render the reference
abstracts instead of the local ones.

## Security posture

Same rule as Barrington: **source documents and any text-laden DB never leave the
device.** `data/` and `source_docs/` are gitignored; only code is committed.
Extraction and abstraction both run locally (segmentation is pure Python;
abstraction uses the local Ollama model — nothing is sent to any external API).
