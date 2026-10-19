# Live projects

Real uv projects from GitHub, pinned, built as they are. Each one builds
in four modes, one for each level of restriction. The environments must
be one store path, and the roots around them another:

    nix run --file . test-live
    nix run --file . run -- live.cli.fastmcp

| project | what it is | it needs |
| --- | --- | --- |
| `uv-fastapi-example` | a small app, locked in 2024 | `python = pkgs.python312` |
| `fastapi-cli` | a library on pdm-backend | nothing |
| `typer` | a library on pdm-backend, with pillow and mypy | nothing |
| `fastmcp` | a hatchling workspace, with a root of no code | nothing |
| `mcp-python-sdk` | a workspace of 17 members | nothing |

**uv-fastapi-example needs CPython 3.12.** Its lock pins pydantic-core
2.20.1, which has no wheel for 3.14, and whose sdist needs Rust and the
network of cargo. It also pins httptools 0.6.1, whose C is written for the
API of 3.12. The project says `requires-python = ">=3.12"`, and 3.12 is
what it was locked for.

## What they taught pydd

Each row is a change to pydd that every project now gets, not a fix for
one project.

| failure | the change |
| --- | --- |
| a lock in the wild holds no installer and no build backend | pydd's own lock of both, `lib/python/build-systems`, behind the project's lock |
| the planner's inputs passed 128 KiB, and `execve` refused them | `PYDD_INPUTS` and `PYDD_MENU` go in as files (`passAsFile`) |
| an sdist with no `[build-system]` got no setuptools | PEP 518's default, `setuptools>=40.8.0` |
| httptools had C, and nothing gave it a compiler | a source with C, C++, Cython or Fortran gets the default compiler |
| pillow's manylinux wheel needs `libz.so.1` | auto-patchelf searches every library PEP 599 lets a wheel assume |
| pdm-backend writes into the project's top directory | the copy of the tree is writable at its top too |
| hatchling's editable build needs `editables` | the planner calls `get_requires_for_build_editable` (nixidae/nixidae#847) |
| uv-dynamic-versioning reads git, and a store path has none | the locked version, or `0.0.0`, through each plugin's variable |
| fastmcp's root installs no code, so nothing redirects | an editable of no code is not an error |
| setuptools writes an import finder with the build path | the tokenizer rewrites each string literal that names it |
| two single-module packages each ship `__pycache__` | the environment merges directories |
| fastapi and fastapi-cli both install `bin/fastapi` | one wins by a fixed rule, with a warning: the one that requires the other |
| pyyaml found no `-lyaml`, and fell back to pure Python without an error | the nixpkgs hints read every output of an input, not only the output stdenv chose |
