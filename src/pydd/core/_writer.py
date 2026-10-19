"""The two ways a planner reaches the store: the ``nix`` CLI, or huggorm.

Both take the same JSON and call the same libstore code. The CLI needs
nothing but a ``nix`` on ``PATH``; huggorm skips a process per derivation.
"""

from __future__ import annotations

import json
import os
import posixpath
import subprocess
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._derivation import Derivation


class Writer(Protocol):
    def add(self, drv: Derivation) -> str:
        """Write ``drv`` and return its absolute ``.drv`` path."""
        ...

    def submit(self, path: str, output: str) -> None:
        """Register ``path`` as output ``output`` of the build that runs now."""
        ...


class CliWriter:
    """``nix derivation add`` and ``nix store submit-output``."""

    def __init__(self, nix: str = "nix") -> None:
        self.add_command = [nix, "derivation", "add"]
        self.submit_command = [nix, "store", "submit-output"]

    def add(self, drv: Derivation) -> str:
        result = subprocess.run(
            self.add_command,
            input=json.dumps(drv.to_json()),
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def submit(self, path: str, output: str) -> None:
        subprocess.run([*self.submit_command, path, output], check=True)


class DrvWriter(CliWriter):
    """``pydd-drv add`` and ``pydd-drv submit``: the same calls, with no ``nix``.

    A planner then carries a binary over libstore instead of the closure of
    the Nix CLI (nixidae/nixidae#782).
    """

    def __init__(self, tool: str = "pydd-drv") -> None:
        self.add_command = [tool, "add"]
        self.submit_command = [tool, "submit"]


class HuggormWriter:
    """``Store.add_derivation`` and ``Store.submit_output``, in process."""

    def __init__(self, uri: str | None = None) -> None:
        # Here, so the CLI writer runs where the bindings are absent.
        import huggorm

        # NIX_CONFIG enables ca-derivations; huggorm reads it only when asked.
        huggorm.load_config()
        self.store: Any = huggorm.Store(uri or os.environ["NIX_REMOTE"])

    def add(self, drv: Derivation) -> str:
        path = self.store.add_derivation(json.dumps(drv.to_json()))
        return str(self.store.print_store_path(path))

    def submit(self, path: str, output: str) -> None:
        self.store.submit_output(self.store.parse_store_path(path), output)


class OutWriter(HuggormWriter):
    """A planner on released Nix, which has no ``builder-rpc-v0``.

    Nix reads one derivation, as ATerm, from ``$out``. Each derivation the
    planner adds goes to a writable ``dummy://`` in memory, which holds no
    input and needs none, so a planner is the same code for both protocols.
    Only the root leaves it, and only when every path the root names is
    already in the sandbox: Nix registers no reference to anything else, and
    the root then silently becomes another derivation.
    """

    def __init__(self, uri: str = "dummy://?read-only=false", present: Callable[[str], bool] = os.path.exists) -> None:
        super().__init__(uri)
        self.present = present

    def submit(self, path: str, output: str) -> None:
        if output != "out":
            raise ValueError(f"a planner on released Nix writes only its output 'out', not {output!r}")
        store_path = self.store.parse_store_path(path)
        drv = self.store.read_derivation(store_path)
        store_dir = posixpath.dirname(path)
        named = [str(self.store.print_store_path(src)) for src in drv.input_srcs()]
        named += [f"{store_dir}/{base}" for base in drv.input_drvs()]
        missing = sorted(name for name in named if not self.present(name))
        if missing:
            raise RuntimeError(
                f"{path} names {', '.join(missing)}, which this build does not have. "
                "On released Nix a planner writes one derivation, so every other one "
                "it names must be an input of the planner, made before the build."
            )
        aterm: bytes = self.store.get_fs_accessor(store_path, False).read_file("")
        with open(os.environ["out"], "wb") as sink:  # noqa: SIM112 - Nix names it `out`
            sink.write(aterm)


class RecursiveWriter:
    """A planner on released Nix with recursive-nix.

    The build gets a daemon socket, so each derivation goes into the store as
    on `builder-rpc-v0`. A release has no `SubmitOutput`, so the root goes to
    `$out` as ATerm, as with `OutWriter`. Every derivation the root names is
    one this build added, and Nix lets an output name what the build added.
    """

    def __init__(self, inner: Writer) -> None:
        self.inner = inner

    def add(self, drv: Derivation) -> str:
        return self.inner.add(drv)

    def submit(self, path: str, output: str) -> None:
        if output != "out":
            raise ValueError(f"a planner on released Nix writes only its output 'out', not {output!r}")
        # recursive-nix puts each path the build added into its sandbox.
        with open(path, "rb") as source, open(os.environ["out"], "wb") as sink:  # noqa: SIM112 - Nix names it `out`
            sink.write(source.read())


def huggorm_store_writer(writer: Writer) -> HuggormWriter | None:
    """The huggorm writer that `writer` is, or wraps, or nothing."""
    inner = writer.inner if isinstance(writer, RecursiveWriter) else writer
    return inner if isinstance(inner, HuggormWriter) else None


def writer_from_environ() -> Writer:
    """Open the restricted daemon socket that `builder-rpc-v0` gives a builder,
    with the writer that `PYDD_WRITER` names.

    A planner runs only inside such a build, so a different `NIX_REMOTE` is an
    error and not a fallback. With `PYDD_PROTOCOL=out` the build is one of
    released Nix instead, which has no socket and gives the planner `$out`.
    With `PYDD_PROTOCOL=recursive` it is a recursive-nix build of released
    Nix, which has both.
    """
    protocol = os.environ.get("PYDD_PROTOCOL", "rpc")
    if protocol == "out":
        if "out" not in os.environ:
            raise RuntimeError("PYDD_PROTOCOL is 'out', and this build has no $out to write the root to.")
        return OutWriter()
    remote = os.environ.get("NIX_REMOTE", "")
    if not remote.startswith("unix://"):
        raise RuntimeError(
            f"NIX_REMOTE is {remote!r}. A planner runs only inside a "
            "builder-rpc-v0 or recursive-nix build, which sets it to the daemon socket."
        )
    # A `builder-rpc-v0` build gets no `$out`. The output arrives through
    # `submit_output` instead, so there is no path to write to.
    if protocol == "rpc" and "out" in os.environ:
        raise RuntimeError(f"unexpected: out is set to {os.environ['out']!r}")  # noqa: SIM112 - Nix names it `out`

    writer: Writer
    match os.environ.get("PYDD_WRITER", "cli"):
        case "huggorm":
            writer = HuggormWriter(remote)
        case "drv":
            writer = DrvWriter()
        case _:
            writer = CliWriter()
    return RecursiveWriter(writer) if protocol == "recursive" else writer


WRITERS = {"cli": CliWriter, "huggorm": HuggormWriter, "drv": DrvWriter}
