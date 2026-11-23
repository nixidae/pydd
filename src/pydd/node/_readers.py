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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote

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
    #: The patch that pnpm or Yarn applies to this version, relative to the project.
    patch: str | None = None
    #: The lock's tool runs this version's install script.
    scripted: bool = False
    #: The version of each peer that this version's dependent provides. Placement
    #: puts this version where each of them resolves.
    peers: dict[str, Ref] = field(default_factory=dict[str, Ref])
    #: The lock's entry that this version instantiates with `peers`. Versions of
    #: one entry share a place where the peers of each resolve as it needs.
    base: str | None = None

    def entry(self, scripted: bool) -> Entry:
        scripted = scripted or self.scripted
        entry: Entry = {"name": self.name, "version": self.version, "resolved": self.resolved}
        if self.integrity:
            entry["integrity"] = self.integrity
        if self.dependencies:
            entry["dependencies"] = dict.fromkeys(sorted(self.dependencies), "*")
        if self.optional:
            entry["optionalDependencies"] = dict.fromkeys(sorted(self.optional), "*")
        if self.peers:
            entry["peerDependencies"] = dict.fromkeys(sorted(self.peers), "*")
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


def ancestors(path: str) -> list[str]:
    """The directories whose `node_modules` Node searches from `path`, the top first."""
    parts = path.split("/") if path else []
    return [
        "/".join(parts[:depth]) for depth in range(len(parts) + 1) if not depth or parts[depth - 1] != "node_modules"
    ]


def ordered(wanted: dict[str, Ref], peers: Callable[[str, Ref], dict[str, Ref]]) -> list[str]:
    """`wanted` by name, with each dependency after the siblings that it takes as peers."""
    names = sorted(name for name, ref in wanted.items() if not peers(name, ref))
    waiting = sorted(name for name, ref in wanted.items() if peers(name, ref))
    while waiting:
        ready = [
            name for name in waiting if not any(peer in waiting for peer in peers(name, wanted[name]) if peer != name)
        ]
        for name in ready or waiting[:1]:
            names.append(name)
            waiting.remove(name)
    return names


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

    def peers_of(target: str) -> dict[str, Ref]:
        return {} if target.startswith("link:") else graph.versions[target].peers

    def placed(place: str, target: str) -> bool:
        """Each peer of `target` resolves from `place` to the version its dependent provides."""
        for peer, ref in peers_of(target).items():
            seen = lookup(layout, place, peer)
            if seen is None or layout[seen] != graph.target("", peer, ref):
                return False
        return True

    def shares(placed_target: str, target: str, place: str) -> bool:
        """`target` can use `placed_target` at `place`: one lock entry, and its peers resolve there."""
        if placed_target.startswith("link:") or target.startswith("link:"):
            return False
        base = graph.versions[target].base
        return base is not None and graph.versions[placed_target].base == base and placed(place, target)

    while queue:
        path, importer, wanted = queue.pop(0)
        for name in ordered(wanted, lambda name, ref, importer=importer: peers_of(graph.target(importer, name, ref))):
            target = graph.target(importer, name, wanted[name])
            found = lookup(layout, path, name)
            if found is not None and (layout[found] == target or shares(layout[found], target, found)):
                continue
            if found is None:
                place = f"node_modules/{name}"
            else:
                place = f"{path}/node_modules/{name}" if path else f"node_modules/{name}"
            if peers_of(target):
                # The highest place that `path` sees, and from which every peer resolves.
                # Below `found`, which `path` would see first.
                floor = -1 if found is None else len(found) - len(f"node_modules/{name}")
                choices = [
                    f"{directory}/node_modules/{name}" if directory else f"node_modules/{name}"
                    for directory in ancestors(path)
                    if len(directory) > floor
                    and (directory in ("", path) or directory in layout or directory in graph.importers)
                ]
                place = next(
                    (choice for choice in choices if choice not in layout and placed(choice, target)),
                    place,
                )
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


def is_berry(text: str) -> bool:
    return "# yarn lockfile v1" not in text


def read_yarn(directory: str, text: str) -> Graph:
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


#: The `__metadata.version` of the Yarn 2+ locks that the reader is tested with.
BERRY_LOCK_VERSIONS = ("8", "10")

#: Yarn's own patches, which add Plug'n'Play support. pydd installs a
#: `node_modules` tree, so the package goes in as the registry gives it.
BUILTIN_PATCH = re.compile(r"^optional!builtin<[^>]+>$")


def berry_scalar(text: str) -> str:
    return json.loads(text) if text.startswith('"') else text


def parse_berry(text: str) -> dict[str, Document]:
    """A Yarn 2+ lock as a map from each of its descriptors to its block.

    The lock is YAML in the shape that Yarn writes: a block per key, maps
    nested by two spaces, scalars plain or in JSON quotes.
    """
    blocks: dict[str, Document] = {}
    stack: list[tuple[int, Document]] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if body.startswith('"'):
            key, end = json.JSONDecoder().raw_decode(body)
            rest = body[end:]
        else:
            key, sep, rest = body.partition(":")
            rest = sep + rest
        if not rest.startswith(":"):
            raise SystemExit(f"pydd.node.npm: yarn.lock has a line that pydd does not read: {line!r}")
        value = rest[1:].strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if indent == 0:
            block: Document = {}
            # One block answers several descriptors: `"a@npm:^1.0, a@npm:^1.2":`.
            for descriptor in key.split(", "):
                blocks[descriptor] = block
            stack = [(0, block)]
            continue
        if not stack:
            raise SystemExit(f"pydd.node.npm: yarn.lock has a line that pydd does not read: {line!r}")
        parent = stack[-1][1]
        if value:
            parent[key] = berry_scalar(value)
        else:
            parent[key] = {}
            stack.append((indent, parent[key]))
    return blocks


def berry_ident(locator: str) -> tuple[str, str]:
    """`@scope/name@npm:1.0.0` as its name and its reference, `npm:1.0.0`."""
    name, _, reference = locator[1:].partition("@")
    return locator[0] + name, reference


def berry_range(spec: str) -> str:
    """A range as Yarn writes it in the lock: one without a protocol is `npm:`."""
    return spec if re.match(r"^[a-zA-Z][\w+.-]*:", spec) else f"npm:{spec}"


@dataclass(frozen=True)
class Resolution:
    """One entry of `resolutions` in the project's `package.json`."""

    name: str
    range: str | None
    reference: str
    parent: str | None = None
    parent_range: str | None = None

    @classmethod
    def parse(cls, pattern: str, reference: str) -> Resolution:
        match = re.fullmatch(
            r"(?:(?P<parent>(?:@[^/@]+/)?[^/@]+)(?:@(?P<prange>[^/]+))?/)?(?P<name>(?:@[^/@]+/)?[^/@]+)(?:@(?P<range>.+))?",
            pattern,
        )
        if match is None:
            raise SystemExit(f"pydd.node.npm: package.json has a resolution that pydd does not read: {pattern}")
        return cls(match["name"], match["range"], reference, match["parent"], match["prange"])

    def rewrite(self, parent: tuple[str, str], name: str, spec: str) -> str | None:
        """The new range of `name@spec`, a dependency of the `parent` locator, or `None`.

        Yarn's `reduceDependency`: the names match, and each range that the
        pattern gives equals the parent's reference or the dependency's range.
        """
        if self.parent is not None:
            parent_name, parent_reference = parent
            if self.parent != parent_name:
                return None
            if self.parent_range is not None and berry_range(self.parent_range) != parent_reference:
                return None
        if self.name != name or (self.range is not None and berry_range(self.range) != spec):
            return None
        return berry_range(self.reference)


def berry_conditions(text: str) -> dict[str, list[str]]:
    """`(os=android | os=linux) & cpu=x64` as npm's fields, `{"os": ["android", "linux"], "cpu": ["x64"]}`.

    A conjunction of fields, each one value or a choice of values; npm's
    lists hold no more than that.
    """
    fields: dict[str, list[str]] = {}
    for part in text.split(" & "):
        choices = [choice.partition("=") for choice in part.removeprefix("(").removesuffix(")").split(" | ")]
        keys = {key for key, _, _ in choices}
        key = keys.pop()
        if keys or key not in ("os", "cpu", "libc") or key in fields or any(not sep for _, sep, _ in choices):
            raise SystemExit(f"pydd.node.npm: yarn.lock has the condition {text!r}, which pydd does not read.")
        fields[key] = [value for _, _, value in choices]
    return fields


def berry_patch(name: str, reference: str) -> tuple[str, str | None]:
    """The locator that a `patch:` reference patches, and its patch file relative to the project.

    `patch:zod@npm%3A4.3.6#~/.yarn/patches/zod.patch::version=4.3.6&hash=18f503`
    """
    body = reference.removeprefix("patch:").split("::", 1)[0]
    source, _, paths = body.partition("#")
    files = [path for path in paths.split("&") if not BUILTIN_PATCH.match(path)]
    if len(files) > 1 or any(not path.startswith("~/") for path in files):
        raise SystemExit(
            f"pydd.node.npm: yarn.lock patches {name} with {paths}. pydd applies one patch "
            "file from the project (`~/...`), and leaves out Yarn's own (`builtin<...>`)."
        )
    return unquote(source), files[0].removeprefix("~/") if files else None


def berry_enables_scripts(directory: str) -> bool:
    rc = Path(directory, ".yarnrc.yml")
    if not rc.is_file():
        return True
    return not re.search(r"^enableScripts:\s*false\s*$", rc.read_text(encoding="utf-8"), re.MULTILINE)


def read_berry(directory: str, text: str, integrity: dict[str, Document]) -> Graph:
    """A Yarn 2+ lock as a graph, with each package's npm hash from `integrity`.

    `integrity` maps each `name@npm:version` to `{"integrity": ...}`, and
    `"scripts": true` for a package with an install script. The lock has
    neither: its checksum is of Yarn's own zip archive.
    """
    blocks = parse_berry(text)
    metadata = blocks.pop("__metadata", {})
    if str(metadata.get("version")) not in BERRY_LOCK_VERSIONS:
        raise SystemExit(
            f"pydd.node.npm: yarn.lock has version {metadata.get('version')}. pydd reads "
            f"versions {' and '.join(BERRY_LOCK_VERSIONS)}, of Yarn 4."
        )
    root = manifest(directory, "")
    patterns: dict[str, str] = root.get("resolutions") or {}
    resolutions = [Resolution.parse(pattern, ref) for pattern, ref in patterns.items()]
    root_meta: dict[str, Document] = root.get("dependenciesMeta") or {}
    unbuilt = {spec for spec, meta in root_meta.items() if meta.get("built") is False}
    scripts = berry_enables_scripts(directory)

    members: dict[str, str] = {}
    for block in blocks.values():
        name, reference = berry_ident(block["resolution"])
        if reference.startswith("workspace:"):
            path = reference.removeprefix("workspace:")
            members[block["resolution"]] = "" if path == "." else path

    def edge(parent: str, name: str, spec: str) -> Ref:
        """The version key, or `link:<path>`, that `parent` reaches as `name@spec`."""
        spec = berry_range(spec)
        for resolution in resolutions:
            if (rewritten := resolution.rewrite(berry_ident(parent), name, spec)) is not None:
                spec = rewritten
                break
        block = blocks.get(f"{name}@{spec}")
        if block is None:
            raise SystemExit(f"pydd.node.npm: yarn.lock has no entry for {name}@{spec}, a dependency of {parent}.")
        target: str = block["resolution"]
        return f"link:{members[target]}" if target in members else target

    def source(locator: str) -> tuple[str, str, str | None]:
        """The registry version that a locator installs, and its patch file."""
        name, reference = berry_ident(locator)
        patch = None
        if reference.startswith("patch:"):
            locator, patch = berry_patch(name, reference)
            name, reference = berry_ident(locator)
        if not reference.startswith("npm:"):
            raise SystemExit(
                f"pydd.node.npm: yarn.lock resolves {locator}. pydd reads packages from a registry "
                "(`npm:`), workspace members and patches of those."
            )
        return name, reference.removeprefix("npm:"), patch

    # Each locator as the lock gives it: its dependencies are locators.
    base: dict[str, Version] = {}
    #: The peers of each locator, optional ones included: a dependent that has one provides it.
    peers: dict[str, list[str]] = {}
    missing: list[str] = []
    for block in {id(block): block for block in blocks.values()}.values():
        locator: str = block["resolution"]
        if locator in members:
            continue
        name, version, patch = source(locator)
        known = integrity.get(f"{name}@npm:{version}")
        if known is None:
            missing.append(f"{name}@npm:{version}")
            continue
        meta: dict[str, Document] = block.get("dependenciesMeta") or {}
        optional = {dep for dep, flags in meta.items() if flags.get("optional")}
        specs: dict[str, str] = block.get("dependencies") or {}
        deps = {dep: edge(locator, dep, spec) for dep, spec in specs.items()}
        bins: dict[str, str] | None = block.get("bin")
        platform = berry_conditions(block["conditions"]) if "conditions" in block else {}
        peers[locator] = sorted(block.get("peerDependencies") or {})
        base[locator] = Version(
            name=name,
            version=version,
            resolved=known.get("tarball", registry_url(name, version)),
            integrity=known["integrity"],
            dependencies={dep: ref for dep, ref in deps.items() if dep not in optional},
            optional={dep: ref for dep, ref in deps.items() if dep in optional},
            bin=bins,
            patch=patch,
            scripted=scripts
            and bool(known.get("scripts"))
            and name not in unbuilt
            and f"{name}@{version}" not in unbuilt,
            os=platform.get("os"),
            cpu=platform.get("cpu"),
            libc=platform.get("libc"),
        )
    if missing:
        raise SystemExit(
            f"pydd.node.npm: the integrity file has no hash for {len(missing)} packages of yarn.lock, "
            f"{', '.join(sorted(missing)[:5])} and more. Run `pydd-yarn-integrity` in the project."
        )

    versions: dict[str, Version] = {}

    def instance(locator: str, provided: dict[str, Ref]) -> Ref:
        """The version of `locator` with the peers that its dependent provides, as Yarn's virtual package.

        Two dependents that provide different peers get two versions, so
        placement nests the second where its peers resolve.
        """
        bound = {peer: provided[peer] for peer in peers[locator] if peer in provided}
        key = locator + ("(" + ",".join(f"{peer}={ref}" for peer, ref in sorted(bound.items())) + ")" if bound else "")
        if key in versions:
            return key
        version = base[locator]
        versions[key] = made = replace(version, dependencies={}, optional={}, peers=bound, base=locator)
        made.dependencies = provide(version.dependencies | version.optional, bound)
        made.optional = {dep: made.dependencies.pop(dep) for dep in version.optional}
        return key

    def provide(deps: dict[str, Ref], bound: dict[str, Ref]) -> dict[str, Ref]:
        """Each dependency's version, a sibling before the siblings that take it as a peer."""
        done: dict[str, Ref] = {}
        waiting = dict(deps)
        while waiting:
            ready = [
                dep
                for dep, ref in waiting.items()
                if ref.startswith("link:") or not any(peer in waiting for peer in peers[ref] if peer != dep)
            ]
            # Siblings that are each other's peers: Yarn too resolves one of them first.
            for dep in ready or [min(waiting)]:
                ref = waiting.pop(dep)
                done[dep] = ref if ref.startswith("link:") else instance(ref, bound | done)
        return done

    def relative(importer: str, ref: Ref) -> Ref:
        """An importer's link is relative to the importer, as `Graph.target` reads it."""
        if not ref.startswith("link:"):
            return ref
        return "link:" + posixpath.relpath(ref.removeprefix("link:") or ".", importer or ".")

    importers: dict[str, dict[str, dict[str, Ref]]] = {}
    for locator, path in sorted(members.items(), key=lambda item: item[1]):
        data = manifest(directory, path)
        locked: dict[str, dict[str, Ref]] = {}
        for kind in ("dependencies", "devDependencies", "optionalDependencies"):
            declared: dict[str, str] = data.get(kind) or {}
            locked[kind] = {name: edge(locator, name, spec) for name, spec in declared.items()}
        resolved = provide({name: ref for refs in locked.values() for name, ref in refs.items()}, {})
        importers[path] = {
            kind: {name: relative(path, resolved[name]) for name in refs} for kind, refs in locked.items()
        }
    return Graph(versions, importers, lambda _name, ref: ref)


def read(
    directory: str,
    lock: str,
    scripts: Iterable[str] = (),
    members: Iterable[str] | None = None,
    integrity: str | None = None,
) -> dict[str, Entry]:
    """The `packages` of an npm lock, from a lock of another tool in `directory`.

    `scripts` names more packages whose install scripts run. A graph lock
    places only `members`, their linked members and the project, when given.
    `integrity` is the file of npm hashes that a Yarn 2+ lock needs.
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
        case "yarn.lock" if is_berry(text):
            if integrity is None:
                raise SystemExit(
                    "pydd.node.npm: yarn.lock is a lock of Yarn 2 or newer. Its checksums are of Yarn's own "
                    "zip archives, so pydd takes npm's hashes from an integrity file: run "
                    "`pydd-yarn-integrity` in the project, and pass the file as `yarnIntegrity`."
                )
            graph = read_berry(directory, text, json.loads(Path(integrity).read_text(encoding="utf-8")))
            if members is not None:
                graph.only(members)
            packages = hoist(graph, directory, set(scripts))
        case "yarn.lock":
            graph = read_yarn(directory, text)
            if members is not None:
                graph.only(members)
            packages = hoist(graph, directory, set(scripts))
        case _:
            raise SystemExit(f"pydd.node.npm: pydd reads no lock named {lock}.")
    mark(packages)
    return packages
