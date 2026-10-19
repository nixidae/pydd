"""Give each ELF file of a build output the RUNPATH that its DT_NEEDED entries need.

`graph.nix` runs this at the end of a node that builds with build tools
(nixidae/nixidae#785). A build of C code links what its build tools offer, but
how a backend calls the linker decides which directories reach the RUNPATH.
pyzmq links `libsodium.so.26` through the link interface of zeromq, and its
RUNPATH then names zeromq and not libsodium.

**The closure comes from the ELF files themselves.** A name that the RUNPATH
of the file does not resolve is searched for in the `lib` directory of each
build tool, and then in the RUNPATH of each library found there, and so on.
libzmq names libsodium in its own RUNPATH, so no build tool has to name it.

The script only adds a directory that is missing. A file whose entries all
resolve keeps its bytes, so a build that was correct keeps its store path.

    fixupPaths  the `lib` directories of the build tools, separated by `:`
    patchelf    the `patchelf` program
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"


def patchelf(*args: str) -> str:
    return subprocess.run([os.environ["patchelf"], *args], check=True, capture_output=True, text=True).stdout.strip()


def is_elf(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(4) == ELF_MAGIC


def runpath(path: Path) -> list[str]:
    return [entry for entry in patchelf("--print-rpath", str(path)).split(":") if entry]


def needed(path: Path) -> list[str]:
    try:
        return patchelf("--print-needed", str(path)).split()
    except subprocess.CalledProcessError:
        # A static archive member or an object file has no dynamic section.
        return []


class Search:
    """The directories that a name may be found in, widened through RUNPATHs."""

    def __init__(self, roots: list[str]) -> None:
        self.order: list[str] = []
        self.pending = list(roots)

    def find(self, name: str) -> str | None:
        for directory in self.order:
            if (Path(directory) / name).exists():
                return directory
        while self.pending:
            directory = self.pending.pop(0)
            if directory in self.order or not Path(directory).is_dir():
                continue
            self.order.append(directory)
            for library in Path(directory).glob("*.so*"):
                if is_elf(library):
                    self.pending.extend(runpath(library))
            if (Path(directory) / name).exists():
                return directory
        return None


def fix(path: Path, search: Search) -> list[str]:
    """Add what `path` misses to its RUNPATH, and name what stays missing."""
    current = runpath(path)
    missing: list[str] = []
    for name in needed(path):
        if any((Path(directory) / name).exists() for directory in current):
            continue
        found = search.find(name)
        if found is None:
            missing.append(name)
            continue
        patchelf("--add-rpath", found, str(path))
        current.append(found)
        print(f"fixup: {path}: {name} from {found}", file=sys.stderr)
    return missing


def main() -> int:
    out = Path(sys.argv[1])
    search = Search([entry for entry in os.environ.get("fixupPaths", "").split(":") if entry])
    for path in sorted(out.rglob("*")):
        if is_elf(path):
            for name in fix(path, search):
                # The interpreter provides some names, such as libpython of a
                # static build, so an unresolved name is reported and not fatal.
                print(f"fixup: {path}: {name} resolves nowhere", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
