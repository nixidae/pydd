"""The `dependencies` override: edges that a package adds and drops in the walk."""

from __future__ import annotations

import pytest
from test_script import LOCK, SCRIPT, target

from pydd.python import EdgeEdit, LockError, edits_from
from pydd.python._script import ScriptProject
from pydd.python._uvlock import added_pins


def names(edits: dict[str, EdgeEdit]) -> list[str]:
    roots, _ = ScriptProject.from_json({"lock": str(LOCK), "source": str(SCRIPT)}, edits).selection(target())
    return sorted(root.split("==")[0] for root in roots)


def test_no_edit_walks_the_lock_as_written() -> None:
    assert names({}) == ["markdown-it-py", "mdurl", "pygments", "rich"]


def test_a_drop_removes_the_edge_and_what_only_it_reached() -> None:
    assert names(edits_from({"dependencies": {"rich": {"drop": ["Markdown_It_Py"]}}})) == ["pygments", "rich"]


def test_an_add_reaches_a_package_of_the_lock() -> None:
    edits = edits_from({"dependencies": {"rich": {"drop": ["markdown-it-py"], "add": ["mdurl"]}}})
    assert names(edits) == ["mdurl", "pygments", "rich"]


def test_a_drop_of_no_edge_fails_by_name() -> None:
    with pytest.raises(LockError, match=r"drops \['httpx'\]"):
        names(edits_from({"dependencies": {"rich": {"drop": ["httpx"]}}}))


def test_an_add_outside_the_lock_fails_by_name() -> None:
    with pytest.raises(LockError, match=r"adds 'httpx', and the lock"):
        names(edits_from({"dependencies": {"rich": {"add": ["httpx"]}}}))


def test_an_edit_of_a_package_outside_the_lock_fails() -> None:
    with pytest.raises(LockError, match=r"names \['numpy'\]"):
        names(edits_from({"dependencies": {"numpy": {"drop": ["x"]}}}))


@pytest.mark.parametrize("text", ["mdurl>=1", "mdurl; sys_platform == 'linux'"])
def test_an_add_takes_a_name_and_extras_only(text: str) -> None:
    with pytest.raises(ValueError, match=r"a name\s+with extras"):
        edits_from({"dependencies": {"rich": {"add": [text]}}})


def test_an_unknown_key_fails() -> None:
    with pytest.raises(ValueError, match=r"\['remove'\]"):
        edits_from({"dependencies": {"rich": {"remove": ["x"]}}})


PINS = {"rich": "rich==15.0.0", "mdurl": "mdurl==0.1.2", "pygments": "Pygments==2.19.1"}


def test_a_flat_add_pins_the_name_once_its_package_is_selected() -> None:
    edits = edits_from({"dependencies": {"rich": {"add": ["mdurl", "pygments"]}}})
    assert added_pins(edits, ["rich", "pygments"], PINS, "pylock.toml") == ["mdurl==0.1.2"]
    assert added_pins(edits, ["pygments"], PINS, "pylock.toml") == []


def test_a_flat_drop_fails_by_name() -> None:
    with pytest.raises(LockError, match=r"'rich' drops \['pygments'\], and poetry.lock"):
        added_pins(edits_from({"dependencies": {"rich": {"drop": ["pygments"]}}}), ["rich"], PINS, "poetry.lock")


def test_a_flat_add_outside_the_lock_fails_by_name() -> None:
    with pytest.raises(LockError, match=r"adds 'httpx'"):
        added_pins(edits_from({"dependencies": {"rich": {"add": ["httpx"]}}}), ["rich"], PINS, "pylock.toml")
