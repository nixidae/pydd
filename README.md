# pydd

Dynamic derivations, planned in Python.

A planner is a derivation that decides at build time what Nix builds next.
It writes a graph of derivations through the restricted store socket of a
`builder-rpc-v0` build, and submits the root. `builtins.outputOf` on the
planner builds the graph.

[The site](https://nixidae.github.io/pydd/) compares the modes with
measurements, and holds the research notes.

The motivating case is Python packaging. `uv2nix` and `pyproject.nix` carry a
copy of PEP 508 markers, PEP 440 specifiers and PEP 425 tags written in Nix.
`packaging` is the reference implementation of all three, and a planner runs
it unchanged.

## Requirements

- Nix master, for `builder-rpc-v0` (NixOS/nix#15793). It is in no release.
  On a release, a planner writes only one derivation, as ATerm, to `$out`.
  `mkPlanner { protocol = "out"; }` does that (`examples/released`), and
  the host makes every other node. Python environments use it with
  `plannerProtocol = "out"` (see Python environments).
- `experimental-features = nix-command ca-derivations dynamic-derivations`.

## Three ways to write a graph

| | writes through | needs | example |
| --- | --- | --- | --- |
| `pydd.mkPlanner`, `writer = "cli"` | `nix derivation add`, `nix store submit-output` | Python stdlib, a `nix` | `examples/hello` |
| `pydd.mkPlanner`, `writer = "huggorm"` | `Store.add_derivation`, `Store.submit_output` | huggorm | `examples/hello` |
| `pydd.evaluate.mkEvaluated` | the Nix evaluator, in the planner | huggorm | `examples/evaluated` |
| `pydd.mkPlanner`, `writer = "drv"` | `pydd-drv add`, `pydd-drv submit` | Python stdlib, `pydd-drv` | `examples/hello` |
| any program | `pydd-drv graph` | `pydd-drv` | `examples/graph` |

**`pydd-drv` is for a planner in any language** (#1). It is a small C++
tool over libstore, built for Nix master and for 2.35
(`tools/pydd-drv`). `graph` reads a whole graph as JSON on stdin: Nix's
derivation JSON per node, keyed by name. A node names another by key in
`inputs.drvs`, and reads its output as `{{key^out}}` (`{{key^drv^out}}`
for a dynamic one). The tool writes leaves first, computes each
placeholder, and submits the root. With `--out` it writes the root as
ATerm to `$out`, for released Nix. It measured 148.9 MiB of closure
against 166.5 MiB for `nix`: libnixstore is most of both.

No mode renders ATerm. A node is derivation JSON, and Nix computes every
output path it can. For the same plan, the CLI and huggorm writers submit
the same root `.drv`.

## Python environments

`pydd.python.uv { project = ./.; }` builds the environment of a uv project
(`examples/venv`). Nix only parses `uv.lock` and makes one fetch per file.
The planner reads the lock the way uv installs it, and chooses an artefact
for each package with `packaging`. Only the chosen artefacts are
downloaded.

**The writer chooses one of two paths from plan to derivations.** Both
paths make the same derivations:

| writer | the plan becomes derivations through | needs |
| --- | --- | --- |
| `huggorm` | `lib/python/graph.nix`, evaluated in the planner | huggorm |
| `cli` | `pydd.modes.render`, in Python | `packaging`, a `nix` |

`graph.nix` is the reference. `checks.tests` holds the renderer to it for
each node kind, and `test-projects` builds each project both ways and
compares the two environments.

**On released Nix an environment keeps one node per package.** With
recursive-nix a planner writes every node through the build's socket, and
its root to `$out` (`protocol = "recursive"`). Without it, a planner
writes only its root, so the host makes the rest:

- the install node of every wheel in the lock, from `graph.nix`;
- a planner for each sdist, as on `builder-rpc-v0`;
- a planner for each local project, which submits that project's node
  (two for a project built from its own sdist);
- a planner for the environment under an application.

`graph.nix` reads a node that another planner writes, of kind `planned`,
through `builtins.outputOf`. Every test project builds on stock Nix 2.35
both ways, with the same environment paths as on `builder-rpc-v0`.
[The modes page](https://nixidae.github.io/pydd/modes.html) compares all
nine modes, and the level of restriction each one needs.

**An sdist gets a planner of its own.** Its build backend is declared
inside the archive, so no evaluation can know it. That planner reads the
archive, chooses the backend from the same lock, and submits the build.

**The result is an ordinary derivation** (#7). Its output is a tree of
links into the environment, or into the application when the call names
one. Its output floats (#14), so every mode that gives one environment
gives one root. It takes `override` over the arguments of `uv`, `overrideAttrs` and
the install and fixup hooks, and it works in `environment.systemPackages` and
`buildInputs`. Fixup runs without strip, patchelf or `patchShebangs`, so
`wrapGAppsHook3` and `wrapProgram` wrap a program and change no file of
the environment (nixidae/nixidae#745). Its `passthru` holds `planner`, `dynamic` (the output that
the planner submits), `env`, `app` and `shell`. The reader handles
workspaces, git, URL and path sources, and local registries.
`tests/projects` builds one project for each (see Tests). uv locks neither
build backends nor the installer, so a dependency group puts them in the
lock.

`pydd.python.poetry { project = ./.; }` does the same for a Poetry
project, at lock version 2.1 or later (`examples/poetry`). Only the reader
of the lock file differs. `poetry.lock` records no URL, so this reader
takes PyPI packages only.

This planner is a port of the ddrn lab of nanopynix. `docs/` holds its
research notes.

## Cargo projects

`pydd.rust.cargo { project = ./.; }` builds a Cargo project, with one node
for each unit of cargo's unit graph: a library, a proc-macro, a binary,
and a build script's compile and run. Cargo plans the build, offline over
a vendor directory that nixpkgs makes from `Cargo.lock`. A node reads its
build script's output when it builds. The lane uses only `pydd.core`, and
builds in the `cli`, `huggorm`, `drv` and `recursive` modes. `out` cannot
build it: there the host makes every node, and only cargo knows the
units. A crate that links a system library gets it from nixpkgs'
`defaultCrateOverrides`, or from `[package.metadata.pydd.build-tools]`. [The Rust page](https://nixidae.github.io/pydd/rust.html) has
the detail.

## npm projects

`pydd.node.npm { project = ./.; }` builds an npm project from its
`package-lock.json`, with one node per package. The planner reads the lock
in the build, chooses the packages for the platform, and writes a
fixed-output fetch and a node for each one. A package with an install
script runs it in its own node, node-gyp included. The root copies each
node to the path that the lock gives it, so Node resolves every `require`
as after `npm ci`, and runs `npm run build`. The lane builds in every
mode. In `out` the host makes each node from the lock, and the planner
writes only the root. The planner also reads `pnpm-lock.yaml`, `bun.lock`
and Yarn v1's `yarn.lock`, and places their packages as npm does. `out`
reads npm's lock only. [The npm
page](https://nixidae.github.io/pydd/node.html) has the detail.

## Extending a build

pydd is a framework. A package that needs more than its lock file says
gets it through one of these extension points, and each one works in
every mode, released Nix included.

| need | extension point | status |
| --- | --- | --- |
| native inputs of a source build | none: nixpkgs hints (`nixpkgsHints`, `lib/python/hints.nix`) | done, #3 |
| a library linked through another one | none: the fixup step (`scripts/fixup-elf.py`) | done, #4 |
| tools that nixpkgs does not imply | `[tool.pydd.build-tools]`, or `buildTools` | done |
| a package from nixpkgs instead of PyPI | `[tool.pydd.from-nixpkgs]`, or `fromNixpkgs` | done |
| the root itself | `override`, `overrideAttrs`, hooks | done, #7 |
| env, `config_settings`, patches, hooks | `[tool.pydd.overrides.<package>]`, or `overrides` | done, #5 |
| a build that nothing above covers | `builders.<package> = artifact: drv` | done, #6 |

**The hints follow your nixpkgs.** They come from `python.pkgs`, so an
overlay or `packageOverrides` that changes a nixpkgs Python package
changes what pydd builds that package with. A declaration in
`[tool.pydd.build-tools]` or `buildTools` replaces the hints for that
package.

**A node override is data.** The planner writes it into the node that
builds the package from source, so it works in every mode. A wheel node
takes none.

    [tool.pydd.overrides.numpy]
    config-settings = { setup-args = ["-Dblas=blas", "-Dlapack=lapack"] }
    patches = ["patches/numpy.patch"]
    env = { NPY_DISABLE_SVML = "1" }
    pre-build = "export CFLAGS=-O2"
    post-install = "rm -r $out/bin"
    fixup = false
    build-tools = ["openblas"]

The `overrides` argument of `uv` and `poetry` takes the same keys in
camelCase, and wins per key. `lib/python/overrides.nix` and `graph.nix`
(at `overridesOf`) give each key. `tests/projects/numpy-sdist` chooses
BLAS this way.

**The escape hatch is a host function.** `builders.<package>` of `uv`
and `poetry` takes one artefact, `{ package, version, filename, kind, src }`,
and returns a derivation that holds `lib/python<version>/site-packages`,
such as `python.pkgs.buildPythonPackage` makes. So that build has stdenv,
`overrideAttrs` and every setup hook. The planner still selects the
artefact, and names the derivation made for it. `null` keeps the
planner's own node. `tests/projects` builds psutil this way
(`native-host`).

**The fixup step needs no declaration.** A node that builds with tools
adds to each ELF file's `RUNPATH` the directory of every `DT_NEEDED`
entry that it does not resolve. It searches the tools' `lib` directories,
then the `RUNPATH` of each library it finds there. So pyzmq finds
libsodium through libzmq. A file that already resolves keeps its bytes.

## Constraints

- A node the planner writes has a floating output, or one Nix computes.
  Another node names it by `pydd.downstream_placeholder`.
- A planner writes its graph leaves first: Nix checks each input when it
  writes a node.
- The planner is named `<root>.drv`. Nix requires a submitted object to
  have the name of the planner's output, and the root is `<root>.drv`.
- In a Nix expression, `builtins.storePath` and `builtins.appendContext`
  call `EnsurePath`, which `builder-rpc-v0` refuses. `mkEvaluated` attaches
  context from Python instead, so the expression needs neither.

## A tour

Read the examples in this order. Each folder has a README with the
command to run and what it prints.

1. `examples/hello`: the smallest planner, two nodes in Python.
2. `examples/evaluated`: the same idea, with the graph as a Nix file.
3. `examples/nested`: a planner whose graph reads another planner's graph.
4. `examples/venv`: a Python environment from a `uv.lock`.
5. `examples/poetry`: the same environment from a `poetry.lock`.
6. `examples/released`: `hello` on a released Nix, with no `builder-rpc-v0`.
7. `examples/graph`: a planner in shell, with no Python, through `pydd-drv graph`.
8. `examples/cargo`: a Cargo project, one node per unit of cargo's unit graph.
9. `examples/npm`: an npm project, one node per package of its lock.

An example is `examples.<name>.<writer>`:

    nix run --file . run -- examples.hello.cli
    nix run --file . run -- examples.venv.huggorm check
    nix run --file . run -- examples.venv.cli check

Your daemon may be older than Nix master. `run` builds in a private store
with a Nix from master, and copies the planner's closure in. `PYDD_STORE`
names the store; the default is `~/.cache/pydd/store`.

## Layout

Three layers, kept apart (nixidae/nixidae#791). The core knows no
ecosystem. An ecosystem knows no mode. A mode is one way to turn a plan
into a built root, and modes do not import each other.

| layer | Nix | Python | holds |
| --- | --- | --- | --- |
| core | `lib/core/` | `src/pydd/core/` | `mkPlanner`, `candidate`, `Derivation`, placeholders, `Planner`, the writers, the `rpc` and `out` protocols, the root wrapper `root.nix` |
| ecosystem | `lib/python/` | `src/pydd/python/` | lock readers, selection, hints, the plan document, `graph.nix` (each node kind), build scripts |
| ecosystem | `lib/rust/` | `src/pydd/rust/` | the Cargo planner over the unit graph, and the node builder `scripts/unit.py` |
| ecosystem | `lib/node/` | `src/pydd/node/` | the npm planner over `package-lock.json`, the readers of pnpm, bun and Yarn locks, and the node builder `scripts/package.py` |
| modes | `lib/modes/` | `src/pydd/modes/` | `render` (Python renderer), `evaluate` (`graph.nix` in huggorm, and `mkEvaluated`), `nix_cli` (`graph.nix` in `nix eval`, over `nix-eval.nix`), `out` (released Nix: host-made nodes and planned nodes) |

A planner's `PYTHONPATH` holds the core, its ecosystem and its one mode.

| path | holds |
| --- | --- |
| `tools/pydd-drv` | the C++ writer for a planner in any language (#1) |
| `examples/` | the tour |
| `docs/` | the research notes of the ddrn lab |
| `tests/`, `nix/` | the test suite, and the way to nixidae's pins |

## Tests

    nix build --file . checks.tests
    nix run --file . test-projects

`test-projects` builds the uv projects of `tests/projects`, which cover
workspaces, git, URL and path sources, a local registry, binary and native
wheels, applications, and nixpkgs packages in place of PyPI ones. Each one
needs `builder-rpc-v0` or a release, so it builds in the private store of
`run`, `run-released` or `run-recursive`. It builds each project in nine
modes, in two groups: the plain modes, and the modes whose source nodes
are stdenv's. It fails when two modes of a group give different store
paths for one environment, or for the root around it.
`tools/runner.py` runs the projects in parallel, `--jobs` at a time
(half the cores by default), and the modes of one project in order. Each
evaluation and each build has a timeout (`--eval-timeout`, `--build-timeout`),
so a stuck build fails the run instead of holding it.

`nix run --file . test-live` builds real projects from GitHub, pinned and
unchanged, in four modes: fastapi-cli, typer, fastmcp, the MCP Python SDK
and an example app of uv. `tests/live/README.md` lists what each one
needed, and what each one changed in pydd.

`nix run --file . test-rust` builds the Cargo projects of `tests/rust` in
four modes, and compares the binaries and the roots. `test-rust-live`
builds ripgrep, fd, hyperfine and bat from GitHub, as they are, in three.

`nix run --file . test-node` builds the npm projects of `tests/node` in
five modes. `test-node-live` builds http-server, nodemon,
npm-check-updates and json-server, at the releases that nixpkgs builds,
in four.

The suite holds pydd's JSON and placeholders to what Nix's own
`derivation` makes. `tests/python` holds the Python planner to the lock
files that uv wrote.
