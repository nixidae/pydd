"""`pydd.modes.render`, held to what `graph.nix` makes of the same document.

`render-oracle.nix` evaluates `graph.nix` over a document that holds every
node kind, and records the `.drvPath` of each node. The renderer must write
the same derivation for each one, or the two paths submit different roots.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_against_nix import drv_path

from pydd.core import Derivation, nix32
from pydd.modes.render import Graph
from pydd.python import OVERRIDE_KEYS

ORACLE: dict[str, Any] = json.loads(Path(os.environ["PYDD_RENDER_ORACLE"]).read_text())


def rendered(name: str) -> str:
    graph = Graph(ORACLE["document"] | {"root": name}, drv_path, "/nix/store")
    value = graph.built(name)
    return graph.paths.get(name, value.text)


@pytest.mark.parametrize("name", sorted(ORACLE["expected"]))
def test_each_kind_renders_what_graph_nix_makes(name: str) -> None:
    assert rendered(name) == ORACLE["expected"][name]


@pytest.mark.parametrize("name", sorted(ORACLE["expectedStdenv"]))
def test_each_source_node_renders_stdenv_as_data_as_graph_nix(name: str) -> None:
    graph = Graph(ORACLE["stdenvDocument"] | {"root": name}, drv_path, "/nix/store")
    graph.built(name)
    assert graph.paths[name] == ORACLE["expectedStdenv"][name]


def test_the_oracle_covers_every_override_key() -> None:
    keys = {key for node in ORACLE["document"]["nodes"] for key in node.get("overrides", {})}
    assert keys == set(OVERRIDE_KEYS)


def test_the_oracle_covers_every_kind() -> None:
    kinds = {node["kind"] for node in ORACLE["document"]["nodes"]}
    assert kinds == {
        "unpack",
        "wheel",
        "editable",
        "local-wheel",
        "local-sdist",
        "sdist",
        "sdist-build",
        "nixpkgs",
        "host",
        "venv",
        "application",
        "planned",
        "backend",
    }


def test_a_new_revision_of_a_local_project_rebuilds_only_what_reads_it() -> None:
    """An artefact's node depends on its lock entry, and not on a local project. Issue #894.

    uv, poetry and pylock lanes all write this document, and no reader puts
    the project in an artefact's node. Every local tree moves here at once.
    """

    def written(document: dict[str, Any]) -> dict[str, str]:
        drvs: dict[str, str] = {}

        def add(drv: Derivation) -> str:
            text = json.dumps(drv.to_json(), sort_keys=True)
            drvs[drv.name] = text
            return f"/nix/store/{nix32(hashlib.sha256(text.encode()).digest())[:32]}-{drv.name}.drv"

        Graph(document, add, "/nix/store").root()
        return drvs

    moved = [
        node | {"source": node["source"].replace("-tree", "-tree-b")} if "source" in node else node
        for node in ORACLE["document"]["nodes"]
    ]
    before = written(ORACLE["document"])
    after = written(ORACLE["document"] | {"nodes": moved})
    assert {name for name, text in before.items() if after.get(name) != text} == {
        "app-editable",
        "git-from-tree",
        "lib-sdist",
        "lib-from-sdist",
        "app-env",
        "app",
    }
