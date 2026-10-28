"""The environment that gives a build its native tools, with no setup hook."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict


class Native(TypedDict):
    """A package's native tools and environment, by package name in a lane's menu."""

    tools: list[str]
    env: dict[str, str]


def tool_env(tools: Sequence[str], path: str = "") -> dict[str, str]:
    """Variables that find each store path of ``tools`` as stdenv's hooks would.

    ``bin`` goes on ``PATH``, before ``path``; ``lib/pkgconfig`` and
    ``share/pkgconfig`` on ``PKG_CONFIG_PATH``; ``include`` as ``-isystem``
    in ``CFLAGS`` and ``CXXFLAGS``; ``lib`` on ``LIBRARY_PATH``. gcc reads
    ``LIBRARY_PATH`` for ``-l``, so a probe such as ``-lz`` finds the library.
    """
    if not tools:
        return {"PATH": path} if path else {}
    includes = " ".join(f"-isystem {tool}/include" for tool in tools)
    return {
        "PATH": ":".join([*(f"{tool}/bin" for tool in tools), *([path] if path else [])]),
        "PKG_CONFIG_PATH": ":".join(f"{tool}/{d}/pkgconfig" for tool in tools for d in ("lib", "share")),
        "CFLAGS": includes,
        "CXXFLAGS": includes,
        "LIBRARY_PATH": ":".join(f"{tool}/lib" for tool in tools),
    }
