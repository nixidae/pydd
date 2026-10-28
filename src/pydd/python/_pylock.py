"""Read a `pylock.toml` (PEP 751): which packages one target installs, and nothing more.

**The file is already resolved, and this reader chooses no version.** It runs
the steps that PEP 751 gives an installer, as `packaging.pylock.Pylock.select`
does: the lock's `requires-python` and `environments`, then each package's
`marker` and `requires-python`. It stops before the last step. `select` picks
a wheel by the tags of the running interpreter, and the planner picks one by
the tags of the target.

**A directory or a git source is a local tree**, and it arrives through the
`editables` seam of the inputs, as the project does for `poetry.lock`. So this
reader returns pinned packages only.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from packaging.pylock import Pylock, PylockValidationError
from packaging.utils import canonicalize_name

from pydd.python._uvlock import EdgeEdit, LockError, added_pins

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from packaging.requirements import Requirement

    from pydd.python._select import Target


@dataclass(frozen=True, slots=True)
class PylockProject:
    """A `pylock.toml`, which is one selection."""

    lock: Pylock
    #: The edges that the overrides add, by package. `added_pins` refuses a drop.
    edits: Mapping[str, EdgeEdit] = field(default_factory=dict[str, EdgeEdit])

    @classmethod
    def from_json(cls, spec: Mapping[str, Any], edits: Mapping[str, EdgeEdit] | None = None) -> PylockProject:
        with open(spec["lock"], "rb") as handle:
            document = tomllib.load(handle)
        try:
            return cls(lock=Pylock.from_dict(document), edits=edits or {})
        except PylockValidationError as error:
            raise LockError(f"{spec['lock']}: {error}") from error

    def selection(self, target: Target) -> tuple[list[str], list[Any]]:
        """The pinned packages that this target installs, and no local tree."""
        python = target.markers["python_full_version"]
        if self.lock.requires_python and not self.lock.requires_python.contains(python):
            raise LockError(f"pylock.toml requires Python {self.lock.requires_python}, and the target is {python}")
        if self.lock.environments and not any(
            marker.evaluate(dict(target.markers), context="requirement") for marker in self.lock.environments
        ):
            raise LockError("pylock.toml lists environments, and the target matches none of them")

        # PEP 751: `extras` is empty and `dependency_groups` is `default-groups`
        # unless the installer is told otherwise. The export resolved both.
        environment: dict[str, str | frozenset[str]] = {
            **target.markers,
            "extras": frozenset[str](),
            "dependency_groups": frozenset(self.lock.default_groups or []),
        }
        roots: list[str] = []
        seen: set[str] = set()
        for package in self.lock.packages:
            if package.marker is not None and not package.marker.evaluate(environment, context="lock_file"):
                print(f"plan: {package.name}: its marker excludes this target", file=sys.stderr)
                continue
            if package.requires_python and not package.requires_python.contains(python):
                raise LockError(
                    f"pylock.toml: {package.name} requires Python {package.requires_python}, and the target is {python}"
                )
            if package.name in seen:
                raise LockError(f"pylock.toml selects more than one {package.name} for this target")
            seen.add(package.name)
            if package.directory is not None or package.vcs is not None:
                continue
            if package.version is None:
                raise LockError(f"pylock.toml gives {package.name} no version, and this reader pins each package")
            roots.append(f"{package.name}=={package.version}")

        pins: dict[str, str] = {
            canonicalize_name(package.name): f"{package.name}=={package.version}"
            for package in self.lock.packages
            if package.version is not None
        }
        roots += added_pins(self.edits, (canonicalize_name(root.split("==")[0]) for root in roots), pins, "pylock.toml")
        print(f"plan: pylock.toml selects {len(roots)} packages", file=sys.stderr)
        return roots, []

    def backend(self, requirements: Iterable[Requirement], target: Target, subject: str) -> list[Requirement]:
        """Refuse, so that pydd's own lock gives the backend with its closure.

        **uv's export records no edges**: no package carries `dependencies`.
        `build-system.requires` names only the top of a backend's closure, so
        this lock cannot expand it, and `build_systems.backend` falls back on
        this error.
        """
        raise LockError(f"{subject}: pylock.toml records no dependency edges, so it cannot expand a build backend")
