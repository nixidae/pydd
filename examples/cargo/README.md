# cargo

A Cargo project, built with one derivation for each unit of cargo's unit
graph: each library, build script, proc-macro and binary.

`project` links OpenSSL and zstd through openssl-sys and zstd-sys. Nothing
declares them. The hints come from nixpkgs' `defaultCrateOverrides`, the
table that `buildRustCrate` reads, so each -sys crate finds its library
through pkg-config.

Cargo plans the build, and the planner writes it. `cargo build
--unit-graph` gives every unit, with its features, its profile and its
platform, offline over a vendor directory that nixpkgs makes from
`Cargo.lock`. A crate's nodes read only that crate's store path.

Read `default.nix`, then `lib/rust/default.nix` and `src/pydd/rust`.
From the top of the repository:

    nix run --file . internal.run -- examples.cargo.cli
    nix run --file . internal.run -- examples.cargo.cli check

The first lists the binary. `check` runs it, and builds `tests`: the
library's unit test, the doctest and the binary's test harness, each a
node of its own:

    hello from cargo-demo sha256=ea76
    bin
    cargo-demo-0.1.0-doctest.log
    cargo-demo-0.1.0-test-cargo-demo-run.log
    cargo-demo-0.1.0-test-cargo_demo-run.log

`root.shell` is a development shell with cargo, rustc, clippy, rustfmt and
the tools of every -sys crate:

    nix develop --file . internal.examples.cargo.cli.shell
