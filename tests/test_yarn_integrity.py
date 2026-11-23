"""`pydd-yarn-integrity`: the npm hashes of a Yarn 2+ lock, from the registry's metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pydd.node import yarn_integrity
from pydd.node.yarn_integrity import locked, record, render, update

PROJECT = Path(__file__).parent / "node" / "berry-hello"
REGISTRY = "https://registry.npmjs.org"


def metadata(version: str, **data: Any) -> dict[str, Any]:
    return {"versions": {version: data}}


def test_a_version_takes_the_tarball_integrity() -> None:
    dist = {"integrity": "sha512-AAAA", "tarball": f"{REGISTRY}/ms/-/ms-2.1.3.tgz"}
    assert record("ms", "2.1.3", metadata("2.1.3", dist=dist), REGISTRY) == {"integrity": "sha512-AAAA"}


def test_an_old_version_has_only_sha1() -> None:
    dist = {"shasum": "00ff", "tarball": f"{REGISTRY}/ms/-/ms-0.1.0.tgz"}
    assert record("ms", "0.1.0", metadata("0.1.0", dist=dist), REGISTRY) == {"integrity": "sha1-AP8="}


def test_a_script_and_an_unusual_tarball_are_kept() -> None:
    dist = {"integrity": "sha512-AAAA", "tarball": "https://example.org/x.tgz"}
    entry = record("x", "1.0.0", metadata("1.0.0", dist=dist, hasInstallScript=True), REGISTRY)
    assert entry == {"integrity": "sha512-AAAA", "tarball": "https://example.org/x.tgz", "scripts": True}


def test_a_version_the_registry_lacks_is_refused() -> None:
    with pytest.raises(SystemExit, match=r"no ms@9\.9\.9"):
        record("ms", "9.9.9", metadata("2.1.3"), REGISTRY)


def test_the_lock_names_each_registry_version() -> None:
    wanted = locked((PROJECT / "yarn.lock").read_text())
    assert "semver@npm:7.8.5" in wanted
    assert all("@npm:" in key for key in wanted)
    assert len(wanted) == 8


def test_an_update_fetches_only_what_is_new(monkeypatch: pytest.MonkeyPatch) -> None:
    known = json.loads((PROJECT / "yarn-integrity.json").read_text())
    dropped = known.pop("semver@npm:7.8.5")
    known["gone@npm:1.0.0"] = {"integrity": "sha512-AAAA"}
    asked: list[str] = []

    def fetch(name: str, registry: str) -> dict[str, Any]:
        asked.append(name)
        return metadata("7.8.5", dist={"integrity": dropped["integrity"]})

    monkeypatch.setattr(yarn_integrity, "fetch", fetch)
    records, fetched = update((PROJECT / "yarn.lock").read_text(), known, REGISTRY, 2)
    assert asked == ["semver"]
    assert fetched == 1
    assert render(records) == (PROJECT / "yarn-integrity.json").read_text()
