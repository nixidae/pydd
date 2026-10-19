"""`pydd.modes.render`, held to what `graph.nix` makes of the same document.

`render-oracle.nix` evaluates `graph.nix` over a document that holds every
node kind, and records the `.drvPath` of each node. The renderer must write
the same derivation for each one, or the two paths submit different roots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_against_nix import drv_path

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
