"""A derivation as data, in the JSON that ``nix derivation add`` reads.

Nix parses this JSON, fills in every output path it can compute, checks the
invariants, and writes the ``.drv`` itself. pydd renders ATerm in one case
only, which ``pydd.core._aterm`` names.
"""

from __future__ import annotations

import posixpath
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ._hash import placeholder

#: How Nix ingests an output.
type Method = Literal["nar", "flat", "text"]


@dataclass(frozen=True, slots=True)
class Floating:
    """A content-addressed output whose path is known only after the build.

    The shape to reach for first: nobody computes a hash, and another node
    names it through :func:`pydd.downstream_placeholder`.
    """

    method: Method = "nar"
    hash_algo: str = "sha256"


@dataclass(frozen=True, slots=True)
class Fixed:
    """An output whose content hash is known in advance, such as a download.

    ``hash`` is SRI, as in ``sha256-...``.
    """

    hash: str
    method: Method = "flat"


@dataclass(frozen=True, slots=True)
class InputAddressed:
    """An output addressed by the derivation itself. Nix computes the path."""


type Output = Floating | Fixed | InputAddressed


def _one_floating_output() -> dict[str, Output]:
    return {"out": Floating()}


@dataclass(frozen=True, slots=True)
class Derivation:
    """One derivation, before Nix writes it.

    Store paths are absolute. ``drvs`` maps each input ``.drv`` to the outputs
    the build reads; ``srcs`` lists every other store path it reads.
    ``dynamic`` maps an input ``.drv`` to an output of it that is itself a
    derivation, and that output to the outputs the build reads of it.
    """

    name: str
    system: str
    builder: str
    args: Sequence[str] = ()
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    srcs: Collection[str] = ()
    drvs: Mapping[str, Collection[str]] = field(default_factory=dict[str, Collection[str]])
    outputs: Mapping[str, Output] = field(default_factory=_one_floating_output)
    dynamic: Mapping[str, Mapping[str, Collection[str]]] = field(
        default_factory=dict[str, Mapping[str, Collection[str]]]
    )

    def to_json(self) -> dict[str, Any]:
        """The derivation in Nix's JSON format, version 4."""
        env = {"name": self.name, "builder": self.builder, "system": self.system, **self.env}
        # Nix fills in the variable of every output whose path it computes, but
        # not that of a floating one: there is no path to fill in.
        for output, kind in self.outputs.items():
            if isinstance(kind, Floating):
                env.setdefault(output, placeholder(output))
        return {
            "version": 4,
            "name": self.name,
            "system": self.system,
            "builder": self.builder,
            "args": list(self.args),
            "env": env,
            "inputs": {
                "srcs": sorted({posixpath.basename(path) for path in self.srcs}),
                "drvs": {
                    posixpath.basename(drv): {
                        "outputs": sorted(self.drvs.get(drv, ())),
                        "dynamicOutputs": {
                            output: {"outputs": sorted(read), "dynamicOutputs": {}}
                            for output, read in sorted(self.dynamic.get(drv, {}).items())
                        },
                    }
                    for drv in sorted({*self.drvs, *self.dynamic})
                },
            },
            "outputs": {name: _output_json(kind) for name, kind in self.outputs.items()},
        }


def _output_json(output: Output) -> dict[str, str]:
    match output:
        case Floating(method, hash_algo):
            return {"method": method, "hashAlgo": hash_algo}
        case Fixed(hash_, method):
            return {"method": method, "hash": hash_}
        case InputAddressed():
            return {}
