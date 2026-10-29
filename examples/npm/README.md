# npm

An npm project, built with one derivation for each package of its
`package-lock.json`.

The planner reads the lock in the build. It leaves out the packages of
other platforms: esbuild has one for each, as optional dependencies. For
each other package it writes a fixed-output fetch, with the hash of the
lock, and a node that unpacks it. esbuild has an install script, so its
node runs it, over a copy of the packages that it can `require`.

The root copies each node to the path that the lock gives it, runs `npm
run build`, and then removes esbuild, a development dependency.

Read `default.nix`, then `lib/node/default.nix` and `src/pydd/node`.
From the top of the repository:

    nix run --file . internal.run -- examples.npm.cli
    nix run --file . internal.run -- examples.npm.cli check

`check` runs the program, and lists what the result holds:

    hello from npm-demo nested
    ansi-styles
    chalk
    color-convert
    color-name
    has-flag
    supports-color
