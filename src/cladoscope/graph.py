"""
graph.py -- the on-disk graph schema shared by translate.py, analyze.py, and
emit.py.

This module is the *only* place that should read or write entity_graph.json
directly. Everything else imports Graph/Entity/Edge from here and calls
load_graph()/save_graph(), so the schema has one authoritative definition
instead of several call sites that could drift apart from each other.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

SCHEMA_VERSION = 1

EdgeStrength = Literal["needs-def", "needs-decl"]


@dataclass
class Entity:
    """One USR-identified entity: where it's declared, where it's (if at
    all) defined. decl/def files being disjoint and both non-empty is the
    signal analyze.colocation_groups() uses to force files into the same
    module/level -- see [dcl.inline]/6."""

    name: str | None
    kind: str | None
    decl: list[str] = field(default_factory=list)
    defn: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"name": self.name, "kind": self.kind, "decl": self.decl, "defn": self.defn}

    @staticmethod
    def from_json(d: dict) -> "Entity":
        return Entity(
            name=d.get("name"),
            kind=d.get("kind"),
            decl=list(d.get("decl", [])),
            defn=list(d.get("defn", [])),
        )


@dataclass
class Edge:
    """One reference from `source` to `target`. `strength` says whether the
    reference could be satisfied by a forward declaration (needs-decl) or
    requires the complete definition (needs-def) -- only needs-def edges
    constrain the module DAG. `count` is how many times translate.py saw
    this exact (source, target, strength) triple across all parsed TUs."""

    source: str
    target: str
    strength: EdgeStrength
    count: int = 1

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "strength": self.strength,
            "count": self.count,
        }

    @staticmethod
    def from_json(d: dict) -> "Edge":
        strength = d["strength"]
        if strength not in ("needs-def", "needs-decl"):
            raise ValueError(f"unknown edge strength {strength!r}")
        return Edge(
            source=d["source"], target=d["target"], strength=strength, count=int(d.get("count", 1))
        )


@dataclass
class Graph:
    entities: dict[str, Entity]  # keyed by USR
    edges: list[Edge]
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "entities": {usr: e.to_json() for usr, e in self.entities.items()},
            "edges": [e.to_json() for e in self.edges],
        }


def save_graph(graph: Graph, path: str) -> None:
    with open(path, "w") as f:
        json.dump(graph.to_json(), f, indent=1)


def load_graph(path: str) -> Graph:
    with open(path) as f:
        raw = json.load(f)
    version = raw.get("schema_version", 0)
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version {version} != expected {SCHEMA_VERSION}. "
            "Re-run `translate` to regenerate this file, or update graph.py to "
            "handle the old format explicitly -- don't silently guess."
        )
    entities = {usr: Entity.from_json(e) for usr, e in raw["entities"].items()}
    edges = [Edge.from_json(e) for e in raw["edges"]]
    graph = Graph(entities=entities, edges=edges, schema_version=version)
    validate_graph(graph)
    return graph


def validate_graph(graph: Graph) -> None:
    """Basic invariant checks, run automatically by load_graph(). The point
    is to fail loudly here, at load time, rather than have analyze.py or
    emit.py produce a confusing downstream symptom from malformed input."""
    errors: list[str] = []
    for usr, e in graph.entities.items():
        if not e.decl and not e.defn:
            errors.append(f"entity {usr} ({e.name}) has neither decl nor defn files")
    for i, edge in enumerate(graph.edges):
        if not edge.source or not edge.target:
            errors.append(f"edge #{i} has an empty source/target: {edge}")
        if edge.count < 1:
            errors.append(f"edge #{i} has non-positive count: {edge}")
    if errors:
        shown = "\n  ".join(errors[:10])
        more = f"\n  ...and {len(errors) - 10} more" if len(errors) > 10 else ""
        raise ValueError(f"{len(errors)} graph validation error(s):\n  {shown}{more}")
