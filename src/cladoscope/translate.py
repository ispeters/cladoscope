"""
translate.py -- the expensive step: parse a compile_commands.json with
libclang and produce a Graph (see graph.py) recording where each entity is
declared vs. defined, and whether each reference to it needs only a
declaration or the full definition.

This is deliberately the *only* file in the package that imports
clang.cindex or does any AST walking. Nothing downstream needs libclang at
all: analyze.py and emit.py work purely off the JSON this step produces, so
they can iterate in seconds without re-parsing the whole project. Run this
occasionally (minutes, dominated by Clang's own semantic analysis), not as
part of a tight iteration loop.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shlex
import subprocess
import sys

import clang.cindex as ci

from .graph import Edge, Entity, Graph, SCHEMA_VERSION


def build_argv(cmd: dict) -> list[str]:
    """
    Extract compiler args for libclang from a compile_commands.json entry.

    Naive `cmd["command"].split()` filtering of ("-c", "-o") tokens leaves
    -o's *value* (the output object path) behind as a stray positional
    argument, which then collides with the filename passed separately to
    index.parse() and makes libclang refuse to construct a command line at
    all. Also use shlex instead of str.split() for shell-correct
    tokenization.
    """
    toks = shlex.split(cmd["command"])[1:]  # drop the compiler executable
    src = os.path.abspath(cmd["file"])
    out: list[str] = []
    skip_next = False
    for a in toks:
        if skip_next:
            skip_next = False
            continue
        if a in ("-o", "-MF", "-MT", "-MQ"):
            skip_next = True
            continue
        if a == "-c":
            continue
        if os.path.isabs(a) and os.path.abspath(a) == src:
            continue  # the source file itself; passed separately to parse()
        out.append(a)
    return out


def wrapped_compiler_isystem_dirs(cxx: str | None = None) -> list[str]:
    """
    Ask the wrapped compiler (via `$CXX -E -x c++ -v -`) for its real, final
    include search path, rather than trying to reconstruct it from
    environment variables that may not capture everything a toolchain
    wrapper bakes in at build time.
    """
    cxx = cxx or os.environ.get("CXX", "clang++")
    out = subprocess.run(
        [cxx, "-E", "-x", "c++", "-v", "-"],
        input="",
        capture_output=True,
        text=True,
    ).stderr
    m = re.search(
        r"#include <\.\.\.> search starts here:\n(.*?)\nEnd of search list\.",
        out,
        re.S,
    )
    if not m:
        print("warning: couldn't parse compiler -v output for include dirs", file=sys.stderr)
        return []
    dirs = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.endswith("(framework directory)"):
            continue
        if line:
            dirs.append(line)
    return [f"-isystem{d}" for d in dirs]


# Cursor kinds whose *use* requires a complete definition, not just a declaration.
NEEDS_DEF_KINDS = {
    ci.CursorKind.CXX_BASE_SPECIFIER,
    ci.CursorKind.MEMBER_REF_EXPR,
    ci.CursorKind.MEMBER_REF,
    ci.CursorKind.CALL_EXPR,
    ci.CursorKind.CONCEPT_SPECIALIZATION_EXPR
    if hasattr(ci.CursorKind, "CONCEPT_SPECIALIZATION_EXPR")
    else ci.CursorKind.UNEXPOSED_EXPR,
}

REF_KINDS = {
    ci.CursorKind.TYPE_REF,
    ci.CursorKind.TEMPLATE_REF,
    ci.CursorKind.DECL_REF_EXPR,
    ci.CursorKind.MEMBER_REF_EXPR,
    ci.CursorKind.MEMBER_REF,
    ci.CursorKind.CXX_BASE_SPECIFIER,
    ci.CursorKind.OVERLOADED_DECL_REF,
    ci.CursorKind.CALL_EXPR,  # CPO invocations (connect, schedule, set_value, ...)
    # NAMESPACE_REF deliberately excluded: namespaces have no single
    # definition, so get_definition() on one arbitrarily returns whichever
    # reopening fragment it happens to land on, producing edges to files
    # that merely reopen a namespace and have no real relationship to the
    # referencing code.
}


def _rel(path: str | None, root: str) -> str | None:
    if not path:
        return None
    p = os.path.abspath(path)
    r = os.path.abspath(root)
    return os.path.relpath(p, r) if p.startswith(r + os.sep) else None


class _Builder:
    """Accumulates decl/def sites and edge counts while walking translation
    units; to_graph() converts the accumulated state into a Graph."""

    def __init__(self, root: str):
        self.root = root
        self.ents: dict[str, dict] = collections.defaultdict(
            lambda: {"decl": set(), "defn": set(), "kind": None, "name": None}
        )
        self.edge_counts: collections.Counter = collections.Counter()

    def note_decl(self, c) -> None:
        usr = c.get_usr()
        if not usr:
            return
        f = _rel(c.location.file.name if c.location.file else None, self.root)
        if not f:
            return
        e = self.ents[usr]
        e["kind"] = e["kind"] or str(c.kind)
        e["name"] = e["name"] or c.spelling
        (e["defn"] if c.is_definition() else e["decl"]).add(f)

    def note_ref(self, c, enclosing_file: str) -> None:
        ref = c.referenced
        if ref is None:
            return
        usr = ref.get_usr()
        if not usr:
            return
        d = ref.get_definition()
        strength = "needs-def" if c.kind in NEEDS_DEF_KINDS else "needs-decl"
        target = None
        if d is not None and d.location.file:
            target = _rel(d.location.file.name, self.root)
        if target is None and ref.location.file:
            target = _rel(ref.location.file.name, self.root)
            strength = "needs-decl"  # only a declaration was reachable
        if target and target != enclosing_file:
            self.edge_counts[(enclosing_file, target, strength)] += 1

    def to_graph(self) -> Graph:
        entities = {
            usr: Entity(name=e["name"], kind=e["kind"], decl=sorted(e["decl"]), defn=sorted(e["defn"]))
            for usr, e in self.ents.items()
            if e["decl"] or e["defn"]
        }
        edges = [
            Edge(source=a, target=b, strength=s, count=n) for (a, b, s), n in self.edge_counts.items()
        ]
        return Graph(entities=entities, edges=edges, schema_version=SCHEMA_VERSION)


def _walk(cursor, builder: _Builder, seen: set) -> None:
    stack = [cursor]
    while stack:
        c = stack.pop()
        loc = c.location.file.name if c.location.file else None
        f = _rel(loc, builder.root)
        if f:
            if c.is_definition() or c.kind.is_declaration():
                builder.note_decl(c)
            if c.kind in REF_KINDS:
                builder.note_ref(c, f)
        for ch in c.get_children():
            key = (ch.hash, ch.location.offset)
            if key in seen:
                continue
            seen.add(key)
            stack.append(ch)


def _load_compile_commands(path: str, root: str) -> list[dict]:
    cmds = json.load(open(path))
    src_root = os.path.abspath(root)
    before = len(cmds)
    cmds = [
        c
        for c in cmds
        if os.path.abspath(c["file"]).startswith(os.path.dirname(src_root) + os.sep)
        and "@" not in c["command"]  # drop response-file (module-map) invocations
    ]
    print(
        f"filtered {before} -> {len(cmds)} compile-commands entries "
        f"(dropped toolchain/module-map entries outside the project)",
        file=sys.stderr,
    )
    return cmds


def translate(
    compile_commands_path: str,
    root: str = "include",
    libclang: str | None = None,
    limit: int = 0,
) -> Graph:
    """Parse every TU in a compile_commands.json and return the resulting
    Graph. This is the slow step -- run it occasionally, not as part of an
    iteration loop; use analyze.py / emit.py against its saved output for
    everything after that."""
    if libclang:
        ci.Config.set_library_file(libclang)

    cmds = _load_compile_commands(compile_commands_path, root)
    if limit:
        cmds = cmds[:limit]

    sysroot_extra = wrapped_compiler_isystem_dirs()
    print(f"resolved {len(sysroot_extra)} system include dirs from $CXX -v", file=sys.stderr)

    builder = _Builder(root)
    index = ci.Index.create()
    for i, cmd in enumerate(cmds, 1):
        argv = build_argv(cmd) + sysroot_extra
        src = cmd["file"]
        print(f"[{i}/{len(cmds)}] {os.path.basename(src)}", file=sys.stderr)
        try:
            tu = index.parse(
                src, args=argv, options=ci.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            )
        except ci.TranslationUnitLoadError as e:
            print(f"  !! {e}", file=sys.stderr)
            if i <= 2:  # show the actual argv on the first couple failures
                print(f"     argv: {argv}", file=sys.stderr)
            continue
        fatal = [d for d in tu.diagnostics if d.severity >= d.Error]
        if fatal:
            print(f"  !! {len(fatal)} error diagnostic(s):", file=sys.stderr)
            for d in fatal[:5]:
                print(f"     {d}", file=sys.stderr)
        _walk(tu.cursor, builder, set())

    graph = builder.to_graph()
    print(f"\n{len(graph.entities)} entities, {len(graph.edges)} edges", file=sys.stderr)
    strength_counts = collections.Counter(e.strength for e in graph.edges)
    print(f"edge strengths: {dict(strength_counts)}", file=sys.stderr)
    return graph
