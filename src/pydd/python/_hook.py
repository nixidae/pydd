"""Ask a build backend what more it needs, through PEP 517's `get_requires_for_build_*`.

**The answer exists only once the backend is built**, so the planner that asks
runs after the backend environment, and takes that environment as an input
(nixidae/nixidae#847). It runs the target's interpreter, as the build node
does, in the unpacked project, with the backend's sites on `PYTHONPATH`.
"""

from __future__ import annotations

import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from packaging.requirements import Requirement

#: Run under the target's interpreter, which may hold no `packaging`, so the
#: answer travels back as JSON text and is parsed here. `backend-path` goes
#: first, as `scripts/build-sdist.sh` puts it.
HOOK = """
import importlib, json, sys, tomllib
from pathlib import Path

hook, name, settings = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
declaration = Path("pyproject.toml")
table = tomllib.loads(declaration.read_text()) if declaration.exists() else {}
for entry in reversed(table.get("build-system", {}).get("backend-path", [])):
    path = (Path.cwd() / entry).resolve()
    if not path.is_relative_to(Path.cwd()):
        raise RuntimeError(f"backend-path names {entry!r}, which leaves the project")
    sys.path.insert(0, str(path))
module, _, attribute = name.partition(":")
backend = importlib.import_module(module)
if attribute:
    backend = getattr(backend, attribute)
function = getattr(backend, "get_requires_for_build_" + hook, None)
print("\\n" + json.dumps(list(function(settings)) if function else []))
"""


def unpack(archive: str, into: Path) -> Path:
    """Unpack a source distribution, and return its one top directory."""
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(into)
    else:
        with tarfile.open(archive) as handle:
            handle.extractall(into, filter="data")
    tops = list(into.iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise RuntimeError(f"{archive} holds {len(tops)} entries at its top, and an sdist holds one directory")
    return tops[0]


def requires_for_build(
    *,
    hook: str,
    python: str,
    archive: str,
    subdirectory: str,
    backend: str,
    sites: list[str],
    config_settings: str | None,
    path: str = "",
    env: dict[str, str] | None = None,
) -> list[Requirement]:
    """What `get_requires_for_build_<hook>` of `backend` returns for this archive.

    A backend that defines no such hook needs nothing more, which PEP 517 says
    is the same as an empty list.
    """
    with tempfile.TemporaryDirectory() as scratch:
        project = unpack(archive, Path(scratch) / "src") / subdirectory
        result = subprocess.run(
            [python, "-c", HOOK, hook, backend, config_settings or "null"],
            cwd=project,
            env=(env or {}) | {"PYTHONPATH": ":".join(sites), "PATH": path, "HOME": scratch, "TMPDIR": scratch},
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"{Path(archive).name}: get_requires_for_build_{hook} of {backend} failed\n{result.stderr}")
    return [Requirement(text) for text in json.loads(result.stdout.splitlines()[-1])]
