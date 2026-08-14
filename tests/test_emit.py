import networkx as nx
import pytest

from cladoscope.analyze import build_include_graph, needs_def_quotient_graph
from cladoscope.emit import EmitConfig, level_quotient_graph, write_level_modules


def test_level_quotient_graph_orders_dependencies_before_dependents(synthetic_repo):
    root, graph = synthetic_repo
    inc = build_include_graph(root)
    q, _dropped = needs_def_quotient_graph(graph, inc)

    levels = level_quotient_graph(q)
    flat_levels = [sorted(f for group in level for f in group) for level in levels]

    a_level = next(i for i, lvl in enumerate(flat_levels) if "a.hpp" in lvl)
    b_level = next(i for i, lvl in enumerate(flat_levels) if "b.hpp" in lvl)
    assert a_level < b_level  # b.hpp depends on a.hpp, so a.hpp must come first


def test_level_quotient_graph_rejects_a_real_cycle():
    cyclic = nx.DiGraph()
    cyclic.add_edge(frozenset({"x.hpp"}), frozenset({"y.hpp"}))
    cyclic.add_edge(frozenset({"y.hpp"}), frozenset({"x.hpp"}))

    with pytest.raises(ValueError, match="not a DAG"):
        level_quotient_graph(cyclic)


def test_write_level_modules_produces_valid_import_chain(tmp_path, synthetic_repo):
    root, graph = synthetic_repo
    inc = build_include_graph(root)
    q, _dropped = needs_def_quotient_graph(graph, inc)
    levels = level_quotient_graph(q)

    out_dir = tmp_path / "modules"
    out_dir.mkdir()
    written = write_level_modules(levels, str(out_dir), EmitConfig())

    assert len(written) == len(levels)
    for idx, path in enumerate(written):
        text = open(path).read()
        assert f"export module stdexec.level_{idx};" in text
        # every level must import all levels below it, and none above
        for j in range(idx):
            assert f"import stdexec.level_{j};" in text
        for j in range(idx + 1, len(levels)):
            assert f"import stdexec.level_{j};" not in text


def test_write_level_modules_respects_custom_config(tmp_path, synthetic_repo):
    root, graph = synthetic_repo
    inc = build_include_graph(root)
    q, _dropped = needs_def_quotient_graph(graph, inc)
    levels = level_quotient_graph(q)

    out_dir = tmp_path / "modules"
    out_dir.mkdir()
    config = EmitConfig(module_prefix="myproject.level", config_header="<myproject/config.hpp>")
    written = write_level_modules(levels, str(out_dir), config)

    text = open(written[0]).read()
    assert "export module myproject.level_0;" in text
    assert "#include <myproject/config.hpp>" in text
    assert "stdexec" not in text
