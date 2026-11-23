"""The readers of pnpm, bun and Yarn locks, against npm's locks of the same projects."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pydd.node._readers import (
    Resolution,
    berry_conditions,
    berry_patch,
    bun_path,
    parse_berry,
    parse_yarn,
    pnpm_key,
    read,
    read_berry,
    read_pnpm,
)

PROJECTS = Path(__file__).parent / "node"

#: Each project of another tool, with the npm project that has the same manifest.
PAIRS = [
    ("pnpm-hello", "pnpm-lock.yaml", "hello-npm"),
    ("pnpm-workspace", "pnpm-lock.yaml", "workspace"),
    ("bun-native", "bun.lock", "native-npm"),
    ("yarn-esbuild", "yarn.lock", "esbuild-app"),
    ("berry-esbuild", "yarn.lock", "esbuild-app"),
    ("berry-workspace", "yarn.lock", "workspace"),
    ("berry-hello", "yarn.lock", "hello-npm"),
]


def integrity(project: Path) -> str | None:
    file = project / "yarn-integrity.json"
    return str(file) if file.is_file() else None


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
    packages = read(str(PROJECTS / project), lockfile, integrity=integrity(PROJECTS / project))
    assert view(packages) == view(expected)


def test_a_nested_version_stays_under_its_dependent() -> None:
    packages = read(str(PROJECTS / "pnpm-hello"), "pnpm-lock.yaml")
    assert packages["node_modules/ansi-styles"]["version"] == "6.2.3"
    assert packages["node_modules/chalk/node_modules/ansi-styles"]["version"] == "4.3.0"


def test_pnpm_lock_after_the_env_document(tmp_path: Path) -> None:
    project = PROJECTS / "pnpm-hello"
    shutil.copytree(project, tmp_path, dirs_exist_ok=True)
    env = (
        "---\nlockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    configDependencies: {}\n"
        "    packageManagerDependencies:\n      pnpm:\n        specifier: 11.0.0\n        version: 11.0.0\n\n"
    )
    lock = tmp_path / "pnpm-lock.yaml"
    lock.write_text(env + "---\n" + lock.read_text())
    assert read(str(tmp_path), "pnpm-lock.yaml") == read(str(project), "pnpm-lock.yaml")


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


def test_yarn_berry_needs_its_integrity_file() -> None:
    with pytest.raises(SystemExit, match="pydd-yarn-integrity"):
        read(str(PROJECTS / "berry-hello"), "yarn.lock")


def test_yarn_berry_without_a_hash_is_refused(tmp_path: Path) -> None:
    shutil.copytree(PROJECTS / "berry-hello", tmp_path, dirs_exist_ok=True)
    (tmp_path / "yarn-integrity.json").write_text("{}")
    with pytest.raises(SystemExit, match="no hash for 8 packages"):
        read(str(tmp_path), "yarn.lock", integrity=str(tmp_path / "yarn-integrity.json"))


def test_an_unknown_yarn_berry_lock_version_is_refused(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    with pytest.raises(SystemExit, match="has version 6"):
        read_berry(str(tmp_path), "__metadata:\n  version: 6\n  cacheKey: 8\n", {})


def test_yarn_berry_resolution_rewrites_the_range() -> None:
    """berry-hello pins semver through `resolutions`; its lock has no `semver@npm:^7.6.3`."""
    project = PROJECTS / "berry-hello"
    assert "semver@npm:^7.6.3" not in parse_berry((project / "yarn.lock").read_text())
    packages = read(str(project), "yarn.lock", integrity=integrity(project))
    assert packages["node_modules/semver"]["version"] == "7.8.5"


def test_yarn_berry_resolution_patterns() -> None:
    scoped = Resolution.parse("css-loader@npm:6.11.0/postcss", "8.5.26")
    assert scoped.rewrite(("css-loader", "npm:6.11.0"), "postcss", "npm:^8.4.0") == "npm:8.5.26"
    assert scoped.rewrite(("css-loader", "npm:7.0.0"), "postcss", "npm:^8.4.0") is None
    ranged = Resolution.parse("@scope/ws@npm:^8", "^8.20.1")
    assert ranged.rewrite(("app", "workspace:."), "@scope/ws", "npm:^8") == "npm:^8.20.1"
    assert ranged.rewrite(("app", "workspace:."), "@scope/ws", "npm:^8.1") is None
    patch = "patch:zod@npm%3A4.3.6#~/.yarn/patches/zod.patch"
    assert Resolution.parse("zod", patch).rewrite(("x", "npm:1.0.0"), "zod", "npm:^4") == patch


def test_yarn_berry_lock_blocks() -> None:
    blocks = parse_berry(
        '__metadata:\n  version: 10\n\n"@a/b@npm:^1.0.0, @a/b@npm:^1.2.0":\n  version: 1.2.3\n'
        '  resolution: "@a/b@npm:1.2.3"\n  dependencies:\n    "@c/d": "npm:^2"\n    e: "npm:1.0.0"\n'
        "  dependenciesMeta:\n    e:\n      optional: true\n  conditions: (os=android | os=linux) & cpu=x64\n"
    )
    assert blocks["@a/b@npm:^1.0.0"] is blocks["@a/b@npm:^1.2.0"]
    block = blocks["@a/b@npm:^1.0.0"]
    assert block["dependencies"] == {"@c/d": "npm:^2", "e": "npm:1.0.0"}
    assert block["dependenciesMeta"] == {"e": {"optional": "true"}}
    assert berry_conditions(block["conditions"]) == {"os": ["android", "linux"], "cpu": ["x64"]}


def test_yarn_berry_patches() -> None:
    reference = "patch:zod@npm%3A4.3.6#~/.yarn/patches/zod-npm-4.3.6.patch::version=4.3.6&hash=18f503"
    assert berry_patch("zod", reference) == ("zod@npm:4.3.6", ".yarn/patches/zod-npm-4.3.6.patch")
    builtin = "patch:resolve@npm%3A1.22.8#optional!builtin<compat/resolve>::version=1.22.8&hash=9bd1a5"
    assert berry_patch("resolve", builtin) == ("resolve@npm:1.22.8", None)


#: A project on react 18 with `old`, which brings react 17, and `plugin`,
#: which takes react as a peer. Both the project and `old` depend on `plugin`.
PEER_LOCK = """__metadata:
  version: 10

"peers@workspace:.":
  version: 0.0.0-use.local
  resolution: "peers@workspace:."

"react@npm:^18":
  version: 18.3.1
  resolution: "react@npm:18.3.1"

"react@npm:^17":
  version: 17.0.2
  resolution: "react@npm:17.0.2"

"old@npm:^1":
  version: 1.0.0
  resolution: "old@npm:1.0.0"
  dependencies:
    plugin: "npm:^1"
    react: "npm:^17"

"plugin@npm:^1":
  version: 1.0.0
  resolution: "plugin@npm:1.0.0"
  peerDependencies:
    react: "*"
"""

PEER_HASHES = {
    key: {"integrity": "sha512-AAAA"}
    for key in ("react@npm:18.3.1", "react@npm:17.0.2", "old@npm:1.0.0", "plugin@npm:1.0.0")
}


def peer_project(tmp_path: Path, dependencies: dict[str, str], lock: str = PEER_LOCK) -> dict[str, dict[str, Any]]:
    (tmp_path / "package.json").write_text(json.dumps({"name": "peers", "dependencies": dependencies}))
    (tmp_path / "yarn.lock").write_text(lock)
    (tmp_path / "yarn-integrity.json").write_text(json.dumps(PEER_HASHES))
    return read(str(tmp_path), "yarn.lock", integrity=str(tmp_path / "yarn-integrity.json"))


def test_a_package_goes_where_its_peers_resolve(tmp_path: Path) -> None:
    packages = peer_project(tmp_path, {"react": "^18", "old": "^1", "plugin": "^1"})
    assert packages["node_modules/react"]["version"] == "18.3.1"
    assert packages["node_modules/old/node_modules/react"]["version"] == "17.0.2"
    # The project's plugin sees react 18 from the top; old's must see react 17.
    assert "node_modules/plugin" in packages
    assert "node_modules/old/node_modules/plugin" in packages
    assert packages["node_modules/plugin"]["peerDependencies"] == {"react": "*"}


def test_one_copy_where_the_peers_agree(tmp_path: Path) -> None:
    packages = peer_project(
        tmp_path,
        {"react": "^18", "old": "^1", "plugin": "^1"},
        PEER_LOCK.replace('react: "npm:^17"', 'react: "npm:^18"'),
    )
    assert [path for path in packages if path.endswith("/plugin")] == ["node_modules/plugin"]
    assert [path for path in packages if path.endswith("/react")] == ["node_modules/react"]


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


def test_members_keep_the_members_they_link_to() -> None:
    project = str(PROJECTS / "pnpm-workspace")
    app = read(project, "pnpm-lock.yaml", members=["packages/app"])
    assert {"packages/app", "packages/greet", "node_modules/ms"} <= set(app)
    greet = read(project, "pnpm-lock.yaml", members=["packages/greet"])
    assert "packages/app" not in greet
    assert "node_modules/workspace-app" not in greet
    assert "node_modules/ms" in greet


def test_an_unknown_member_is_refused() -> None:
    with pytest.raises(SystemExit, match="no workspace member apps/nope"):
        read(str(PROJECTS / "pnpm-workspace"), "pnpm-lock.yaml", members=["apps/nope"])


def test_an_unknown_bun_lock_version_is_refused(tmp_path: Path) -> None:
    """The npm and pnpm readers refuse a lock version they do not know; bun did not. nixidae/nixidae#1003."""
    shutil.copytree(PROJECTS / "bun-native", tmp_path, dirs_exist_ok=True)
    lock = tmp_path / "bun.lock"
    lock.write_text(lock.read_text().replace('"lockfileVersion": 1', '"lockfileVersion": 9'))
    with pytest.raises(SystemExit, match=r"bun\.lock has version 9"):
        read(str(tmp_path), "bun.lock")
