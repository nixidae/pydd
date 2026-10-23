"""Compose one virtual environment from the nodes that the planner chose.

`graph.nix` runs this as the builder of the root node of the graph, under the
interpreter that the environment is for.

**The approach comes from `pyproject.nix`.** Its `build/hooks/make-venv` merges
the store path of each package of a set into one environment, and this is the
small version of the same idea:

- `venv.EnvBuilder` makes a real virtual environment, so the result has
  `pyvenv.cfg`, `bin/python` and `bin/activate`, and an interpreter that finds
  its own `site-packages` with no `PYTHONPATH`.
- Each entry of a member's prefix becomes one symlink, merged by directory:
  `site-packages`, and also the `share/` and `include/` that a wheel's
  `.data/data` and `.data/headers` install to. A Jupyter kernel spec lives
  in `share/`, so an environment of `site-packages` alone loses it.
- **A console script gets its shebang rewritten**, from the interpreter that
  installed it to the `bin/python` of this environment. Without that rewrite
  the script runs the interpreter of the store path, which does not see the
  environment. `pyproject.nix` calls this `write_bin`.
- **Two members may provide one file**, as fastapi and fastapi-cli both
  provide `bin/fastapi`. uv installs both, warns, and keeps whichever lands
  last. This keeps one by a fixed rule, and warns: the member that requires
  the other, by its `Requires-Dist`, and otherwise the earlier member.

The environment gives:
    out        the output path
    members    the output of each node that belongs in the environment
    installed  one `name==version` for each member, one to a line

**A build step runs it too**, as `make-venv.py OUT SITES`, for the
environment that a backend, `installer` or a build hook runs in. `SITES` is
the `site-packages` of each node, joined by `:`, as `backendPath` and
`installerPath` give it. That environment is scratch: it gets no `manifest`,
and it dies with the build.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import sys
import sysconfig
from pathlib import Path
from venv import EnvBuilder


def site_packages_of(root: Path) -> Path:
    """The `site-packages` directory under `root`, for this interpreter."""
    scheme = "venv" if "venv" in sysconfig.get_scheme_names() else "posix_prefix"
    return Path(sysconfig.get_path("purelib", scheme=scheme, vars={"base": str(root), "platbase": str(root)}))


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def distribution_of(member: Path) -> tuple[str, frozenset[str]]:
    """The name of a member's distribution, and every name it requires.

    Every `Requires-Dist` counts, under any marker or extra: the question is
    which of two members is built on the other, not what this target installs.
    """
    name = ""
    requires: set[str] = set()
    for metadata in site_packages_of(member).glob("*.dist-info/METADATA"):
        for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Name:"):
                name = canonical(line.split(":", 1)[1].strip())
            elif line.startswith("Requires-Dist:"):
                requires.add(canonical(re.split(r"[\s;<>=!~\[(]", line.split(":", 1)[1].strip())[0]))
            elif not line:
                break
    return name, frozenset(requires)


class Owners:
    """Which member each clashing path of the environment came from."""

    def __init__(self) -> None:
        self.by_path: dict[Path, Path] = {}
        self.distributions: dict[Path, tuple[str, frozenset[str]]] = {}

    def distribution(self, member: Path) -> tuple[str, frozenset[str]]:
        if member not in self.distributions:
            self.distributions[member] = distribution_of(member)
        return self.distributions[member]

    def take(self, target: Path, member: Path) -> bool:
        """Whether `member` wins `target` over the member that holds it now."""
        holder = self.by_path.get(target)
        if holder is None:
            raise RuntimeError(f"{member} provides {target.name}, which the environment itself holds")
        new_name, new_requires = self.distribution(member)
        old_name, old_requires = self.distribution(holder)
        wins = old_name in new_requires and new_name not in old_requires
        kept = member if wins else holder
        print(
            f"warning: {new_name or member} and {old_name or holder} both provide {target.name}; keeping {kept}",
            file=sys.stderr,
        )
        if wins:
            self.by_path[target] = member
        return wins


def link_tree(source: Path, target: Path, member: Path, owners: Owners) -> None:
    """Symlink one entry of a member into the environment.

    Two entries with the same content are not a clash: a package set that
    ships one file twice is common. Two files that differ go to one member, by
    `Owners.take`. **Two directories merge**, as a namespace package needs,
    and as the `__pycache__` of two single-module distributions do: the link
    becomes a directory of links, and each entry is linked on its own.
    """
    if not (target.exists() or target.is_symlink()):
        owners.by_path[target] = member
        target.symlink_to(source)
        return
    if target.is_dir() and source.is_dir():
        if target.is_symlink():
            if target.resolve() == source.resolve():
                return
            held = Path(os.readlink(target))
            holder = owners.by_path[target]
            target.unlink()
            target.mkdir()
            for entry in sorted(held.iterdir()):
                owners.by_path[target / entry.name] = holder
                (target / entry.name).symlink_to(entry)
        for entry in sorted(source.iterdir()):
            link_tree(entry, target / entry.name, member, owners)
        return
    if target.is_file() and source.is_file():
        if filecmp.cmp(target, source, shallow=False):
            return
        if owners.take(target, member):
            target.unlink()
            target.symlink_to(source)
        return
    raise RuntimeError(f"one member provides '{target.name}' as a file and another as a directory: {source}")


def is_python_shebang(line: bytes) -> bool:
    """Whether `line` runs a Python.

    The test is on the name of the interpreter, and not on its whole path.
    `installer` writes the shebang from `sys.executable`, and CPython reports
    that after it resolves the symlink, so an install through `python3` gives a
    shebang that names `python3.14`. A comparison against the path that the
    derivation used would miss it.
    """
    if not line.startswith(b"#!"):
        return False
    words = line[2:].strip().split()
    if not words:
        return False
    return Path(os.fsdecode(words[0])).name.startswith("python")


def link_script(source: Path, target: Path, venv_python: bytes) -> None:
    """Put one entry of `bin` into the environment, and correct its shebang.

    A console script that a wheel carries has the shebang of the interpreter
    that installed it. That interpreter is the one in the store, and it does
    not see this environment. The rewrite is what makes `bin/idna` find `idna`.
    """
    if source.is_symlink():
        shutil.copy(source, target, follow_symlinks=False)
        return

    with source.open("rb") as handle:
        first = handle.readline()
        if is_python_shebang(first):
            with target.open("wb") as out:
                out.write(b"#!" + venv_python + b"\n")
                shutil.copyfileobj(handle, out)
            # The binary distribution format: a Unix installer adds +x to a
            # script that a Windows build wrote, and `installer` does not.
            target.chmod(source.stat().st_mode | 0o111)
            return

    target.symlink_to(source)


def compose(out: Path, members: list[Path]) -> Path:
    """Make the environment at `out` from `members`, and give its `site-packages`."""
    # `EnvBuilder` takes the interpreter that runs this script as the base of
    # the environment, which is the interpreter that `graph.nix` names.
    EnvBuilder(symlinks=True, with_pip=False).create(out)

    site = site_packages_of(out)
    site.mkdir(parents=True, exist_ok=True)
    binary = out / "bin"
    binary.mkdir(parents=True, exist_ok=True)

    venv_python = os.fsencode(binary / "python")
    owners = Owners()

    for member in members:
        for entry in sorted(member.iterdir()):
            if entry.name not in {"bin", "nix-support"}:
                link_tree(entry, out / entry.name, member, owners)

        member_bin = member / "bin"
        if member_bin.is_dir():
            for entry in sorted(member_bin.iterdir()):
                target = binary / entry.name
                if target.exists() or target.is_symlink():
                    if not owners.take(target, member):
                        continue
                    target.unlink()
                else:
                    owners.by_path[target] = member
                link_script(entry, target, venv_python)
    return site


def main() -> int:
    if len(sys.argv) > 1:
        out, sites = Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else ""
        compose(out, [Path(site).parents[2] for site in sites.split(":") if site])
        return 0

    out = Path(os.environ["out"])
    members = [Path(member) for member in os.environ["members"].split()]
    site = compose(out, members)
    (out / "manifest").write_text(os.environ["installed"] + "\n", encoding="utf-8")

    print(f"{len(members)} members, {len(list(site.iterdir()))} entries in site-packages", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
