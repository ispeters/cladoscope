# cladoscope

A Python library, built on libclang, for describing, analysing, and
transforming the entity dependency graph of a C++ codebase and generating
code from it.

Grew out of planning a C++20/23 modules migration for a large header-based
codebase, where the plain `#include` graph lies about the real dependency
structure (usually because of a hand-written forward-declaration header
laundering real cycles into an apparent DAG) -- but the entity-graph vocabulary
and the analysis/transformation/codegen pipeline aren't specific to that use
case; migration is the first consumer, not the only intended one.

Originally built against [NVIDIA/stdexec](https://github.com/NVIDIA/stdexec).
`translate.py` and `analyze.py` don't know anything stdexec-specific;
`emit.py`'s default `EmitConfig` does (macro names, prelude includes) since
stdexec is the only consumer so far.

## Why entity-level, not header-level

The `#include` graph tells you what a file textually pulls in, not what it
actually *needs*. A header can forward-declare a type it only ever refers to
by pointer/reference, `#include` nothing about its definition, and still
compile fine — until you try to put that header and the type's real
definition in different module units, at which point `[dcl.inline]/6`-style
constraints (an inline/constexpr entity must be *defined* in the same
translation unit it's declared in) become load-bearing in a way they never
were for a textual `#include` build.

`translate.py` walks the real Clang AST (via `libclang`) and records, per
entity, *where it's declared* and *where it's defined*, plus every reference
to it annotated by whether that reference only needed the declaration
(`needs-decl`) or the full definition (`needs-def`). Only `needs-def` edges
constrain a module DAG.

## Usage

```
pip install -e .

# 1. Slow, occasional: parse every TU in a non-modular build's
#    compile_commands.json into entity_graph.json.
cladoscope translate build/compile_commands.json --root include

# 2. Fast, iterate freely: report SCCs and candidate root modules over the
#    filtered, quotiented graph.
cladoscope analyze entity_graph.json --source-root include

# 3. Fast: write leveled .cppm module files from the same graph.
cladoscope emit entity_graph.json --source-root include --out-dir modules
```

Step 1 needs `libclang` pointed at a real LLVM install (see
`translate.wrapped_compiler_isystem_dirs` for how the system include paths
are derived) and takes on the order of 10-20 minutes for a codebase the size
of stdexec. Steps 2 and 3 only touch the JSON `translate` wrote and run in
seconds — that split is the whole point: you should be able to try a dozen
different filtering or quotienting ideas without re-parsing anything.

## Package layout

| Module | Job |
|---|---|
| `graph.py` | The schema (`Entity`, `Edge`, `Graph`), `load_graph`/`save_graph`/`validate_graph`. The only file that should read or write the JSON directly. |
| `translate.py` | The `libclang` parsing step. Only file that imports `clang.cindex`. |
| `analyze.py` | Pure functions over a loaded `Graph`: the include-graph soundness filter, decl/def co-location grouping, quotienting, SCC/condensation reporting. |
| `emit.py` | Turns a leveled quotient graph into generated `.cppm` files, driven by an `EmitConfig` rather than hardcoded project knowledge. |
| `__main__.py` | The one CLI entry point (`translate`/`analyze`/`emit` subcommands). Owns all the "print a report" logic so nothing else duplicates it. |

## Status

Investigatory. Schema has a `schema_version` field and `load_graph` refuses
to load a mismatched version rather than guessing, specifically so this can
keep changing shape without silently producing wrong answers against a stale
file. No test suite yet — this is still young enough that the shape of
`analyze.py` in particular is expected to keep moving.
