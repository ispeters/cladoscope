"""
Single CLI entry point, exposing translate/analyze/emit as subcommands so
there's exactly one place that turns library calls into printed reports --
nothing else in the package duplicates this logic.

    python -m cladoscope translate build/compile_commands.json --root include
    python -m cladoscope analyze   entity_graph.json --source-root include
    python -m cladoscope emit      entity_graph.json --source-root include --out-dir modules
"""
from __future__ import annotations

import argparse
import sys


def _add_translate_parser(sub) -> None:
    t = sub.add_parser("translate", help="parse compile_commands.json into entity_graph.json (slow)")
    t.add_argument("compile_commands")
    t.add_argument("--root", default="include", help="include root to keep edges within")
    t.add_argument("--libclang", help="path to libclang.so/.dylib")
    t.add_argument("--out", default="entity_graph.json")
    t.add_argument("--limit", type=int, default=0, help="only parse first N TUs, for quick iteration")


def _add_analyze_parser(sub) -> None:
    a = sub.add_parser("analyze", help="report SCCs and root components over the filtered graph (fast)")
    a.add_argument("entity_graph_json")
    a.add_argument("--source-root", required=True, help="project include root, to scan #include lines")


def _add_emit_parser(sub) -> None:
    e = sub.add_parser("emit", help="write leveled .cppm module files (fast)")
    e.add_argument("entity_graph_json")
    e.add_argument("--source-root", required=True)
    e.add_argument("--out-dir", default="modules")


def _run_translate(args) -> None:
    from .graph import save_graph
    from .translate import translate

    graph = translate(args.compile_commands, root=args.root, libclang=args.libclang, limit=args.limit)
    save_graph(graph, args.out)
    print(f"wrote {args.out}", file=sys.stderr)


def _run_analyze(args) -> None:
    from .analyze import build_include_graph, condensation_summary, find_sccs, flatten, needs_def_quotient_graph
    from .graph import load_graph

    graph = load_graph(args.entity_graph_json)
    include_graph = build_include_graph(args.source_root)
    q, dropped = needs_def_quotient_graph(graph, include_graph)

    dropped_needs_def = sum(1 for e in dropped if e.strength == "needs-def")
    print(
        f"{len(graph.edges)} total edges, {len(dropped)} dropped as impossible "
        f"({dropped_needs_def} of them needs-def)",
        file=sys.stderr,
    )

    sccs = find_sccs(q)
    print(f"\n{len(sccs)} nontrivial SCC(s) over the filtered, quotiented needs-def graph:", file=sys.stderr)
    for c in sorted(sccs, key=len, reverse=True):
        print(f"  --- {len(c)} group(s) ---", file=sys.stderr)
        for node in sorted(c, key=flatten):
            print(f"      {flatten(node)}", file=sys.stderr)

    cond, roots = condensation_summary(q)
    print(f"\ncondensed DAG: {cond.number_of_nodes()} nodes, {cond.number_of_edges()} edges", file=sys.stderr)
    print(f"root (dependency-free) components: {len(roots)}", file=sys.stderr)
    for n in roots:
        print("   ", flatten(cond.nodes[n]["members"]), file=sys.stderr)


def _run_emit(args) -> None:
    import os

    from .analyze import build_include_graph, needs_def_quotient_graph
    from .emit import EmitConfig, level_quotient_graph, write_level_modules
    from .graph import load_graph

    os.makedirs(args.out_dir, exist_ok=True)
    graph = load_graph(args.entity_graph_json)
    include_graph = build_include_graph(args.source_root)
    q, _dropped = needs_def_quotient_graph(graph, include_graph)
    levels = level_quotient_graph(q)

    for idx, level in enumerate(levels):
        flattened = sorted({f for group in level for f in group})
        print(f"level {idx}: {flattened}", file=sys.stderr)

    written = write_level_modules(levels, args.out_dir, EmitConfig())
    print(f"\nwrote {len(written)} module file(s) to {args.out_dir}/", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="cladoscope")
    sub = ap.add_subparsers(dest="command", required=True)
    _add_translate_parser(sub)
    _add_analyze_parser(sub)
    _add_emit_parser(sub)

    args = ap.parse_args(argv)
    {
        "translate": _run_translate,
        "analyze": _run_analyze,
        "emit": _run_emit,
    }[args.command](args)


if __name__ == "__main__":
    main()
