# Getting started

pydd builds the environment of a uv project with one derivation per
package. A planner reads `uv.lock` at build time and writes those
derivations.

## Configure Nix

The planner writes its derivations through recursive Nix. Put this in
`nix.conf`:

```ini
experimental-features = nix-command ca-derivations dynamic-derivations recursive-nix
system-features = recursive-nix
```

## Build an environment

Put this in `env.nix`, beside `pyproject.toml` and `uv.lock`:

```nix
let
  pydd = (import (builtins.fetchTarball "https://github.com/nixidae/pydd/archive/main.tar.gz") { }).lib.recursive;
in
pydd.python.uv {
  project = ./.;
}
```

`lib.recursive` brings its own Nix, huggorm and Python, so it takes no
`pkgs`.

```console
$ nix build --file env.nix
$ ./result/bin/python -c 'import requests'
```

The build downloads only the files that the planner chooses for this
platform. The [README](https://github.com/nixidae/pydd#readme) shows the
other modes, Cargo and npm projects, and how to change one package of an
environment. [Modes](modes.md) compares the modes with measurements.
