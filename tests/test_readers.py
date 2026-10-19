"""The readers of pnpm, bun and Yarn locks, against npm's locks of the same projects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pydd.node._readers import bun_path, parse_yarn, pnpm_key, read, read_pnpm, read_yarn

PROJECTS = Path(__file__).parent / "node"

#: Each project of another tool, with the npm project that has the same manifest.
PAIRS = [
    ("pnpm-hello", "pnpm-lock.yaml", "hello-npm"),
    ("pnpm-workspace", "pnpm-lock.yaml", "workspace"),
    ("bun-native", "bun.lock", "native-npm"),
    ("yarn-esbuild", "yarn.lock", "esbuild-app"),
]


def view(packages: dict[str, dict[str, Any]]) -> dict[str, tuple[Any, ...]]:
    return {
        path: (
            entry.get("version"),
            entry.get("integrity"),
            bool(entry.get("dev")),
            bool(entry.get("optional")),
            entry.get("resolved") if entry.get("link") else None,
        )
        for path, entry in packages.items()
        if path
    }


@pytest.mark.parametrize(("project", "lockfile", "npm"), PAIRS)
def test_reader_places_packages_as_npm_does(project: str, lockfile: str, npm: str) -> None:
    expected = json.loads((PROJECTS / npm / "package-lock.json").read_text())["packages"]
    assert view(read(str(PROJECTS / project), lockfile)) == view(expected)


def test_a_nested_version_stays_under_its_dependent() -> None:
    packages = read(str(PROJECTS / "pnpm-hello"), "pnpm-lock.yaml")
    assert packages["node_modules/ansi-styles"]["version"] == "6.2.3"
    assert packages["node_modules/chalk/node_modules/ansi-styles"]["version"] == "4.3.0"


def test_pnpm_gives_no_program_names() -> None:
    semver = read(str(PROJECTS / "pnpm-hello"), "pnpm-lock.yaml")["node_modules/semver"]
    assert semver.get("hasBin") is True
    assert "bin" not in semver


def test_bun_runs_scripts_of_trusted_dependencies() -> None:
    packages = read(str(PROJECTS / "bun-native"), "bun.lock")
    scripted = {path for path, entry in packages.items() if entry.get("hasInstallScript")}
    assert scripted == {"node_modules/better-sqlite3"}


def test_the_call_names_more_scripts() -> None:
    packages = read(str(PROJECTS / "pnpm-hello"), "pnpm-lock.yaml", ["chalk"])
    assert packages["node_modules/chalk"].get("hasInstallScript") is True


def test_bun_paths() -> None:
    members = {"workspace-app": "packages/app"}
    assert bun_path("chalk/ansi-styles", members) == "node_modules/chalk/node_modules/ansi-styles"
    assert bun_path("@a/b/@c/d", members) == "node_modules/@a/b/node_modules/@c/d"
    assert bun_path("workspace-app/ms", members) == "packages/app/node_modules/ms"
    assert bun_path("workspace-app", members) == "node_modules/workspace-app"


def test_yarn_block_with_several_ranges() -> None:
    blocks = parse_yarn(
        "# yarn lockfile v1\n\n"
        'inherits@2, "inherits@^2.0.4":\n'
        '  version "2.0.4"\n'
        '  resolved "https://registry.yarnpkg.com/inherits/-/inherits-2.0.4.tgz#abc"\n'
        "  dependencies:\n"
        '    "@scope/x" "^1.0.0"\n'
    )
    assert blocks["inherits@2"] is blocks["inherits@^2.0.4"]
    assert blocks["inherits@2"]["dependencies"] == {"@scope/x": "^1.0.0"}


def test_pnpm_alias() -> None:
    assert pnpm_key("string-width-cjs", "string-width@4.2.3") == "string-width@4.2.3"
    assert pnpm_key("ms", "2.1.3") == "ms@2.1.3"
    assert pnpm_key("react-dom", "18.2.0(react@18.2.0)") == "react-dom@18.2.0(react@18.2.0)"


def test_yarn_berry_is_refused(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    with pytest.raises(SystemExit, match="zip archives"):
        read_yarn(str(tmp_path), "__metadata:\n  version: 8\n  cacheKey: 10c0\n")


def test_old_pnpm_lock_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"version 6\.0"):
        read_pnpm(str(tmp_path), {"lockfileVersion": "6.0"})


def pnpm_lock(patched: dict[str, str]) -> dict[str, Any]:
    integrity = {"integrity": "sha512-AAAA"}
    return {
        "lockfileVersion": "9.0",
        "patchedDependencies": patched,
        "importers": {".": {"dependencies": {"a": {"specifier": "1.0.0", "version": "1.0.0(patch_hash=x)"}}}},
        "packages": {"a@1.0.0": {"resolution": integrity}, "b@2.0.0": {"resolution": integrity}},
        # b's key holds a's patch hash through its peer, and b has no patch.
        "snapshots": {"a@1.0.0(patch_hash=x)": {}, "b@2.0.0(a@1.0.0(patch_hash=x))": {}},
    }


def test_pnpm_patch_names_its_file(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pnpm-workspace.yaml").write_text("patchedDependencies:\n  a@1.0.0: patches/a.patch\n")
    graph, _ = read_pnpm(str(tmp_path), pnpm_lock({"a@1.0.0": "x"}))
    assert graph.versions["a@1.0.0(patch_hash=x)"].patch == "patches/a.patch"
    assert graph.versions["b@2.0.0(a@1.0.0(patch_hash=x))"].patch is None


def test_pnpm_patch_without_its_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    with pytest.raises(SystemExit, match="names its patch file"):
        read_pnpm(str(tmp_path), pnpm_lock({"a@1.0.0": "x"}))


def test_pnpm_file_ref_with_peers_is_no_alias() -> None:
    ref = "file:modules/text(react@19.3.0)"
    assert pnpm_key("text", ref) == f"text@{ref}"
