"""
Entity registry — the mutable tree of funds, sub-funds, portfolios, and
property/deals that every module selects against.

Design (see docs/multi-property-plan.md):
  * A flat adjacency list: each entity names its `parent_id` (nullable), so the
    hierarchy is arbitrary depth and sub-funds are optional. Re-parenting is a
    single UPDATE — moving a deal under a fund, or an asset between portfolios,
    never touches the warehouse because fact rows are keyed by the entity's own
    id (== warehouse deal_id), which does not change when it moves.
  * Per-deal config (the assumptions currently hardcoded in the TIF / distribution
    / debt engines) lives in an editable `deal_config` table, keyed by deal + module.

This store is deliberately dependency-light (stdlib sqlite3 + json) and holds no
Flask state, so it is safe to use from request handlers and background threads.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS registry_entity (
    id                 TEXT PRIMARY KEY,
    type               TEXT NOT NULL,     -- fund | subfund | portfolio | deal | property | lease | disposition
    parent_id          TEXT,              -- nullable FK -> registry_entity.id
    label              TEXT NOT NULL,
    module             TEXT,              -- single-module binding (retail entities)
    modules            TEXT,              -- JSON array of modules (deal-analytics entities)
    warehouse_deal_id  TEXT,              -- for deal entities; defaults to id
    sort_order         INTEGER DEFAULT 0,
    meta               TEXT DEFAULT '{}', -- JSON blob (notes, portfolio_match, etc.)
    FOREIGN KEY (parent_id) REFERENCES registry_entity(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_parent ON registry_entity(parent_id);
CREATE INDEX IF NOT EXISTS idx_entity_type   ON registry_entity(type);

CREATE TABLE IF NOT EXISTS deal_config (
    deal_id     TEXT NOT NULL,
    module      TEXT NOT NULL,   -- tif | distribution | debt | ...
    config      TEXT NOT NULL,   -- JSON of externalized assumptions
    updated_at  REAL,
    PRIMARY KEY (deal_id, module)
);
"""

# Entity types that participate in the deal-analytics modules (warehouse-backed).
DEAL_TYPES = {"deal"}


class RegistryError(Exception):
    """Raised for invalid registry operations (e.g. cyclic re-parenting)."""


class RegistryStore:
    """SQLite-backed registry of entities + per-deal config."""

    def __init__(self, db_path: str = os.path.join("data", "registry.db")):
        self.db_path = db_path
        # SQLite connections are thread-affine, and this store is a process-wide
        # singleton shared across the threaded dev server's request threads. Give
        # each thread its own connection (thread-local) so a connection is never
        # used off the thread that created it. SQLite's own file locking makes
        # cross-thread writes safe, so no app-level lock is needed.
        self._local = threading.local()

    # ─── Lifecycle ───────────────────────────────────────────────────────
    def connect(self) -> "RegistryStore":
        self._c()  # ensure the calling thread has a connection + schema
        return self

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _c(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(REGISTRY_SCHEMA)
            conn.commit()
            self._local.conn = conn
        return conn

    # ─── Row mapping ─────────────────────────────────────────────────────
    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "type": row["type"],
            "parent_id": row["parent_id"],
            "label": row["label"],
            "module": row["module"],
            "modules": json.loads(row["modules"]) if row["modules"] else [],
            "warehouse_deal_id": row["warehouse_deal_id"] or row["id"],
            "sort_order": row["sort_order"],
            "meta": json.loads(row["meta"]) if row["meta"] else {},
        }

    # ─── Reads ───────────────────────────────────────────────────────────
    def get_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        cur = self._c().execute(
            "SELECT * FROM registry_entity WHERE id = ?", (entity_id,))
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def all_entities(self) -> list[dict[str, Any]]:
        cur = self._c().execute(
            "SELECT * FROM registry_entity ORDER BY sort_order, label")
        return [self._row_to_entity(r) for r in cur.fetchall()]

    def children_of(self, parent_id: Optional[str]) -> list[dict[str, Any]]:
        if parent_id is None:
            cur = self._c().execute(
                "SELECT * FROM registry_entity WHERE parent_id IS NULL "
                "ORDER BY sort_order, label")
        else:
            cur = self._c().execute(
                "SELECT * FROM registry_entity WHERE parent_id = ? "
                "ORDER BY sort_order, label", (parent_id,))
        return [self._row_to_entity(r) for r in cur.fetchall()]

    def ancestors(self, entity_id: str) -> list[dict[str, Any]]:
        """Return [parent, grandparent, ...] up to the root (for rollup paths)."""
        chain: list[dict[str, Any]] = []
        node = self.get_entity(entity_id)
        seen = {entity_id}
        while node and node["parent_id"]:
            parent = self.get_entity(node["parent_id"])
            if not parent or parent["id"] in seen:
                break
            chain.append(parent)
            seen.add(parent["id"])
            node = parent
        return chain

    def get_tree(self) -> list[dict[str, Any]]:
        """Nested tree of all entities, each with a `children` list."""
        entities = self.all_entities()
        by_id = {e["id"]: {**e, "children": []} for e in entities}
        roots: list[dict[str, Any]] = []
        for e in entities:
            node = by_id[e["id"]]
            parent = by_id.get(e["parent_id"]) if e["parent_id"] else None
            (parent["children"] if parent else roots).append(node)
        return roots

    def descendants(self, node_id: str) -> list[dict[str, Any]]:
        """All entities beneath `node_id` (any depth), excluding the node itself.
        Built from one adjacency map so it never re-queries per level."""
        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        for e in self.all_entities():
            children_by_parent.setdefault(e["parent_id"], []).append(e)
        out: list[dict[str, Any]] = []
        stack = list(children_by_parent.get(node_id, []))
        seen = {node_id}
        while stack:
            node = stack.pop()
            if node["id"] in seen:
                continue
            seen.add(node["id"])
            out.append(node)
            stack.extend(children_by_parent.get(node["id"], []))
        return out

    def descendant_deals(self, node_id: str) -> list[dict[str, Any]]:
        """The deal leaves that roll up into `node_id`. If the node is itself a
        deal, it returns just that deal — so a rollup works at any level (fund,
        sub-fund, portfolio, or a single deal)."""
        node = self.get_entity(node_id)
        if node and node["type"] == "deal":
            return [node]
        return [e for e in self.descendants(node_id) if e["type"] == "deal"]

    def list_by_module(self, module: str) -> list[dict[str, Any]]:
        """Entities usable by a given module: single-module retail bindings plus
        deal-analytics entities whose `modules` array includes the module."""
        out = []
        for e in self.all_entities():
            if e["module"] == module or module in e["modules"]:
                out.append(e)
        return out

    def list_deals(self) -> list[dict[str, Any]]:
        cur = self._c().execute(
            "SELECT * FROM registry_entity WHERE type = 'deal' "
            "ORDER BY sort_order, label")
        return [self._row_to_entity(r) for r in cur.fetchall()]

    # ─── Writes ──────────────────────────────────────────────────────────
    def upsert_entity(
        self,
        entity_id: str,
        type: str,
        label: str,
        parent_id: Optional[str] = None,
        module: Optional[str] = None,
        modules: Optional[list[str]] = None,
        warehouse_deal_id: Optional[str] = None,
        sort_order: int = 0,
        meta: Optional[dict] = None,
    ) -> dict[str, Any]:
        self._c().execute(
            """INSERT INTO registry_entity
                 (id, type, parent_id, label, module, modules,
                  warehouse_deal_id, sort_order, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 type=excluded.type, parent_id=excluded.parent_id,
                 label=excluded.label, module=excluded.module,
                 modules=excluded.modules,
                 warehouse_deal_id=excluded.warehouse_deal_id,
                 sort_order=excluded.sort_order, meta=excluded.meta""",
            (entity_id, type, parent_id, label, module,
             json.dumps(modules) if modules else None,
             warehouse_deal_id, sort_order,
             json.dumps(meta or {})),
        )
        self._c().commit()
        return self.get_entity(entity_id)  # type: ignore[return-value]

    def move_entity(self, entity_id: str, new_parent_id: Optional[str]) -> dict[str, Any]:
        """Re-parent an entity. Rejects self-parenting and cycles."""
        if not self.get_entity(entity_id):
            raise RegistryError(f"unknown entity: {entity_id}")
        if new_parent_id is not None:
            if new_parent_id == entity_id:
                raise RegistryError("an entity cannot be its own parent")
            if not self.get_entity(new_parent_id):
                raise RegistryError(f"unknown parent: {new_parent_id}")
            # Walk up from the proposed parent; if we reach entity_id, it's a cycle.
            for anc in self.ancestors(new_parent_id):
                if anc["id"] == entity_id:
                    raise RegistryError("move would create a cycle")
            # ancestors() stops at the current root, so also check the parent itself
            if new_parent_id == entity_id:
                raise RegistryError("move would create a cycle")
        self._c().execute(
            "UPDATE registry_entity SET parent_id = ? WHERE id = ?",
            (new_parent_id, entity_id))
        self._c().commit()
        return self.get_entity(entity_id)  # type: ignore[return-value]

    def delete_entity(self, entity_id: str) -> None:
        """Delete an entity; children are re-parented to its parent (not orphaned)."""
        ent = self.get_entity(entity_id)
        if not ent:
            return
        self._c().execute(
            "UPDATE registry_entity SET parent_id = ? WHERE parent_id = ?",
            (ent["parent_id"], entity_id))
        self._c().execute("DELETE FROM registry_entity WHERE id = ?", (entity_id,))
        self._c().commit()

    # ─── Per-deal config ─────────────────────────────────────────────────
    def get_deal_config(self, deal_id: str, module: str) -> Optional[dict[str, Any]]:
        cur = self._c().execute(
            "SELECT config FROM deal_config WHERE deal_id = ? AND module = ?",
            (deal_id, module))
        row = cur.fetchone()
        return json.loads(row["config"]) if row else None

    def set_deal_config(self, deal_id: str, module: str, config: dict[str, Any]) -> None:
        self._c().execute(
            """INSERT INTO deal_config (deal_id, module, config, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(deal_id, module) DO UPDATE SET
                 config=excluded.config, updated_at=excluded.updated_at""",
            (deal_id, module, json.dumps(config), time.time()))
        self._c().commit()

    # ─── Seeding ─────────────────────────────────────────────────────────
    def is_empty(self) -> bool:
        cur = self._c().execute("SELECT COUNT(*) AS n FROM registry_entity")
        return cur.fetchone()["n"] == 0

    def seed_from(self, entities: list[dict[str, Any]]) -> int:
        """Insert seed entities only if the registry is empty (idempotent boot)."""
        if not self.is_empty():
            return 0
        n = 0
        for e in entities:
            self.upsert_entity(
                entity_id=e["id"], type=e["type"], label=e["label"],
                parent_id=e.get("parent_id"), module=e.get("module"),
                modules=e.get("modules"), warehouse_deal_id=e.get("warehouse_deal_id"),
                sort_order=e.get("sort_order", 0), meta=e.get("meta"),
            )
            n += 1
        return n
