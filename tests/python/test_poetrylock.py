"""The `poetry.lock` reader, against the lock that Poetry 2 wrote for the example."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement

from pydd.python import LockError, Target, target_tags
from pydd.python._poetrylock import PoetryProject

LOCK = Path(__file__).resolve().parents[2] / "examples/poetry/fixtures/myapp/poetry.lock"

MARKERS = {
    "implementation_name": "cpython",
    "implementation_version": "3.14.0",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_system": "Linux",
    "platform_version": "",
    "python_full_version": "3.14.0",
    "python_version": "3.14",
    "sys_platform": "linux",
    "extra": "",
}


def target(**markers: str) -> Target:
    return Target(
        tags=tuple(target_tags("3.14", "linux_x86_64", (), "glibc", "2.44")),
        markers={**MARKERS, **markers},
        python_version="3.14",
        platform="linux_x86_64",
    )


def project(**options: Any) -> PoetryProject:
    with LOCK.open("rb") as handle:
        return PoetryProject(lock=tomllib.load(handle), **options)


def names(roots: list[str]) -> set[str]:
    return {root.split("==")[0] for root in roots}


class TestSelection:
    def test_the_main_group_is_the_default(self) -> None:
        roots, local = project().selection(target())
        assert names(roots) == {"certifi", "charset-normalizer", "idna", "ninja", "pysocks", "requests", "urllib3"}
        assert local == []

    def test_a_marker_excludes_a_package(self) -> None:
        assert "colorama" not in names(project().selection(target())[0])
        assert "colorama" in names(project().selection(target(sys_platform="win32"))[0])

    def test_a_group_is_installed_only_when_asked(self) -> None:
        assert "installer" not in names(project().selection(target())[0])
        assert names(project(groups=["build"]).selection(target())[0]) == {"flit-core", "installer"}

    def test_each_root_is_an_exact_pin(self) -> None:
        roots, _ = project().selection(target())
        assert all("==" in root for root in roots)


class TestBackend:
    def test_a_backend_selects_its_pin(self) -> None:
        pins = project().backend([Requirement("flit_core >=3.11,<5")], target(), "myapp")
        assert [str(pin) for pin in pins] == ["flit-core==4.1.0"]

    def test_a_backend_that_the_lock_does_not_hold_names_the_fix(self) -> None:
        with pytest.raises(RuntimeError, match="poetry add --group build hatchling"):
            project().backend([Requirement("hatchling")], target(), "myapp")

    def test_the_dependencies_of_a_backend_come_along(self) -> None:
        pins = project().backend([Requirement("requests")], target(), "x")
        assert {pin.name for pin in pins} == {"requests", "certifi", "charset-normalizer", "idna", "urllib3"}


class TestRefusals:
    def test_an_old_lock_version_is_refused(self) -> None:
        old = PoetryProject(lock={"metadata": {"lock-version": "2.0"}, "package": []})
        with pytest.raises(LockError, match=r"lock-version 2\.0"):
            old.selection(target())

    def test_a_source_other_than_pypi_is_refused(self) -> None:
        lock = {
            "metadata": {"lock-version": "2.1"},
            "package": [{"name": "x", "version": "1", "groups": ["main"], "source": {"type": "git"}}],
        }
        with pytest.raises(LockError, match="type 'git'"):
            PoetryProject(lock=lock).selection(target())
