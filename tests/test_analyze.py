from cladoscope.analyze import (
    build_include_graph,
    colocation_groups,
    condensation_summary,
    filter_impossible_edges,
    find_sccs,
    flatten,
    needs_def_quotient_graph,
    quotient_graph,
)


def test_build_include_graph_reflects_real_includes(synthetic_repo):
    root, _graph = synthetic_repo
    inc = build_include_graph(root)

    assert inc.has_edge("b.hpp", "a.hpp")
    assert inc.has_edge("detail/schedulers.hpp", "a.hpp")
    # c.hpp has no #include lines, so it should have no outgoing edges,
    # in particular not one to a.hpp
    assert not list(inc.successors("c.hpp"))


def test_filter_drops_edge_with_no_real_include_path(synthetic_repo):
    root, graph = synthetic_repo
    inc = build_include_graph(root)

    legal, dropped = filter_impossible_edges(graph, inc)

    assert len(dropped) == 1
    assert dropped[0].source == "c.hpp" and dropped[0].target == "a.hpp"
    assert all(not (e.source == "c.hpp" and e.target == "a.hpp") for e in legal)


def test_filter_keeps_edges_with_a_real_include_path(synthetic_repo):
    root, graph = synthetic_repo
    inc = build_include_graph(root)

    legal, _dropped = filter_impossible_edges(graph, inc)

    assert any(e.source == "b.hpp" and e.target == "a.hpp" for e in legal)


def test_colocation_groups_merges_decl_defn_split(synthetic_repo):
    _root, graph = synthetic_repo
    groups = colocation_groups(graph)

    assert groups["detail/schedulers.hpp"] == groups["detail/read_env.hpp"]
    assert "detail/schedulers.hpp" in groups["detail/read_env.hpp"]


def test_colocation_groups_leaves_unconstrained_files_singleton(synthetic_repo):
    _root, graph = synthetic_repo
    groups = colocation_groups(graph)

    # bar is declared and defined in the same file (a.hpp), so it imposes
    # no cross-file constraint
    assert groups["a.hpp"] == frozenset({"a.hpp"})


def test_quotient_graph_never_splits_a_colocation_group(synthetic_repo):
    _root, graph = synthetic_repo
    groups = colocation_groups(graph)
    q = quotient_graph(graph.edges, groups)

    schedulers_node = groups["detail/schedulers.hpp"]
    read_env_node = groups["detail/read_env.hpp"]
    assert schedulers_node == read_env_node
    # the merged group must appear as exactly one node, never two
    matching_nodes = [n for n in q.nodes if "detail/schedulers.hpp" in n]
    assert len(matching_nodes) <= 1


def test_needs_def_quotient_graph_is_acyclic(synthetic_repo):
    root, graph = synthetic_repo
    inc = build_include_graph(root)
    q, _dropped = needs_def_quotient_graph(graph, inc)

    assert find_sccs(q) == []


def test_condensation_summary_reports_true_root(synthetic_repo):
    root, graph = synthetic_repo
    inc = build_include_graph(root)
    q, _dropped = needs_def_quotient_graph(graph, inc)

    cond, roots = condensation_summary(q)
    root_files = {f for n in roots for f in flatten(cond.nodes[n]["members"])}

    # a.hpp has no needs-def dependencies of its own, so it (or its group)
    # must be a root component
    assert "a.hpp" in root_files
