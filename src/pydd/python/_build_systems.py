"""pydd's lock of the installer and the common build backends, as a fallback.

uv resolves build dependencies apart from the project, so a `uv.lock` in the
wild holds no installer and no build backend. `lib/python/build-systems`
locks the common ones, and its artefacts reach the menu marked
`buildSystems`. The project's own lock always answers first.
"""

from __future__ import annotations

import sys
from functools import cache
from typing import TYPE_CHECKING, Protocol

from packaging.requirements import Requirement

from ._select import choose
from ._uvlock import Lock, LockError, build_requirements

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from ._select import Artifact, Target
    from ._uvlock import EdgeEdit


class Reader(Protocol):
    def backend(self, requirements: Iterable[Requirement], target: Target, subject: str) -> list[Requirement]: ...


@cache
def _lock(path: str) -> Lock:
    return Lock.read(path)


class UvLockReader:
    """A `uv.lock`, as `Reader`."""

    def __init__(self, path: str, edits: Mapping[str, EdgeEdit] | None = None) -> None:
        self.path = path
        self.edits = edits

    def backend(self, requirements: Iterable[Requirement], target: Target, subject: str) -> list[Requirement]:
        try:
            return build_requirements(_lock(self.path), target.markers, requirements, self.edits)
        except LockError as error:
            raise RuntimeError(f"{subject}: {error}") from error


def backend(
    reader: Reader | None,
    requirements: list[Requirement],
    target: Target,
    subject: str,
    fallback: str | None,
) -> list[Requirement]:
    """The closure of a `build-system.requires`, from the project's lock or else pydd's."""
    if reader is None:
        return requirements
    try:
        return reader.backend(requirements, target, subject)
    except (RuntimeError, LockError) as own:
        if fallback is None:
            raise
        try:
            closure = build_requirements(_lock(fallback), target.markers, requirements)
        except LockError as error:
            raise RuntimeError(
                f"{subject}: neither the project's lock nor pydd's build systems hold "
                f"{[str(requirement) for requirement in requirements]}. ({own}) ({error})"
            ) from error
        print(f"plan:   {subject}: the build system comes from pydd's lock", file=sys.stderr)
        return closure


def installer(artifacts: Sequence[Artifact], name: str, target: Target) -> Artifact | None:
    """The installer: the project's own, or else pydd's."""
    requirement = Requirement(name)
    own = [artifact for artifact in artifacts if not artifact.build_systems]
    return choose(own, requirement, target) or choose(artifacts, requirement, target)
