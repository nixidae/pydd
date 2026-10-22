"""Plan a Cargo project: one node for each unit of cargo's unit graph.

A unit is what cargo would give one `rustc` call: a library, a proc-macro, a
binary, the compile of a build script, or the run of one. Cargo batches
nothing, so neither does this planner, and a change to one crate rebuilds
that crate and what depends on it.

The output of a build script is known only when it runs, so a node reads the
output of its build script's node at build time (`scripts/unit.py`).
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

from pydd.core import Derivation, Node, Planner, tool_env

RUN = "run-custom-build"


def store_root(path: str) -> str:
    """The store path that holds `path`."""
    return "/".join(path.split("/")[:4])


@dataclass(frozen=True)
class Menu:
    """What `lib/rust/default.nix` gives the planner."""

    name: str
    project: str
    vendor: str
    cargo: str
    rustc: str
    cc: str
    coreutils: str
    python: str
    script: str
    target: str
    #: Tools and environment of a crate, by crate name (`lib/rust/hints.nix`).
    native: dict[str, Any]
    #: The project's directory inside `project`, which may hold path
    #: dependencies outside it.
    subdir: str
    #: Cargo's own flags for what to build: `--profile`, `--package`, features.
    arguments: list[str]
    #: The platform that build scripts and proc-macros run on, and its linker.
    host: str
    host_cc: str
    #: The prefix of the target's tools, as `aarch64-unknown-linux-gnu-`, and
    #: the target's `ar`.
    prefix: str
    bintools: str
    #: `build` for the project's binaries, `test` for a graph that runs its tests.
    command: str = "build"

    @property
    def cross(self) -> bool:
        return self.cc != self.host_cc

    @property
    def manifest(self) -> str:
        return posixpath.normpath(posixpath.join(self.project, self.subdir, "Cargo.toml"))

    def toolchain(self) -> list[str]:
        return [self.rustc, self.cc, self.coreutils, *((self.host_cc, self.bintools) if self.cross else ())]

    def tools(self) -> str:
        return ":".join(f"{tool}/bin" for tool in self.toolchain())

    def inside(self, path: str) -> str:
        """`path` with the vendor directory resolved to the crate's own store path."""
        if not path.startswith(self.vendor + "/"):
            return path
        entry, _, rest = path.removeprefix(self.vendor + "/").partition("/")
        crate = os.path.realpath(posixpath.join(self.vendor, entry))
        return posixpath.join(crate, rest) if rest else crate


def cargo(menu: Menu, home: str, *args: str) -> Any:
    # nixpkgs writes the source replacement of every registry and git
    # source into the vendor directory, with the directory relative.
    with open(posixpath.join(menu.vendor, ".cargo", "config.toml"), encoding="utf-8") as source:
        replacement = source.read().replace('directory = "cargo-vendor-dir"', f'directory = "{menu.vendor}"')
    with open(posixpath.join(home, "config.toml"), "w", encoding="utf-8") as config:
        config.write(replacement)
    env = {
        **os.environ,
        "CARGO_HOME": home,
        "CARGO_TARGET_DIR": posixpath.join(home, "target"),
        # `--unit-graph` is unstable, and this is how a stable cargo takes it.
        "RUSTC_BOOTSTRAP": "1",
        "PATH": f"{menu.cargo}/bin:{menu.tools()}",
    }
    result = subprocess.run(
        [f"{menu.cargo}/bin/cargo", *args, "--offline", "--locked", "--manifest-path", menu.manifest],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def version_env(version: str) -> dict[str, str]:
    core, _, pre = version.partition("+")[0].partition("-")
    major, minor, patch = core.split(".")
    return {
        "CARGO_PKG_VERSION": version,
        "CARGO_PKG_VERSION_MAJOR": major,
        "CARGO_PKG_VERSION_MINOR": minor,
        "CARGO_PKG_VERSION_PATCH": patch,
        "CARGO_PKG_VERSION_PRE": pre,
    }


def package_env(package: dict[str, Any], manifest_dir: str) -> dict[str, str]:
    """What cargo sets for every unit of `package`."""
    return {
        "CARGO_PKG_NAME": package["name"],
        "CARGO_PKG_AUTHORS": ":".join(package.get("authors") or []),
        "CARGO_PKG_DESCRIPTION": package.get("description") or "",
        "CARGO_PKG_HOMEPAGE": package.get("homepage") or "",
        "CARGO_PKG_REPOSITORY": package.get("repository") or "",
        "CARGO_PKG_LICENSE": package.get("license") or "",
        "CARGO_PKG_LICENSE_FILE": package.get("license_file") or "",
        "CARGO_PKG_README": package.get("readme") or "",
        "CARGO_PKG_RUST_VERSION": package.get("rust_version") or "",
        "CARGO_MANIFEST_DIR": manifest_dir,
        "CARGO_MANIFEST_PATH": f"{manifest_dir}/Cargo.toml",
        **version_env(package["version"]),
    }


#: Crate types that rustc links into a final artefact, where LTO runs.
LINKED = {"bin", "cdylib", "staticlib", "dylib"}


def proc_macro_suffix(host: str) -> str:
    """The file a proc-macro is: a shared library of the host it runs on."""
    return ".dylib" if "-apple-" in host else ".so"


def lto_flags(unit: dict[str, Any]) -> list[str]:
    """What cargo passes for the profile's `lto`, by the role of the unit.

    The artefact that links runs LTO, and every library it links keeps its
    bitcode for it. Cargo runs no LTO for a build script or a proc-macro.
    """
    lto = unit["profile"]["lto"]
    kinds = set(unit["target"]["kind"])
    if lto == "false" or kinds & {"custom-build", "proc-macro"}:
        return ["-C", "embed-bitcode=no"]
    if lto == "off":
        return ["-C", "embed-bitcode=no", "-C", "lto=off"]
    if set(unit["target"]["crate_types"]) & LINKED or unit["mode"] == "test":
        return ["-C", "lto" if lto in ("true", "fat") else f"lto={lto}"]
    return []


def profile_flags(unit: dict[str, Any]) -> list[str]:
    profile = unit["profile"]
    flags = lto_flags(unit)
    if profile["opt_level"] != "0":
        flags += ["-C", f"opt-level={profile['opt_level']}"]
    if profile["debuginfo"]:
        flags += ["-C", f"debuginfo={profile['debuginfo']}"]
    flags += ["-C", f"debug-assertions={'on' if profile['debug_assertions'] else 'off'}"]
    flags += ["-C", f"overflow-checks={'on' if profile['overflow_checks'] else 'off'}"]
    if profile.get("codegen_units"):
        flags += ["-C", f"codegen-units={profile['codegen_units']}"]
    # A build script and a proc-macro run inside cargo's own process model,
    # which unwinds.
    kinds = set(unit["target"]["kind"])
    if profile["panic"] == "abort" and not kinds & {"custom-build", "proc-macro"}:
        flags += ["-C", "panic=abort"]
    strip = (profile.get("strip") or {}).get("resolved", {}).get("Named")
    if strip and strip != "none":
        flags += ["-C", f"strip={strip}"]
    return flags


class Graph:
    """The units of one unit graph, made into nodes leaves first."""

    def __init__(self, planner: Planner, menu: Menu, graph: Any, metadata: Any) -> None:
        self.planner = planner
        self.menu = menu
        self.units: list[dict[str, Any]] = graph["units"]
        self.roots: list[int] = graph["roots"]
        self.packages = {package["id"]: package for package in metadata["packages"]}
        self.members = set(metadata["workspace_members"])
        self.nodes: dict[int, Node] = {}
        self.hashes: dict[int, str] = {}

    def is_build_script(self, index: int) -> bool:
        return "custom-build" in self.units[index]["target"]["kind"]

    def is_binary(self, index: int) -> bool:
        """A binary that is built, as an integration test runs it, and never linked."""
        unit = self.units[index]
        return "bin" in unit["target"]["kind"] and unit["mode"] == "build"

    def linked(self, index: int) -> list[int]:
        """Every unit whose output `index` links: its dependencies, without build scripts, transitively."""
        seen: dict[int, None] = {}
        stack = [dep["index"] for dep in self.units[index]["dependencies"]]
        while stack:
            dep = stack.pop()
            if dep in seen or self.is_build_script(dep) or self.is_binary(dep):
                continue
            seen[dep] = None
            stack += [d["index"] for d in self.units[dep]["dependencies"]]
        return sorted(seen)

    def metadata(self, index: int) -> str:
        """`-C metadata` of a unit, from what cargo decided and nothing of this host."""
        if index not in self.hashes:
            unit = self.units[index]
            facts = {
                "pkg_id": unit["pkg_id"],
                "target": [unit["target"]["name"], unit["target"]["kind"]],
                "mode": unit["mode"],
                "platform": unit["platform"],
                "features": unit["features"],
                "profile": unit["profile"],
                "dependencies": [self.metadata(dep["index"]) for dep in unit["dependencies"]],
            }
            digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
            self.hashes[index] = digest[:16]
        return self.hashes[index]

    def name(self, index: int) -> str:
        unit = self.units[index]
        package = self.packages[unit["pkg_id"]]
        base = f"{package['name']}-{package['version']}"
        if self.is_build_script(index):
            return f"{base}-build-script" + ("-run" if unit["mode"] == RUN else "")
        if unit["mode"] == "test":
            return f"{base}-test-{unit['target']['name']}"
        if unit["mode"] == "doctest":
            return f"{base}-doctest"
        if "bin" in unit["target"]["kind"]:
            return unit["target"]["name"]
        return base

    def node(self, index: int) -> Node:
        if index not in self.nodes:
            for dep in self.units[index]["dependencies"]:
                self.node(dep["index"])
            unit = self.units[index]
            match unit["mode"]:
                case "run-custom-build":
                    spec = self.run_spec(index)
                case "doctest":
                    spec = self.doctest_spec(index)
                case _:
                    spec = self.compile_spec(index)
            crate = self.packages[unit["pkg_id"]]["name"]
            native = self.menu.native.get(crate, {"tools": [], "env": {}})
            self.nodes[index] = self.planner.add(self.derivation(self.name(index), spec, self.inputs(index), native))
        return self.nodes[index]

    def inputs(self, index: int) -> list[int]:
        unit = self.units[index]
        if unit["mode"] == RUN:
            return [dep["index"] for dep in unit["dependencies"]]
        direct = [dep["index"] for dep in unit["dependencies"]]
        return sorted(set(direct) | set(self.linked(index)))

    def derivation(
        self,
        name: str,
        spec: dict[str, Any],
        inputs: list[int],
        native: dict[str, Any] | None = None,
        extra: list[Node] | None = None,
    ) -> Derivation:
        menu = self.menu
        tools: list[str] = (native or {}).get("tools", [])
        # A build script finds a library through pkg-config, or compiles C
        # with the `cc` crate, which reads `CFLAGS`. libz-sys probes for zlib
        # with `-lz`, and builds its own copy when the probe fails.
        env = tool_env(tools, menu.tools()) | {"PYDD_UNIT": json.dumps(spec, sort_keys=True)}
        if tools and menu.cross:
            # The `pkg-config` crate refuses to cross-compile unless told, and
            # nixpkgs names the target's pkg-config with the target's prefix.
            env["PKG_CONFIG_ALLOW_CROSS"] = "1"
            for tool in tools:
                if os.path.exists(f"{tool}/bin/{menu.prefix}pkg-config"):
                    env["TARGET_PKG_CONFIG"] = f"{tool}/bin/{menu.prefix}pkg-config"
        env |= (native or {}).get("env", {})
        return Derivation(
            name=name,
            system=self.planner.system,
            builder=f"{menu.python}/bin/python3",
            args=["-I", menu.script],
            env=env,
            srcs=sorted({menu.python, menu.script, *menu.toolchain(), *tools, *spec.get("srcs", [])}),
            drvs={node.path: ["out"] for node in [*(self.nodes[dep] for dep in inputs), *(extra or [])]},
        )

    def manifest_dir(self, unit: dict[str, Any]) -> str:
        package = self.packages[unit["pkg_id"]]
        return self.menu.inside(posixpath.dirname(package["manifest_path"]))

    def build_script_run(self, index: int) -> int | None:
        for dep in self.units[index]["dependencies"]:
            if self.units[dep["index"]]["mode"] == RUN:
                return dep["index"]
        return None

    def compile_spec(self, index: int) -> dict[str, Any]:
        unit = self.units[index]
        target = unit["target"]
        package = self.packages[unit["pkg_id"]]
        manifest_dir = self.manifest_dir(unit)
        crate_name = target["name"].replace("-", "_")
        build_script = self.is_build_script(index)
        # A library may name several, as Tauri's `staticlib`, `cdylib` and
        # `rlib`. rustc writes one artefact for each, and a dependent's
        # `--extern` reads the rlib.
        crate_types = target["crate_types"]
        crate_type = crate_types[0]
        testing = unit["mode"] == "test"
        metadata = self.metadata(index)
        flags = [
            "--crate-name",
            crate_name,
            f"--edition={target['edition']}",
            *(["--test"] if testing else [arg for kind in crate_types for arg in ("--crate-type", kind)]),
            *profile_flags(unit),
            *(arg for feature in unit["features"] for arg in ("--cfg", f'feature="{feature}"')),
            "-C",
            f"metadata={metadata}",
        ]
        # A library's file name carries the metadata, as `--extern` names it.
        # A binary and a build script are written with `-o`, which ignores it.
        if crate_type != "bin" and not testing:
            flags += ["-C", f"extra-filename=-{metadata}"]
        # Nix rewrites a CA output's own path after the build. On darwin that
        # breaks the signature ld64 gives the dylib, and macOS kills the rustc
        # that loads it. An `@rpath` install name holds no such path.
        if crate_type == "proc-macro" and "-apple-" in self.menu.host:
            flags += ["-C", f"link-arg=-Wl,-install_name,@rpath/lib{crate_name}-{metadata}.dylib"]
        if unit["platform"] is not None:
            flags += ["--target", unit["platform"]]
            if self.menu.cross:
                flags += ["-C", f"linker={self.menu.cc}/bin/{self.menu.prefix}cc"]
        if unit["pkg_id"] not in self.members:
            flags += ["--cap-lints", "warn"]
        if crate_type == "proc-macro":
            flags += ["-C", "prefer-dynamic", "--extern", "proc_macro"]

        externs = self.externs(index)
        run = self.build_script_run(index)
        env = package_env(package, manifest_dir) | {"CARGO_CRATE_NAME": crate_name} | self.binaries(index)
        if "bin" in target["kind"]:
            env["CARGO_BIN_NAME"] = target["name"]
        if unit["pkg_id"] in self.members:
            env["CARGO_PRIMARY_PACKAGE"] = "1"
        return {
            "action": "compile",
            "src": self.menu.inside(target["src_path"]),
            "srcs": [store_root(manifest_dir)],
            "flags": flags,
            "externs": externs,
            "search": [self.nodes[dep].output() + "/lib" for dep in self.linked(index)],
            # A linked unit's link flags already hold those of its own dependencies.
            "link_from": [
                self.nodes[dep["index"]].output()
                for dep in unit["dependencies"]
                if not self.is_build_script(dep["index"]) and self.units[dep["index"]]["mode"] != RUN
            ],
            "build_script": self.nodes[run].output() if run is not None else None,
            "env": env,
            "output": "build-script" if build_script else ("bin" if crate_type == "bin" or testing else "lib"),
            "file": target["name"],
            # Which of the package's `rustc-link-lib` and link arguments apply.
            "kinds": target["kind"],
            "testing": testing,
            "has_lib": any(
                set(other["kind"]) & {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}
                for other in package["targets"]
            ),
        }

    def externs(self, index: int) -> list[dict[str, str]]:
        externs = []
        for dep in self.units[index]["dependencies"]:
            dep_unit = self.units[dep["index"]]
            if self.is_build_script(dep["index"]) or self.is_binary(dep["index"]):
                continue
            dep_crate = dep_unit["target"]["name"].replace("-", "_")
            proc_macro = dep_unit["target"]["crate_types"][0] == "proc-macro"
            suffix = proc_macro_suffix(self.menu.host) if proc_macro else ".rlib"
            path = f"{self.nodes[dep['index']].output()}/lib/lib{dep_crate}-{self.metadata(dep['index'])}{suffix}"
            externs.append({"name": dep["extern_crate_name"], "path": path})
        return externs

    def binaries(self, index: int) -> dict[str, str]:
        """`CARGO_BIN_EXE_<name>` of each binary that an integration test runs."""
        return {
            f"CARGO_BIN_EXE_{self.units[dep['index']]['target']['name']}": (
                f"{self.nodes[dep['index']].output()}/bin/{self.units[dep['index']]['target']['name']}"
            )
            for dep in self.units[index]["dependencies"]
            if self.is_binary(dep["index"])
        }

    def doctest_spec(self, index: int) -> dict[str, Any]:
        """The doctests of a library: rustdoc compiles each one, and runs it, in this node."""
        unit = self.units[index]
        target = unit["target"]
        manifest_dir = self.manifest_dir(unit)
        flags = [
            "--crate-name",
            target["name"].replace("-", "_"),
            f"--edition={target['edition']}",
            *(arg for feature in unit["features"] for arg in ("--cfg", f'feature="{feature}"')),
        ]
        if unit["platform"] is not None:
            flags += ["--target", unit["platform"]]
        run = self.build_script_run(index)
        return {
            "action": "doctest",
            "src": self.menu.inside(target["src_path"]),
            "srcs": [store_root(manifest_dir)],
            "cwd": manifest_dir,
            "flags": flags,
            "externs": self.externs(index),
            "search": [self.nodes[dep].output() + "/lib" for dep in self.linked(index)],
            "link_from": [self.nodes[dep].output() for dep in self.linked(index)],
            "build_script": self.nodes[run].output() if run is not None else None,
            "env": package_env(self.packages[unit["pkg_id"]], manifest_dir),
        }

    def check(self, index: int) -> Node:
        """A node that runs the test binary of `index`."""
        unit = self.units[index]
        manifest_dir = self.manifest_dir(unit)
        spec = {
            "action": "check",
            "program": f"{self.nodes[index].output()}/bin/{unit['target']['name']}",
            "cwd": manifest_dir,
            "srcs": [store_root(manifest_dir)],
            "env": package_env(self.packages[unit["pkg_id"]], manifest_dir) | self.binaries(index),
        }
        inputs = [index, *(dep["index"] for dep in unit["dependencies"] if self.is_binary(dep["index"]))]
        return self.planner.add(self.derivation(f"{self.name(index)}-run", spec, inputs))

    def cross_env(self) -> dict[str, str]:
        """The compilers of both platforms, as the `cc` crate reads them."""
        menu = self.menu
        return {
            "TARGET_CC": f"{menu.cc}/bin/{menu.prefix}cc",
            "TARGET_CXX": f"{menu.cc}/bin/{menu.prefix}c++",
            "TARGET_AR": f"{menu.bintools}/bin/{menu.prefix}ar",
            "HOST_CC": f"{menu.host_cc}/bin/cc",
            "HOST_CXX": f"{menu.host_cc}/bin/c++",
        }

    def run_spec(self, index: int) -> dict[str, Any]:
        unit = self.units[index]
        package = self.packages[unit["pkg_id"]]
        manifest_dir = self.manifest_dir(unit)
        program = next(dep["index"] for dep in unit["dependencies"] if self.units[dep["index"]]["mode"] != RUN)
        profile = unit["profile"]
        # A unit with no platform builds for the host: a build script of a
        # proc-macro's dependency gets the host as its TARGET.
        target = unit["platform"] or self.menu.host
        env = package_env(package, manifest_dir) | {
            "TARGET": target,
            "HOST": self.menu.host,
            # Cargo says `debug` for a profile that inherits `dev`, and the unit
            # graph does not say what a profile inherits.
            "PROFILE": "debug" if profile["name"] == "dev" or profile["opt_level"] == "0" else "release",
            "OPT_LEVEL": profile["opt_level"],
            "DEBUG": "true" if profile["debuginfo"] else "false",
            "NUM_JOBS": "1",
            "CARGO": f"{self.menu.cargo}/bin/cargo",
            "RUSTC": f"{self.menu.rustc}/bin/rustc",
            "RUSTDOC": f"{self.menu.rustc}/bin/rustdoc",
            "CARGO_ENCODED_RUSTFLAGS": "",
            **(self.cross_env() if self.menu.cross else {}),
            "CARGO_CFG_FEATURE": ",".join(unit["features"]),
            **{f"CARGO_FEATURE_{feature.upper().replace('-', '_')}": "1" for feature in unit["features"]},
        }
        # ring's build script checks it against its own version.
        if package.get("links"):
            env["CARGO_MANIFEST_LINKS"] = package["links"]
        # Only a member's script writes into its source, so only a member
        # pays for the copy.
        writable = {"writable": True} if unit["pkg_id"] in self.members else {}
        return {
            "action": "run",
            "program": f"{self.nodes[program].output()}/bin/build-script-build",
            "cwd": manifest_dir,
            "srcs": [store_root(manifest_dir), self.menu.cargo],
            "target": target,
            "links": package.get("links"),
            "metadata_from": [
                self.nodes[dep["index"]].output()
                for dep in unit["dependencies"]
                if self.units[dep["index"]]["mode"] == RUN
            ],
            "env": env,
            **writable,
        }

    def tests(self) -> Node:
        """A root that holds the log of every test of the graph: it builds when they pass."""
        logs: dict[str, str] = {}
        inputs: list[Node] = []
        for index in self.roots:
            node = self.node(index)
            if self.units[index]["mode"] == "test":
                node = self.check(index)
            elif self.units[index]["mode"] != "doctest":
                continue
            logs[node.drv.name] = node.output()
            inputs.append(node)
        spec = {"action": "join", "bins": [], "logs": logs}
        return self.planner.add(self.derivation(f"{self.menu.name}-tests", spec, [], extra=inputs))

    def root(self) -> Node:
        # A workspace's roots hold its libraries too, which install nothing.
        roots = [index for index in self.roots if "bin" in self.units[index]["target"]["crate_types"]]
        for index in self.roots:
            self.node(index)
        spec = {"action": "join", "bins": [self.nodes[index].output() + "/bin" for index in roots]}
        return self.planner.add(self.derivation(self.menu.name, spec, list(self.roots)))


def main() -> int:
    planner = Planner.from_environ()
    menu = Menu(**planner.menu)
    with tempfile.TemporaryDirectory() as home:
        args = [*menu.arguments, "--target", menu.target]
        command = ["test", "--no-run"] if menu.command == "test" else ["build"]
        graph = cargo(menu, home, *command, *args, "--unit-graph", "-Z", "unstable-options")
        metadata = cargo(menu, home, "metadata", "--format-version", "1", "--filter-platform", menu.target)
    built = Graph(planner, menu, graph, metadata)
    planner.submit(built.tests() if menu.command == "test" else built.root())
    return 0
