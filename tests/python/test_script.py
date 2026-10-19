"""The PEP 723 reader, against `projects/script-app`."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydd.python import LockError, Target, target_tags
from pydd.python._script import ScriptProject, script_metadata

APP = Path(__file__).resolve().parents[1] / "projects/script-app"
SCRIPT = APP / "hello.py"
LOCK = APP / "hello.py.lock"

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


def test_the_block_is_read() -> None:
    assert script_metadata(SCRIPT.read_text()) == {"requires-python": ">=3.13", "dependencies": ["rich>=15.0.0"]}


def test_a_script_without_a_block_declares_nothing() -> None:
    assert script_metadata("print('hi')\n") == {}


def test_the_lock_pins_the_closure_of_the_block() -> None:
    roots, local = ScriptProject.from_json({"lock": str(LOCK), "source": str(SCRIPT)}).selection(target())
    assert sorted(root.split("==")[0] for root in roots) == ["markdown-it-py", "mdurl", "pygments", "rich"]
    assert local == []


def test_a_block_that_outgrew_its_lock_is_refused(tmp_path: Path) -> None:
    script = tmp_path / "hello.py"
    script.write_text(SCRIPT.read_text().replace('["rich>=15.0.0"]', '["rich>=15.0.0", "httpx"]'))
    with pytest.raises(LockError, match=r"uv lock --script"):
        ScriptProject.from_json({"lock": str(LOCK), "source": str(script)})


def test_a_target_outside_requires_python_is_refused() -> None:
    project = ScriptProject.from_json({"lock": str(LOCK), "source": str(SCRIPT)})
    with pytest.raises(LockError):
        project.selection(target(python_full_version="3.12.0", python_version="3.12"))
