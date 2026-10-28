"""What every planner of a Python environment shares.

An environment has more than one planner. The outer one selects the environment,
and the planner of a source distribution selects the backend that the archive
asks for. Both make packaging decisions with `packaging`, both write a plan
document, and both submit its root through `pydd.modes`.

This package is not installed from an index. A planner runs in a venv that
holds it as a package of the store (`lib/core/planner.nix`).
"""

from __future__ import annotations

from ._env import env as env
from ._freshness import (
    Pin as Pin,
    # `check` alone says nothing about what it checks, and this package holds
    # more than one check.
    check as check_freshness,
)
from ._plan import (
    OVERRIDE_KEYS as OVERRIDE_KEYS,
    LocalNode as LocalNode,
    Plan as Plan,
    build_tools_from as build_tools_from,
    nix_string as nix_string,
    overrides_from as overrides_from,
)
from ._sdist import (
    PYPROJECT as PYPROJECT,
    has_native_sources as has_native_sources,
    is_pyproject as is_pyproject,
    read_pyproject as read_pyproject,
)
from ._select import (
    FALLBACK_BACKEND as FALLBACK_BACKEND,
    Artifact as Artifact,
    Target as Target,
    build_system as build_system,
    choose as choose,
    target_from_environ as target_from_environ,
    target_platforms as target_platforms,
    target_tags as target_tags,
    wheel_rank as wheel_rank,
)
from ._uvlock import (
    Edge as Edge,
    EdgeEdit as EdgeEdit,
    File as File,
    Lock as Lock,
    LockError as LockError,
    Package as Package,
    PackageId as PackageId,
    Selection as Selection,
    build_requirements as build_requirements,
    edits_from as edits_from,
    filter_conflicts as filter_conflicts,
    select_lock as select_lock,
)

__all__ = [
    "FALLBACK_BACKEND",
    "OVERRIDE_KEYS",
    "PYPROJECT",
    "Artifact",
    "Edge",
    "EdgeEdit",
    "File",
    "LocalNode",
    "Lock",
    "LockError",
    "Package",
    "PackageId",
    "Pin",
    "Plan",
    "Selection",
    "Target",
    "build_requirements",
    "build_system",
    "build_tools_from",
    "check_freshness",
    "choose",
    "edits_from",
    "env",
    "filter_conflicts",
    "has_native_sources",
    "is_pyproject",
    "nix_string",
    "overrides_from",
    "read_pyproject",
    "select_lock",
    "target_from_environ",
    "target_platforms",
    "target_tags",
    "wheel_rank",
]
