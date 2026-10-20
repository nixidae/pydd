"""A recursive-nix planner builds again after a GC deletes its output.

Nix keeps the realisation of a deleted content-addressed output, and builds
the planner again with `$out` at that old path, which is the root's own
`.drv`. Before `RecursiveWriter` held such a root back, the build failed, and
its cleanup left the `.drv` valid in the database with an empty file.

Builds the planner, deletes its output, builds it again, and checks that the
second build gives the same path with the same bytes. It needs the host's
daemon, with recursive-nix: no sandbox has a store that keeps realisations.

    nix run --file . test-planner-after-gc [-- ATTRIBUTE]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ATTRIBUTE = "rust.recursive.serde-app.root.planner"


def build(attribute: str) -> Path:
    result = subprocess.run(
        ["nix", "build", "--file", ".", attribute, "--no-link", "--print-out-paths", "-L"],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def main() -> int:
    attribute = sys.argv[1] if len(sys.argv) > 1 else ATTRIBUTE
    first = build(attribute)
    if not first.name.endswith(".drv"):
        raise SystemExit(f"{attribute} gave {first}, which is not a planner's .drv")
    text = first.read_bytes()
    if not text:
        raise SystemExit(f"{first} is empty: a store path that an earlier failure left. `nix store delete` it.")
    subprocess.run(["nix", "store", "delete", str(first)], check=True)
    second = build(attribute)
    if second != first or second.read_bytes() != text:
        raise SystemExit(f"after the delete, {attribute} gave {second}, and before it {first}")
    print(f"ok: {second}, built again after a delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
