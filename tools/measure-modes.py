"""Measure each mode of pydd over the test projects, for docs/modes.md.

For each mode and project this times the planning of the environment: every
planner it takes, nested ones included, in the private store of its mode.
Every node is built already, by `test-projects`, so the time is the
planners' alone. A nonce in every planner (`PYDD_MEASURE`, read by
`mkPlanner`) makes Nix run each again: `--rebuild` refuses a
content-addressed planner.

It also times `measure-floor`, a derivation that does nothing: every planner
time holds that much.

Writes docs/_generated/measurements.json and docs/_generated/measurements.md.
Run it from the repository, after `nix run --file . test-projects`. Names
of projects measure only those, and keep the rest of the existing file.
`--render` only writes the table again, from the JSON.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import TypedDict

MODES = {
    "cli": ("NIX_MASTER", "store", "builder-rpc-v0"),
    "huggorm": ("NIX_MASTER", "store", "builder-rpc-v0"),
    "drv": ("NIX_MASTER", "store", "builder-rpc-v0"),
    "recursive": ("NIX_RELEASED", f"store-{os.environ.get('NIX_RELEASED_VERSION', '')}", "recursive-nix"),
    "recursive-eval": ("NIX_RELEASED", f"store-{os.environ.get('NIX_RELEASED_VERSION', '')}", "recursive-nix"),
    "recursive-nixpkgs": ("NIX_RELEASED", f"store-{os.environ.get('NIX_RELEASED_VERSION', '')}", "recursive-nix"),
    "out": ("NIX_RELEASED", f"store-{os.environ.get('NIX_RELEASED_VERSION', '')}", ""),
    "stdenv": ("NIX_MASTER", "store", "builder-rpc-v0"),
    "out-stdenv": ("NIX_RELEASED", f"store-{os.environ.get('NIX_RELEASED_VERSION', '')}", ""),
}
#: The files that make each mode, beside the core and the ecosystem it shares.
MODE_FILES = {
    "cli": ["src/pydd/modes/render"],
    "huggorm": ["src/pydd/modes/evaluate", "lib/modes/evaluate.nix"],
    "drv": ["src/pydd/modes/render", "tools/pydd-drv/main.cc"],
    "recursive": ["src/pydd/modes/render"],
    "recursive-eval": ["src/pydd/modes/nix_cli", "lib/modes/nix-eval.nix"],
    "recursive-nixpkgs": ["src/pydd/modes/nix_cli", "lib/modes/nix-eval.nix"],
    "out": ["src/pydd/modes/out", "lib/modes/out.nix", "src/pydd/modes/render"],
    "stdenv": ["src/pydd/modes/render", "lib/python/stdenv-template.nix", "lib/python/carry.nix"],
    "out-stdenv": [
        "src/pydd/modes/out",
        "lib/modes/out.nix",
        "src/pydd/modes/render",
        "lib/python/stdenv-template.nix",
        "lib/python/carry.nix",
    ],
}
RUNS = 3
#: What `run` sets: a private store cannot create a cgroup.
NIX_ENV = {
    "NIX_CONFIG": "extra-experimental-features = nix-command ca-derivations dynamic-derivations\nuse-cgroups = false",
}
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "pydd"
OUT = Path("docs/_generated")


class ModeCost(TypedDict):
    closureBytes: int
    lines: int
    floorSeconds: float


class PlannerTime(TypedDict):
    seconds: float
    packages: int | None


class Measurements(TypedDict):
    """`measurements.json`."""

    revision: str
    date: str
    machine: str
    runs: int
    modes: dict[str, ModeCost]
    #: By project, then by mode.
    planners: dict[str, dict[str, PlannerTime]]


def nix(*args: str, binary: str = "nix", env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [binary, *args], capture_output=True, text=True, check=False, env={**os.environ, **(env or {})}
    )
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"{binary} {' '.join(args)} failed")
    return result.stdout.strip()


def lines(paths: list[str]) -> int:
    total = 0
    for path in paths:
        files = [Path(path)] if Path(path).is_file() else sorted(Path(path).rglob("*.py"))
        total += sum(len(file.read_text().splitlines()) for file in files)
    return total


def closure_bytes(mode: str) -> int:
    path = nix("build", "--file", ".", f"projects.{mode}.pure-app.closure", "--no-link", "--print-out-paths")
    info = json.loads(nix("path-info", "--json", "--closure-size", path))
    # A list before Nix 2.19, and an object keyed by path since.
    size: int = (info[0] if isinstance(info, list) else next(iter(info.values())))["closureSize"]
    return size


def floor_seconds(mode: str) -> float:
    binary_var, store_name, _ = MODES[mode]
    store = str(CACHE / store_name)
    times: list[float] = []
    for _ in range(RUNS):
        drv = nix(
            "eval",
            "--store",
            store,
            "--impure",
            "--raw",
            "--file",
            ".",
            "measure-floor.drvPath",
            binary=os.environ[binary_var],
            env={"PYDD_MEASURE": uuid.uuid4().hex},
        )
        start = time.monotonic()
        nix("build", "--store", store, f"{drv}^out", "--no-link", binary=os.environ[binary_var], env=NIX_ENV)
        times.append(time.monotonic() - start)
    return median(times)


def planner_seconds(mode: str, project: str) -> tuple[float, int | None]:
    binary_var, store_name, features = MODES[mode]
    store = str(CACHE / store_name)
    env = NIX_ENV
    times: list[float] = []
    packages: int | None = None
    for _ in range(RUNS):
        # Into the private store: the planner's `.drv` must be valid there.
        drv = nix(
            "eval",
            "--store",
            store,
            "--impure",
            "--raw",
            "--file",
            ".",
            f"measure.{mode}.{project}.drvPath",
            binary=os.environ[binary_var],
            env=env | {"PYDD_MEASURE": uuid.uuid4().hex},
        )
        start = time.monotonic()
        result = subprocess.run(
            [
                os.environ[binary_var],
                "build",
                "--store",
                store,
                # The environment, so every planner of it runs, and no node.
                f"{drv}^out^out",
                "--system-features",
                features,
                "--no-link",
                "-L",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **env},
        )
        times.append(time.monotonic() - start)
        # A planner that did not run prints no plan, and its time means nothing.
        if result.returncode or "plan:" not in result.stderr:
            sys.stderr.write(result.stderr[-4000:])
            raise SystemExit(f"the planner of {project} ({mode}) did not run")
        found = re.search(r"resolves (\d+) packages", result.stderr)
        packages = int(found.group(1)) if found else None
    return median(times), packages


def main() -> int:
    if sys.argv[1:] == ["--render"]:
        measured: Measurements = json.loads((OUT / "measurements.json").read_text())
        (OUT / "measurements.md").write_text(render(measured))
        return 0
    every = nix("eval", "--raw", "--file", ".", "projects.cli", "--apply", "p: toString (builtins.attrNames p)").split()
    projects = sys.argv[1:] or every
    previous = OUT / "measurements.json"
    kept: dict[str, dict[str, PlannerTime]] = (
        json.loads(previous.read_text())["planners"] if sys.argv[1:] and previous.exists() else {}
    )
    released = set(
        nix("eval", "--raw", "--file", ".", "projects.out", "--apply", "p: toString (builtins.attrNames p)").split()
    )
    data = Measurements(
        revision=nix("log", "-r", "@-", "--no-graph", "-T", "commit_id.short(8)", binary="jj"),
        date=datetime.now(UTC).strftime("%Y-%m-%d"),
        machine=f"{platform.machine()}, {os.cpu_count()} cores",
        runs=RUNS,
        modes={
            mode: ModeCost(
                closureBytes=closure_bytes(mode),
                lines=lines(MODE_FILES[mode]),
                floorSeconds=round(floor_seconds(mode), 2),
            )
            for mode in MODES
        },
        planners={},
    )
    planners: dict[str, dict[str, PlannerTime]] = {
        project: by_mode for project, by_mode in kept.items() if project in every and project not in projects
    }
    for project in projects:
        planners[project] = {}
        for mode in MODES:
            if mode.startswith("out") and project not in released:
                continue
            seconds, packages = planner_seconds(mode, project)
            planners[project][mode] = PlannerTime(seconds=round(seconds, 2), packages=packages)
            print(f"{project:24} {mode:8} {seconds:6.2f} s  {packages} packages", file=sys.stderr)
    data["planners"] = {project: planners[project] for project in every if project in planners}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "measurements.json").write_text(json.dumps(data, indent=2) + "\n")
    (OUT / "measurements.md").write_text(render(data))
    return 0


def render(data: Measurements) -> str:
    modes = data["modes"]
    planners = data["planners"]
    names = list(MODES)
    out = [
        f"Measured at `{data['revision']}` on {data['date']}, {data['machine']}; the median of {data['runs']} runs.",
        "",
        "| | " + " | ".join(names) + " |",
        "| --- |" + " ---: |" * len(names),
        "| planner closure (MiB) | "
        + " | ".join(f"{modes[mode]['closureBytes'] / 2**20:.1f}" for mode in names)
        + " |",
        "| lines of the mode | " + " | ".join(str(modes[mode]["lines"]) for mode in names) + " |",
        "| a build that does nothing (s) | "
        + " | ".join(f"{modes[mode]['floorSeconds']:.2f}" for mode in names)
        + " |",
        "",
        "Planner time, in seconds:",
        "",
        "| project | packages | " + " | ".join(names) + " |",
        "| --- | ---: |" + " ---: |" * len(names),
    ]
    for project, by_mode in planners.items():
        cells = [f"{by_mode[mode]['seconds']:.2f}" if mode in by_mode else "-" for mode in names]
        packages = by_mode["cli"]["packages"]
        out.append(f"| {project} | {'-' if packages is None else packages} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
