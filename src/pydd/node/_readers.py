"""Read a pnpm, bun or Yarn v1 lock into the `packages` of an npm lock.

The npm lane plans from npm's map of install paths. bun's lock holds those
paths already. pnpm's and Yarn's hold a graph of versions, so the reader
places each version as npm does: as high in the tree as no other version of
that name prevents. Node then resolves each `require` to the version that the
lock names.

What a lock leaves out comes from the project or from the call: pnpm and bun
run the install scripts of the packages that the project allows, and Yarn v1
records no scripts, no programs and no platforms.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The lock files that the npm lane reads, in the order that it looks for them.
LOCKS = ("package-lock.json", "pnpm-lock.yaml", "bun.lock", "yarn.lock")

REGISTRY = "https://registry.npmjs.org"

#: A dependency's version in a lock, or a link to a workspace member, as `link:<path>`.
type Ref = str

#: A lock, a `package.json` or one of their blocks, as its tool wrote it.
#: The readers take the keys they know and pass the rest through.
type Document = dict[str, Any]

#: An entry of npm's `packages`. An npm lock passes through with every key
#: that npm wrote.
type Entry = dict[str, Any]


def find_lock(directory: str) -> str:
    for name in LOCKS:
        if Path(directory, name).is_file():
            return name
    raise SystemExit(f"pydd.node.npm: {directory} holds none of {', '.join(LOCKS)}.")


def manifest(directory: str, path: str) -> Document:
    data: Document = json.loads(Path(directory, path, "package.json").read_text(encoding="utf-8"))
    return data


def registry_url(name: str, version: str, registry: str = REGISTRY) -> str:
    return f"{registry.rstrip('/')}/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz"


def split_id(ident: str) -> tuple[str, str]:
    """`@scope/name@1.0.0` as its name and version."""
    name, _, version = ident[1:].partition("@")
    return ident[0] + name, version


def importer_entry(directory: str, path: str, deps: dict[str, dict[str, Ref]]) -> Entry:
    """The entry of the project or a workspace member: its manifest, with the dependencies the lock resolves."""
    data = manifest(directory, path)
    entry = {key: data[key] for key in ("name", "version", "bin", "workspaces") if key in data}
    for kind, names in deps.items():
        if names:
            entry[kind] = dict.fromkeys(sorted(names), "*")
    return entry


@dataclass
class Version:
    """One version of a package, as a graph lock names it."""

    name: str
    version: str
    resolved: str
    integrity: str | None = None
    dependencies: dict[str, Ref] = field(default_factory=dict[str, Ref])
    optional: dict[str, Ref] = field(default_factory=dict[str, Ref])
    os: list[str] | None = None
    cpu: list[str] | None = None
    libc: list[str] | None = None
    bin: dict[str, str] | None = None
    has_bin: bool = False
    #: The patch that pnpm applies to this version, relative to the project.
    patch: str | None = None

    def entry(self, scripted: bool) -> Entry:
        entry: Entry = {"name": self.name, "version": self.version, "resolved": self.resolved}
        if self.integrity:
            entry["integrity"] = self.integrity
        if self.dependencies:
            entry["dependencies"] = dict.fromkeys(sorted(self.dependencies), "*")
        if self.optional:
            entry["optionalDependencies"] = dict.fromkeys(sorted(self.optional), "*")
        for key, value in (("os", self.os), ("cpu", self.cpu), ("libc", self.libc), ("bin", self.bin)):
            if value is not None:
                entry[key] = value
        if self.has_bin and self.bin is None:
            entry["hasBin"] = True
        if self.patch is not None:
            entry["patch"] = self.patch
        if scripted:
            entry["hasInstallScript"] = True
        return entry


@dataclass
class Graph:
    """A lock as a graph of versions: what each importer and each version depends on."""

    versions: dict[str, Version]
    #: Each importer's dependencies by kind (`dependencies`, `devDependencies`,
    #: `optionalDependencies`), by importer path, the project first.
    importers: dict[str, dict[str, dict[str, Ref]]]
    #: How a version is named in `versions`, from a dependency's name and ref.
    key: Callable[[str, Ref], str]

    def target(self, importer: str, name: str, ref: Ref) -> str:
        """A version's key, or the member a link names as `link:<path>`."""
        if ref.startswith("link:"):
            # npm names the project itself by the empty path.
            path = posixpath.normpath(posixpath.join(importer, ref.removeprefix("link:")))
            return "link:" + ("" if path == "." else path)
        return self.key(name, ref)

    def only(self, members: Iterable[str]) -> None:
        """Keep the project, `members` and each member they link to, before placement."""
        kept: set[str] = set()
        stack = ["", *members]
        while stack:
            importer = stack.pop()
            if importer in kept:
                continue
            if importer not in self.importers:
                raise SystemExit(f"pydd.node.npm: the lock holds no workspace member {importer}.")
            kept.add(importer)
            for refs in self.importers[importer].values():
                for name, ref in refs.items():
                    if (target := self.target(importer, name, ref)).startswith("link:"):
                        stack.append(target.removeprefix("link:"))
        self.importers = {path: kinds for path, kinds in self.importers.items() if path in kept}


def lookup(layout: dict[str, str], path: str, name: str) -> str | None:
    """The path that Node finds `name` at from `path`, in a partial layout."""
    parts = path.split("/") if path else []
    for depth in range(len(parts), -1, -1):
        if depth and parts[depth - 1] == "node_modules":
            continue
        directory = "/".join(parts[:depth])
        candidate = f"{directory}/node_modules/{name}" if directory else f"node_modules/{name}"
        if candidate in layout:
            return candidate
    return None


def hoist(graph: Graph, directory: str, scripted: set[str]) -> dict[str, Entry]:
    """Place every version as npm does, and give the result as npm's `packages`.

    A version goes to the top `node_modules` unless another version of that
    name is already visible from its dependent; then it goes inside the
    dependent. Placement never moves a version that is already placed, so
    every `require` resolved before still resolves the same way.
    """
    layout: dict[str, str] = {}
    packages: dict[str, Entry] = {}
    queue: list[tuple[str, str, dict[str, Ref]]] = []
    for importer, kinds in graph.importers.items():
        packages[importer] = importer_entry(directory, importer, kinds)
        wanted: dict[str, Ref] = {}
        for refs in kinds.values():
            wanted |= refs
        queue.append((importer, importer, wanted))
    while queue:
        path, importer, wanted = queue.pop(0)
        for name in sorted(wanted):
            target = graph.target(importer, name, wanted[name])
            found = lookup(layout, path, name)
            if found is not None and layout[found] == target:
                continue
            if found is None:
                place = f"node_modules/{name}"
            else:
                place = f"{path}/node_modules/{name}" if path else f"node_modules/{name}"
            if place in layout:
                raise SystemExit(f"pydd.node.npm: {path or 'the project'} needs two packages named {name}.")
            layout[place] = target
            if target.startswith("link:"):
                packages[place] = {"resolved": target.removeprefix("link:"), "link": True}
                continue
            version = graph.versions[target]
            packages[place] = version.entry(version.name in scripted)
            # A link in a version's dependencies is relative to the project.
            queue.append((place, "", version.dependencies | version.optional))
    # npm links every workspace member at the top, needed or not.
    for member in graph.importers:
        member_name = packages[member].get("name")
        if member and member_name and f"node_modules/{member_name}" not in layout:
            packages[f"node_modules/{member_name}"] = {"resolved": member, "link": True}
    return dict(sorted(packages.items()))


def mark(packages: dict[str, Entry]) -> None:
    """Set npm's `dev` and `optional` on each package, from what reaches it.

    `dev`: no path from a production dependency of an importer reaches it.
    `optional`: every path to it passes an optional dependency.
    """
    from pydd.node._npm import Lock

    lock = Lock(packages, "")
    importers = [path for path in packages if "node_modules/" not in path]

    def walk(start: dict[str, list[str]], kinds: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        stack = [(path, name) for path, names in start.items() for name in names]
        while stack:
            path, name = stack.pop()
            found = lock.resolve(path, name)
            if found is None or found in seen:
                continue
            seen.add(found)
            entry = packages[found]
            stack += [(found, dep) for kind in kinds for dep in entry.get(kind, {})]
        return seen

    def names(kinds: Iterable[str]) -> dict[str, list[str]]:
        return {path: [name for kind in kinds for name in packages[path].get(kind, {})] for path in importers}

    production = walk(
        names(("dependencies", "optionalDependencies")),
        ("dependencies", "optionalDependencies", "peerDependencies"),
    )
    required = walk(names(("dependencies", "devDependencies")), ("dependencies", "peerDependencies"))
    for path, entry in packages.items():
        if "node_modules/" not in path or entry.get("link"):
            continue
        if path not in production:
            entry["dev"] = True
        if path not in required:
            entry["optional"] = True


def peel(ref: Ref) -> str:
    """A pnpm version without its peers: `18.2.0(react@18.2.0)` is `18.2.0`."""
    return ref.split("(", 1)[0]


def pnpm_key(name: str, ref: Ref) -> str:
    # An alias names the real package and its version: `string-width-cjs: string-width@4.2.3`.
    # A `file:` ref holds an `@` in its peers, and is no alias.
    if re.match(r"^@?[^@(]+@", ref) and not ref[0].isdigit() and not ref.startswith("file:"):
        return ref
    return f"{name}@{ref}"


def pnpm_resolved(name: str, version: str, resolution: Document) -> str:
    if "tarball" in resolution:
        tarball: str = resolution["tarball"]
        return tarball
    if resolution.get("type") == "git":
        repo = resolution["repo"].removeprefix("git+")
        return f"git+{repo}#{resolution['commit']}"
    if resolution.get("type") == "directory":
        return f"file:{resolution['directory']}"
    return registry_url(name, version)


def pnpm_importer(importer: Document) -> dict[str, dict[str, Ref]]:
    """An importer's locked versions, by kind."""
    kinds: dict[str, dict[str, Ref]] = {}
    for kind in ("dependencies", "devDependencies", "optionalDependencies"):
        specs: dict[str, Document] = importer.get(kind) or {}
        kinds[kind] = {name: spec["version"] for name, spec in specs.items()}
    return kinds


def read_pnpm(directory: str, data: Document) -> tuple[Graph, set[str]]:
    version = str(data.get("lockfileVersion", ""))
    if not version.startswith("9."):
        raise SystemExit(
            f"pydd.node.npm: pnpm-lock.yaml has version {version}. pydd reads version 9, of pnpm 9 and later."
        )
    settings: Document = {}
    workspace = Path(directory, "pnpm-workspace.yaml")
    if workspace.is_file():
        import yaml

        settings = yaml.safe_load(workspace.read_text(encoding="utf-8")) or {}
    root = manifest(directory, "").get("pnpm", {})
    # The lock holds only each patch's hash; the project names its file.
    patches: dict[str, str] = root.get("patchedDependencies", {}) | (settings.get("patchedDependencies") or {})
    patched: Document = data.get("patchedDependencies") or {}
    metadata: dict[str, Document] = data.get("packages") or {}
    snapshots: dict[str, Document] = data.get("snapshots") or {}
    versions: dict[str, Version] = {}
    for key, snapshot in snapshots.items():
        name, ref = split_id(key)
        meta = metadata[f"{name}@{peel(ref)}"]
        listed = lambda value: [value] if isinstance(value, str) else value  # noqa: E731
        patch = None
        # Not `patch_hash=` in the key: a peer's own patch shows there too.
        if f"{name}@{peel(ref)}" in patched:
            patch = patches.get(f"{name}@{peel(ref)}", patches.get(name))
            if patch is None:
                raise SystemExit(
                    f"pydd.node.npm: pnpm-lock.yaml patches {name}@{peel(ref)}, and neither "
                    "pnpm-workspace.yaml nor package.json names its patch file."
                )
        versions[key] = Version(
            name=name,
            # A directory has no version in the lock, and its key names the directory.
            version=(
                manifest(directory, meta["resolution"]["directory"]).get("version", "0")
                if meta["resolution"].get("type") == "directory"
                else peel(ref)
            ),
            resolved=pnpm_resolved(name, peel(ref), meta["resolution"]),
            integrity=meta["resolution"].get("integrity"),
            dependencies=dict(snapshot.get("dependencies") or {}),
            optional=dict(snapshot.get("optionalDependencies") or {}),
            os=listed(meta.get("os")),
            cpu=listed(meta.get("cpu")),
            libc=listed(meta.get("libc")),
            has_bin=bool(meta.get("hasBin")),
            patch=patch,
        )
    importers: dict[str, dict[str, dict[str, Ref]]] = {}
    locked: dict[str, Document] = data.get("importers") or {}
    for path, importer in sorted(locked.items(), key=lambda item: item[0] != "."):
        importers["" if path == "." else path] = pnpm_importer(importer)
    graph = Graph(versions, importers, pnpm_key)
    built = set(root.get("onlyBuiltDependencies", []))
    built |= set(settings.get("onlyBuiltDependencies", []))
    # pnpm 11: `allowBuilds`, by name or by `name@version`.
    allow: dict[str, object] = settings.get("allowBuilds") or {}
    built |= {split_id(spec)[0] if "@" in spec[1:] else spec for spec, allowed in allow.items() if allowed is True}
    return graph, built


def bun_path(key: str, members: dict[str, str]) -> str:
    """An install path from a bun key: `chalk/ansi-styles` is `node_modules/chalk/node_modules/ansi-styles`.

    A key that starts with a workspace member's name lies inside that member.
    """
    parts = key.split("/")
    names = []
    while parts:
        part = parts.pop(0)
        names.append(f"{part}/{parts.pop(0)}" if part.startswith("@") and parts else part)
    prefix = ""
    if len(names) > 1 and names[0] in members:
        prefix = members[names.pop(0)] + "/"
    return prefix + "/".join(f"node_modules/{name}" for name in names)


BUN_LOCK_VERSIONS = (0, 1)
"""The `bun.lock` versions this reader knows; another one is refused, not misread."""


def read_bun(directory: str, text: str) -> tuple[dict[str, Entry], set[str]]:
    # bun writes JSON with trailing commas.
    data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    version = data.get("lockfileVersion")
    if version not in BUN_LOCK_VERSIONS:
        raise SystemExit(
            f"pydd.node.npm: bun.lock has version {version}. pydd reads versions 0 and 1, of bun 1.2 and later."
        )
    workspaces: dict[str, Document] = data["workspaces"]
    members = {meta["name"]: path for path, meta in workspaces.items() if path}
    packages: dict[str, Entry] = {}
    for path, meta in workspaces.items():
        packages[path] = importer_entry(
            directory,
            path,
            {kind: meta.get(kind, {}) for kind in ("dependencies", "devDependencies", "optionalDependencies")},
        )
    trusted = set(manifest(directory, "").get("trustedDependencies", []))
    for key, value in data["packages"].items():
        path = bun_path(key, members)
        name, ref = split_id(value[0])
        if ref.startswith("workspace:"):
            packages[path] = {"resolved": ref.removeprefix("workspace:"), "link": True}
            continue
        if len(value) != 4 or not isinstance(value[1], str):
            raise SystemExit(f"pydd.node.npm: bun.lock gives {key} as {value[0]}. pydd reads packages from a registry.")
        _, registry, meta, integrity = value
        entry: Entry = {
            "name": name,
            "version": ref,
            "resolved": registry_url(name, ref, registry or REGISTRY),
            "integrity": integrity,
        }
        for field_name in ("dependencies", "optionalDependencies", "peerDependencies", "peerDependenciesMeta", "bin"):
            if field_name in meta:
                entry[field_name] = meta[field_name]
        for field_name in ("os", "cpu", "libc"):
            if field_name in meta:
                entry[field_name] = [meta[field_name]] if isinstance(meta[field_name], str) else meta[field_name]
        if name in trusted:
            entry["hasInstallScript"] = True
        packages[path] = entry
    return dict(sorted(packages.items())), trusted


def parse_yarn(text: str) -> dict[str, Document]:
    """A Yarn v1 lock as a map from each of its `name@range` keys to its block."""
    blocks: dict[str, Document] = {}
    current: Document = {}
    section: dict[str, str] | None = None

    def unquote(word: str) -> str:
        return word[1:-1] if word.startswith('"') else word

    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            current = {}
            for key in line.rstrip(":").split(", "):
                blocks[unquote(key.strip())] = current
            continue
        word, _, value = line.strip().partition(" ")
        word = unquote(word.rstrip(":"))
        if indent == 2 and not value:
            section = current.setdefault(word, {})
        elif indent == 2:
            current[word] = unquote(value)
            section = None
        elif section is not None:
            section[word] = unquote(value)
    return blocks


def read_yarn(directory: str, text: str) -> Graph:
    if "# yarn lockfile v1" not in text:
        raise SystemExit(
            "pydd.node.npm: yarn.lock is a lock of Yarn 2 or newer. Its checksums are of Yarn's own "
            "zip archives, not of the packages, so Nix cannot check a fetch against them."
        )
    blocks = parse_yarn(text)
    root = manifest(directory, "")
    patterns = root.get("workspaces", [])
    patterns = patterns.get("packages", []) if isinstance(patterns, dict) else patterns
    members: dict[str, str] = {}
    for pattern in patterns:
        for found in sorted(Path(directory).glob(pattern)):
            if (found / "package.json").is_file():
                path = found.relative_to(directory).as_posix()
                members[manifest(directory, path)["name"]] = path

    # Several ranges share one block: the version is the block, by its first key.
    first: dict[int, str] = {}
    for spec, block in blocks.items():
        first.setdefault(id(block), spec)

    def key(name: str, ref: Ref) -> str:
        return first[id(blocks[f"{name}@{ref}"])]

    def refs(importer: str, deps: dict[str, str]) -> dict[str, Ref]:
        return {
            name: f"link:{posixpath.relpath(members[name], importer or '.')}"
            if name in members and f"{name}@{spec}" not in blocks
            else spec
            for name, spec in deps.items()
        }

    versions: dict[str, Version] = {}
    for spec, block in blocks.items():
        if first[id(block)] != spec:
            continue
        name = spec[0] + spec[1:].split("@", 1)[0]
        resolved = block.get("resolved", "").split("#", 1)[0]
        versions[spec] = Version(
            name=name,
            version=block["version"],
            resolved=resolved,
            integrity=block.get("integrity"),
            dependencies=refs("", block.get("dependencies", {})),
            optional=refs("", block.get("optionalDependencies", {})),
            # Yarn v1 records no programs, so the node reads them from the package.
            has_bin=True,
        )
    importers: dict[str, dict[str, dict[str, Ref]]] = {}
    for path in ["", *sorted(members.values())]:
        data = manifest(directory, path)
        importers[path] = {
            kind: refs(path, data.get(kind, {})) for kind in ("dependencies", "devDependencies", "optionalDependencies")
        }
    return Graph(versions, importers, key)


def read(
    directory: str, lock: str, scripts: Iterable[str] = (), members: Iterable[str] | None = None
) -> dict[str, Entry]:
    """The `packages` of an npm lock, from a lock of another tool in `directory`.

    `scripts` names more packages whose install scripts run. A graph lock
    places only `members`, their linked members and the project, when given.
    """
    text = Path(directory, lock).read_text(encoding="utf-8")
    match lock:
        case "pnpm-lock.yaml":
            import yaml

            # A lock can open with a document that locks pnpm itself
            # (packageManagerDependencies). The project's lock is the last one.
            *_, data = yaml.safe_load_all(text)
            graph, built = read_pnpm(directory, data)
            if members is not None:
                graph.only(members)
            packages = hoist(graph, directory, built | set(scripts))
        case "bun.lock":
            packages, _ = read_bun(directory, text)
            for entry in packages.values():
                if entry.get("name") in set(scripts):
                    entry["hasInstallScript"] = True
        case "yarn.lock":
            graph = read_yarn(directory, text)
            if members is not None:
                graph.only(members)
            packages = hoist(graph, directory, set(scripts))
        case _:
            raise SystemExit(f"pydd.node.npm: pydd reads no lock named {lock}.")
    mark(packages)
    return packages
