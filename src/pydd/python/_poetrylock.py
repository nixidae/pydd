"""Read a `poetry.lock`: which packages one target installs, and nothing more.

**Poetry already chose every version, and this reader chooses none.** Lock
version 2.1, which Poetry 2 writes, gives each package the dependency groups
that reach it and the markers under which it applies, already combined over
every path from the project. So this reader walks no edge to choose the
members: it reads `groups` and evaluates `markers`, which is what the
installer of Poetry does with the same file.

**The project itself is not in `poetry.lock`.** Its tree arrives through the
`editables` seam of the inputs, so this reader returns pinned packages only.

**The edges still matter for a build backend.** A backend that a dependency
group locked needs its own dependencies, and `requires` names only the top of
that closure. `backend` walks `[package.dependencies]` for that, as
`build_requirements` walks `uv.lock`.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from pydd.python._uvlock import EdgeEdit, LockError, added_pins

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydd.python._select import Target


def _holds(marker: str | None, target: Target, extras: Iterable[str]) -> bool:
    """Whether `marker` holds on `target`, with no extra or with one of `extras`."""
    if marker is None:
        return True
    parsed = Marker(marker)
    return any(parsed.evaluate({**target.markers, "extra": extra}) for extra in ("", *extras))


@dataclass(frozen=True, slots=True)
class PoetryProject:
    """A `poetry.lock`, and the two decisions that the lock file does not record."""

    lock: dict[str, Any]
    #: The dependency groups to install. `None` is `poetry install --only main`,
    #: the runtime dependencies.
    groups: list[str] | None = None
    #: The extras of the project to enable.
    extras: list[str] = field(default_factory=list[str])
    #: The edges that the overrides add, by package. `added_pins` refuses a drop.
    edits: Mapping[str, EdgeEdit] = field(default_factory=dict[str, EdgeEdit])

    @classmethod
    def from_json(cls, spec: Mapping[str, Any], edits: Mapping[str, EdgeEdit] | None = None) -> PoetryProject:
        with open(spec["lock"], "rb") as handle:
            lock = tomllib.load(handle)
        return cls(lock=lock, groups=spec.get("groups"), extras=list(spec.get("extras") or []), edits=edits or {})

    @property
    def packages(self) -> list[dict[str, Any]]:
        return self.lock.get("package", [])

    def _check_version(self) -> None:
        version = str(self.lock.get("metadata", {}).get("lock-version", ""))
        major, _, minor = version.partition(".")
        if not (major.isdigit() and minor.isdigit() and (int(major), int(minor)) >= (2, 1)):
            raise LockError(
                f"poetry.lock says lock-version {version or 'nothing'}, and this reader needs 2.1 or "
                "later: an older lock gives a package no groups and no markers. Run `poetry lock` with "
                "Poetry 2."
            )

    def selection(self, target: Target) -> tuple[list[str], list[Any]]:
        """The pinned packages that this target installs, and no local tree."""
        self._check_version()
        wanted = set(self.groups if self.groups is not None else ["main"])

        roots: list[str] = []
        for package in self.packages:
            name = package["name"]
            if (source := package.get("source")) is not None:
                raise LockError(
                    f"poetry.lock resolves '{name}' from a source of type '{source.get('type')}'. "
                    "This reader takes packages of PyPI only."
                )
            groups = [group for group in package.get("groups", []) if group in wanted]
            if not groups:
                continue
            # **An optional package is one that only an extra asks for**, and
            # its markers then compare `extra`. With no extra, it is not part
            # of the install.
            if package.get("optional", False) and not self.extras:
                continue
            markers = package.get("markers")
            per_group = [markers.get(group) for group in groups] if isinstance(markers, dict) else [markers]
            if not any(_holds(marker, target, self.extras) for marker in per_group):
                print(f"plan: {name}: markers exclude this target", file=sys.stderr)
                continue
            roots.append(f"{name}=={package['version']}")

        pins = {
            canonicalize_name(package["name"]): f"{package['name']}=={package['version']}" for package in self.packages
        }
        roots += added_pins(self.edits, (canonicalize_name(root.split("==")[0]) for root in roots), pins, "poetry.lock")
        print(f"plan: poetry.lock selects {len(roots)} packages", file=sys.stderr)
        return roots, []

    def backend(self, requirements: Iterable[Requirement], target: Target, subject: str) -> list[Requirement]:
        """The closure of a `build-system.requires`, as one pin for each name.

        **The backend has to be in the lock**, and only a dependency group puts
        it there: Poetry locks no build system, exactly as uv does not.
        """
        by_name = {canonicalize_name(package["name"]): package for package in self.packages}
        pins: dict[str, Requirement] = {}
        pending = list(requirements)
        while pending:
            requirement = pending.pop()
            if requirement.marker is not None and not requirement.marker.evaluate(target.markers):
                continue
            name = canonicalize_name(requirement.name)
            if name in pins:
                continue
            package = by_name.get(name)
            if package is None:
                raise RuntimeError(
                    f"{subject}: poetry.lock holds no '{requirement.name}'. Put it in a dependency group, "
                    f"with `poetry add --group build {requirement.name}`, and lock the project again."
                )
            pins[name] = Requirement(f"{package['name']}=={package['version']}")
            for dependency, spec in package.get("dependencies", {}).items():
                for item in spec if isinstance(spec, list) else [spec]:
                    entry = item if isinstance(item, dict) else {"version": item}
                    if entry.get("optional", False):
                        continue
                    if _holds(entry.get("markers"), target, ()):
                        pending.append(Requirement(dependency))
        return list(pins.values())
