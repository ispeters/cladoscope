"""
cladoscope -- a Python library, built on libclang, for describing, analysing,
and transforming the entity dependency graph of a C++ codebase and
generating code from it.

Grew out of planning a C++20/23 modules migration for NVIDIA/stdexec, where
the plain #include graph lies about the real dependency structure (usually
because of a hand-maintained forward-declaration header laundering real
cycles into an apparent DAG). translate.py and analyze.py aren't specific to
that use case or to stdexec. emit.py's *output* is currently stdexec-shaped
(EmitConfig's defaults), since that's the only consumer so far; pass a
different EmitConfig for another project or another kind of generated
artifact.

Typical flow:

    translate()  compile_commands.json -> Graph          (slow, occasional)
    save_graph() / load_graph()         Graph <-> JSON on disk
    analyze.*()  Graph -> filtered, quotiented module DAG (fast, iterate freely)
    emit.*()     leveled DAG -> generated .cppm files
"""
from .graph import Edge, Entity, Graph, SCHEMA_VERSION, load_graph, save_graph, validate_graph

__all__ = [
    "Graph",
    "Entity",
    "Edge",
    "SCHEMA_VERSION",
    "load_graph",
    "save_graph",
    "validate_graph",
]
