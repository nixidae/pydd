"""Plan an npm project from its `package-lock.json`: one node for each package.

The lock names every package by the path that npm installs it at, with its
URL and its hash. A package is a fixed-output fetch and a node that unpacks
it. A package with an install script runs it in that node, over a copy of
the part of the tree that the script can `require`. The root copies every
node to the path the lock gives it, so Node resolves each `require` exactly
as it would after `npm ci`.

Node resolves a module from its real path, so a link into the store would
look for its dependencies beside the store path. That is why the root
copies, and why a node holds no dependency of its own.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from pydd.core import Derivation, Fixed, Node, Planner, tool_env
from pydd.node import _readers

#: Nix's systems as npm's `cpu` and `os` name them.
CPU = {"x86_64": "x64", "aarch64": "arm64", "i686": "ia32", "armv7l": "arm", "riscv64": "riscv64"}
OS = {"linux": "linux", "darwin": "darwin", "freebsd": "freebsd"}

#: The order of preference among the hashes of one `integrity`.
ALGORITHMS = ("sha512", "sha384", "sha256", "sha1")

LIFECYCLE = ("preinstall", "install", "postinstall")


@dataclass(frozen=True)
class Menu:
    """What `lib/node/default.nix` gives the planner."""

    name: str
    project: str
    #: The project's directory inside `project`, which may hold more.
    subdir: str
    nodejs: str
    python: str
    script: str
    cacert: str
    #: What every script runs with: stdenv's own tools, a C compiler and
    #: the Python and make that node-gyp calls.
    tools: list[str]
    #: Tools and environment of a package, by package name.
    native: dict[str, Any]
    #: npm's `--omit`: `dev`, `optional` or `peer`.
    omit: list[str]
    #: The scripts of `package.json` that the root runs, in order, in the
    #: project and in each workspace member that defines them.
    scripts: list[str]
    libc: str = "glibc"
    #: The hash of each git dependency's unpacked archive, by `<name>-<version>`.
    hashes: dict[str, str] = field(default_factory=dict[str, str])
    #: The derivation and output of each store path above that one builds, as
    #: `[drv, output]`. A node names that derivation as its input, as a string
    #: with that path names it in Nix, and names every other path as a source.
    refs: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    #: The lock file in the project's directory: npm's, pnpm's, bun's or Yarn's.
    lockfile: str = "package-lock.json"
    #: More packages whose install scripts run. pnpm and bun run only those
    #: that the project allows, and Yarn v1 records none.
    install_scripts: list[str] = field(default_factory=list[str])

    @property
    def directory(self) -> str:
        return posixpath.normpath(posixpath.join(self.project, self.subdir))


def dump(value: Any) -> str:
    """JSON as `builtins.toJSON` writes it, so the host makes the same node (`lib/node/graph.nix`)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


#: What `builtins.derivation` adds to the environment of a floating output.
FLOATING = {"outputHashMode": "recursive", "outputHashAlgo": "sha256"}


def name_of(path: str, entry: dict[str, Any]) -> str:
    """The name that a package installs as. An alias installs under the alias."""
    if "node_modules/" not in path:
        return entry.get("name", posixpath.basename(path))
    return path.rsplit("node_modules/", 1)[1]


def store_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9+._?=-]", "-", name.removeprefix("@"))


def strongest(integrity: str) -> str:
    """The strongest hash of an `integrity`, which may list several."""
    hashes = dict(item.split("-", 1) for item in integrity.split())
    for algorithm in ALGORITHMS:
        if algorithm in hashes:
            return f"{algorithm}-{hashes[algorithm]}"
    raise ValueError(f"no hash that Nix reads in {integrity!r}")


def fits(wanted: list[str] | None, actual: str) -> bool:
    """npm's rule for `os`, `cpu` and `libc`: a `!` excludes, and any other entry is a choice."""
    if not wanted:
        return True
    if f"!{actual}" in wanted:
        return False
    chosen = [item for item in wanted if not item.startswith("!")]
    return not chosen or actual in chosen


GIT = re.compile(r"^git\+(?:ssh|https?)://(?:git@)?([^/:]+)[/:](.+?)(?:\.git)?#([0-9a-f]+)$")


def hosted_archive(resolved: str) -> str | None:
    """The archive of a commit that a git source on a known host names, as nixpkgs' prefetch-npm-deps finds it."""
    match = GIT.match(resolved)
    if match is None:
        return None
    host, project, commit = match.groups()
    match host:
        case "github.com":
            return f"https://codeload.github.com/{project}/tar.gz/{commit}"
        case "gitlab.com":
            return f"https://gitlab.com/{project}/repository/archive.tar.gz?ref={commit}"
        case "git.sr.ht":
            return f"https://git.sr.ht/{project}/archive/{commit}.tar.gz"
    return None


def fetch_name(url: str) -> str:
    """The name of a fetch, from its URL alone, so two paths of one URL give one node."""
    match = GIT.match(url)
    if match is not None:
        _, project, commit = match.groups()
        return f"{store_name(posixpath.basename(project))}-{commit[:7]}-source"
    return store_name(posixpath.basename(url.split("?", 1)[0]))


def bins_of(path: str, entry: dict[str, Any]) -> dict[str, str]:
    """The `bin` of a package as a map, by program name."""
    match entry.get("bin"):
        case str(file):
            return {name_of(path, entry).rsplit("/", 1)[-1]: file}
        case dict(bins):
            return bins
        case _:
            return {}


@dataclass
class Lock:
    """The `packages` of a lock file, with npm's choices for one platform."""

    packages: dict[str, dict[str, Any]]
    system: str
    libc: str = "glibc"
    omit: list[str] = field(default_factory=lambda: ["dev"])

    @classmethod
    def read(cls, data: dict[str, Any], system: str, libc: str, omit: list[str]) -> Lock:
        if data.get("lockfileVersion", 1) < 2 or "packages" not in data:
            raise SystemExit(
                "pydd.node.npm: the lock file has version 1, which names no install paths. "
                "Run `npm install --package-lock-only` with npm 7 or newer, and commit the file."
            )
        return cls(data["packages"], system, libc, omit)

    def on_platform(self, path: str) -> bool:
        entry = self.packages[path]
        arch, _, kernel = self.system.partition("-")
        return (
            fits(entry.get("cpu"), CPU.get(arch, arch))
            and fits(entry.get("os"), OS.get(kernel, kernel))
            and (kernel != "linux" or fits(entry.get("libc"), self.libc))
        )

    def present(self, path: str) -> bool:
        """Whether npm installs `path` for a build: on this platform, or a required package."""
        entry = self.packages[path]
        return self.on_platform(path) or not entry.get("optional", False)

    def shipped(self, path: str) -> bool:
        """Whether `path` stays in the result, after npm's `--omit`."""
        entry = self.packages[path]
        if not self.present(path):
            return False
        if "dev" in self.omit and entry.get("dev", False):
            return False
        if "optional" in self.omit and entry.get("optional", False):
            return False
        return not ("peer" in self.omit and entry.get("peer", False))

    def resolve(self, path: str, name: str) -> str | None:
        """The path that Node finds `name` at from the package at `path`.

        Node tries `node_modules/<name>` in the directory of the package and
        then in each parent. A link resolves to the directory it names.
        """
        parts = path.split("/") if path else []
        for depth in range(len(parts), -1, -1):
            if depth and parts[depth - 1] == "node_modules":
                continue
            directory = "/".join(parts[:depth])
            candidate = f"{directory}/node_modules/{name}" if directory else f"node_modules/{name}"
            entry = self.packages.get(candidate)
            if entry is None:
                continue
            if entry.get("link"):
                return entry["resolved"]
            return candidate if self.present(candidate) else None
        return None

    def dependencies(self, path: str) -> list[str]:
        """The paths that the package at `path` can `require` directly."""
        entry = self.packages[path]
        optional = set(entry.get("optionalDependencies", {}))
        optional |= {name for name, meta in entry.get("peerDependenciesMeta", {}).items() if meta.get("optional")}
        names = [
            *entry.get("dependencies", {}),
            *entry.get("optionalDependencies", {}),
            *entry.get("peerDependencies", {}),
        ]
        if "node_modules/" not in path:
            # The project and a workspace member build with their development
            # dependencies.
            names += entry.get("devDependencies", {})
        found = []
        for name in dict.fromkeys(names):
            resolved = self.resolve(path, name)
            if resolved is None:
                if name in optional:
                    continue
                raise SystemExit(f"pydd.node.npm: the lock gives {path or 'the project'} no {name}.")
            found.append(resolved)
        return found

    def closure(self, path: str) -> list[str]:
        """Every path that a script of the package at `path` can reach, `path` not included."""
        seen: dict[str, None] = {}
        stack = self.dependencies(path)
        while stack:
            dep = stack.pop()
            if dep in seen or dep == path:
                continue
            seen[dep] = None
            stack += self.dependencies(dep)
        return sorted(seen)

    def nested(self, path: str) -> list[str]:
        """The packages that the lock installs inside `path`, which come from their own nodes."""
        prefix = f"{path}/node_modules/" if path else "node_modules/"
        return sorted(
            other
            for other, entry in self.packages.items()
            if other.startswith(prefix) and not entry.get("inBundle") and not entry.get("link")
        )

    def is_package(self, path: str) -> bool:
        """An entry that npm fetches, rather than a link, a workspace member or a bundled package."""
        entry = self.packages[path]
        return "node_modules/" in path and not entry.get("link") and not entry.get("inBundle")


def bin_probes(lock: Lock, paths: list[str]) -> list[list[str]]:
    """The packages of `paths` whose programs the lock does not name, as `[.bin directory, package path]`.

    pnpm and Yarn v1 record only that a package has programs, or nothing. A
    node reads the names from the package's own `package.json`.
    """
    probes = []
    for path in sorted(paths):
        entry = lock.packages[path]
        if entry.get("link") or "bin" in entry or not entry.get("hasBin"):
            continue
        probes.append([path.rsplit("node_modules/", 1)[0] + "node_modules/.bin", path])
    return probes


def bin_links(lock: Lock, paths: list[str]) -> dict[str, str]:
    """The `.bin` link of each program of each package of `paths`, relative to its target.

    npm writes a package's programs into the `node_modules/.bin` that holds it.
    """
    links = {}
    for path in sorted(paths):
        entry = lock.packages[path]
        if entry.get("link"):
            target_path, entry = entry["resolved"], lock.packages[entry["resolved"]]
        else:
            target_path = path
        modules = path.rsplit("node_modules/", 1)[0] + "node_modules"
        for program, file in bins_of(path, entry).items():
            target = posixpath.relpath(posixpath.join(target_path, file), f"{modules}/.bin")
            links[f"{modules}/.bin/{posixpath.basename(program)}"] = target
    return links


class Graph:
    """The packages of one lock file, made into nodes leaves first."""

    def __init__(self, planner: Planner, menu: Menu, lock: Lock) -> None:
        self.planner = planner
        self.menu = menu
        self.lock = lock
        self.nodes: dict[str, Node] = {}
        self.fetches: dict[str, Node] = {}
        #: The git source of each archive's URL.
        self.git: dict[str, str] = {}
        self.planning: list[str] = []

    def references(self, paths: set[str], inputs: list[Node]) -> tuple[list[str], dict[str, list[str]]]:
        """The sources and the input derivations of a node that names `paths` and the outputs of `inputs`."""
        drvs: dict[str, set[str]] = {node.path: {"out"} for node in inputs}
        srcs = set()
        for path in paths:
            if path in self.menu.refs:
                drv, output = self.menu.refs[path]
                drvs.setdefault(drv, set()).add(output)
            else:
                srcs.add(path)
        return sorted(srcs), {drv: sorted(outputs) for drv, outputs in drvs.items()}

    def derivation(
        self,
        name: str,
        spec: dict[str, Any],
        inputs: list[Node],
        package: str | None = None,
        scripted: bool = False,
        as_file: bool = False,
    ) -> Derivation:
        menu = self.menu
        native = menu.native.get(package or "", {"tools": [], "env": {}}) if scripted else {"tools": [], "env": {}}
        tools = [*native["tools"], *(menu.tools if scripted else [])]
        env = tool_env(tools, f"{menu.nodejs}/bin") | {"PYDD_NPM": dump(spec)} | FLOATING
        env |= native["env"]
        if as_file:
            # One variable holds at most 128 KiB, and the root's spec names every package.
            env["passAsFile"] = "PYDD_NPM"
        srcs, drvs = self.references({menu.python, menu.script, menu.nodejs, *tools, *spec.get("srcs", [])}, inputs)
        return Derivation(
            name=name,
            system=self.planner.system,
            builder=f"{menu.python}/bin/python3",
            args=["-I", menu.script],
            env=env,
            srcs=srcs,
            drvs=drvs,
        )

    def fixed(self, url: str, spec: dict[str, Any], output: Fixed) -> Node:
        """A fixed-output node of `url`, once for each URL."""
        if url not in self.fetches:
            menu = self.menu
            srcs, drvs = self.references({menu.python, menu.script, menu.cacert}, [])
            drv = Derivation(
                name=fetch_name(url if spec["action"] == "fetch" else self.git[url]),
                system=self.planner.system,
                builder=f"{menu.python}/bin/python3",
                args=["-I", menu.script],
                env={
                    "PYDD_NPM": dump(spec),
                    "SSL_CERT_FILE": f"{menu.cacert}/etc/ssl/certs/ca-bundle.crt",
                    "outputHash": output.hash,
                    "outputHashMode": "flat" if output.method == "flat" else "recursive",
                },
                srcs=srcs,
                drvs=drvs,
                outputs={"out": output},
            )
            self.fetches[url] = self.planner.add(drv)
        return self.fetches[url]

    def fetch(self, path: str) -> Node:
        entry = self.lock.packages[path]
        url, integrity = entry["resolved"], entry.get("integrity")
        if integrity is None:
            raise SystemExit(
                f"pydd.node.npm: {path} comes from {url}, and the lock holds no hash of it. "
                "npm-lockfile-fix writes the missing hashes into a lock."
            )
        return self.fixed(url, {"action": "fetch", "url": url}, Fixed(strongest(integrity)))

    def archive(self, path: str) -> Node:
        """A fixed-output node that unpacks the archive of a git dependency's commit."""
        entry = self.lock.packages[path]
        resolved: str = entry["resolved"]
        url = hosted_archive(resolved)
        if url is None:
            raise SystemExit(
                f"pydd.node.npm: {path} comes from {resolved}. pydd fetches a git dependency "
                "from GitHub, GitLab or sourcehut, by its commit. Give the package as "
                "`overrides.<name>.src`."
            )
        key = f"{name_of(path, entry)}-{entry.get('version', '0')}"
        if key not in self.menu.hashes:
            raise SystemExit(
                f"pydd.node.npm: {path} comes from {resolved}. Give its hash as "
                f'`outputHashes."{key}"`. `nix store prefetch-file --unpack {url}` prints it.'
            )
        self.git[url] = resolved
        return self.fixed(url, {"action": "archive", "url": url}, Fixed(self.menu.hashes[key], "nar"))

    def tarball(self, path: str) -> tuple[str, list[Node], list[str]]:
        """Where the package of `path` is, as a tarball or a directory, the node that
        fetches it, and the store path that holds it.
        """
        entry = self.lock.packages[path]
        replaced = self.menu.native.get(name_of(path, entry), {}).get("src")
        if replaced is not None:
            return replaced, [], [replaced]
        resolved: str = entry["resolved"]
        if re.match(r"^https?://", resolved):
            node = self.fetch(path)
            return node.output(), [node], []
        if resolved.startswith(("git+", "git:")):
            node = self.archive(path)
            return node.output(), [node], []
        if "://" in resolved:
            raise SystemExit(
                f"pydd.node.npm: {path} comes from {resolved}. pydd fetches packages from a "
                "registry, from GitHub, GitLab or sourcehut, and from the project's own files."
            )
        local = posixpath.normpath(posixpath.join(self.menu.directory, resolved.removeprefix("file:")))
        return local, [], [self.menu.project]

    def package(self, path: str) -> Node:
        if path in self.nodes:
            return self.nodes[path]
        if path in self.planning:
            cycle = " -> ".join([*self.planning[self.planning.index(path) :], path])
            raise SystemExit(f"pydd.node.npm: install scripts depend on each other in a cycle: {cycle}")
        self.planning.append(path)
        entry = self.lock.packages[path]
        tarball, inputs, srcs = self.tarball(path)
        name = name_of(path, entry)
        scripted = bool(entry.get("hasInstallScript"))
        patch = entry.get("patch")
        tree: dict[str, str] = {}
        links: dict[str, str] = {}
        probes: list[list[str]] = []
        if scripted:
            closure = self.lock.closure(path)
            for dep in closure:
                if not self.lock.is_package(dep):
                    raise SystemExit(
                        f"pydd.node.npm: the install script of {path} needs {dep}, which is not a package."
                    )
                node = self.package(dep)
                tree[dep] = node.output()
                inputs.append(node)
            links = bin_links(self.lock, [*closure, path])
            probes = bin_probes(self.lock, [*closure, path])
        spec = {
            "action": "package",
            "tarball": tarball,
            "srcs": srcs,
            "path": path,
            "name": name,
            "version": entry.get("version", "0"),
            "bins": bins_of(path, entry),
            "scripts": scripted,
            "git": entry.get("resolved", "").startswith(("git+", "git:")) and tarball not in srcs,
            "tree": tree,
            "links": links,
            "nested": [nested for nested in self.lock.nested(path) if nested in tree],
            "nodejs": self.menu.nodejs,
        }
        # Only when the lock lacks them, so a node of an npm lock keeps its path.
        if probes:
            spec["probes"] = probes
        if entry.get("hasBin") and "bin" not in entry:
            spec["bins"] = None
        if patch is not None:
            spec["patch"] = posixpath.join(self.menu.directory, patch)
            spec["srcs"] = sorted({*srcs, self.menu.project})
        # A patched node runs `patch`, one of the script tools.
        drv = self.derivation(
            f"{store_name(name)}-{entry.get('version', '0')}", spec, inputs, name, scripted or patch is not None
        )
        self.planning.pop()
        self.nodes[path] = self.planner.add(drv)
        return self.nodes[path]

    def root(self, scripts: list[str]) -> Node:
        lock = self.lock
        present = [path for path in lock.packages if path and lock.present(path)]
        tree = {path: self.package(path).output() for path in present if lock.is_package(path)}
        links = {
            path: posixpath.relpath(lock.packages[path]["resolved"] or ".", posixpath.dirname(path))
            for path in lock.packages
            if lock.packages[path].get("link")
        }
        shipped = {path for path in present if lock.shipped(path)}
        members = [path for path in lock.packages if path and "node_modules/" not in path]
        bins = {
            program: posixpath.normpath(posixpath.join(member, file))
            for member in ["", *members]
            for program, file in bins_of(member, lock.packages[member]).items()
        }
        spec = {
            "action": "root",
            "name": lock.packages[""].get("name", self.menu.name),
            "source": self.menu.directory,
            "srcs": [self.menu.project],
            "tree": tree,
            "links": links,
            "build_bins": bin_links(lock, [*tree, *links]),
            "bins": bin_links(lock, [path for path in [*tree, *links] if path in shipped or path in links]),
            "omit": sorted(path for path in tree if path not in shipped),
            "scripts": scripts,
            # pnpm, Yarn and bun run a script in the project only.
            "workspaces": bool(members) and self.menu.lockfile == "package-lock.json",
            "programs": bins,
            "nodejs": self.menu.nodejs,
        }
        if build_probes := bin_probes(lock, [*tree, *links]):
            spec["build_probes"] = build_probes
            spec["probes"] = bin_probes(lock, [path for path in [*tree, *links] if path in shipped or path in links])
        inputs = [self.nodes[path] for path in tree]
        return self.planner.add(
            self.derivation(self.menu.name, spec, inputs, lock.packages[""].get("name"), True, as_file=True)
        )


def main() -> int:
    planner = Planner.from_environ()
    menu = Menu(**planner.menu)
    if menu.lockfile == "package-lock.json":
        with open(posixpath.join(menu.directory, menu.lockfile), encoding="utf-8") as handle:
            lock = Lock.read(json.load(handle), planner.system, menu.libc, menu.omit)
        for path, entry in lock.packages.items():
            if path and name_of(path, entry) in menu.install_scripts:
                entry["hasInstallScript"] = True
    else:
        packages = _readers.read(menu.directory, menu.lockfile, menu.install_scripts)
        lock = Lock(packages, planner.system, menu.libc, menu.omit)
    planner.submit(Graph(planner, menu, lock).root(menu.scripts))
    return 0
