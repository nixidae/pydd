"""The dynamic-derivation core: derivations as JSON, placeholders, planners and writers.

It knows no language ecosystem and no way of turning a plan into a graph.
It is standard library only, and the huggorm writer imports huggorm when it
is made.
"""

from __future__ import annotations

from ._derivation import Derivation, Fixed, Floating, InputAddressed, Method, Output
from ._hash import (
    downstream_placeholder,
    dynamic_placeholder,
    fixed_output_path,
    nix32,
    output_path_name,
    placeholder,
)
from ._planner import Node, Planner
from ._tools import Native, tool_env
from ._writer import (
    CliWriter,
    DrvWriter,
    HuggormWriter,
    OutWriter,
    RecursiveWriter,
    Writer,
    huggorm_store_writer,
    writer_from_environ,
)

__all__ = [
    "CliWriter",
    "Derivation",
    "DrvWriter",
    "Fixed",
    "Floating",
    "HuggormWriter",
    "InputAddressed",
    "Method",
    "Native",
    "Node",
    "OutWriter",
    "Output",
    "Planner",
    "RecursiveWriter",
    "Writer",
    "downstream_placeholder",
    "dynamic_placeholder",
    "fixed_output_path",
    "huggorm_store_writer",
    "nix32",
    "output_path_name",
    "placeholder",
    "tool_env",
    "writer_from_environ",
]
