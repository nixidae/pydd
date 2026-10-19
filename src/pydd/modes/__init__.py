"""The ways to turn a plan document into derivations, one subpackage each.

`render` writes them in Python. `evaluate` evaluates `graph.nix` in huggorm.
Each mode imports only what it needs, so a planner carries the files of its
own mode and no other.
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any

from ..core._writer import huggorm_store_writer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..core._writer import Writer
    from ..python._plan import Plan


def submit(writer: Writer, document: dict[str, Any]) -> None:
    """Turn `document` into derivations, and submit the root.

    `PYDD_RENDER` names the path. `nix` evaluates `graph.nix` in huggorm, and
    needs the huggorm writer. `nix-cli` evaluates it with `nix eval`, which
    needs EnsurePath: recursive-nix, or a `builder-rpc-v0` that allows it.
    `python` renders the same derivations with `pydd.modes.render`, through
    any writer.
    """
    store_dir = os.environ.get("NIX_STORE", "/nix/store")
    match os.environ.get("PYDD_RENDER", "nix"):
        case "nix":
            inner = huggorm_store_writer(writer)
            if inner is None:
                raise RuntimeError("PYDD_RENDER=nix evaluates graph.nix in huggorm, so it needs PYDD_WRITER=huggorm")
            from .evaluate._document import submit_evaluated

            root_drv = submit_evaluated(inner, document, os.environ["PYDD_GRAPH_NIX"], store_dir)
        case "nix-cli":
            from .nix_cli import submit_nix_cli

            root_drv = submit_nix_cli(document)
        case "python":
            from .render import render

            root_drv = render(document, writer.add, store_dir)
            print(f"==> the renderer wrote {root_drv}", file=sys.stderr)
        case other:
            raise RuntimeError(f"PYDD_RENDER is {other!r}, and it takes 'nix', 'nix-cli' or 'python'")

    writer.submit(root_drv, "out")
    print("==> submitted it as output 'out'", file=sys.stderr)


def root(plan: Plan, inputs: dict[str, Any], name: str, application: str | Sequence[str] | None) -> str:
    """The node that the planner of an environment submits.

    A local project is the root of a planner of its own (`PYDD_PLANNED`), so
    its node here becomes a node of kind `planned`. `PYDD_ONLY` names the one
    local node that this planner writes itself, when it is such a planner. On
    released Nix (`PYDD_PROTOCOL=out`) `pydd.modes.out` decides the rest.
    """
    planned: dict[str, dict[str, str]] = json.loads(os.environ.get("PYDD_PLANNED") or "{}")
    only = os.environ.get("PYDD_ONLY")
    for key, node in plan.local_nodes.items():
        if key in planned and key != only:
            plan.replace(node, plan.planned_entry(node, planned[key]))
    if only is not None:
        if only not in plan.local_nodes:
            raise RuntimeError(f"PYDD_ONLY is {only!r}, and the plan has no local project of that key")
        return plan.local_nodes[only]
    if os.environ.get("PYDD_PROTOCOL") == "out":
        from .out import root as out_root

        return out_root(plan, inputs, name, application)
    return plan.root(name, application=application)
