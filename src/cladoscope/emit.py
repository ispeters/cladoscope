"""
emit.py -- the final step: turn a leveled module DAG into generated .cppm
files. Project-specific knowledge (macro names, prelude includes, the
config header to pull in) lives in an EmitConfig passed in, not hardcoded
here -- so this module stays project-agnostic even though the EmitConfig
defaults we ship are shaped for stdexec, its only consumer so far.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import networkx as nx


@dataclass
class EmitConfig:
    module_prefix: str = "stdexec.level"
    prelude_includes: list[str] = field(
        default_factory=lambda: ["<cassert>", "<cstdarg>", "<cstdint>", "<cstdio>", "<cstdlib>"]
    )
    purview_macro: str = "STDEXEC_IN_MODULE_PURVIEW"
    config_header: str = "<stdexec/__detail/__config.hpp>"
    diagnostic_pragmas: list[str] = field(
        default_factory=lambda: ["-Winclude-angled-in-module-purview"]
    )


def level_quotient_graph(q: nx.DiGraph) -> list[list[frozenset[str]]]:
    """Topologically level a DAG of co-location groups: level 0 is every
    group with no outgoing edges (nothing left it depends on), level N is
    every remaining group once all its dependencies have been placed in
    levels < N. Raises ValueError with the offending cycle if `q` isn't
    actually a DAG -- callers should run needs_def_quotient_graph() (which
    filters + quotients) first; this only re-confirms and reports precisely
    rather than assuming."""
    g = q.copy()
    levels: list[list[frozenset[str]]] = []
    while g.number_of_nodes() > 0:
        level = [n for n in g if g.out_degree(n) == 0]
        if not level:
            cycle = list(nx.find_cycle(g))
            raise ValueError(f"graph is not a DAG -- cycle found: {cycle}")
        levels.append(level)
        g.remove_nodes_from(level)
    return levels


def write_level_modules(levels: list[list[frozenset[str]]], out_dir: str, config: EmitConfig) -> list[str]:
    """Write one .cppm per level, each importing all lower levels and
    #include-ing its member headers under the project's module-purview
    macro. Returns the list of paths written."""
    written = []
    for idx, level in enumerate(levels):
        files = sorted(f for group in level for f in group)
        path = os.path.join(out_dir, f"{config.module_prefix}_{idx}.cppm")
        with open(path, "w") as f:
            print("module;", file=f)
            print(file=f)
            for inc in config.prelude_includes:
                print(f"#include {inc}", file=f)
            print(file=f)
            print(f"export module {config.module_prefix}_{idx};", file=f)
            print(file=f)
            for j in range(idx):
                print(f"import {config.module_prefix}_{j};", file=f)
            if idx > 0:
                print(file=f)
            print("#pragma clang diagnostic push", file=f)
            for p in config.diagnostic_pragmas:
                print(f'#pragma clang diagnostic ignored "{p}"', file=f)
            print(file=f)
            print(f"#include {config.config_header}", file=f)
            print(file=f)
            print(f"#define {config.purview_macro}", file=f)
            print(file=f)
            for include in files:
                print(f"#include <{include}>", file=f)
            print(file=f)
            print("#pragma clang diagnostic pop", file=f)
        written.append(path)
    return written
