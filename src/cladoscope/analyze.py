"""
analyze.py -- the fast step: load a Graph produced by translate.py and derive
useful structure from it (a sound module DAG, decl/def co-location groups,
SCCs, candidate root nodes). Nothing here touches libclang or re-parses
source; the only filesystem access is a plain textual scan of #include lines,
used solely to sanity-check entity-graph edges, not to derive the module DAG
itself. This is meant to be re-run against the same entity_graph.json many
times while you experiment with filtering/quotienting choices.
"""
from __future__ import annotations

import os
import re

import networkx as nx

from .graph import Edge, Graph

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[>"]', re.M)


def build_include_graph(source_root: str, extensions: tuple[str, ...] = (".hpp", ".cuh")) -> nx.DiGraph:
    """A plain textual #include graph over the project's own headers, used
    only to check whether an entity-graph edge is even *possible* -- this is
    NOT the basis for module-DAG decisions themselves (needs-def edges,
    filtered through this, are)."""
    files = set()
    for dirpath, _dirnames, filenames in os.walk(source_root):
        for fn in filenames:
            if fn.endswith(extensions):
                files.add(os.path.relpath(os.path.join(dirpath, fn), source_root))

    g = nx.DiGraph()
    g.add_nodes_from(files)
    for f in files:
        text = open(os.path.join(source_root, f), errors="replace").read()
        for m in _INCLUDE_RE.finditer(text):
            candidates = [
                os.path.normpath(os.path.join(os.path.dirname(f), m.group(1))),
                os.path.normpath(m.group(1)),
            ]
            for c in candidates:
                if c in files:
                    g.add_edge(f, c)
                    break
    return g


def filter_impossible_edges(graph: Graph, include_graph: nx.DiGraph) -> tuple[list[Edge], list[Edge]]:
    """Drop any entity-graph edge A->B where A does not transitively
    #include B. Such edges are libclang misattributions -- most commonly,
    cursors inside an implicit template instantiation get reported at the
    template *definition's* location rather than the instantiating TU's, so
    a heavily-instantiated template (e.g. `connect`) manufactures edges from
    itself to whatever the instantiating TU happened to also use.

    Returns (legal_edges, dropped_edges).
    """
    closure_cache: dict[str, frozenset[str]] = {}

    def closure(f: str) -> frozenset[str]:
        if f not in closure_cache:
            reachable = nx.descendants(include_graph, f) if f in include_graph else set()
            closure_cache[f] = frozenset(reachable | {f})
        return closure_cache[f]

    legal: list[Edge] = []
    dropped: list[Edge] = []
    for e in graph.edges:
        if e.source not in include_graph:
            # Outside the scanned source root (e.g. a system header) -- we
            # can't check this one, so don't drop it on this basis alone.
            legal.append(e)
        elif e.target in closure(e.source):
            legal.append(e)
        else:
            dropped.append(e)
    return legal, dropped


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def colocation_groups(graph: Graph) -> dict[str, frozenset[str]]:
    """Files that MUST live in the same module/level because some entity is
    declared in one and defined in another. Grounded in [dcl.inline]/6: an
    inline or constexpr function's definition must be in the same
    translation unit as its declaration, and each module unit is one TU, so
    a decl/def split across a module boundary is unconditionally ill-formed.

    Returns, for every file that appears in any entity's decl/def list, the
    frozenset of files it's grouped with (a file with no such constraint
    maps to a singleton set containing only itself)."""
    uf = UnionFind()
    for e in graph.entities.values():
        decl_files, defn_files = set(e.decl), set(e.defn)
        if decl_files and defn_files and not (decl_files & defn_files):
            for a in decl_files:
                for b in defn_files:
                    uf.union(a, b)

    all_files: set[str] = set()
    for e in graph.entities.values():
        all_files.update(e.decl)
        all_files.update(e.defn)

    root_to_members: dict[str, set[str]] = {}
    for f in all_files:
        root_to_members.setdefault(uf.find(f), set()).add(f)

    return {f: frozenset(members) for members in root_to_members.values() for f in members}


def quotient_graph(edges: list[Edge], groups: dict[str, frozenset[str]]) -> nx.DiGraph:
    """Collapse each co-location group into a single node (a frozenset of
    filenames) and re-point edges accordingly, so a group can never end up
    split across levels -- the grouping is enforced structurally here rather
    than left as something a human has to notice and fix by hand."""

    def node_for(f: str) -> frozenset[str]:
        return groups.get(f, frozenset({f}))

    g = nx.DiGraph()
    for e in edges:
        a, b = node_for(e.source), node_for(e.target)
        if a == b:
            g.add_node(a)
            continue
        if g.has_edge(a, b):
            g[a][b]["weight"] += e.count
        else:
            g.add_edge(a, b, weight=e.count)
    return g


def needs_def_quotient_graph(graph: Graph, include_graph: nx.DiGraph) -> tuple[nx.DiGraph, list[Edge]]:
    """The graph module-DAG decisions should actually be made against:
    needs-def edges only, impossible edges filtered out, co-location groups
    collapsed to single nodes. Returns (quotient_graph, dropped_edges)."""
    legal, dropped = filter_impossible_edges(graph, include_graph)
    needs_def = [e for e in legal if e.strength == "needs-def"]
    groups = colocation_groups(graph)
    return quotient_graph(needs_def, groups), dropped


def find_sccs(g: nx.DiGraph) -> list[set]:
    return [c for c in nx.strongly_connected_components(g) if len(c) > 1]


def condensation_summary(g: nx.DiGraph):
    """Returns (condensed_dag, root_nodes) where root_nodes are the SCCs
    with no outgoing needs-def edges -- candidates for the base module(s)
    everything else builds on."""
    sccs = list(nx.strongly_connected_components(g))
    cond = nx.condensation(g, scc=sccs)
    roots = [n for n in cond if cond.out_degree(n) == 0]
    return cond, roots


def flatten(node_or_members) -> list[str]:
    """A quotient-graph node is a frozenset of filenames; a condensation
    node's 'members' attribute is a set of such frozensets. This handles
    either, returning a sorted flat list of filenames for display."""
    if isinstance(node_or_members, frozenset) and node_or_members and isinstance(next(iter(node_or_members)), str):
        return sorted(node_or_members)
    return sorted({f for group in node_or_members for f in group})
