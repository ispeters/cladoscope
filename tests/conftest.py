"""
Shared fixtures. `synthetic_repo` builds a tiny on-disk header tree plus a
matching Graph, deliberately reproducing the two real bug shapes this
tooling exists to catch:

  - a decl/defn split forced across files (`detail/schedulers.hpp` declares
    `foo`, `detail/read_env.hpp` defines it) -- the actual shape found in
    stdexec that colocation_groups()/quotienting exists to handle.
  - a spurious high-weight edge with no real #include path from source to
    target (`c.hpp` -> `a.hpp`) -- mimicking the libclang misattribution
    where cursors inside an implicit template instantiation get reported at
    the template *definition's* location rather than the instantiating
    TU's, which filter_impossible_edges() exists to drop.
"""
from __future__ import annotations

import os

import pytest

from cladoscope.graph import Edge, Entity, Graph


@pytest.fixture
def synthetic_repo(tmp_path):
    os.makedirs(tmp_path / "detail", exist_ok=True)
    files = {
        "a.hpp": "#pragma once\n// true root, no includes\n",
        "b.hpp": '#pragma once\n#include "a.hpp"\n',
        "detail/schedulers.hpp": '#pragma once\n#include "a.hpp"\n// declares foo, defined in read_env.hpp\n',
        "detail/read_env.hpp": '#pragma once\n#include "detail/schedulers.hpp"\n// defines foo\n',
        # deliberately does NOT include a.hpp, directly or transitively
        "c.hpp": "#pragma once\n// no includes at all\n",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content)

    entities = {
        "usr::foo": Entity(
            name="foo",
            kind="FUNCTION_DECL",
            decl=["detail/schedulers.hpp"],
            defn=["detail/read_env.hpp"],
        ),
        "usr::bar": Entity(name="bar", kind="FUNCTION_DECL", decl=["a.hpp"], defn=["a.hpp"]),
    }
    edges = [
        Edge(source="b.hpp", target="a.hpp", strength="needs-def", count=3),
        Edge(source="detail/read_env.hpp", target="detail/schedulers.hpp", strength="needs-decl", count=1),
        Edge(source="c.hpp", target="a.hpp", strength="needs-def", count=240),  # spurious
    ]
    graph = Graph(entities=entities, edges=edges)
    return str(tmp_path), graph
