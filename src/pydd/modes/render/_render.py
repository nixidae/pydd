"""The graph of a plan document, rendered in Python instead of by `graph.nix`.

**This is the second path, and `graph.nix` is the reference.** Each kind below
makes the derivation that the same kind of `graph.nix` makes, byte for byte, so
the planner submits the same root `.drv` either way. `tests/test_render.py`
holds every kind to `graph.nix`, and `test-projects` holds whole environments
to it.

A value carries its string context, as a Nix string does: the inputs of a node
are the context of its attributes, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...core._derivation import Derivation, Floating
from ...core._hash import downstream_placeholder, dynamic_placeholder
from ...python._plan import BUILT_PAIRS, output_name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

#: One element of string context: `("src", path)`, `("out", drv, output)`, or
#: `("dyn", drv, inner, output)` for output `output` of the derivation that
#: output `inner` of `drv` is.
type Element = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Text:
    """A string and its context."""

    text: str
    context: frozenset[Element] = frozenset()


def cat(*parts: Text | str) -> Text:
    """String interpolation: the texts joined, the contexts merged."""
    texts = [part if isinstance(part, Text) else Text(part) for part in parts]
    return Text("".join(t.text for t in texts), frozenset().union(*(t.context for t in texts)))


def join(separator: str, items: Iterable[Text]) -> Text:
    """`builtins.concatStringsSep`."""
    parts: list[Text | str] = []
    for index, item in enumerate(items):
        if index:
            parts.append(separator)
        parts.append(item)
    return cat(*parts)


def carried(data: dict[str, Any]) -> Text:
    """A string that carries its context, as `{ text, __context }`.

    `__context` is what `builtins.getContext` gives: an opaque path, or the
    outputs of a `.drv`.
    """
    elements: set[Element] = set()
    for path, kind in data["__context"].items():
        if kind.get("path"):
            elements.add(("src", path))
        for output in kind.get("outputs", []):
            elements.add(("out", path, output))
        if kind.get("allOutputs"):
            raise ValueError(f"{path}: a context of all outputs has no place in a node")
    return Text(data["text"], frozenset(elements))


def lift(data: Any, store_dir: str) -> Any:
    """`data` with the context that `pydd.modes.evaluate` gives the evaluator."""
    match data:
        case dict() if "__context" in data:
            return carried(data)
        case dict():
            lifted: dict[str, Any] = {}
            for name, item in data.items():
                drv = data.get(BUILT_PAIRS.get(name, ""))
                if isinstance(drv, str) and isinstance(item, str):
                    lifted[name] = Text(item, frozenset({("out", drv, output_name(data, name))}))
                elif name in BUILT_PAIRS.values():
                    lifted[name] = Text(item)
                else:
                    lifted[name] = lift(item, store_dir)
            return lifted
        case list():
            return [lift(item, store_dir) for item in data]
        case str() if data.startswith(store_dir + "/"):
            return Text(data, frozenset({("src", "/".join(data.split("/")[:4]))}))
        case str():
            return Text(data)
    return data


def output_of(value: Text, output: str) -> Text:
    """`builtins.outputOf value output`, for a value that names a built output."""
    built = [element for element in value.context if element[0] == "out"]
    if len(built) != 1:
        raise ValueError(f"outputOf needs one built output in the context of {value.text!r}")
    _, drv, inner = built[0]
    return Text(dynamic_placeholder(drv, output, inner), frozenset({("dyn", drv, inner, output)}))


class Graph:
    """The nodes of one plan document, written leaves first through `add`."""

    def __init__(self, document: dict[str, Any], add: Callable[[Derivation], str], store_dir: str) -> None:
        self.doc: dict[str, Any] = lift(document, store_dir)
        self.add = add
        # A tool is one output, or a list of them, such as `manylinux`.
        self.tool: dict[str, Any] = {
            name: [one["out"] for one in built] if isinstance(built, list) else built["out"]
            for name, built in self.doc["tools"].items()
        }
        self.script: dict[str, Text] = self.doc["scripts"]
        self.nodes: dict[str, dict[str, Any]] = {node["name"].text: node for node in self.doc["nodes"]}
        self.paths: dict[str, str] = {}
        self._built: dict[str, Text] = {}
        self.site = self.doc["interpreter"]["sitePackages"].text
        self.python = cat(self.tool["python"], "/bin/", self.doc["interpreter"]["executable"])
        self.base_path = join(
            ":", (cat(self.tool[name], "/bin") for name in ["coreutils", "unzip", "gnutar", "gzip", "python", "bash"])
        )

    def root(self) -> str:
        """Write every node that the root reads, then the root, and name its `.drv`."""
        name = self.doc["root"].text
        self.built(name)
        return self.paths[name]

    def built(self, name: str) -> Text:
        """`built.${name}` of `graph.nix`: what another node writes for this one."""
        if name not in self._built:
            value = self.build(self.nodes[name])
            if isinstance(value, Derivation):
                path = self.add(value)
                self.paths[name] = path
                value = Text(downstream_placeholder(path), frozenset({("out", path, "out")}))
            self._built[name] = value
        return self._built[name]

    def site_path(self, name: str) -> Text:
        return cat(self.built(name), "/", self.site)

    def installer_path(self) -> Text:
        return self.site_path(self.doc["installer"].text)

    def backend_path(self, node: dict[str, Any]) -> Text:
        return join(":", (self.site_path(name.text) for name in node["backendPath"]))

    @staticmethod
    def fetched(artifact: dict[str, Any]) -> Text:
        return artifact["path"] if "path" in artifact else artifact["out"]

    def venv_scripts(self) -> dict[str, Text]:
        """`venvScripts` of `graph.nix`."""
        return {name: self.script[name] for name in ("makeVenv", "runInstaller")}

    def runs(self, script: str) -> list[Text]:
        return [Text("-c"), cat('exec "', self.python, '" "', self.script[script], '"')]

    def patchelf_inputs(self) -> dict[str, Text]:
        tool = self.tool
        return {
            "PATH": cat(self.base_path, ":", tool["patchelf"], "/bin"),
            "autoPatchelf": cat(tool["autoPatchelf"], "/bin/auto-patchelf"),
            "NIX_BINTOOLS": cat(tool["bintools"]),
            "patchelfLibs": join(" ", (cat(library, "/lib") for library in tool["manylinux"])),
        }

    def build_tools_of(self, node: dict[str, Any]) -> dict[str, Text]:
        roots: list[Text] = [built["out"] for built in node.get("buildTools", [])]
        if not roots:
            return {}
        return {
            "PATH": cat(
                join(":", (cat(root, "/bin") for root in roots)),
                ":",
                self.base_path,
                ":",
                self.tool["patchelf"],
                "/bin",
            ),
            "CPPFLAGS": cat("-g0 ", join(" ", (cat("-isystem ", root, "/include") for root in roots))),
            "LDFLAGS": join(" ", (cat("-L", root, "/lib") for root in roots)),
            "PKG_CONFIG_PATH": join(":", (cat(root, "/lib/pkgconfig") for root in roots)),
        } | (
            {
                "fixupElf": self.script["fixupElf"],
                "fixupPaths": join(":", (cat(root, "/lib") for root in roots)),
                "patchelf": cat(self.tool["patchelf"], "/bin/patchelf"),
            }
            if node.get("overrides", {}).get("fixup", True)
            else {}
        )

    def overrides_of(self, node: dict[str, Any]) -> dict[str, Text]:
        override: dict[str, Any] = node.get("overrides", {})
        attrs: dict[str, Text] = {
            key: override[key] for key in ("configSettings", "preBuild", "postInstall") if key in override
        }
        if "patches" in override:
            attrs["patches"] = join(" ", (patch["out"] for patch in override["patches"]))
            attrs["patch"] = cat(self.tool["patch"], "/bin/patch")
        return attrs | override.get("env", {})

    def local(self, node: dict[str, Any], mode: str) -> dict[str, Any]:
        return {
            "name": node["name"],
            "args": self.runs("buildLocal"),
            "mode": Text(mode),
            "source": node["source"],
            "subdirectory": node.get("subdirectory", Text("")),
        }

    def build(self, node: dict[str, Any]) -> Derivation | Text:
        kind = node["kind"].text
        match kind:
            case "unpack":
                return self.mk(
                    {
                        "name": node["name"],
                        "args": [Text("-c"), cat(". ", self.script["unpackWheel"])],
                        "wheel": self.fetched(node["artifact"]),
                    }
                )
            case "wheel":
                return self.mk(
                    {
                        "name": node["name"],
                        "args": [Text("-c"), cat(". ", self.script["installWheel"])],
                        "wheel": self.fetched(node["artifact"]),
                        "wheelName": node["artifact"]["filename"],
                        "installerPath": self.installer_path(),
                    }
                    | (self.patchelf_inputs() if node["platform"] else {})
                )
            case "editable":
                return self.source(
                    node,
                    self.local(node, "editable")
                    | {
                        "editableRoot": node["root"],
                        "installerPath": self.installer_path(),
                        "backend": node["backend"],
                        "backendPath": self.backend_path(node),
                    },
                )
            case "local-wheel":
                return self.source(
                    node,
                    self.local(node, "wheel")
                    | {
                        "installerPath": self.installer_path(),
                        "backend": node["backend"],
                        "backendPath": self.backend_path(node),
                    },
                )
            case "local-sdist":
                return self.source(
                    node,
                    self.local(node, "sdist") | {"backend": node["backend"], "backendPath": self.backend_path(node)},
                )
            case "sdist":
                artifact = node["artifact"]
                if "plannerDrv" not in artifact:
                    raise ValueError(
                        f"{artifact['filename'].text} needs a planner, and the menu has none. "
                        "A chain of source distributions ran past `sdistPlannerDepth` of planner.nix."
                    )
                return output_of(artifact["plannerOut"], "out")
            case "planned":
                return output_of(node["artifact"]["plannerOut"], "out")
            case "sdist-build":
                built_from = "from" in node
                return self.source(
                    node,
                    {
                        "name": node["name"],
                        "args": [Text("-c"), cat(". ", self.script["buildSdist"])],
                        "sdist": cat(self.built(node["from"].text)) if built_from else self.fetched(node["artifact"]),
                        "subdirectory": Text("") if built_from else node["artifact"].get("subdirectory", Text("")),
                        "backend": node["backend"],
                        "installerPath": self.installer_path(),
                        "backendPath": self.backend_path(node),
                    },
                )
            case "nixpkgs" | "host":
                return self.fetched(node["artifact"])
            case "backend":
                return self.mk(
                    {
                        "name": node["name"],
                        "args": [Text("-c"), Text('echo "$backendPath" > "$out"')],
                        "backendPath": self.backend_path(node),
                    }
                )
            case "venv":
                return self.mk(
                    {
                        "name": node["name"],
                        "args": self.runs("makeVenv"),
                        "members": join(" ", (cat(self.built(name.text)) for name in node["members"])),
                        "installed": join("\n", node["installed"]),
                    }
                )
            case "application":
                return self.mk(
                    {
                        "name": node["name"],
                        "args": self.runs("makeApplication"),
                        "venv": cat(self.built(node["venv"].text)),
                        "bases": join(" ", (cat(self.built(name.text)) for name in node["bases"])),
                    }
                )
        raise ValueError(f"unknown node kind '{kind}'")

    def mk(self, args: dict[str, Any]) -> Derivation:
        """`mk` of `graph.nix`: the base, then `args`, then the floating output."""
        attrs: dict[str, Any] = {
            "system": self.doc["system"],
            "site": Text(self.site),
            "python": self.python,
            **self.venv_scripts(),
            "builder": cat(self.tool["bash"], "/bin/bash"),
            "PATH": self.base_path,
            "SOURCE_DATE_EPOCH": Text("315532800"),
            **args,
            "__contentAddressed": True,
            "outputHashMode": Text("recursive"),
            "outputHashAlgo": Text("sha256"),
        }
        return self.derivation(attrs)

    def source(self, node: dict[str, Any], args: dict[str, Any]) -> Derivation:
        """`mkSource` of `graph.nix`: on stdenv as data, when the document carries it."""
        stdenv: dict[str, Any] | None = self.doc.get("stdenv")
        if stdenv is None:
            return self.mk(args | self.build_tools_of(node) | self.overrides_of(node))
        roots: list[Text] = [built["out"] for built in node.get("buildTools", [])]
        attrs = (
            {key: value for key, value in args.items() if key != "args"}
            | self.overrides_of(node)
            | {
                "__contentAddressed": True,
                "outputHashMode": Text("recursive"),
                "outputHashAlgo": Text("sha256"),
                **self.venv_scripts(),
                "site": Text(self.site),
                "python": self.python,
                "nativeBuildInputs": [*roots, *stdenv["nativeBuildInputs"], self.tool["python"], self.tool["unzip"]],
                "buildInputs": roots,
                "dontUnpack": True,
                "dontConfigure": True,
                "dontInstall": True,
                "buildPhase": cat("(", args["args"][1], ")"),
            }
        )
        return self.derivation(stdenv | attrs)

    @staticmethod
    def coerce(value: Any) -> Text:
        """A value as `derivation` puts it in the environment."""
        match value:
            case Text():
                return value
            case bool():
                return Text("1" if value else "")
            case None:
                return Text("")
            case int():
                return Text(str(value))
            case list():
                return join(" ", (Graph.coerce(item) for item in value))
        raise TypeError(f"no environment value for {type(value).__name__}")

    def derivation(self, attrs: dict[str, Any]) -> Derivation:
        """`builtins.derivation` over `attrs`, which carry their context."""
        ignore_nulls = attrs.get("__ignoreNulls") is True
        argv: list[Text] = attrs.get("args", [])
        outputs = [output.text for output in attrs.get("outputs", [Text("out")])]
        if attrs.get("__contentAddressed") is not True or outputs != ["out"]:
            raise ValueError("a node of this graph has one floating output, 'out'")
        if attrs.get("__structuredAttrs") is True:
            raise ValueError("a node with structured attributes has no environment to render")
        # `derivation` consumes these. It keeps `__structuredAttrs = false`, as "".
        special = {"__ignoreNulls", "__contentAddressed", "args"}
        env = {
            key: self.coerce(value)
            for key, value in attrs.items()
            if key not in special and not (value is None and ignore_nulls)
        }
        srcs: set[str] = set()
        drvs: dict[str, set[str]] = {}
        dynamic: dict[str, dict[str, set[str]]] = {}
        for value in [*env.values(), *argv]:
            for element in value.context:
                match element:
                    case ("src", path):
                        srcs.add(path)
                    case ("out", drv, output):
                        drvs.setdefault(drv, set()).add(output)
                    case ("dyn", drv, inner, output):
                        dynamic.setdefault(drv, {}).setdefault(inner, set()).add(output)
        return Derivation(
            name=env["name"].text,
            system=env["system"].text,
            builder=env["builder"].text,
            args=[value.text for value in argv],
            env={key: value.text for key, value in env.items()},
            srcs=srcs,
            drvs=drvs,
            dynamic=dynamic,
            outputs={"out": Floating()},
        )


def render(document: dict[str, Any], add: Callable[[Derivation], str], store_dir: str) -> str:
    """Write the graph of `document` through `add`, and return the root `.drv`."""
    return Graph(document, add, store_dir).root()
