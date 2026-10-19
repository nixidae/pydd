# A second ecosystem: Cargo

pydd's core knows no ecosystem. The Rust lane tests that claim. It uses
only `pydd.core`: `Planner`, `Derivation`, the writers, and the root
wrapper `lib/core/root.nix`. It has no plan document and no `graph.nix`.

    root = pydd.rust.cargo { project = ./.; };

The result is an ordinary derivation of the project's binaries, as for
Python. It takes `override` over the arguments, `overrideAttrs` and the
install hooks.

## Cargo plans, pydd writes

Python needs a planner of its own, because a lock file of Python says
nothing of how to build a package. Cargo already has a planner.
`cargo build --unit-graph` gives each unit of a build: its features, its
profile, its edition, whether it builds for the host or the target, and
what it depends on. `Cargo.lock` holds none of the first four.

So the planner runs cargo, offline, over a vendor directory that nixpkgs
makes from `Cargo.lock` (`rustPlatform.importCargoLock`). Each crate of
that directory is its own store path, so a node depends only on its own
crate. The planner sets `RUSTC_BOOTSTRAP=1`, because `--unit-graph` is
unstable.

## One node per unit

The planner writes one node for each unit. That is cargo's own
granularity, and pydd batches nothing.

| unit | node | output |
| --- | --- | --- |
| library | `serde-1.0.229` | `lib/libserde-<metadata>.rlib`, `link.json` |
| proc-macro | `serde_derive-1.0.229` | `lib/libserde_derive-<metadata>.so` |
| build script, compile | `serde-1.0.229-build-script` | `bin/build-script-build` |
| build script, run | `serde-1.0.229-build-script-run` | `out/` (`OUT_DIR`), `flags.json` |
| binary | `serde-app` | `bin/serde-app` |

One more node joins the binaries of the roots, under the project's name.
`tests/rust/serde-app` has 24 units, so 25 nodes.

**A node reads its build script's output when it builds.** No evaluation,
and no planner, knows what a build script prints. The run node writes
what the script printed to `flags.json`, and each node of the package
reads that file. The keys reach the units that cargo gives them to:

| key | reaches |
| --- | --- |
| `rustc-cfg`, `rustc-env` | every unit of the package |
| `rustc-link-search` | the package, and every unit that links it, through `link.json` |
| `rustc-link-lib` | the package's library, or every target when it has none |
| `rustc-link-arg` | the package's binaries, tests, examples, benches and cdylib |
| `rustc-link-arg-bins`, `-bin=NAME`, `-tests`, `-examples`, `-benches`, `-cdylib` | those targets of the package |
| `metadata`, with `links` | the build scripts of the packages that depend on it, as `DEP_<LINKS>_<KEY>` |

A build script also gets `CARGO`, `RUSTC`, `TARGET`, `HOST`, `PROFILE`,
`OPT_LEVEL`, `DEBUG`, the `CARGO_CFG_*` of the target and the
`CARGO_FEATURE_*` of the unit, as from cargo. `rustc-check-cfg` is
dropped: rustc then checks no cfg names, and a check warns and fails
nothing. `CARGO_MANIFEST_LINKS` comes with `links`.

**A build script sees cargo's filesystem.** tauri-build was the test:

- `OUT_DIR` is `$out/target/<profile>/build/<package>/out`, as cargo lays
  it out. A script that finds the target directory by walking up to
  `build`, or three levels, finds `$out/target/<profile>`.
- A workspace member's script runs in a writable copy of the source,
  because cargo leaves the checkout writable and tauri-build writes into
  it. The copy's path goes back to the store path in what it prints.
- The crate compiles against a writable copy of `OUT_DIR`: tauri's
  `generate_context!` writes compressed assets there at compile time.
- A failing script's stdout reaches the log. Many say why only there.

**`-C metadata` comes from what cargo decided.** It hashes the package ID,
the target, the mode, the platform, the features, the profile and the
metadata of each dependency. It holds no path of the host, so every mode
gives the same file names.

## Projects and sources

`project` is a package or the root of a workspace (Lillecarl/pydd#18).
A virtual workspace names no package, so the call passes `name`. Every
other choice is cargo's own flag, and the planner passes it to the unit
graph:

| argument | cargo flag |
| --- | --- |
| `packages` | `--package`, once for each |
| `features` | `--features` |
| `noDefaultFeatures`, `allFeatures` | `--no-default-features`, `--all-features` |
| `profile` (`"release"`) | `--profile` |

**A path dependency inside the project needs nothing.** One outside it
needs `root`, a directory that holds both: `tests/rust/monorepo` builds
`tool` with `shared` beside it.

**`files` places builds of other lanes into the source.** A crate that
embeds a web build at compile time, as rust-embed does, reads a directory
that the repository does not hold. `files.dist = "${frontend}/dist";`
copies it to `dist` under `root`. The lock and the manifests are still
read from `project`, so evaluation does not wait on that build.
kubecove-server embeds the output of `pydd.node.npm` this way.

**A git dependency needs its hash**, as `importCargoLock` takes it:
`outputHashes."itoa-1.0.15" = "sha256-...";`, or
`allowBuiltinFetchGit = true`. nixpkgs writes cargo's source replacement
for each git source into the vendor directory, and the planner uses that
file.

The root joins the binaries of every root unit. A workspace's libraries
build too, as with `cargo build`, and install nothing.

## Tests

`root.tests` runs every test of the project, as `cargo test` does. It is
a second planner, over `cargo test --no-run`:

| unit | nodes |
| --- | --- |
| a test binary (`--test`) | one compiles it, one runs it |
| an integration test | the same, with `CARGO_BIN_EXE_<name>` of each binary it runs |
| the doctests of a library | one runs `rustdoc --test` |

Each node that runs a test keeps its output as `log`, and the root links
every log. So the root builds only when every test passes, and a failed
test names its own node. A unit that the tests' graph shares with the
build's graph is the same node. `tests/rust/workspace` has one test of
each kind.

## Real projects

`tests/rust-live` builds ripgrep, fd, hyperfine and bat from GitHub, as
they are, in three modes: `nix run --file . test-rust-live`. Its README
lists what each one needed. Two changes came from them:

- **LTO by the role of the unit.** The unit graph gives each unit the
  profile's `lto`. Cargo runs LTO only where it links a binary, and the
  libraries that binary links keep their bitcode. A build script and a
  proc-macro get none. The planner does the same.
- **`LIBRARY_PATH` for each tool.** libz-sys probes for zlib by linking
  with `-lz`, and builds its own copy when that fails. gcc reads
  `LIBRARY_PATH` for `-l`.

## Native libraries

A crate that links a system library, such as openssl-sys, needs that
library and often `pkg-config` (Lillecarl/pydd#17). Three sources give
the nodes of a crate its tools, and a later one wins for the crate it
names:

| source | example | holds |
| --- | --- | --- |
| nixpkgs hints, `nixpkgsHints = true` | `defaultCrateOverrides.openssl-sys` | tools, variables |
| the project | `[package.metadata.pydd.build-tools]`, `[package.metadata.pydd.overrides.<crate>]` | tools, `env` |
| the call | `buildTools.<crate>`, `overrides.<crate>.env` | tools, `env` |

**The hints are nixpkgs' own table.** `defaultCrateOverrides` tells
`buildRustCrate` the inputs of each crate that needs one, so an overlay
that changes it changes pydd's nodes too. A variable in capitals in an
entry becomes a variable of the node: zstd-sys gets
`ZSTD_SYS_USE_PKG_CONFIG`, and so links the libzstd of nixpkgs rather
than the copy it bundles.

**A tool reaches the build script and the compile of its crate, and no
other node.** `bin` goes on `PATH`, `lib/pkgconfig` and `share/pkgconfig`
on `PKG_CONFIG_PATH`, `lib` on `LIBRARY_PATH`, and `include` into
`CFLAGS` and `CXXFLAGS` as `-isystem`. The library is linked where the build script says, and the
linker wrapper of nixpkgs writes the RUNPATH.

`tests/rust/native-app` builds openssl and zstd with no declaration.
`tests/rust/native-declared` turns the hints off and declares the same
inputs, in `Cargo.toml` for openssl-sys and in the call for zstd-sys.
With the hints off and nothing declared, openssl-sys fails: it finds
neither `pkg-config` nor OpenSSL.

**cargo-dyndrv has no hints.** Its caller writes an `extern` set by hand,
keyed by the full crate ID, with the `PATH` and the environment of each
build script.

## A development shell

`root.shell` has cargo, rustc, clippy, rustfmt and rust-analyzer, and the
tools and variables of every crate. They go through stdenv's setup hooks,
as a nixpkgs build gets them, so a plain `cargo build` in the shell finds
OpenSSL and zstd. `examples/cargo` shows it.

## Cross-compilation

`targetPkgs` names the package set the binaries run on, such as
`pkgs.pkgsCross.aarch64-multiplatform`. rustc, cargo, build scripts and
proc-macros run on its `buildPackages`; the libraries, the hints and the
linker of the target's units come from it. A build script gets
`TARGET_CC`, `TARGET_AR`, `HOST_CC` and the target's pkg-config, as the
`cc` and `pkg-config` crates read them.

**A hint's native input is the build machine's.** nixpkgs splices
`pkg-config` in a cross package set, and the plain value is built for the
target. This machine runs aarch64 binaries through binfmt, so such a
pkg-config works in a shell, and fails in the sandbox with status 255.
The hints take `__spliced.buildHost`.

`tests/rust` builds three projects for aarch64, and runs each binary under
`qemu-aarch64`. The rest is deferred, and Lillecarl/pydd#21 lists it:
tests under cross, a declared tool's platform, and other targets.

## The modes

Each project of `tests/rust` gives one binary and one root in four modes:
`nix run --file . test-rust`.

| mode | Nix | writes through |
| --- | --- | --- |
| `cli` | master, `builder-rpc-v0` | `nix derivation add` |
| `huggorm` | master, `builder-rpc-v0` | huggorm |
| `drv` | master, `builder-rpc-v0` | `pydd-drv` |
| `recursive` | 2.35, recursive-nix | `nix derivation add`, root to `$out` |

**`out` cannot build a Cargo project.** In `out`, the host makes every
node, and the planner writes only its root. Only cargo, in a build, knows
the units. So `pydd.rust.cargo` refuses `plannerProtocol = "out"`. Python
gets around this because the host can make a node for every wheel of the
lock. A Cargo lock gives no such list of nodes.

## Compared with cargo-dyndrv

[cargo-dyndrv](https://github.com/obsidiansystems/cargo-dyndrv) also
writes one derivation per unit of the unit graph. It is a Rust binary that
replaces `cargo` inside `buildRustPackage`, and talks to the daemon through
harmonia. It keeps build-script output as files of flags, and runs each
build script through a small wrapper binary.

pydd's lane is a Python planner over `pydd.core`, so it runs in every
mode that the core has, recursive-nix included. A node builds with a
Python script and `rustc`, and keeps the output of a build script as JSON.

cargo-dyndrv also runs outside a build: it writes the derivations through
the user's daemon and prints the root. pydd has no such mode yet
(Lillecarl/pydd#22).

## What is missing

| need | issue |
| --- | --- |
| fetch only the crates that the graph uses | Lillecarl/pydd#20 |
| cross: tests, declared tools, other targets | Lillecarl/pydd#21 |
| run the planner on the host, as cargo-dyndrv can | Lillecarl/pydd#22 |

**Every crate of the lock is fetched, also the ones that never build.**
`importCargoLock` makes a fetch for each crate of `Cargo.lock`, so a Linux
build fetches `windows-sys` too. #20 holds the alternative.
