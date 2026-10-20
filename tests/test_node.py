"""The npm planner against the lock files of `tests/node`."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pydd.core import Derivation, Fixed, Planner, fixed_output_path, nix32
from pydd.node import _readers
from pydd.node._npm import Graph, Lock, Menu, bin_links, fits, hosted_archive, strongest

PROJECTS = Path(__file__).parent / "node"
STORE = "/nix/store/00000000000000000000000000000000"
LOCKFILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock")
#: What a fixture needs in its menu to plan at all.
NEEDS = {"git-dep": {"hashes": {"isarray-2.0.5": "sha256-pGqpNBCt0CJ97Rvkg7XRNtCvByBNWTKXVle0zj4byZg="}}}


def lockfile(project: str) -> str:
    return next(name for name in LOCKFILES if (PROJECTS / project / name).is_file())


def lock(project: str, system: str = "x86_64-linux", omit: list[str] | None = None) -> Lock:
    omit = ["dev"] if omit is None else omit
    name = lockfile(project)
    if name != "package-lock.json":
        return Lock(_readers.read(str(PROJECTS / project), name), system, "glibc", omit)
    data = json.loads((PROJECTS / project / name).read_text())
    return Lock.read(data, system, "glibc", omit)


class KeepingWriter:
    def __init__(self) -> None:
        self.added: list[Derivation] = []

    def add(self, drv: Derivation) -> str:
        self.added.append(drv)
        spec = json.loads(drv.env.get("PYDD_NPM", "{}"))
        # Nix resolves a floating input to its output, whose path follows the
        # content. A copy out of the project stands for that output here.
        text = copied(Path(spec["path"])) if spec.get("action") == "copy" else json.dumps(drv.to_json(), sort_keys=True)
        digest = hashlib.sha256(text.encode()).digest()
        return f"/nix/store/{nix32(digest)[:32]}-{drv.name}.drv"

    def submit(self, path: str, output: str) -> None:
        pass


def copied(path: Path) -> str:
    """The content of a file or a directory, as text."""
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    return json.dumps({str(file.relative_to(path)): file.read_bytes().hex() for file in files if file.is_file()})


def plan(fixture: str, **changes: Any) -> tuple[KeepingWriter, dict[str, dict[str, Any]]]:
    writer = KeepingWriter()
    planner = Planner(writer, "x86_64-linux", None)
    fields: dict[str, Any] = {
        "name": fixture,
        "project": f"{STORE}-{fixture}-source",
        "subdir": ".",
        "nodejs": f"{STORE}-nodejs",
        "python": f"{STORE}-python3",
        "script": f"{STORE}-package.py",
        "cacert": f"{STORE}-cacert",
        "tools": [f"{STORE}-cc"],
        "native": {},
        "omit": ["dev"],
        "scripts": ["build"],
        "lockfile": lockfile(fixture),
    }
    menu = Menu(**(fields | changes))
    Graph(planner, menu, lock(fixture)).root(menu.scripts)
    return writer, {drv.name: json.loads(drv.env["PYDD_NPM"]) for drv in writer.added}


def test_a_nested_package_shadows_the_top_one() -> None:
    hello = lock("hello-npm")
    assert hello.resolve("", "ansi-styles") == "node_modules/ansi-styles"
    assert hello.resolve("node_modules/chalk", "ansi-styles") == "node_modules/chalk/node_modules/ansi-styles"
    # supports-color is not nested under chalk, so chalk finds the top one.
    assert hello.resolve("node_modules/chalk", "supports-color") == "node_modules/supports-color"


def test_a_workspace_link_resolves_to_its_member() -> None:
    workspace = lock("workspace")
    assert workspace.resolve("packages/app", "@workspace/greet") == "packages/greet"
    assert workspace.resolve("packages/greet", "ms") == "node_modules/ms"


def test_the_platform_chooses_among_optional_packages() -> None:
    x86 = lock("esbuild-app")
    arm = lock("esbuild-app", system="aarch64-linux")
    assert x86.present("node_modules/@esbuild/linux-x64")
    assert not x86.present("node_modules/@esbuild/darwin-arm64")
    assert arm.present("node_modules/@esbuild/linux-arm64")
    assert not arm.present("node_modules/@esbuild/linux-x64")


def test_omit_keeps_development_packages_out_of_the_result() -> None:
    esbuild = lock("esbuild-app")
    assert esbuild.present("node_modules/esbuild")
    assert not esbuild.shipped("node_modules/esbuild")
    assert esbuild.shipped("node_modules/ms")
    assert lock("esbuild-app", omit=[]).shipped("node_modules/esbuild")


@pytest.mark.parametrize(
    ("wanted", "actual", "result"),
    [
        (None, "linux", True),
        (["linux"], "linux", True),
        (["darwin"], "linux", False),
        (["!win32"], "linux", True),
        (["!linux"], "linux", False),
        (["!win32", "darwin"], "linux", False),
    ],
)
def test_os_and_cpu_as_npm_reads_them(wanted: list[str] | None, actual: str, result: bool) -> None:
    assert fits(wanted, actual) is result


def test_the_strongest_hash_of_an_integrity() -> None:
    assert strongest("sha1-abc= sha512-def==") == "sha512-def=="
    assert strongest("sha1-abc=") == "sha1-abc="


def test_a_fixed_output_has_the_path_nix_gives_it() -> None:
    # nix eval: builtins.derivation { name = "ms-2.1.3.tgz"; outputHashMode = "flat"; outputHash = ...; }
    sri = "sha512-6FlzubTLZG3J2a/NVCAleEhjzq5oxgHyaCU9yYXvcLsvoVaHJq/s5xXI6/XXP6tz7R9xAOtHnSO/tXtF3WRTlA=="
    assert fixed_output_path("ms-2.1.3.tgz", sri) == "/nix/store/nyjhgv4dh970fs5d125nbn7sc3dzxjzx-ms-2.1.3.tgz"
    empty = "sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="
    assert fixed_output_path("x", empty, "nar") == "/nix/store/d6q30mlzfljh7ha1b1m6fnifi34qr99p-x"


def test_one_fetch_and_one_node_for_each_package() -> None:
    writer, specs = plan("hello-npm")
    packages = [path for path in lock("hello-npm").packages if path]
    fetches = [drv for drv in writer.added if isinstance(drv.outputs["out"], Fixed)]
    assert len(fetches) == len(packages)
    assert len(writer.added) == 2 * len(packages) + 1
    assert specs["hello-npm"]["action"] == "root"
    assert "node_modules/chalk/node_modules/ansi-styles" in specs["hello-npm"]["tree"]


def test_an_install_script_gets_what_it_can_require() -> None:
    _, specs = plan("native-npm")
    sqlite = specs["better-sqlite3-11.10.0"]
    assert sqlite["scripts"]
    assert "node_modules/prebuild-install" in sqlite["tree"]
    assert "node_modules/bindings" in sqlite["tree"]
    assert sqlite["links"]["node_modules/.bin/prebuild-install"] == "../prebuild-install/bin.js"
    # A package with no script needs none of its dependencies.
    assert specs["bindings-1.5.0"]["tree"] == {}


def test_the_root_builds_with_development_packages_and_leaves_them_out() -> None:
    _, specs = plan("esbuild-app")
    root = specs["esbuild-app"]
    assert "node_modules/esbuild" in root["tree"]
    assert "node_modules/@esbuild/linux-x64" in root["tree"]
    assert "node_modules/@esbuild/darwin-arm64" not in root["tree"]
    assert "node_modules/esbuild" in root["omit"]
    assert "node_modules/.bin/esbuild" in root["build_bins"]
    assert "node_modules/.bin/esbuild" not in root["bins"]


def test_the_programs_of_workspace_members() -> None:
    _, specs = plan("workspace")
    root = specs["workspace"]
    assert root["programs"] == {"workspace-app": "packages/app/main.js"}
    assert root["links"] == {
        "node_modules/@workspace/greet": "../../packages/greet",
        "node_modules/workspace-app": "../packages/app",
    }
    assert bin_links(lock("workspace"), ["node_modules/workspace-app"]) == {
        "node_modules/.bin/workspace-app": "../../packages/app/main.js"
    }


def test_install_scripts_in_a_cycle_are_refused() -> None:
    data = {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"a": "1"}},
            **{
                f"node_modules/{name}": {
                    "version": "1.0.0",
                    "resolved": f"https://registry.example/{name}.tgz",
                    "integrity": "sha512-AAAA",
                    "hasInstallScript": True,
                    "dependencies": {other: "1"},
                }
                for name, other in (("a", "b"), ("b", "a"))
            },
        },
    }
    menu = Menu("cycle", STORE, ".", STORE, STORE, STORE, STORE, [], {}, ["dev"], [])
    graph = Graph(Planner(KeepingWriter(), "x86_64-linux", None), menu, Lock.read(data, "x86_64-linux", "glibc", []))
    with pytest.raises(SystemExit, match="cycle"):
        graph.root([])


def test_a_lock_of_version_one_is_refused() -> None:
    with pytest.raises(SystemExit, match="version 1"):
        Lock.read({"lockfileVersion": 1, "dependencies": {}}, "x86_64-linux", "glibc", [])


@pytest.mark.parametrize(
    ("resolved", "url"),
    [
        (
            "git+ssh://git@github.com/juliangruber/isarray.git#63ea4ca0a0d6b0574d6a470ebd26880c3026db4a",
            "https://codeload.github.com/juliangruber/isarray/tar.gz/63ea4ca0a0d6b0574d6a470ebd26880c3026db4a",
        ),
        (
            "git+https://gitlab.com/owner/project.git#0123abc",
            "https://gitlab.com/owner/project/repository/archive.tar.gz?ref=0123abc",
        ),
        ("git+https://git.sr.ht/~owner/project#0123abc", "https://git.sr.ht/~owner/project/archive/0123abc.tar.gz"),
        ("git+https://example.com/project.git#0123abc", None),
        ("git+ssh://git@github.com/owner/project.git", None),
    ],
)
def test_a_hosted_git_source_as_an_archive(resolved: str, url: str | None) -> None:
    assert hosted_archive(resolved) == url


def test_a_git_dependency_and_a_replaced_source() -> None:
    sha = "sha256-pGqpNBCt0CJ97Rvkg7XRNtCvByBNWTKXVle0zj4byZg="
    writer, specs = plan(
        "git-dep",
        hashes={"isarray-2.0.5": sha},
        native={"ms": {"tools": [], "env": {}, "src": f"{STORE}-ms-patched"}},
    )
    archive = next(drv for drv in writer.added if drv.name == "isarray-63ea4ca-source")
    assert archive.outputs["out"] == Fixed(sha, "nar")
    assert specs["isarray-2.0.5"]["tarball"].endswith("-isarray-63ea4ca-source")
    # A replaced source has no fetch.
    assert specs["ms-2.1.3"]["tarball"] == f"{STORE}-ms-patched"
    assert not any(drv.name == "ms-2.1.3.tgz" for drv in writer.added)


def test_a_git_dependency_needs_its_hash() -> None:
    with pytest.raises(SystemExit, match=r"isarray-2\.0\.5"):
        plan("git-dep")


def changed(before: KeepingWriter, after: KeepingWriter) -> set[str]:
    """The names of the derivations of `before` that `after` does not make as they are."""
    made = {json.dumps(drv.to_json(), sort_keys=True) for drv in after.added}
    return {drv.name for drv in before.added if json.dumps(drv.to_json(), sort_keys=True) not in made}


@pytest.mark.parametrize("project", sorted(path.name for path in PROJECTS.iterdir() if path.is_dir()))
def test_a_new_revision_of_the_project_rebuilds_only_the_root(project: str, tmp_path: Path) -> None:
    """A package depends on its lock entry, and not on the files beside it. Issue #894.

    A derivation that names the project's store path moves with every commit,
    and every package that depends on it rebuilds with it. A `file:`
    dependency's copy names it too, and nothing above the copy moves. The
    planner reads the project, so each revision is a real copy.
    """
    revisions = [tmp_path / "a", tmp_path / "b"]
    for revision in revisions:
        shutil.copytree(PROJECTS / project, revision)
    before, after = (plan(project, project=str(revision), **NEEDS.get(project, {}))[0] for revision in revisions)
    copies = {drv.name for drv in before.added if '"action":"copy"' in drv.env["PYDD_NPM"]}
    assert changed(before, after) == {project, *copies}


def test_a_new_lock_entry_rebuilds_that_package_and_what_runs_it(tmp_path: Path) -> None:
    """bindings gets a new hash. better-sqlite3's install script requires it, and the root holds both."""
    shutil.copytree(PROJECTS / "native-npm", tmp_path, dirs_exist_ok=True)
    data = json.loads((tmp_path / "package-lock.json").read_text())
    data["packages"]["node_modules/bindings"]["integrity"] = "sha512-" + "A" * 86 + "=="
    (tmp_path / "package-lock.json").write_text(json.dumps(data))
    before, _ = plan("native-npm")
    after, _ = plan(str(tmp_path), name="native-npm", project=f"{STORE}-native-npm-source")
    assert changed(before, after) == {"bindings-1.5.0.tgz", "bindings-1.5.0", "better-sqlite3-11.10.0", "native-npm"}
