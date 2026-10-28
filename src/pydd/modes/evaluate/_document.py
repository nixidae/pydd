"""A plan document, turned into derivations by `graph.nix`, evaluated in huggorm."""

from __future__ import annotations

import sys
from typing import Any

from ...core._writer import HuggormWriter
from ...python._plan import BUILT_PAIRS, output_name


def document_value(state: Any, data: Any, store_dir: str) -> Any:
    """`data` as a Nix value, with the string context that `graph.nix` needs.

    **The context is attached here, because the Nix language cannot.**
    `builtins.storePath` and `builtins.appendContext` call `EnsurePath`, and a
    `builder-rpc-v0` socket refuses it. So a store path gets plain context, and
    the output of a menu entry gets the context of its derivation: reading it
    builds that derivation, and an entry that no node reads is never built.
    """
    match data:
        case dict() if "__context" in data:
            # A string that carries its context: `pydd.modes.render._render.carried`.
            elements = []
            for path, kind in data["__context"].items():
                if kind.get("path"):
                    elements.append(path.split("/")[3])
                elements += [f"!{output}!{path.split('/')[3]}" for output in kind.get("outputs", [])]
            return state.make_string(data["text"], elements)
        case dict():
            value = state.make_attrs()
            for name, item in data.items():
                drv = data.get(BUILT_PAIRS.get(name, ""))
                if isinstance(drv, str) and isinstance(item, str):
                    element = f"!{output_name(data, name)}!{drv.split('/')[3]}"
                    state.attrs_set(value, name, state.make_string(item, [element]))
                elif name in BUILT_PAIRS.values():
                    state.attrs_set(value, name, state.make_string(item))
                else:
                    state.attrs_set(value, name, document_value(state, item, store_dir))
            return value
        case list():
            value = state.make_list()
            for item in data:
                state.list_append(value, document_value(state, item, store_dir))
            return value
        case str() if data.startswith(store_dir + "/"):
            return state.make_string(data, [data.split("/")[3]])
        case str():
            return state.make_string(data)
        case bool():
            return state.make_bool(data)
        case int():
            return state.make_int(data)
        case float():
            return state.make_float(data)
        case None:
            return state.make_null()
    raise TypeError(f"no Nix value for {type(data).__name__}")


def submit_evaluated(writer: HuggormWriter, document: dict[str, Any], graph_nix: str, store_dir: str) -> str:
    """Evaluate `graph.nix` over `document`, and name the root `.drv` it wrote.

    **The evaluator reads a fixed file, and the plan is its argument.** The
    decisions of a planner are data, and `graph.nix` is the code that turns
    them into derivations. No planner writes a Nix expression by hand.
    """
    import huggorm

    state = huggorm.EvalState(writer.store)
    graph = state.eval_file(graph_nix)
    root = graph(document_value(state, document, store_dir))

    # The store path of the `.drv` that the evaluator wrote. It is not an
    # output path: this build cannot realise anything.
    root_drv: str = writer.store.print_store_path(root.drv_path())
    print(f"==> the evaluator wrote {root_drv}", file=sys.stderr)
    return root_drv
