"""Build one unit of a Cargo project: a node that `pydd.rust` wrote.

`PYDD_UNIT` holds the unit as JSON. Its paths into other nodes are
placeholders, which Nix has replaced with real paths by now.

A compile writes `$out/lib/<file>` (or `$out/bin/<name>`) and
`$out/link.json`, the search paths of the unit and of what it links, as
`[flag, value]` pairs: cargo passes `rustc-link-search` on to every
dependent, and `rustc-link-lib` and the link arguments to the package
alone. A run of a
build script writes `OUT_DIR` under `$out/target`, laid out as cargo lays
it out, and what the script printed, by kind, to `$out/flags.json`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

#: `[flag, value]`, such as `["-L", path]` or `["-l", name]`. A list, as
#: JSON gives it back.
type LinkPair = list[str]
#: `[scope, binary, argument]` of a `cargo:rustc-link-arg*`.
type LinkArgument = list[str]


class Extern(TypedDict):
    name: str
    path: str


class Unit(TypedDict, total=False):
    """`PYDD_UNIT`, as `pydd.rust` writes it. Each action has its own keys."""

    action: Literal["compile", "run", "doctest", "check", "join"]
    src: str
    srcs: list[str]
    cwd: str
    program: str
    flags: list[str]
    externs: list[Extern]
    search: list[str]
    link_from: list[str]
    build_script: str | None
    env: dict[str, str]
    output: Literal["lib", "bin", "build-script"]
    file: str
    kinds: list[str]
    testing: bool
    has_lib: bool
    target: str
    links: str | None
    metadata_from: list[str]
    writable: bool
    bins: list[str]
    logs: dict[str, str]


class Printed(TypedDict):
    """What a build script printed, by kind: its node's `flags.json`."""

    cfg: list[str]
    env: dict[str, str]
    search: list[LinkPair]
    libs: list[LinkPair]
    args: list[LinkArgument]
    metadata: dict[str, str]
    out_dir: NotRequired[str]


# A test imports this file with neither set.
unit: Unit = json.loads(os.environ.get("PYDD_UNIT", "{}"))
out = os.environ.get("out", "")  # noqa: SIM112 - Nix names it `out`


def read_link(node: str) -> list[LinkPair]:
    try:
        with open(f"{node}/link.json", encoding="utf-8") as handle:
            pairs: list[LinkPair] = json.load(handle)
    except FileNotFoundError:
        return []
    return pairs


def read_printed(node: str) -> Printed:
    with open(f"{node}/flags.json", encoding="utf-8") as handle:
        printed: Printed = json.load(handle)
    return printed


def write_json(path: str, value: object) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, sort_keys=True)


def unique[T](items: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(items))


#: Target kinds that are a library, which takes the package's `-l`.
LIBRARIES = {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}


def link_argument_applies(scope: str, name: str) -> bool:
    """Whether a `rustc-link-arg*` of the package reaches this unit."""
    kinds = set(unit.get("kinds", []))
    testing = unit.get("testing", False)
    match scope:
        case "all":
            return testing or bool(kinds & {"bin", "example", "bench", "test", "cdylib"})
        case "bins":
            return "bin" in kinds and not testing
        case "bin":
            return "bin" in kinds and not testing and name == unit["file"]
        case "tests":
            return testing or "test" in kinds
        case "examples":
            return "example" in kinds
        case "benches":
            return "bench" in kinds
        case "cdylib":
            return "cdylib" in kinds
    return False


def prepare() -> tuple[list[str], dict[str, str], list[LinkPair], list[LinkPair]]:
    """The flags, environment and link pairs of a unit, with its build script's output.

    `search` goes on to every unit that links this one; `link` is the
    unit's own.
    """
    flags = list(unit["flags"])
    env = dict(os.environ) | unit["env"]
    search: list[LinkPair] = []
    for dependency in unit["link_from"]:
        search += read_link(dependency)
    link: list[LinkPair] = []
    if script := unit["build_script"]:
        printed = read_printed(script)
        # Cargo's OUT_DIR stays writable while the crate compiles, and
        # tauri's `generate_context!` writes compressed assets into it.
        out_dir = os.path.join(os.environ.get("NIX_BUILD_TOP", "/tmp"), "out")
        subprocess.run(["cp", "-r", printed.get("out_dir", f"{script}/out"), out_dir], check=True)
        subprocess.run(["chmod", "-R", "u+w", out_dir], check=True)
        env["OUT_DIR"] = out_dir
        env |= printed["env"]
        for cfg in printed["cfg"]:
            flags += ["--cfg", cfg]
        search = printed["search"] + search
        kinds = set(unit.get("kinds", []))
        if (kinds & LIBRARIES and not unit.get("testing")) or not unit.get("has_lib"):
            link += printed["libs"]
        arguments = [arg for scope, name, arg in printed["args"] if link_argument_applies(scope, name)]
        link += [["-C", f"link-arg={arg}"] for arg in arguments]
    # Pairs, so that `-l ssl -l crypto` keeps both `-l`.
    search = [list(pair) for pair in unique(tuple(pair) for pair in search)]
    link = search + [list(pair) for pair in unique(tuple(pair) for pair in link)]
    for path in unit["search"]:
        flags.append(f"-Ldependency={path}")
    for extern in unit["externs"]:
        flags += ["--extern", f"{extern['name']}={extern['path']}"]
    return flags, env, link, search


def compile_unit() -> None:
    flags, env, link, search = prepare()
    match unit["output"]:
        case "lib":
            directory = f"{out}/lib"
            target = ["--emit=link", "--out-dir", directory]
        case "bin":
            directory = f"{out}/bin"
            target = ["--emit=link", "-o", f"{directory}/{unit['file']}"]
        case "build-script":
            directory = f"{out}/bin"
            target = ["--emit=link", "-o", f"{directory}/build-script-build"]
    os.makedirs(directory)
    command = ["rustc", unit["src"], *flags, *target, *(arg for pair in link for arg in pair)]
    print(" ".join(command), file=sys.stderr)
    subprocess.run(command, env=env, check=True)
    write_json(f"{out}/link.json", search)


def logged(command: list[str], env: Mapping[str, str], cwd: str | None = None) -> None:
    """Run a test, and keep what it printed as the output."""
    os.makedirs(out)
    print(" ".join(command), file=sys.stderr)
    result = subprocess.run(command, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sys.stderr.write(result.stdout)
    with open(f"{out}/log", "w", encoding="utf-8") as log:
        log.write(result.stdout)
    if result.returncode != 0:
        sys.exit(result.returncode)


def doctest_unit() -> None:
    flags, env, _, search = prepare()
    # rustdoc takes search paths, and a library records the libraries it
    # links in its own metadata.
    paths = [arg for pair in search for arg in pair]
    command = ["rustdoc", "--test", unit["src"], *flags, *paths, "--test-run-directory", unit["cwd"]]
    logged(command, env)


def check_unit() -> None:
    logged([unit["program"]], dict(os.environ) | unit["env"], cwd=unit["cwd"])


def rustc_flags(value: str) -> list[LinkPair]:
    """`cargo:rustc-flags`, which takes `-l` and `-L`, with or without a space."""
    pairs: list[LinkPair] = []
    words = value.split()
    while words:
        word = words.pop(0)
        if word in ("-l", "-L"):
            pairs.append([word, words.pop(0)])
        elif word[:2] in ("-l", "-L"):
            pairs.append([word[:2], word[2:]])
        else:
            sys.exit(f"cargo:rustc-flags takes only -l and -L, not {word!r}")
    return pairs


def parse_cfg(text: str) -> dict[str, str]:
    """`rustc --print cfg` as cargo gives it to a build script: CARGO_CFG_<KEY>, values joined by commas."""
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        # Cargo leaves this out: the profile decides it, and `PROFILE` says so.
        if key == "debug_assertions":
            continue
        values.setdefault(key, [])
        if value:
            values[key].append(value.strip('"'))
    return {f"CARGO_CFG_{key.upper()}": ",".join(found) for key, found in values.items()}


def run_unit() -> None:
    # Cargo's `target/<profile>/build/<package>-<hash>/out`. tauri-build
    # finds its target directory as the parent of the ancestor named
    # `build`, and other scripts go up three levels; both land on
    # `$out/target/<profile>`.
    out_dir = f"{out}/target/{unit['env']['PROFILE']}/build/{unit['env']['CARGO_PKG_NAME']}/out"
    os.makedirs(out_dir)
    env = dict(os.environ) | unit["env"] | {"OUT_DIR": out_dir}
    cfg = subprocess.run(
        ["rustc", "--print", "cfg", "--target", unit["target"]], stdout=subprocess.PIPE, text=True, check=True
    )
    env = parse_cfg(cfg.stdout) | env
    for dependency in unit["metadata_from"]:
        env |= read_printed(dependency)["metadata"]

    cwd = unit["cwd"]
    root = copy = ""
    if unit.get("writable"):
        # Cargo leaves a workspace member's source writable, and tauri-build
        # writes into it. The copy's path goes back to the store path in what
        # the script prints, so a printed source path still resolves.
        root = "/".join(cwd.split("/")[:4])
        copy = os.path.join(os.environ.get("NIX_BUILD_TOP", "/tmp"), "source")
        subprocess.run(["cp", "-r", root, copy], check=True)
        subprocess.run(["chmod", "-R", "u+w", copy], check=True)
        cwd = copy + cwd.removeprefix(root)
        for name in ("CARGO_MANIFEST_DIR", "CARGO_MANIFEST_PATH"):
            env[name] = copy + env[name].removeprefix(root)
    # Not `check=True`: a failing script often says why on stdout only, as
    # tauri-build does, and the log must show it before the unit fails.
    result = subprocess.run([unit["program"]], cwd=cwd, env=env, stdout=subprocess.PIPE, text=True)
    stdout = result.stdout.replace(copy, root) if copy else result.stdout
    sys.stderr.write(stdout)
    if result.returncode != 0:
        sys.exit(f"pydd: the build script of {unit['cwd']} failed with exit status {result.returncode}.")
    printed = Printed(cfg=[], env={}, search=[], libs=[], args=[], metadata={}, out_dir=out_dir)
    links = (unit["links"] or "").upper().replace("-", "_")
    for line in stdout.splitlines():
        if not line.startswith("cargo:"):
            continue
        key, _, value = line.removeprefix("cargo:").removeprefix(":").partition("=")
        match key:
            case "rustc-cfg":
                printed["cfg"].append(value)
            case "rustc-env":
                name, _, setting = value.partition("=")
                printed["env"][name] = setting
            case "rustc-link-lib":
                printed["libs"].append(["-l", value])
            case "rustc-link-search":
                printed["search"].append(["-L", value])
            case "rustc-flags":
                for flag, setting in rustc_flags(value):
                    printed["search" if flag == "-L" else "libs"].append([flag, setting])
            case "rustc-link-arg":
                printed["args"].append(["all", "", value])
            case "rustc-link-arg-bin":
                name, _, argument = value.partition("=")
                printed["args"].append(["bin", name, argument])
            case "rustc-link-arg-bins" | "rustc-link-arg-tests" | "rustc-link-arg-examples" | "rustc-link-arg-benches":
                printed["args"].append([key.removeprefix("rustc-link-arg-"), "", value])
            case "rustc-link-arg-cdylib" | "rustc-cdylib-link-arg":
                printed["args"].append(["cdylib", "", value])
            case "error":
                sys.exit(f"build script error: {value}")
            case "metadata" if links:
                name, _, setting = value.partition("=")
                printed["metadata"][f"DEP_{links}_{name.upper()}"] = setting
            case "warning" | "rerun-if-changed" | "rerun-if-env-changed" | "rustc-check-cfg":
                pass
            case _ if links and line.startswith("cargo:") and not line.startswith("cargo::"):
                printed["metadata"][f"DEP_{links}_{key.upper()}"] = value
    write_json(f"{out}/flags.json", printed)


if __name__ == "__main__":
    match unit["action"]:
        case "compile":
            compile_unit()
        case "run":
            run_unit()
        case "doctest":
            doctest_unit()
        case "check":
            check_unit()
        case "join":
            os.makedirs(f"{out}/bin")
            for directory in unit["bins"]:
                for name in sorted(os.listdir(directory)):
                    os.symlink(f"{directory}/{name}", f"{out}/bin/{name}")
            for name, path in unit.get("logs", {}).items():
                os.symlink(f"{path}/log", f"{out}/{name}.log")
