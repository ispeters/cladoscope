from cladoscope.graph import Edge, Entity, Graph, SCHEMA_VERSION, load_graph, save_graph, validate_graph

import json

import pytest


def test_save_load_roundtrip(tmp_path, synthetic_repo):
    _root, graph = synthetic_repo
    path = tmp_path / "entity_graph.json"
    save_graph(graph, str(path))
    loaded = load_graph(str(path))

    assert loaded.entities.keys() == graph.entities.keys()
    assert len(loaded.edges) == len(graph.edges)
    assert loaded.schema_version == SCHEMA_VERSION


def test_load_rejects_mismatched_schema_version(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION - 1, "entities": {}, "edges": []}))

    with pytest.raises(ValueError, match="schema_version"):
        load_graph(str(path))


def test_validate_rejects_empty_edge_endpoints():
    bad = Graph(entities={}, edges=[Edge(source="", target="x", strength="needs-def")])
    with pytest.raises(ValueError, match="empty source/target"):
        validate_graph(bad)


def test_validate_rejects_non_positive_count():
    bad = Graph(
        entities={},
        edges=[Edge(source="a.hpp", target="b.hpp", strength="needs-def", count=0)],
    )
    with pytest.raises(ValueError, match="non-positive count"):
        validate_graph(bad)


def test_validate_rejects_entity_with_no_decl_or_defn():
    bad = Graph(entities={"usr::x": Entity(name="x", kind="FUNCTION_DECL")}, edges=[])
    with pytest.raises(ValueError, match="neither decl nor defn"):
        validate_graph(bad)


def test_validate_accepts_wellformed_graph(synthetic_repo):
    _root, graph = synthetic_repo
    validate_graph(graph)  # should not raise
