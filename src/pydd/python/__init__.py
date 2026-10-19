"""What every planner of a Python environment shares.

An environment has more than one planner. The outer one selects the environment,
and the planner of a source distribution selects the backend that the archive
asks for. Both make packaging decisions with `packaging`, both write a plan
document, and both submit its root through `pydd.modes`.

This package is not installed. A planner reaches it through `PYTHONPATH`
inside its build sandbox, which `default.nix` sets from a store path.
"""

from __future__ import annotations

from ._env import env as env
from ._freshness import (
    Pin as Pin,
)
from ._freshness import (
    # `check` alone says nothing about what it checks, and this package holds
    # more than one check.
    check as check_freshness,
)
from ._plan import (
    OVERRIDE_KEYS as OVERRIDE_KEYS,
)
from ._plan import (
    LocalNode as LocalNode,
)
from ._plan import (
    Plan as Plan,
)
from ._plan import (
    build_tools_from as build_tools_from,
)
from ._plan import (
    nix_string as nix_string,
)
from ._plan import (
    overrides_from as overrides_from,
)
from ._sdist import (
    PYPROJECT as PYPROJECT,
)
from ._sdist import (
    has_native_sources as has_native_sources,
)
from ._sdist import (
    is_pyproject as is_pyproject,
)
from ._sdist import (
    read_pyproject as read_pyproject,
)
from ._select import (
    FALLBACK_BACKEND as FALLBACK_BACKEND,
)
from ._select import (
    Artifact as Artifact,
)
from ._select import (
    Target as Target,
)
from ._select import (
    build_system as build_system,
)
from ._select import (
    choose as choose,
)
from ._select import (
    target_from_environ as target_from_environ,
)
from ._select import (
    target_platforms as target_platforms,
)
from ._select import (
    target_tags as target_tags,
)
from ._select import (
    wheel_rank as wheel_rank,
)
from ._uvlock import (
    Edge as Edge,
)
from ._uvlock import (
    File as File,
)
from ._uvlock import (
    Lock as Lock,
)
from ._uvlock import (
    LockError as LockError,
)
from ._uvlock import (
    Package as Package,
)
from ._uvlock import (
    PackageId as PackageId,
)
from ._uvlock import (
    Selection as Selection,
)
from ._uvlock import (
    build_requirements as build_requirements,
)
from ._uvlock import (
    filter_conflicts as filter_conflicts,
)
from ._uvlock import (
    select_lock as select_lock,
)

__all__ = [
    "FALLBACK_BACKEND",
    "OVERRIDE_KEYS",
    "PYPROJECT",
    "Artifact",
    "Edge",
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
