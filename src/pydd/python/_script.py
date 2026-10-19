"""Read a PEP 723 script and the `<script>.lock` that `uv lock --script` writes.

The lock is a `uv.lock` whose roots are `[manifest] requirements`, not
workspace members, so this reader walks it with `select_lock` from those
requirements, extras included.

**The script is the declaration, and the lock must match it.** A `uv.lock`
keeps `[package.metadata]` to compare against `pyproject.toml`; a script lock
keeps `[manifest] requirements` instead. This reader compares that table with
the `dependencies` of the `# /// script` block, and refuses a lock that the
block has outgrown.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from pydd.python._uvlock import EdgeEdit, Lock, LockError, build_requirements, select_lock

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydd.python._select import Target

#: The reference expression of PEP 723.
BLOCK = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")


def script_metadata(text: str) -> dict[str, Any]:
    """The TOML of the one `script` block of `text`, or an empty table."""
    blocks = [match for match in BLOCK.finditer(text) if match.group("type") == "script"]
    if len(blocks) > 1:
        raise LockError("the script holds more than one `# /// script` block, and PEP 723 forbids that")
    if not blocks:
        return {}
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:] for line in blocks[0].group("content").splitlines(keepends=True)
    )
    return tomllib.loads(content)


def _key(requirement: Requirement) -> tuple[str, tuple[str, ...], str, str]:
    return (
        canonicalize_name(requirement.name),
        tuple(sorted(canonicalize_name(extra) for extra in requirement.extras)),
        str(requirement.specifier),
        str(requirement.marker or ""),
    )


def _manifest_requirement(entry: Mapping[str, Any]) -> Requirement:
    extras = f"[{','.join(entry['extras'])}]" if entry.get("extras") else ""
    marker = f"; {entry['marker']}" if entry.get("marker") else ""
    return Requirement(f"{entry['name']}{extras}{entry.get('specifier', '')}{marker}")


@dataclass(frozen=True, slots=True)
class ScriptProject:
    """A script lock, and the requirements of the script that it pins."""

    path: str
    lock: Lock
    requirements: tuple[Requirement, ...]
    #: The edges that the overrides add and drop, by package.
    edits: Mapping[str, EdgeEdit] = field(default_factory=dict[str, EdgeEdit])

    @classmethod
    def from_json(cls, spec: Mapping[str, Any], edits: Mapping[str, EdgeEdit] | None = None) -> ScriptProject:
        with open(spec["lock"], "rb") as handle:
            document = tomllib.load(handle)
        locked = tuple(_manifest_requirement(entry) for entry in document.get("manifest", {}).get("requirements", []))
        declared = tuple(
            Requirement(text) for text in script_metadata(Path(spec["source"]).read_text()).get("dependencies", [])
        )
        if sorted(map(_key, declared)) != sorted(map(_key, locked)):
            raise LockError(
                f"{Path(spec['lock']).name} is stale: the script declares {sorted(map(str, declared))}, and the "
                f"lock pins {sorted(map(str, locked))}. Run `uv lock --script` and build again."
            )
        return cls(path=spec["lock"], lock=Lock.parse(document), requirements=locked, edits=edits or {})

    def selection(self, target: Target) -> tuple[list[str], list[Any]]:
        """The pinned packages that this target installs, and no local tree."""
        roots: dict[str, list[str]] = {}
        for requirement in self.requirements:
            if requirement.marker is not None and not requirement.marker.evaluate(dict(target.markers)):
                continue
            roots.setdefault(canonicalize_name(requirement.name), []).extend(requirement.extras)
        selection = select_lock(self.lock, target.markers, roots, self.edits)
        pins = [f"{package.raw_name}=={package.version}" for package in selection.members]
        print(f"plan: the script lock selects {len(pins)} packages", file=sys.stderr)
        return pins, []

    def backend(self, requirements: Iterable[Requirement], target: Target, subject: str) -> list[Requirement]:
        try:
            return build_requirements(self.lock, target.markers, requirements, self.edits)
        except LockError as error:
            raise RuntimeError(f"{subject}: {error}") from error
