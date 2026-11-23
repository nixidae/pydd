"""Write the npm hashes of a Yarn 2+ lock, which the npm lane fetches with.

A Yarn 2+ lock holds the hash of Yarn's own zip of each package, not of the
tarball, so Nix cannot check a fetch against it. This command takes each
`name@npm:version` of `yarn.lock` from the registry's metadata: the tarball's
`integrity`, and whether the version has an install script. Run it after each
change of the lock; it fetches only the versions that the file lacks.

    pydd-yarn-integrity [PROJECT] [--output FILE]

stdout: one JSON object, the file and its counts.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydd.node._readers import REGISTRY, berry_ident, berry_patch, parse_berry, registry_url

#: The registry's short metadata: versions, `dist` and `hasInstallScript`.
ABBREVIATED = "application/vnd.npm.install-v1+json"

#: The default name of the file, beside `yarn.lock`.
FILE = "yarn-integrity.json"

#: An entry of the file: `integrity`, and `scripts` and `tarball` when they apply.
type Record = dict[str, Any]


def locked(text: str) -> set[str]:
    """Each `name@npm:version` that the lock installs, a patch's source included."""
    found: set[str] = set()
    for block in parse_berry(text).values():
        locator = block.get("resolution")
        if locator is None:
            continue
        name, reference = berry_ident(locator)
        if reference.startswith("patch:"):
            name, reference = berry_ident(berry_patch(name, reference)[0])
        if reference.startswith("npm:"):
            found.add(f"{name}@{reference}")
    return found


def record(name: str, version: str, metadata: dict[str, Any], registry: str) -> Record:
    """The file's entry for one version, from the registry's metadata of its package."""
    if version not in metadata.get("versions", {}):
        raise SystemExit(f"pydd-yarn-integrity: the registry has no {name}@{version}.")
    data = metadata["versions"][version]
    dist = data["dist"]
    integrity = dist.get("integrity") or "sha1-" + base64.b64encode(bytes.fromhex(dist["shasum"])).decode()
    entry: Record = {"integrity": integrity}
    if dist.get("tarball") and dist["tarball"] != registry_url(name, version, registry):
        entry["tarball"] = dist["tarball"]
    if data.get("hasInstallScript"):
        entry["scripts"] = True
    return entry


def fetch(name: str, registry: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{registry.rstrip('/')}/{quote(name, safe='@')}", headers={"Accept": ABBREVIATED})
    with urllib.request.urlopen(request, timeout=60) as response:
        data: dict[str, Any] = json.load(response)
        return data


def render(records: dict[str, Record]) -> str:
    """One line per version, sorted, so that a lock change gives a small diff."""
    lines = [f"{json.dumps(key)}: {json.dumps(records[key], sort_keys=True)}" for key in sorted(records)]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def update(lock: str, known: dict[str, Record], registry: str, jobs: int) -> tuple[dict[str, Record], int]:
    """The file for `lock`: the entries of `known` that it still needs, and the rest from the registry."""
    wanted = locked(lock)
    records = {key: known[key] for key in wanted if key in known}
    missing: dict[str, list[str]] = {}
    for key in sorted(wanted - records.keys()):
        name, reference = berry_ident(key)
        missing.setdefault(name, []).append(reference.removeprefix("npm:"))
    with ThreadPoolExecutor(jobs) as pool:
        for name, metadata in zip(missing, pool.map(lambda name: fetch(name, registry), missing), strict=True):
            for version in missing[name]:
                records[f"{name}@npm:{version}"] = record(name, version, metadata, registry)
    return records, sum(len(versions) for versions in missing.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pydd-yarn-integrity", description=__doc__.split("\n\n")[0])
    parser.add_argument("project", nargs="?", default=".", type=Path, help="the directory with yarn.lock")
    parser.add_argument("--output", type=Path, help=f"the file to write; default PROJECT/{FILE}")
    parser.add_argument("--registry", default=REGISTRY)
    parser.add_argument("--jobs", type=int, default=16, help="registry requests at a time")
    args = parser.parse_args(argv)
    output: Path = args.output or args.project / FILE
    known: dict[str, Record] = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {}
    records, fetched = update((args.project / "yarn.lock").read_text(encoding="utf-8"), known, args.registry, args.jobs)
    output.write_text(render(records), encoding="utf-8")
    json.dump({"file": str(output), "packages": len(records), "fetched": fetched}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
