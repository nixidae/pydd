"""The plan document, and how a planner submits one.

**A plan document is the interface between the two halves of this example.**
A planner decides what the graph is and writes the document. `graph.nix` says
how a node of each kind is built, and it reads no lock file. So a reader of a
different lock format is a new producer of this document, and `graph.nix` does
not change when one arrives.

The document names one node as its `root`. The outer planner roots its
document at the virtual environment. The planner of a source distribution
roots its document at the build of that distribution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from packaging.utils import canonicalize_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packaging.requirements import Requirement

    from ._select import Artifact


def nix_string(text: str) -> str:
    """`text` as a Nix string literal.

    A Nix string takes the escapes of C for the backslash and the quote, and
    it also takes `${` as the start of an interpolation. The JSON below holds
    store paths and file names, so `${` cannot appear, but an escape that
    depends on the input is not an escape.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{escaped}"'


@dataclass(frozen=True, slots=True)
class LocalNode:
    """What a node that builds one tree of the user needs, whichever form.

    **The three forms differ in one hook, and in nothing else.** `build_editable`,
    `build_sdist` and `build_wheel` all run in the same directory, with the same
    backend, on the same path. So the three methods below take this value, and
    only `editable_node` takes anything more.

    `subdirectory` names the project inside `source`. `uv.lock` writes it for a
    git source and for a URL source that hold the project below their root.
    """

    name: str
    source: str
    backend: str
    backend_path: list[str]
    subdirectory: str = ""

    def entry(self, kind: str, suffix: str) -> dict[str, Any]:
        """The node, under the name that `kind` gives it.

        **The name carries a suffix, because the project may also name the
        environment.** `uv init --package` makes a project that is both a member
        of its own environment and the name of it, and two nodes may not share a
        name.
        """
        return {
            "name": f"{self.name}-{suffix}",
            "kind": kind,
            "platform": False,
            "source": self.source,
            "backend": self.backend,
            "backendPath": self.backend_path,
            "subdirectory": self.subdirectory,
        }


def build_tools_from(inputs: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """The store paths that each package needs to build, by PEP 503 name.

    `default.nix` takes the list at its `buildTools` argument, and it writes
    the store path of each entry. **The name is canonicalised here, and not in
    `graph.nix`.** A distribution name has more than one spelling, and
    `packaging` holds the rule that makes two spellings one name.
    """
    given: dict[str, list[dict[str, str]]] = inputs.get("buildTools", {})
    return {canonicalize_name(name): list(roots) for name, roots in given.items()}


#: The keys of a node override, and the type that each value takes.
OVERRIDE_KEYS = {
    "env": dict,
    "configSettings": dict,
    "patches": list,
    "preBuild": str,
    "postInstall": str,
    "fixup": bool,
}


def overrides_from(inputs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The node override of each package, by PEP 503 name, ready for a node.

    `lib/python/overrides.nix` gives the keys. **`configSettings` becomes one
    JSON string here**, so `graph.nix` and the renderer pass the same bytes and
    neither serialises a value.
    """
    given: dict[str, dict[str, Any]] = inputs.get("overrides", {})
    result: dict[str, dict[str, Any]] = {}
    for name, override in given.items():
        for key, value in override.items():
            expected = OVERRIDE_KEYS.get(key)
            if expected is None or not isinstance(value, expected):
                raise ValueError(f"the override of '{name}' gives {key} = {value!r}, and the keys are {OVERRIDE_KEYS}")
        for key, value in override.get("env", {}).items():
            if not isinstance(value, str):
                raise ValueError(f"the override of '{name}' gives env.{key} = {value!r}, and a variable is a string")
        node = dict(override)
        if "configSettings" in node:
            node["configSettings"] = json.dumps(node["configSettings"], sort_keys=True, separators=(",", ":"))
        result[canonicalize_name(name)] = node
    return result


#: The variable that fixes the version, for each VCS-versioning plugin.
PRETEND_VERSION = {
    "setuptools-scm": "SETUPTOOLS_SCM_PRETEND_VERSION",
    "hatch-vcs": "SETUPTOOLS_SCM_PRETEND_VERSION",
    "flit-scm": "SETUPTOOLS_SCM_PRETEND_VERSION",
    "uv-dynamic-versioning": "UV_DYNAMIC_VERSIONING_BYPASS",
    "poetry-dynamic-versioning": "POETRY_DYNAMIC_VERSIONING_BYPASS",
}


@dataclass
class Plan:
    """The graph that a planner decided, in the shape `graph.nix` takes."""

    # A subscripted alias is callable and gives an empty container, and it also
    # tells the type checker what that container holds. A bare `list` here
    # leaves the element type unknown.
    nodes: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    members: list[str] = field(default_factory=list[str])
    installed: list[str] = field(default_factory=list[str])
    #: The node of each member, by PEP 503 name. `application_node` reads
    #: this to find the one member that the caller asked for.
    by_package: dict[str, str] = field(default_factory=dict[str, str])
    #: The name of the node that every install step reads.
    installer: str = ""
    #: The node of each local project, by `<project>:<form>`, where the form is
    #: `editable`, `wheel`, `sdist` or `from-sdist`.
    local_nodes: dict[str, str] = field(default_factory=dict[str, str])
    #: The store paths that each package needs to build, by PEP 503 name.
    #: `build_tools_from` reads the value out of the inputs of the planner.
    build_tools: dict[str, list[dict[str, str]]] = field(default_factory=dict[str, list[dict[str, str]]])
    #: The node override of each package, by PEP 503 name, from `overrides_from`.
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    _seen: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])

    def compile_with(self, package: str, compiler: dict[str, str] | None) -> None:
        """Give `package` the default compiler, unless it has build tools of its own.

        The planner calls this for a source that holds native code
        (`has_native_sources`). A declaration, a hint or an override wins.
        """
        name = canonicalize_name(package)
        if compiler is not None and not self.build_tools.get(name):
            self.build_tools[name] = [compiler]

    def pretend_version(self, package: str, version: str, requirements: Sequence[Requirement]) -> None:
        """Give a tree that a VCS-versioning plugin builds the version, by its variable.

        **A tree in the store has no `.git`**, and setuptools-scm, hatch-vcs,
        uv-dynamic-versioning and poetry-dynamic-versioning read the version
        from there. Each takes a fixed one from a variable instead. The lock
        records the version uv computed, and a dynamic one is "0.0.0". An
        `env` override of the package wins.
        """
        names = {canonicalize_name(requirement.name) for requirement in requirements}
        variables = {variable for plugin, variable in PRETEND_VERSION.items() if plugin in names}
        if not variables:
            return
        override = self.overrides.setdefault(canonicalize_name(package), {})
        env: dict[str, str] = override.setdefault("env", {})
        for variable in sorted(variables):
            env.setdefault(variable, version)

    def tools_for(self, package: str) -> dict[str, Any]:
        """The `buildTools` and `overrides` keys of the node that builds `package`.

        **A package with no entry gets no key at all.** A node is the name of a
        store path, so an empty key would change the output path of every node
        of every graph that this repository has already built.
        """
        name = canonicalize_name(package)
        roots = self.build_tools.get(name)
        override = self.overrides.get(name)
        return ({"buildTools": roots} if roots else {}) | ({"overrides": override} if override else {})

    def add(self, entry: dict[str, Any]) -> str:
        """Add one node, and return its name.

        **A repeated node adds nothing, and a repeated *name* is an error.**
        The same wheel answers more than one requirement, and the second call
        must reuse the first node rather than make a second one. Two different
        nodes under one name are a different thing: `graph.nix` keys a node by
        its name, so the second would replace the first and the graph would
        build something that no planner asked for.
        """
        name: str = entry["name"]
        existing = self._seen.get(name)
        if existing is not None:
            if existing != entry:
                raise ValueError(
                    f"two different nodes are both called '{name}': a node of kind "
                    f"'{existing['kind']}' and a node of kind '{entry['kind']}'. "
                    "A node name is the name of a store path, so it has to be unique."
                )
            return name
        self._seen[name] = entry
        self.nodes.append(entry)
        return name

    def artifact_node(
        self,
        artifact: Artifact,
        kind: str,
        *,
        backend: str = "",
        backend_path: list[str] | None = None,
    ) -> str:
        """Add the node that builds one artefact of the lock file.

        `kind` is what the node *does*, and it is not always the kind of the
        artefact. The installer is a wheel, and its node unpacks it rather
        than installs it, because nothing can install a wheel before that node
        exists.

        **That bootstrap node carries a name of its own.** The same installer
        is a member of the environment when a group or an extra asks for it,
        and the node that installs it is a different node with a different
        output. One name for both makes `add` refuse the graph, so the
        bootstrap takes the `-bootstrap` suffix.

        **A host function replaces the node** (`builders` of `planner.nix`).
        The node of kind `host` names the derivation that it made, and builds
        nothing here. The bootstrap installer keeps its own node.

        A node of kind `sdist-build` runs one PEP 517 hook, so it takes the
        backend and the path that the backend lives on. Both belong in this
        call, and not in an assignment to the node afterwards: `add` returns
        the existing name when a node repeats, and a later assignment would
        then reach the wrong node.
        """
        if artifact.host_drv and kind != "unpack":
            return self.add(
                {
                    "name": f"{artifact.node_name}-host",
                    "kind": "host",
                    "platform": False,
                    "artifact": {
                        "package": artifact.package,
                        "version": artifact.version,
                        "drv": artifact.host_drv,
                        "out": artifact.host_out,
                    },
                }
            )
        entry: dict[str, Any] = {
            "name": f"{artifact.node_name}-bootstrap" if kind == "unpack" else artifact.node_name,
            "kind": kind,
            "artifact": artifact.as_plan(),
            # `graph.nix` gives a node the loader and the libraries of this
            # system only when the tag of the wheel says that the node needs
            # them. The tag is what `packaging` already parsed.
            "platform": artifact.is_platform_wheel,
        }
        if kind == "sdist-build":
            if not backend:
                raise ValueError(f"{artifact.filename}: a node of kind '{kind}' needs a backend")
            entry["backend"] = backend
            entry["backendPath"] = backend_path or []
            # **Only a node that runs a build gets a tool.** A wheel is
            # already built, so the node that installs one needs no compiler
            # and takes no dependency on one.
            entry |= self.tools_for(artifact.package)
        return self.add(entry)

    def editable_node(self, node: LocalNode, *, root: str) -> str:
        """Add the node that builds one local project as an editable.

        `root` is what the PEP 660 redirect names, and it is the one thing that
        the other two forms do not have. Both of those hold the code, so
        nothing redirects to a tree of the user.
        """
        return self.add(node.entry("editable", "editable") | {"root": root} | self.tools_for(node.name))

    def local_wheel_node(self, node: LocalNode) -> str:
        """Add the node that builds a wheel out of a tree, and installs it.

        **This is what `pip install .` does, and what a git source needs.** A
        git checkout is a directory, so no archive stands between the tree and
        the wheel: one PEP 517 `build_wheel` call answers it. A `directory`
        source of `uv.lock` takes the same route, because `uv` installs one
        that way and not as an editable.
        """
        return self.add(node.entry("local-wheel", "from-tree") | self.tools_for(node.name))

    def local_sdist_node(self, node: LocalNode) -> str:
        """Add the node that builds the source distribution of a local project.

        **This node is what `uv build` writes.** It runs the PEP 517
        `build_sdist` hook over the tree of the project, and its output is the
        archive that an index would serve. It installs nothing;
        `local_build_node` reads the archive and builds the wheel.
        """
        return self.add(node.entry("local-sdist", "sdist") | self.tools_for(node.name))

    def local_build_node(self, *, name: str, archive: str, backend: str, backend_path: list[str]) -> str:
        """Add the node that builds a wheel out of a local source distribution.

        **The kind is the same one that an artefact of the index takes.** A
        distribution that a node of this graph built and a distribution that
        the index served are the same thing to the build that reads it, so the
        same node kind and the same script serve both. `archive` names the node
        that holds the archive, and an artefact of the index carries a fetch
        instead.
        """
        return self.add(
            {
                "name": f"{name}-from-sdist",
                "kind": "sdist-build",
                "platform": False,
                "from": archive,
                "backend": backend,
                "backendPath": backend_path,
            }
            | self.tools_for(name)
        )

    def nixpkgs_node(self, *, package: str, version: str, drv: str, out: str) -> str:
        """Add the node that takes one package from nixpkgs, already built.

        **This node builds nothing.** `default.nix` instantiated the package
        outside the sandbox and handed in the `.drv` and the output path with
        the dependency between them discarded, which is the tier 4 mechanism
        that `docs/PACKAGING-FACTS.md` describes. `graph.nix` puts the
        dependency back with `builtins.appendContext`, and the outer store then
        **substitutes** the output from a cache rather than building it.

        The name carries a suffix, because the package may also be the name of
        the environment, and two nodes may not share a name.
        """
        return self.add(
            {
                "name": f"{package}-{version}-nixpkgs",
                "kind": "nixpkgs",
                # The store path is whatever nixpkgs built, and this graph does
                # not patch it. nixpkgs already linked it against this system.
                "platform": False,
                "artifact": {"package": package, "version": version, "drv": drv, "out": out},
            }
        )

    @staticmethod
    def planned_entry(name: str, planner: dict[str, str]) -> dict[str, Any]:
        """A node that another planner writes, as the root of its graph.

        `graph.nix` reads a node of kind `planned` through
        `builtins.outputOf`. The node keeps the name of the node it stands
        for, so every node that names it still does.
        """
        return {
            "name": name,
            "kind": "planned",
            "platform": False,
            "artifact": {"plannerDrv": planner["plannerDrv"], "plannerOut": planner["plannerOut"]},
        }

    def replace(self, name: str, entry: dict[str, Any]) -> None:
        """Put `entry` in the place of the node called `name`."""
        if entry["name"] != name:
            raise ValueError(f"a node that replaces '{name}' has to carry that name, not '{entry['name']}'")
        index = next(index for index, node in enumerate(self.nodes) if node["name"] == name)
        self.nodes[index] = entry
        self._seen[name] = entry

    def backend_node(self, name: str, *, backend_path: Sequence[str]) -> str:
        """Add the node that realises a backend environment, and names its sites.

        **This is the root of the first of two planners of one build.** The
        second asks the backend's `get_requires_for_build_*`, and only a built
        backend can answer, so it takes this output as an input: its text names
        each site, and so its closure holds them (nixidae/nixidae#847).
        """
        return self.add({"name": name, "kind": "backend", "platform": False, "backendPath": list(backend_path)})

    def venv_node(self, name: str) -> str:
        """Add the node that composes the members into an environment."""
        return self.add(
            {
                "name": name,
                "kind": "venv",
                "platform": False,
                "members": list(self.members),
                "installed": list(self.installed),
            }
        )

    def application_node(self, name: str, *, venv: str, bases: Sequence[str]) -> str:
        """Add the node that links the programs of one member out of `venv`.

        `bases` names the node that installed each member, and those nodes are
        the template of the shape. `scripts/make-application.py` gives the two
        rules that decide which paths of a template reach the output.
        """
        return self.add(
            {
                "name": name,
                "kind": "application",
                "platform": False,
                "venv": venv,
                "bases": list(bases),
            }
        )

    def root(self, name: str, *, application: str | Sequence[str] | None = None) -> str:
        """The node that the planner submits, which is one of two shapes.

        **An environment holds every program of every member, and an
        application holds the programs of the members that the caller names.**
        `uv.nix` gives the second shape through its `application` argument, and
        `scripts/make-application.py` gives the whole reason to want it: a
        virtual environment carries `bin/python`, so a NixOS configuration that
        installs one shadows the interpreter of the system.

        `application` takes one name or a list of them. **A list is one output
        over one environment**, and not one output for each name: a project
        that ships three programs ships them together, and three separate
        applications would each hold a link into the same environment.

        The environment is a node either way. The application links out of it,
        so both nodes are in the graph and only the root differs. The
        environment then takes the `-env` suffix, because the application is
        what the caller builds and it carries the name of the project.
        """
        if application is None:
            return self.venv_node(name)

        wanted = [application] if isinstance(application, str) else list(application)
        if not wanted:
            raise ValueError(
                "application is an empty list. Name at least one member, or pass null for the environment itself."
            )

        bases = [self.by_package.get(canonicalize_name(package)) for package in wanted]
        missing = [package for package, base in zip(wanted, bases, strict=True) if base is None]
        if missing:
            held = ", ".join(sorted(self.by_package)) or "no member at all"
            names = ", ".join(f"'{package}'" for package in missing)
            raise ValueError(
                f"application names {names}, and the environment holds {held}. "
                "An application is one member of the environment, so the selection has to install it."
            )

        # `dict.fromkeys` keeps the order that the caller wrote and drops a
        # repeat. Two names of one member are one template.
        found = list(dict.fromkeys(base for base in bases if base is not None))
        return self.application_node(name, venv=self.venv_node(f"{name}-env"), bases=found)

    def member(self, name: str, label: str, *, package: str) -> None:
        """Mark a node as a member of the environment, and not only a build input.

        `label` is the line that the manifest of the environment carries.
        `package` is the distribution name, which `application_node` needs to
        find the node of one member again.
        """
        self.members.append(name)
        self.installed.append(label)
        self.by_package[canonicalize_name(package)] = name

    def document(self, *, root: str, system: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """The whole document, ready for `graph.nix`."""
        return {
            "system": system,
            "interpreter": inputs["interpreter"],
            "tools": inputs["tools"],
            "scripts": inputs["scripts"],
            "nodes": self.nodes,
            "installer": self.installer,
            "root": root,
        } | ({"stdenv": inputs["stdenvTemplate"]} if "stdenvTemplate" in inputs else {})


#: A key whose value is the output path of the derivation that the paired key
#: names, as the menu hands both over with their dependency discarded.
BUILT_PAIRS = {"out": "drv", "plannerOut": "plannerDrv"}


def output_name(entry: dict[str, Any], key: str) -> str:
    """Which output of its derivation the value at `key` is.

    A tool may be an output other than `out`, such as the `lib` of a
    compiler, so its entry names the output.
    """
    return entry.get("output", "out") if key == "out" else "out"
