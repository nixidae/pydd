# Python: the standards, and what pydd does with each

This page lists every PyPA specification that changes what pydd installs or
builds, and gives each one a status. It is the record for nixidae/nixidae#486
(uv) and nixidae/nixidae#488 (Poetry). `PACKAGING-FACTS.md` holds the
measurements; this page holds the inventory.

The source of each row is <https://packaging.python.org/en/latest/specifications/>,
read on 2026-10-19, and the code that the row names.

| status | meaning |
| --- | --- |
| covered | pydd implements it, and a test or fixture shows it |
| delegated | `packaging` or `pypa/installer`, the reference implementations, do it |
| refused | pydd stops with a message that names the gap |
| wrong | pydd builds something, and the result is not what the standard says |
| missing | pydd does not read it, and says nothing |

**A `wrong` row is the worst kind.** It builds, and the fault shows at run
time on another machine. Each one has an issue.

## Selection: which artefact a target takes

| specification | status | where |
| --- | --- | --- |
| PEP 503 name normalisation | delegated | `_select.choose`, `canonicalize_name` |
| PEP 440 versions and specifiers | delegated | `_select.choose`, `requirement.specifier` |
| PEP 508 markers, all eleven keys | covered | `_select.PEP508_KEYS`, `target_from_environ` refuses a short set |
| PEP 425 tags, CPython | delegated | `_select.target_tags`, `cpython_tags` and `compatible_tags` |
| PEP 427 build tag breaks a tie | covered | `_select.build_tag` |
| PEP 384 stable ABI (`abi3`) | delegated | `cpython_tags`; the pyzmq case in `target_tags` |
| ABI flags `t` (free-threaded) and `d` (debug) | covered, nixidae/nixidae#842 | `planner.nix` passes the target's `pythonABITags` in `PYDD_TARGET`, and `target_tags` gives them to `cpython_tags`, which would otherwise read the planner's interpreter. `projects/abi-probe` |
| PEP 600 `manylinux_2_Y` | covered, nixidae/nixidae#843 | `planner.nix` passes the target's libc and its version in `PYDD_TARGET`; `target_platforms` runs from that glibc down to 2.17. `projects/platform-probe` |
| PEP 656 `musllinux` | covered, nixidae/nixidae#846 | the same, for musl: `musllinux_1_Y` and no manylinux. An unknown libc is refused. Unit tests only: no musl interpreter is cached |
| macOS platform tags | covered, nixidae/nixidae#844 | `planner.nix` passes `macosx_<darwinArch>` and `darwinMinVersion` in `PYDD_TARGET`; `target_platforms` gives both to `mac_platforms`. Unit tests only: no darwin builder runs the projects |
| PyPy tags and versions | covered, nixidae/nixidae#845 | `target_tags` takes `generic_tags` with `ppXY` and the target's `pypyXY_ppNN` ABI. The language version is `pythonVersion`, and `python_full_version` is `3.Y.99`, which `markers` overrides. Another implementation is refused |
| A PyPy environment | covered, nixidae/nixidae#855 | every node runs `python.executable` and installs to `python.sitePackages`, which the plan document carries as `interpreter`. `projects/abi-probe` with `pkgs.pypy3` |

## Building: PEP 517, 518 and 660

| specification | status | where |
| --- | --- | --- |
| PEP 518 `[build-system] requires` | covered | `_select.build_system` |
| PEP 517 default backend | covered | `FALLBACK_BACKEND`, `FALLBACK_REQUIRES` |
| `build_wheel`, `build_sdist` | covered | `build-local.py`, `_sdist_planner.py` |
| PEP 660 `build_editable` | covered | `build-local.py`, `projects/workspace` |
| `backend-path` (in-tree backend) | covered | `build-local.py:in_tree_path`, refuses a path that leaves the tree |
| `config_settings` | covered | override key `config-settings` |
| `get_requires_for_build_*` | covered, nixidae/nixidae#847 | each sdist and each local project takes two planners: one submits the backend's environment, and the other takes it built, calls the hook of its form and adds the answer to the path. The answer must be in the lock or in pydd's build systems. A wheel out of a local project's own archive asks `_wheel` of the tree, because the archive is another planner's output. `projects/hook-probe`, `packaged-app`, `workspace` |
| `prepare_metadata_for_build_*` | not needed | the lock file holds the metadata |

## Installing: the wheel format, and the environment

| specification | status | where |
| --- | --- | --- |
| Binary distribution format, `RECORD`, `.dist-info` | delegated | `install-wheel.sh` runs `pypa/installer` |
| Entry points: `console_scripts`, `gui_scripts` | delegated | `pypa/installer` writes the scripts. `projects/wheel-features` |
| `.data/scripts` with `#!python` | delegated | `pypa/installer` rewrites the shebang. `projects/wheel-features` |
| `.data/purelib`, `.data/platlib` | delegated | `pypa/installer` |
| `.data/data` and `.data/headers` | covered, nixidae/nixidae#841 | `pypa/installer` writes them under the node's prefix (`share/`, `include/`), and `make-venv.py` merges the whole prefix. `projects/wheel-features` |
| +x on a script that a Windows build wrote | covered | the spec has a Unix installer add it, and `pypa/installer` does not. `make-venv.py:link_script` adds it. `projects/wheel-features` |
| Shebang rewrite into the environment | covered | `make-venv.py:link_script` |
| The `/bin/sh` trampoline for a long interpreter path | missing | `is_python_shebang` reads the first line only, so a trampoline keeps the store interpreter. A store path is short, so this is a trap, not a fault today |
| PEP 420 namespace packages across distributions | covered | `make-venv.py:link_tree` merges directories. `projects/wheel-features` |
| `.pth` files | covered | linked into `site-packages`; `build-local.py` rewrites an editable one. `projects/wheel-features` runs an `import` line |
| Two distributions that ship one file | covered | `make-venv.py:Owners`, a fixed rule and a warning |
| PEP 610 `direct_url.json`, `INSTALLER`, `REQUESTED` | delegated | what `pypa/installer` writes |

## Lock files and other inputs

| specification | status | where |
| --- | --- | --- |
| `uv.lock`: forks, `resolution-markers`, `conflicts` | covered | `_uvlock.py` |
| `uv.lock`: extras and PEP 735 groups | covered | `uv.nix` arguments `extras`, `groups` |
| `poetry.lock` 2.1 | covered | `_poetrylock.py`, `poetry.nix` |
| PEP 751 `pylock.toml` | covered, nixidae/nixidae#848 | `pylock.nix` and `_pylock.py`; needs `parse-toml-timestamps`. One file is one selection, a backend comes from pydd's lock because uv's export records no edges, and no freshness check runs. `projects/pylock-pure-app`, `pylock-workspace` |
| PEP 723 inline script metadata | covered, nixidae/nixidae#849 | `script.nix` and `_script.py` build the environment from `<script>.lock` and install the script as `bin/<name>`. The block's `dependencies` must match the lock's `[manifest] requirements`. Registry packages only: a git or path source fails by name. `projects/script-app` |
| PEP 625 source distribution names | delegated | `packaging` parses the file name |

## Overrides: a package that is broken upstream

`[tool.pydd.overrides.<package>]`, read by `lib/python/overrides.nix`, gives a
node `env`, `config-settings`, `patches`, `pre-build`, `post-install`,
`fixup` and `build-tools`. `[tool.pydd.from-nixpkgs]` replaces a node with a
package of nixpkgs. Two more keys change the resolution, nixidae/nixidae#850:

- `dependencies = { add, drop }` adds and drops edges of the package in the
  walk of a `uv.lock` or a script lock. An added name must be in the lock, and
  the lock pins its version. `poetry.lock` and `pylock.toml` are read flat:
  an add pins the name when the package is selected, and a drop fails by name.
- `artifact` replaces every artefact of the package with one file:
  `{ url, hash }`, a path or a derivation. The file keeps the version of the
  lock, and `.whl` in its name makes it a wheel.

`projects/script-edges` uses both.

## What comes next

Each `wrong` and `missing` row has an issue under `source: pydd`. The order of
work is the cost of the fault to a real project: `.data` first, then the ABI
flags, then glibc.
