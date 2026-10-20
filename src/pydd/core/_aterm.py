"""A derivation as the ATerm text that Nix writes, and the path of that text.

Only `RecursiveWriter` needs it: a planner whose own output path is the root
it is about to write cannot add the root through the daemon. `RecursiveWriter`
checks this file against Nix on every derivation it adds.
"""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING

from ._derivation import Fixed, Floating
from ._hash import _fold, fixed_output_path, nix32, output_path_name

if TYPE_CHECKING:
    from ._derivation import Derivation

#: The prefix of an output's hash algorithm, by ingestion method.
PREFIX = {"nar": "r:", "text": "text:", "flat": ""}


def _string(text: str) -> str:
    """`printString` of `src/libstore/derivations.cc`."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return '"' + escaped.replace("\t", "\\t") + '"'


def _list(items: list[str]) -> str:
    return "[" + ",".join(items) + "]"


def _tuple(items: list[str]) -> str:
    return "(" + ",".join(items) + ")"


def aterm(drv: Derivation, store: str = "/nix/store") -> str:
    """The `.drv` text of `drv`, with every path Nix fills in. No dynamic inputs."""
    if drv.dynamic:
        raise ValueError(f"{drv.name}: aterm() writes no dynamic input")
    env = drv.to_json()["env"]
    outputs = []
    for name, output in sorted(drv.outputs.items()):
        match output:
            case Floating(method, algorithm):
                outputs.append(_tuple([_string(name), '""', _string(PREFIX[method] + algorithm), '""']))
            case Fixed(sri, method):
                algorithm, _, encoded = sri.partition("-")
                path = fixed_output_path(output_path_name(drv.name, name), sri, method, store)
                env.setdefault(name, path)
                fields = [path, PREFIX[method] + algorithm, base64.b64decode(encoded).hex()]
                outputs.append(_tuple([_string(name), *map(_string, fields)]))
            case _:
                raise ValueError(f"{drv.name}: aterm() writes no input-addressed output")
    drvs = [
        _tuple([_string(path), _list([_string(output) for output in sorted(read)])])
        for path, read in sorted(drv.drvs.items())
    ]
    return "Derive" + _tuple(
        [
            _list(outputs),
            _list(drvs),
            _list([_string(path) for path in sorted(drv.srcs)]),
            _string(drv.system),
            _string(drv.builder),
            _list([_string(arg) for arg in drv.args]),
            _list([_tuple([_string(key), _string(value)]) for key, value in sorted(env.items())]),
        ]
    )


def text_path(name: str, text: str, references: list[str], store: str = "/nix/store") -> str:
    """`makeTextPath`: the store path of `text` that names `references`."""
    kind = ":".join(["text", *sorted(references)])
    clear = f"{kind}:sha256:{hashlib.sha256(text.encode()).hexdigest()}:{store}:{name}"
    return f"{store}/{nix32(_fold(hashlib.sha256(clear.encode()).digest()))}-{name}"


def drv_path(drv: Derivation, store: str = "/nix/store") -> str:
    """Where Nix writes `drv`: its ATerm, naming its sources and input derivations."""
    references = sorted({*drv.srcs, *drv.drvs})
    return text_path(f"{drv.name}.drv", aterm(drv, store), references, store)
