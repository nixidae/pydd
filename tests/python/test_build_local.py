"""`scripts/build-local.py`: the redirect of an editable install."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "src/pydd/python/scripts/build-local.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_local", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FINDER = """\
MAPPING: dict[str, str] = {'demo': '/build/project/src/demo'}
NAMESPACES: dict[str, list[str]] = {}
PATH_PLACEHOLDER = '__editable__.demo-0.1.0.finder' + ".__path_hook__"
"""


def test_a_setuptools_finder_reads_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    text, patched = load().patch_finder(FINDER, "/build/project", "$DEMO_ROOT")
    assert patched
    assert "/build/project" not in text
    monkeypatch.setenv("DEMO_ROOT", "/home/me/demo")
    namespace: dict[str, object] = {}
    exec(compile(text, "finder", "exec"), namespace)
    assert namespace["MAPPING"] == {"demo": "/home/me/demo/src/demo"}
    assert namespace["PATH_PLACEHOLDER"] == "__editable__.demo-0.1.0.finder.__path_hook__"


def test_a_finder_that_names_nothing_is_left_alone() -> None:
    text, patched = load().patch_finder(FINDER, "/elsewhere", "$DEMO_ROOT")
    assert not patched
    assert text == FINDER


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def _editable_wheel(path: Path, build_dir: str) -> None:
    """A wheel as a PEP 660 backend writes it: a finder and a `.pth` that name *build_dir*."""
    files = {
        "__editable___demo_0_1_0_finder.py": FINDER.replace("/build/project", build_dir).encode(),
        "__editable__.demo-0.1.0.pth": f"{build_dir}/src\n".encode(),
        "demo-0.1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: demo\nVersion: 0.1.0\n",
    }
    record = [_record_line(name, data) for name, data in files.items()]
    record.append("demo-0.1.0.dist-info/RECORD,,")
    with zipfile.ZipFile(path, "w") as wheel:
        for name, data in files.items():
            wheel.writestr(name, data)
        wheel.writestr("demo-0.1.0.dist-info/RECORD", "\n".join(record) + "\n")


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as wheel:
        return {info.filename: wheel.read(info) for info in wheel.infolist()}


def test_an_editable_wheel_does_not_depend_on_where_it_was_built(tmp_path: Path) -> None:
    """The installer compiles and hashes what the wheel holds, so the redirect happens before it runs.

    Redirected after the install, the `.pyc` and RECORD kept the build
    directory. Outside a chroot that directory is new for each build
    (`/nix/var/nix/builds/nix-72550-2236953363` in pynixd on sg1), so one
    derivation built to different bytes each time.
    """
    sandbox, elsewhere = tmp_path / "sandbox.whl", tmp_path / "elsewhere.whl"
    random_dir = "/nix/var/nix/builds/nix-72550-2236953363/project"
    _editable_wheel(sandbox, "/build/project")
    _editable_wheel(elsewhere, random_dir)

    assert load().redirect_wheel(sandbox, "/build/project", "$DEMO_ROOT") == 2
    assert load().redirect_wheel(elsewhere, random_dir, "$DEMO_ROOT") == 2

    members = _members(sandbox)
    assert members == _members(elsewhere)
    assert all(b"/build/project" not in data for data in members.values())
    record = members.pop("demo-0.1.0.dist-info/RECORD").decode().splitlines()
    expected = [*(_record_line(name, data) for name, data in members.items()), "demo-0.1.0.dist-info/RECORD,,"]
    assert sorted(record) == sorted(expected)
