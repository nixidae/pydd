"""Build pydd's examples and test sets in a private store, with a given Nix.

The daemon of this machine may be neither Nix master nor a release, so each
lane builds in a chroot store of its own, with the Nix that it needs. The
store keeps /nix/store as its store directory, so the cache substitutes into
it.

    runner run --lane master examples.hello.cli [check] [nix build flags]
    runner test projects [name ...]
    runner test live [name ...]

`test` builds every project of a set in each mode of a group, and fails when
two modes of a group give different environments, or different roots. Each
group is one way to build a source node. stdenv strips and patches what it
builds, so the stdenv modes are a group of their own.

Projects run in parallel, `--jobs` at a time. The modes of one project run in
order, so `huggorm` and `drv` find the nodes that `cli` built instead of
building them beside it. Every step has a timeout, so a stuck build fails
the run instead of holding it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import AsyncIterator, Iterable
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from subprocess import PIPE, CalledProcessError
from typing import IO, Any, Final

import anyio
from anyio.abc import Process

# Every attribute a caller names is relative to this set of default.nix.
ROOT: Final = "internal"


@dataclass(frozen=True)
class Lane:
    """A Nix, the store it builds in, and the system features it offers.

    `url` names the store to the client when a pynixd serves `store`.
    """

    nix: str
    store: Path
    features: str
    url: str | None = None

    @property
    def store_arg(self) -> str:
        return self.url or str(self.store)

    async def nix_run(self, args: list[str], timeout: float, stderr: IO[Any] | None = None, check: bool = True) -> str:
        env = {**os.environ, "NIX_CONFIG": nix_config()}
        with anyio.fail_after(timeout):
            result = await anyio.run_process([self.nix, *args], stdout=PIPE, stderr=stderr, check=check, env=env)
        return result.stdout.decode() if result.returncode == 0 else ""

    async def build(
        self,
        attribute: str,
        targets: list[str],
        extra: list[str],
        timeouts: Timeouts,
        stderr: IO[Any] | None = None,
    ) -> list[str]:
        """Build each target of `attribute`, from one evaluation, and give the paths in order."""
        attribute = f"{ROOT}.{attribute}"
        # What the planner runs on may be built only on the host, such as a
        # huggorm lane, so copy it in rather than build it again. **The host's
        # daemon is the slow path** (nixidae/nixidae#419: 9 s against 2 s for
        # one evaluation), so the chroot store answers first.
        store = ["--store", self.store_arg]
        closure = await self.nix_run(
            ["eval", *store, "--file", ".", f"{attribute}.closure", "--raw"], timeouts.evaluate, stderr
        )
        present = await self.nix_run(["path-info", *store, closure], timeouts.evaluate, stderr, check=False)
        if not present:
            closure = await self.nix_run(
                ["build", "--file", ".", f"{attribute}.closure", "--no-link", "--print-out-paths"],
                timeouts.build,
                stderr,
            )
            await self.nix_run(
                ["copy", "--to", self.store_arg, "--no-check-sigs", closure.strip()], timeouts.build, stderr
            )
        # `nix build` prints the paths in the order of its installables, and
        # each target has one output.
        out = await self.nix_run(
            [
                "build",
                *store,
                "--file",
                ".",
                *(f"{attribute}.{target}" for target in targets),
                "--system-features",
                self.features,
                "--no-link",
                "--print-out-paths",
                "-L",
                *extra,
            ],
            timeouts.build,
            stderr,
        )
        paths = out.split()
        if len(paths) != len(targets):
            raise RuntimeError(f"{len(targets)} targets of {attribute} gave {len(paths)} paths: {paths}")
        return paths


def lanes(pynixd: bool = False) -> dict[str, Lane]:
    """The lanes. With `pynixd`, each store has a name of its own, so a run through pynixd keeps the plain stores."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "pydd"
    store = os.environ.get("PYDD_STORE")
    suffix = "-pynixd" if pynixd else ""
    master, released = os.environ["PYDD_NIX_MASTER"], os.environ["PYDD_NIX_RELEASED"]
    master_store = Path(store + suffix) if store else cache / f"store{suffix}"
    released_store = (
        Path(store + suffix) if store else cache / f"store-{os.environ['PYDD_NIX_RELEASED_VERSION']}{suffix}"
    )
    return {
        "master": Lane(master, master_store, "builder-rpc-v0"),
        "released": Lane(released, released_store, ""),
        "recursive": Lane(released, released_store, "recursive-nix"),
    }


def nix_config() -> str:
    """What the client reads, and what each pynixd's store reads from its own configuration."""
    return (
        "extra-experimental-features = nix-command ca-derivations dynamic-derivations"
        " recursive-nix parse-toml-timestamps\nuse-cgroups = false"
    )


#: The answers pynixd gives itself in a store it may write: pynixd's `TEST_OP_ROUTES`, and its builder.
PYNIXD_ROUTES: Final = dict.fromkeys(
    (
        "AddToStore",
        "AddToStoreNar",
        "AddMultipleToStore",
        "AddIndirectRoot",
        "AddPermRoot",
        "AddSignatures",
        "RegisterDrvOutput",
        "AddBuildLog",
        "OptimiseStore",
        "VerifyStore",
        "CollectGarbage",
        "EnsurePath",
        "BuildDerivation",
    ),
    "pynixd",
)


def pynixd_config(lanes: Iterable[Lane], socket: Path) -> dict[str, Any]:
    """A pynixd that serves one store on `socket`, with the union of the lanes' features.

    **pynixd writes this store itself** (`pynixd_writes`). A deployment keeps
    writes on the daemon; this store is the run's own and goes at the next run.
    """
    lanes = list(lanes)
    nix = {lane.nix for lane in lanes}
    if len(nix) != 1:
        raise RuntimeError(f"one store, one Nix: {sorted(nix)}")
    features = sorted({feature for lane in lanes for feature in lane.features.split()})
    return {
        "unix_path": str(socket),
        "ssh_port": None,
        "http_port": None,
        "gc_enabled": False,
        "metrics_enabled": False,
        "pressure": {"enabled": False},
        "stores": {
            "local": {
                "type": "local-socket",
                "store_path": str(lanes[0].store),
                "nix_bin": nix.pop(),
                "systems": [os.environ["PYDD_SYSTEM"]],
                "system_features": features,
                "probe": False,
                "monitor": False,
                "gc_enabled": False,
                "pynixd_writes": True,
                "op_routes": PYNIXD_ROUTES,
                # The managed daemon and pynixd's builder both read the store's settings from this.
                "extra_env": {"NIX_CONFIG": nix_config()},
            }
        },
    }


@asynccontextmanager
async def served(lanes: dict[str, Lane], log: Path) -> AsyncIterator[dict[str, Lane]]:
    """Run one pynixd for each store of `lanes`, and give the lanes with the URL of each."""
    binary = os.environ["PYDD_PYNIXD"]
    stores: dict[Path, list[Lane]] = {}
    for lane in lanes.values():
        stores.setdefault(lane.store, []).append(lane)
    urls: dict[Path, str] = {}
    async with AsyncExitStack() as stack:
        for index, (root, members) in enumerate(stores.items()):
            # `sun_path` holds 108 bytes, so the socket is in the runtime directory, not beside the logs.
            socket = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"pydd-pynixd-{os.getpid()}-{index}.sock"
            socket.unlink(missing_ok=True)
            config = log / f"pynixd-{index}.json"
            config.write_text(json.dumps(pynixd_config(members, socket), indent=2))
            output = stack.enter_context((log / f"pynixd-{index}.log").open("w"))
            process = await stack.enter_async_context(
                await anyio.open_process(
                    [binary, "daemon"],
                    stdin=None,
                    stdout=output,
                    stderr=output,
                    env={**os.environ, "PYNIXD_CONFIG": str(config), "PYNIXD_LOG_LEVEL": "INFO"},
                )
            )
            stack.push_async_callback(stop, process)
            with anyio.fail_after(120):
                while not socket.exists():
                    if process.returncode is not None:
                        raise RuntimeError(f"pynixd for {root} exited {process.returncode}: {output.name}")
                    await anyio.sleep(0.1)
            urls[root] = f"unix://{socket}?root={root}"
            print(f"pynixd serves {root} ({output.name})", file=sys.stderr, flush=True)
        yield {name: replace(lane, url=urls[lane.store]) for name, lane in lanes.items()}


async def stop(process: Process) -> None:
    with anyio.CancelScope(shield=True):
        if process.returncode is None:
            process.terminate()
        with anyio.move_on_after(30):
            await process.wait()
        if process.returncode is None:
            process.kill()
            await process.wait()


@dataclass(frozen=True)
class Timeouts:
    evaluate: float
    build: float


#: The lane of each mode.
MODES = {
    "cli": "master",
    "huggorm": "master",
    "drv": "master",
    "stdenv": "master",
    "recursive": "recursive",
    "recursive-eval": "recursive",
    "recursive-nixpkgs": "recursive",
    "out": "released",
    "out-stdenv": "released",
}

#: The groups of modes of each set. A mode in `RELEASED` runs only for a
#: project that the set has under `out`.
GROUPS = {
    "projects": {
        "plain": ["cli", "huggorm", "drv", "recursive", "recursive-eval", "out"],
        "stdenv": ["recursive-nixpkgs", "stdenv", "out-stdenv"],
    },
    "live": {
        "live": ["cli", "huggorm", "recursive", "out"],
    },
    "rust": {
        "rust": ["cli", "huggorm", "drv", "recursive"],
    },
    "rust-live": {
        "rust-live": ["cli", "huggorm", "recursive"],
    },
    "node": {
        "node": ["cli", "huggorm", "drv", "recursive", "out"],
    },
    "node-live": {
        "node-live": ["cli", "huggorm", "recursive", "out"],
    },
}
RELEASED = {"out", "out-stdenv"}


@dataclass
class TestSet:
    name: str
    log: Path
    released: set[str]
    lanes: dict[str, Lane]
    timeouts: Timeouts
    groups: dict[str, list[str]]
    failed: list[str] = field(default_factory=list)

    def fail(self, line: str) -> None:
        self.failed.append(line)
        print(line, flush=True)

    async def mode(self, project: str, mode: str) -> tuple[str, str] | None:
        """Build the check, the environment and the root, and give the last two."""
        log = self.log / f"{project}-{mode}.log"
        with log.open("w") as stderr:
            try:
                _, environment, root = await self.lanes[MODES[mode]].build(
                    f"{self.name}.{mode}.{project}", ["out", "environment", "root"], [], self.timeouts, stderr
                )
            except TimeoutError:
                print(
                    f"timed out: --eval-timeout {self.timeouts.evaluate}, --build-timeout {self.timeouts.build}",
                    file=stderr,
                )
                self.fail(f"TIMEOUT  {project} ({mode})  ({log})")
                return None
            except (CalledProcessError, RuntimeError) as error:
                print(error, file=stderr)
                self.fail(f"FAIL  {project} ({mode})  ({log})")
                return None
        print(f"pass  {project} ({mode})", flush=True)
        return environment, root

    async def project(self, project: str, limiter: anyio.CapacityLimiter) -> None:
        async with limiter:
            for group, modes in self.groups.items():
                modes = [mode for mode in modes if mode not in RELEASED or project in self.released]
                attempted = {mode: await self.mode(project, mode) for mode in modes}
                built = {mode: paths for mode, paths in attempted.items() if paths is not None}
                if len(built) != len(attempted):
                    continue
                if len(set(built.values())) == 1:
                    environment, root = next(iter(built.values()))
                    print(f"same  {project}  {environment} {root}  ({group}: {' '.join(modes)})", flush=True)
                else:
                    shown = " ".join(f"{mode}={' '.join(paths)}" for mode, paths in built.items())
                    self.fail(f"DIFFER  {project}  ({group})  {shown}")


async def attribute_names(attribute: str) -> list[str]:
    with anyio.fail_after(300):
        result = await anyio.run_process(
            ["nix", "eval", "--json", "--file", ".", f"{ROOT}.{attribute}", "--apply", "builtins.attrNames"]
        )
    names: list[str] = json.loads(result.stdout)
    return names


async def collect(lanes: Iterable[Lane], cache: Path) -> None:
    """Empty each chroot store before a run.

    **Nothing roots a path there, so emptying it is all a collection does.**
    Every edit gives new planners and new nodes, and a store that no run
    collected grew past 90 GB and filled the disk. `nix store gc` deletes one
    path per database write, and took over 20 minutes for 63k paths, so a
    store under `cache` goes whole, database and WAL included. A store that
    `PYDD_STORE` names elsewhere is the caller's, and gets `nix store gc`.
    """
    for lane in {lane.store: lane for lane in lanes}.values():
        if not lane.store.exists():
            continue
        if lane.store.is_relative_to(cache):
            await anyio.run_process(["chmod", "-R", "u+w", str(lane.store)])
            await anyio.run_process(["rm", "-rf", str(lane.store)])
        else:
            await lane.nix_run(["store", "gc", "--store", str(lane.store)], 3600, sys.stderr)


async def test(arguments: argparse.Namespace) -> int:
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    log = arguments.log or cache / "pydd" / arguments.set
    log.mkdir(parents=True, exist_ok=True)
    groups = {
        group: [mode for mode in modes if not arguments.modes or mode in arguments.modes]
        for group, modes in GROUPS[arguments.set].items()
    }
    groups = {group: modes for group, modes in groups.items() if modes}
    if not groups:
        raise SystemExit(f"no mode of {arguments.set} is one of {', '.join(arguments.modes)}")
    modes = {mode for group in groups.values() for mode in group}
    every = lanes(arguments.pynixd)
    used = {MODES[mode]: every[MODES[mode]] for mode in modes}
    if not arguments.keep_store:
        await collect(used.values(), cache / "pydd")
    # **One process creates each store, before the jobs.** Parallel first
    # opens of a new store raced on its schema, and `nix eval` failed with
    # "database is busy" in the first mode of a project.
    for lane in {lane.store: lane for lane in used.values()}.values():
        await lane.nix_run(["store", "info", "--store", str(lane.store)], 300, sys.stderr)
    async with served(used, log) if arguments.pynixd else nullcontext(used) as serving:
        test_set = TestSet(
            arguments.set,
            log,
            set(await attribute_names(f"{arguments.set}.out")) if modes & RELEASED else set(),
            serving,
            Timeouts(arguments.eval_timeout, arguments.build_timeout),
            groups,
        )
        projects = arguments.projects or await attribute_names(f"{arguments.set}.cli")
        limiter = anyio.CapacityLimiter(arguments.jobs)
        async with anyio.create_task_group() as group:
            for project in projects:
                group.start_soon(test_set.project, project, limiter)
    if test_set.failed:
        print(f"\n{len(test_set.failed)} failed:", *test_set.failed, sep="\n")
        return 1
    return 0


async def run(arguments: argparse.Namespace, extra: list[str]) -> int:
    chosen = {arguments.lane: lanes(arguments.pynixd)[arguments.lane]}
    log = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "pydd" / "run"
    log.mkdir(parents=True, exist_ok=True)
    async with served(chosen, log) if arguments.pynixd else nullcontext(chosen) as serving:
        lane = serving[arguments.lane]
        paths = await lane.build(
            arguments.attribute,
            arguments.targets or ["out"],
            extra,
            Timeouts(arguments.eval_timeout, arguments.build_timeout),
        )
        # `--print-paths` prints the output paths alone, one for each target.
        if arguments.print_paths or os.environ.get("PYDD_PRINT_PATH") == "1":
            print(*paths, sep="\n")
            return 0
        store = ["--store", lane.store_arg]
        for path in paths:
            print(path, file=sys.stderr)
            listing = json.loads(await lane.nix_run(["store", "ls", *store, "--json", path], 60))
            command = "cat" if listing["type"] == "regular" else "ls"
            print(await lane.nix_run(["store", command, *store, path], 60), end="")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="runner", description=__doc__.split("\n\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--eval-timeout", type=float, default=300, help="seconds for one evaluation")
    common.add_argument("--build-timeout", type=float, default=3600, help="seconds for one build")
    common.add_argument(
        "--pynixd", action="store_true", help="serve each store through a pynixd that builds and writes it itself"
    )
    commands = top.add_subparsers(dest="command", required=True)

    one = commands.add_parser(
        "run", parents=[common], help="build targets of one attribute; other flags go to nix build"
    )
    one.add_argument("--lane", choices=["master", "released", "recursive"], required=True)
    one.add_argument("--print-paths", action="store_true", help="print the output paths alone")
    one.add_argument("attribute")
    one.add_argument("targets", nargs="*", help="attributes of it to build (default: out)")

    many = commands.add_parser("test", parents=[common], help="build a test set in every mode, and compare the modes")
    many.add_argument("set", choices=sorted(GROUPS))
    many.add_argument("projects", nargs="*", help="only these projects")
    many.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="projects at a time")
    many.add_argument("--log", type=Path, help="directory of the logs")
    many.add_argument("--keep-store", action="store_true", help="do not collect the chroot stores before the run")
    many.add_argument("--modes", nargs="+", choices=sorted(MODES), help="only these modes")
    return top


def main() -> int:
    arguments, extra = parser().parse_known_args()
    if arguments.command == "test":
        if extra:
            parser().error(f"unrecognized arguments: {' '.join(extra)}")
        return anyio.run(test, arguments)
    return anyio.run(run, arguments, extra)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
