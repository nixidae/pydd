"""Build one node of an npm graph: a fetch, a package or the root.

`PYDD_NPM` holds the node's spec, which `pydd.node._npm` writes. Standard
library only: the node runs on the Python of the planner's menu.
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

LIFECYCLE = ("preinstall", "install", "postinstall")


def fetch(spec: dict[str, Any], out: Path) -> None:
    with urllib.request.urlopen(spec["url"], timeout=300) as response, out.open("wb") as file:
        shutil.copyfileobj(response, file)


def unpack(tarball: str, destination: Path) -> None:
    """Unpack a package as npm does: without its first directory, whatever its name.

    A directory, such as a replaced source, is copied.
    """
    if Path(tarball).is_dir():
        shutil.copytree(tarball, destination, symlinks=True)
        writable(destination)
        return
    # exist_ok: place() already makes this directory as the parent of the
    # packages the lock nests under this one. A file here still fails.
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as archive:
        members = []
        for member in archive.getmembers():
            _, _, rest = member.name.partition("/")
            if not rest or member.isdev():
                continue
            member.name = rest
            if member.isdir():
                member.mode = 0o755
            else:
                member.mode = 0o755 if member.mode & 0o111 else 0o644
            members.append(member)
        archive.extractall(destination, members=members, filter="data")


def writable(root: Path) -> None:
    for directory, _, files in os.walk(root):
        os.chmod(directory, 0o755)
        for file in files:
            path = Path(directory, file)
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IWUSR)


def place(tree: Path, nodes: dict[str, str], links: dict[str, str]) -> None:
    """Copy each node to its path in `tree`, parents first, and link each program into `.bin`."""
    for path in sorted(nodes, key=lambda path: path.count("/")):
        shutil.copytree(nodes[path], tree / path, symlinks=True, dirs_exist_ok=True)
        writable(tree / path)
    for link, target in links.items():
        (tree / link).parent.mkdir(parents=True, exist_ok=True)
        (tree / link).unlink(missing_ok=True)
        (tree / link).symlink_to(target)


def patch_shebang(file: Path) -> None:
    """Point `#!/usr/bin/env node` and its kind at a program on `PATH`, as stdenv's patchShebangs does."""
    with file.open("rb") as handle:
        first = handle.readline()
    if not first.startswith(b"#!"):
        return
    words = first[2:].decode().split()
    if not words:
        return
    if posixpath.basename(words[0]) == "env":
        words = [word for word in words[1:] if not word.startswith("-")]
    if not words or (found := shutil.which(posixpath.basename(words[0]))) is None:
        return
    rest = file.read_bytes()[len(first) :]
    file.write_bytes(f"#!{' '.join([found, *words[1:]])}\n".encode() + rest)


def programs(package: Path, bins: dict[str, str]) -> None:
    for file in bins.values():
        target = (package / file).resolve()
        if not target.is_file():
            continue
        target.chmod(target.stat().st_mode | 0o111)
        patch_shebang(target)


def manifest_bins(package: Path) -> dict[str, str]:
    """The programs that a package's own `package.json` names, by program name."""
    data = json.loads((package / "package.json").read_text())
    match data.get("bin"):
        case str(file):
            return {data["name"].rsplit("/", 1)[-1]: file}
        case dict(bins):
            return bins
        case _:
            return {}


def probe(tree: Path, probes: list[list[str]]) -> dict[str, str]:
    """The `.bin` links of packages whose programs the lock does not name, read from each package."""
    links = {}
    for directory, path in probes:
        for program, file in manifest_bins(tree / path).items():
            links[f"{directory}/{posixpath.basename(program)}"] = posixpath.relpath(
                posixpath.join(path, file), directory
            )
    return links


def node_gyp(top: Path, nodejs: str) -> Path:
    """A `node-gyp` on `PATH`, as npm gives one to a script."""
    directory = top / "node-gyp-bin"
    directory.mkdir(exist_ok=True)
    script = f"{nodejs}/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js"
    wrapper = directory / "node-gyp"
    wrapper.write_text(f'#!{shutil.which("bash")}\nexec {nodejs}/bin/node {script} "$@"\n')
    wrapper.chmod(0o755)
    return directory


def script_env(top: Path, where: Path, tree: Path, nodejs: str, name: str, version: str) -> dict[str, str]:
    """What npm's run-script gives a lifecycle script, without a network."""
    bins = []
    directory = where
    while True:
        if directory.name != "node_modules":
            bins.append(str(directory / "node_modules" / ".bin"))
        if directory == tree:
            break
        directory = directory.parent
    home = top / "home"
    home.mkdir(exist_ok=True)
    return os.environ | {
        "PATH": ":".join([*bins, str(node_gyp(top, nodejs)), os.environ["PATH"]]),
        "HOME": str(home),
        "INIT_CWD": str(where),
        "npm_package_name": name,
        "npm_package_version": version,
        "npm_package_json": str(where / "package.json"),
        "npm_node_execpath": f"{nodejs}/bin/node",
        "npm_config_node_gyp": f"{nodejs}/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js",
        # node-gyp reads Node's headers from here, and downloads none.
        "npm_config_nodedir": nodejs,
        # prebuild-install and node-pre-gyp download a binary unless told.
        "npm_config_build_from_source": "true",
        # node-gyp runs make with this many jobs. Nix's 0 means every core.
        "npm_config_jobs": {"0": "max", "": "1"}.get(cores := os.environ.get("NIX_BUILD_CORES", ""), cores),
        "npm_config_offline": "true",
        "npm_config_update_notifier": "false",
        "npm_config_cache": str(top / "npm-cache"),
    }


def run_lifecycle(top: Path, where: Path, tree: Path, nodejs: str, name: str, version: str) -> None:
    manifest = json.loads((where / "package.json").read_text())
    scripts = dict(manifest.get("scripts", {}))
    # npm's default: a package with a binding.gyp and no install script runs node-gyp.
    if "install" not in scripts and "preinstall" not in scripts and (where / "binding.gyp").exists():
        scripts["install"] = "node-gyp rebuild"
    env = script_env(top, where, tree, nodejs, name, version)
    for event in LIFECYCLE:
        if event not in scripts:
            continue
        print(f"pydd: {name}: {event}: {scripts[event]}", file=sys.stderr, flush=True)
        subprocess.run(
            [shutil.which("bash", path=env["PATH"]) or "bash", "-c", scripts[event]],
            cwd=where,
            env=env | {"npm_lifecycle_event": event, "npm_lifecycle_script": scripts[event]},
            check=True,
        )


def refuse_prepare(package: Path, name: str) -> None:
    """npm builds a git dependency with its `prepare` script and its development dependencies, which the lock omits."""
    scripts = json.loads((package / "package.json").read_text()).get("scripts", {})
    if "prepare" in scripts:
        raise SystemExit(
            f"pydd: {name} comes from git and has a prepare script, which needs packages that "
            "the lock does not name. Give the built package as `overrides.<name>.src`."
        )


def apply_patch(spec: dict[str, Any], package: Path) -> None:
    """Apply the patch of `pnpm patch`, a git diff with paths from the package's root."""
    if "patch" not in spec:
        return
    print(f"pydd: {spec['name']}: applying {posixpath.basename(spec['patch'])}", file=sys.stderr, flush=True)
    subprocess.run(["patch", "--strip=1", "--batch", "--forward", f"--input={spec['patch']}"], cwd=package, check=True)


def package(spec: dict[str, Any], top: Path, out: Path) -> None:
    nodejs = spec["nodejs"]
    if not spec["scripts"]:
        unpack(spec["tarball"], out)
        apply_patch(spec, out)
        if spec["git"]:
            refuse_prepare(out, spec["name"])
        programs(out, manifest_bins(out) if spec["bins"] is None else spec["bins"])
        return
    tree = top / "tree"
    tree.mkdir()
    place(tree, spec["tree"], {})
    where = tree / spec["path"]
    unpack(spec["tarball"], where)
    apply_patch(spec, where)
    if spec["git"]:
        refuse_prepare(where, spec["name"])
    place(tree, {}, spec["links"] | probe(tree, spec.get("probes", [])))
    run_lifecycle(top, where, tree, nodejs, spec["name"], spec["version"])
    for nested in spec["nested"]:
        shutil.rmtree(tree / nested)
    shutil.rmtree(where / "node_modules" / ".bin", ignore_errors=True)
    modules = where / "node_modules"
    if modules.is_dir() and not any(modules.iterdir()):
        modules.rmdir()
    shutil.copytree(where, out, symlinks=True)
    programs(out, manifest_bins(out) if spec["bins"] is None else spec["bins"])


def root(spec: dict[str, Any], top: Path, out: Path) -> None:
    nodejs = spec["nodejs"]
    # Not in `$out/lib/node_modules`: TypeScript, and the tools over it,
    # skip every file under a `node_modules`, the project's own included.
    tree = top / "project"
    shutil.copytree(spec["source"], tree, symlinks=True)
    writable(tree)
    place(tree, spec["tree"], spec["links"])
    build_bins = spec["build_bins"] | probe(tree, spec.get("build_probes", []))
    place(tree, {}, build_bins)
    env = script_env(top, tree, tree, nodejs, spec["name"], "")
    for script in spec["scripts"]:
        arguments = ["--if-present", *(["--workspaces", "--include-workspace-root"] if spec["workspaces"] else [])]
        print(f"pydd: npm run {script}", file=sys.stderr, flush=True)
        subprocess.run([f"{nodejs}/bin/npm", "run", script, *arguments], cwd=tree, env=env, check=True)
    for path in spec["omit"]:
        shutil.rmtree(tree / path, ignore_errors=True)
        # A scope, as `@esbuild`, and a nested `node_modules` can become empty.
        parent = (tree / path).parent
        while parent != tree and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    for link in build_bins:
        (tree / link).unlink(missing_ok=True)
        if (tree / link).parent.is_dir() and not any((tree / link).parent.iterdir()):
            (tree / link).parent.rmdir()
    place(tree, {}, spec["bins"] | probe(tree, spec.get("probes", [])))
    for program, file in spec["programs"].items():
        if not (tree / file).is_file():
            raise SystemExit(f"pydd: the program {program} names {file}, which the project does not hold.")
        (tree / file).chmod((tree / file).stat().st_mode | 0o111)
        patch_shebang(tree / file)
    installed = out / "lib" / "node_modules" / spec["name"]
    shutil.copytree(tree, installed, symlinks=True)
    bin_directory = out / "bin"
    for program, file in spec["programs"].items():
        bin_directory.mkdir(exist_ok=True)
        (bin_directory / program).symlink_to(posixpath.relpath(installed / file, bin_directory))


def main() -> None:
    if "PYDD_NPMPath" in os.environ:
        spec = json.loads(Path(os.environ["PYDD_NPMPath"]).read_text())  # noqa: SIM112 - passAsFile names it
    else:
        spec = json.loads(os.environ["PYDD_NPM"])
    out = Path(os.environ["out"])  # noqa: SIM112 - Nix names it `out`
    top = Path(os.environ.get("NIX_BUILD_TOP", os.getcwd()))
    match spec["action"]:
        case "fetch":
            fetch(spec, out)
        case "archive":
            fetch(spec, top / "archive")
            unpack(str(top / "archive"), out)
        case "package":
            package(spec, top, out)
        case "root":
            root(spec, top, out)


if __name__ == "__main__":
    main()
