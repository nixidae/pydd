"""The `pylock.toml` reader, against the export of `projects/workspace` and hand-written locks."""

from __future__ import annotations

from pathlib import Path

import pytest
from packaging.requirements import Requirement

from pydd.python import LockError, Target, target_tags
from pydd.python._pylock import PylockProject

WORKSPACE = Path(__file__).resolve().parents[1] / "projects/workspace/pylock.toml"

MARKERS = {
    "implementation_name": "cpython",
    "implementation_version": "3.13.0",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_system": "Linux",
    "platform_version": "",
    "python_full_version": "3.13.0",
    "python_version": "3.13",
    "sys_platform": "linux",
}


def target(**markers: str) -> Target:
    return Target(
        tags=tuple(target_tags("3.13", "linux_x86_64", (), "glibc", "2.44")),
        markers={**MARKERS, **markers},
        python_version="3.13",
        platform="linux_x86_64",
    )


def written(tmp_path: Path, packages: str, head: str = "") -> PylockProject:
    lock = tmp_path / "pylock.toml"
    lock.write_text(f'lock-version = "1.0"\ncreated-by = "test"\n{head}\n{packages}')
    return PylockProject.from_json({"lock": str(lock)})


WHEEL = 'wheels = [{ url = "https://example.invalid/{0}-1.0-py3-none-any.whl", hashes = { sha256 = "00" } }]'


def package(name: str, extra: str = "") -> str:
    return f'[[packages]]\nname = "{name}"\nversion = "1.0"\n{extra}\n{WHEEL.replace("{0}", name)}\n'


class TestSelection:
    def test_the_export_pins_every_package_and_leaves_the_members_to_the_seam(self) -> None:
        roots, local = PylockProject.from_json({"lock": str(WORKSPACE)}).selection(target())
        assert sorted(roots) == ["markdown-it-py==4.2.0", "mdurl==0.1.2", "pygments==2.20.0", "rich==15.0.0"]
        assert local == []

    def test_a_marker_excludes_a_package(self, tmp_path: Path) -> None:
        lock = written(tmp_path, package("colorama", "marker = \"sys_platform == 'win32'\"") + package("rich"))
        assert lock.selection(target())[0] == ["rich==1.0"]
        assert sorted(lock.selection(target(sys_platform="win32"))[0]) == ["colorama==1.0", "rich==1.0"]

    def test_a_marker_reads_the_default_groups(self, tmp_path: Path) -> None:
        lock = written(
            tmp_path,
            package("pytest", "marker = \"'dev' in dependency_groups\"")
            + package("ruff", "marker = \"'lint' in dependency_groups\""),
            head='default-groups = ["dev"]',
        )
        assert lock.selection(target())[0] == ["pytest==1.0"]

    def test_a_target_outside_requires_python_is_refused(self, tmp_path: Path) -> None:
        lock = written(tmp_path, package("rich"), head='requires-python = ">=3.14"')
        with pytest.raises(LockError, match=r"requires Python >=3\.14"):
            lock.selection(target())

    def test_an_unsupported_version_is_refused(self, tmp_path: Path) -> None:
        lock = tmp_path / "pylock.toml"
        lock.write_text(f'lock-version = "2.0"\ncreated-by = "test"\n{package("rich")}')
        with pytest.raises(LockError, match=r"version 2\.0 is not supported"):
            PylockProject.from_json({"lock": str(lock)})


def test_a_backend_falls_back_to_pydds_lock() -> None:
    with pytest.raises(LockError, match="no dependency edges"):
        PylockProject.from_json({"lock": str(WORKSPACE)}).backend([Requirement("hatchling")], target(), "ws-core")
