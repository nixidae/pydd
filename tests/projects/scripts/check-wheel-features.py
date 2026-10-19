"""Report what an environment holds of each corner of the wheel format.

`check-wheel-features.nix` runs this with the `bin/python` of the environment.
`projects/wheel-features/make-index.py` names the corner each wheel carries.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import sysconfig
from importlib import metadata
from pathlib import Path

EXPECTED = {"datafull", "nsone", "nstwo", "pthful"}


def main() -> int:
    prefix = Path(sys.prefix)
    failures: list[str] = []

    found = {dist.name for dist in metadata.distributions()}
    print(f"distributions  {sorted(found)}")
    if found != EXPECTED:
        failures.append(f"the environment holds {sorted(found)}, and uv.lock resolves {sorted(EXPECTED)}")

    # PEP 420: two distributions, one namespace, no `__init__.py`.
    for module in ("nsjoint.one", "nsjoint.two"):
        try:
            print(f"import {module}  {importlib.import_module(module).NAME}")
        except ImportError as error:
            failures.append(f"import {module}: {error}")

    # `site` runs an `import` line of a `.pth` file at start.
    print(f"pth ran        {getattr(builtins, 'PTHFUL_RAN', False)}")
    if not getattr(builtins, "PTHFUL_RAN", False):
        failures.append("pthful.pth did not run at start")

    # `.data/data` goes to the prefix, `.data/headers` to the include directory
    # of the prefix, with the distribution's name.
    data = prefix / "share" / "datafull" / "greeting.txt"
    headers = prefix / "include" / f"python{sysconfig.get_python_version()}" / "datafull" / "datafull.h"
    for kind, path in (("data", data), ("headers", headers)):
        print(f"{kind:<14} {path} {'present' if path.is_file() else 'MISSING'}")
        if not path.is_file():
            failures.append(f".data/{kind} is not in the environment: {path}")

    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
