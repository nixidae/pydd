"""The Rust planner and its node builder, against a recorded unit graph.

`tests/rust/planner` holds the unit graph and the metadata that cargo gave
for `tests/rust/serde-app`. The planner writes through a writer that keeps
each derivation, so a test reads the nodes the planner would submit.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from pydd.core import Derivation, Planner, nix32
from pydd.rust._cargo import Graph, Menu, lto_flags

FIXTURES = Path(__file__).parent / "rust" / "planner"
UNIT_SCRIPT = Path(__file__).parent.parent / "src" / "pydd" / "rust" / "scripts" / "unit.py"
TARGET = "x86_64-unknown-linux-gnu"
# A host that differs from the target, so a test can tell which one a unit got.
HOST = "build-unknown-linux-gnu"


class KeepingWriter:
    def __init__(self) -> None:
        self.added: list[Derivation] = []
        self.submitted: list[str] = []

    def add(self, drv: Derivation) -> str:
        self.added.append(drv)
        digest = hashlib.sha256(json.dumps(drv.to_json(), sort_keys=True).encode()).digest()
        return f"/nix/store/{nix32(digest)[:32]}-{drv.name}.drv"

    def submit(self, path: str, output: str) -> None:
        self.submitted.append(path)


def menu(**changes: Any) -> Menu:
    store = "/nix/store/00000000000000000000000000000000"
    fields: dict[str, Any] = {
        "name": "serde-app",
        "project": f"{store}-serde-app-source",
        "vendor": "/nix/store/mg68fms25jzwrjng1idrw992p3v98skj-cargo-vendor-dir",
        "cargo": f"{store}-cargo",
        "rustc": f"{store}-rustc",
        "cc": f"{store}-cc",
        "coreutils": f"{store}-coreutils",
        "python": f"{store}-python3",
        "script": f"{store}-unit.py",
        "target": TARGET,
        "native": {},
        "subdir": ".",
        "arguments": ["--profile", "release"],
        "host": HOST,
        "host_cc": f"{store}-cc",
        "prefix": "",
        "bintools": f"{store}-bintools",
    }
    return Menu(**(fields | changes))


def plan(**changes: Any) -> tuple[KeepingWriter, dict[str, dict[str, Any]]]:
    """Plan serde-app, and give each node's spec by node name."""
    writer = KeepingWriter()
    planner = Planner(writer, "x86_64-linux", None)
    # cargo names the project where the menu has it.
    project = menu(**changes).project
    graph, metadata = (
        json.loads((FIXTURES / f"serde-app.{kind}.json").read_text().replace(menu().project, project))
        for kind in ("unit-graph", "metadata")
    )
    planner.submit(Graph(planner, menu(**changes), graph, metadata).root())
    return writer, {drv.name: json.loads(drv.env["PYDD_UNIT"]) for drv in writer.added}


def test_one_node_for_each_unit_and_one_root() -> None:
    writer, specs = plan()
    graph = json.loads((FIXTURES / "serde-app.unit-graph.json").read_text())
    assert len(writer.added) == len(graph["units"]) + 1
    assert writer.submitted[0].endswith("-serde-app.drv")
    assert specs["serde-app"]["action"] == "join"


def test_host_units_get_no_target() -> None:
    _, specs = plan()
    # proc-macro2 builds for the proc-macro serde_derive, so for the host.
    assert "--target" not in specs["proc-macro2-1.0.107"]["flags"]
    assert specs["serde-1.0.229"]["flags"][specs["serde-1.0.229"]["flags"].index("--target") + 1] == TARGET


def test_a_build_script_runs_for_the_platform_of_its_unit() -> None:
    _, specs = plan()
    assert specs["proc-macro2-1.0.107-build-script-run"]["env"]["TARGET"] == HOST
    assert specs["serde-1.0.229-build-script-run"]["env"]["TARGET"] == TARGET
    assert specs["serde-1.0.229-build-script-run"]["env"]["HOST"] == HOST


def test_a_build_script_reads_the_links_of_its_package() -> None:
    writer = KeepingWriter()
    planner = Planner(writer, "x86_64-linux", None)
    graph = json.loads((FIXTURES / "serde-app.unit-graph.json").read_text())
    metadata = json.loads((FIXTURES / "serde-app.metadata.json").read_text())
    for package in metadata["packages"]:
        if package["name"] == "serde":
            package["links"] = "serde_core"
    planner.submit(Graph(planner, menu(), graph, metadata).root())
    specs = {drv.name: json.loads(drv.env["PYDD_UNIT"]) for drv in writer.added}
    assert specs["serde-1.0.229-build-script-run"]["env"]["CARGO_MANIFEST_LINKS"] == "serde_core"
    assert "CARGO_MANIFEST_LINKS" not in specs["proc-macro2-1.0.107-build-script-run"]["env"]


def test_a_library_builds_every_crate_type_it_names() -> None:
    writer = KeepingWriter()
    planner = Planner(writer, "x86_64-linux", None)
    graph = json.loads((FIXTURES / "serde-app.unit-graph.json").read_text())
    metadata = json.loads((FIXTURES / "serde-app.metadata.json").read_text())
    for unit in graph["units"]:
        if unit["target"]["name"] == "serde" and "lib" in unit["target"]["kind"]:
            unit["target"]["crate_types"] = ["staticlib", "cdylib", "rlib"]
    planner.submit(Graph(planner, menu(), graph, metadata).root())
    specs = {drv.name: json.loads(drv.env["PYDD_UNIT"]) for drv in writer.added}
    flags = specs["serde-1.0.229"]["flags"]
    assert [flags[i + 1] for i, flag in enumerate(flags) if flag == "--crate-type"] == ["staticlib", "cdylib", "rlib"]


def test_a_proc_macro_links_dynamically() -> None:
    _, specs = plan()
    flags = specs["serde_derive-1.0.229"]["flags"]
    assert flags[flags.index("--crate-type") + 1] == "proc-macro"
    assert "prefer-dynamic" in flags
    assert flags[flags.index("--extern") + 1] == "proc_macro"


def test_a_binary_names_its_dependencies_by_placeholder() -> None:
    writer, _ = plan()
    # The binary and the root share the project's name.
    binary = next(spec for drv in writer.added if (spec := json.loads(drv.env["PYDD_UNIT"])).get("output") == "bin")
    names = {extern["name"] for extern in binary["externs"]}
    assert names == {"serde", "serde_json"}
    for extern in binary["externs"]:
        # A placeholder, which Nix replaces with the floating output's path.
        assert extern["path"].startswith("/") and "/lib/lib" in extern["path"]
        assert extern["path"].endswith(".rlib")


def test_metadata_holds_no_path_of_the_host() -> None:
    def metadata(specs: dict[str, dict[str, Any]]) -> dict[str, str]:
        return {
            name: next(flag for flag in spec["flags"] if flag.startswith("metadata="))
            for name, spec in specs.items()
            if "flags" in spec
        }

    _, here = plan()
    _, elsewhere = plan(project="/nix/store/11111111111111111111111111111111-serde-app-source")
    assert metadata(here) == metadata(elsewhere)


def test_a_new_revision_of_the_project_rebuilds_only_its_own_units() -> None:
    """A crate of the vendor directory depends on its lock entry, and not on the project. Issue #894."""
    here, _ = plan()
    elsewhere, _ = plan(project="/nix/store/11111111111111111111111111111111-serde-app-source")
    made = {json.dumps(drv.to_json(), sort_keys=True) for drv in elsewhere.added}
    changed = [drv.name for drv in here.added if json.dumps(drv.to_json(), sort_keys=True) not in made]
    # The binary and the root, which share the project's name.
    assert changed == ["serde-app", "serde-app"]


@pytest.mark.parametrize(
    ("lto", "kinds", "crate_types", "mode", "expected"),
    [
        ("false", ["lib"], ["lib"], "build", ["-C", "embed-bitcode=no"]),
        ("true", ["lib"], ["lib"], "build", []),
        ("true", ["bin"], ["bin"], "build", ["-C", "lto"]),
        ("fat", ["bin"], ["bin"], "build", ["-C", "lto"]),
        ("thin", ["bin"], ["bin"], "build", ["-C", "lto=thin"]),
        ("true", ["lib"], ["lib"], "test", ["-C", "lto"]),
        ("true", ["proc-macro"], ["proc-macro"], "build", ["-C", "embed-bitcode=no"]),
        ("true", ["custom-build"], ["bin"], "build", ["-C", "embed-bitcode=no"]),
        ("off", ["lib"], ["lib"], "build", ["-C", "embed-bitcode=no", "-C", "lto=off"]),
    ],
)
def test_lto_by_the_role_of_the_unit(
    lto: str, kinds: list[str], crate_types: list[str], mode: str, expected: list[str]
) -> None:
    unit = {"profile": {"lto": lto}, "target": {"kind": kinds, "crate_types": crate_types}, "mode": mode}
    assert lto_flags(unit) == expected


@pytest.fixture
def unit_module() -> Any:
    spec = importlib.util.spec_from_file_location("pydd_rust_unit", UNIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rustc_flags_takes_l_and_big_l(unit_module: Any) -> None:
    assert unit_module.rustc_flags("-l ffi -L/usr/lib -lz") == [["-l", "ffi"], ["-L", "/usr/lib"], ["-l", "z"]]
    with pytest.raises(SystemExit):
        unit_module.rustc_flags("-C opt-level=3")


def test_cfg_of_the_target_as_cargo_gives_it(unit_module: Any) -> None:
    printed = 'debug_assertions\ntarget_arch="x86_64"\ntarget_feature="sse"\ntarget_feature="sse2"\nunix\n'
    assert unit_module.parse_cfg(printed) == {
        "CARGO_CFG_TARGET_ARCH": "x86_64",
        "CARGO_CFG_TARGET_FEATURE": "sse,sse2",
        "CARGO_CFG_UNIX": "",
    }


@pytest.mark.parametrize(
    ("scope", "name", "kinds", "testing", "file", "applies"),
    [
        ("all", "", ["bin"], False, "app", True),
        ("all", "", ["lib"], False, "lib", False),
        ("all", "", ["lib"], True, "lib", True),
        ("bins", "", ["bin"], False, "app", True),
        ("bins", "", ["bin"], True, "app", False),
        ("bin", "app", ["bin"], False, "app", True),
        ("bin", "other", ["bin"], False, "app", False),
        ("tests", "", ["test"], True, "it", True),
        ("tests", "", ["bin"], False, "app", False),
        ("cdylib", "", ["cdylib"], False, "plugin", True),
        ("examples", "", ["example"], False, "demo", True),
    ],
)
def test_link_arguments_reach_the_targets_cargo_gives_them(
    unit_module: Any, scope: str, name: str, kinds: list[str], testing: bool, file: str, applies: bool
) -> None:
    unit_module.unit = {"kinds": kinds, "testing": testing, "file": file}
    assert unit_module.link_argument_applies(scope, name) is applies
