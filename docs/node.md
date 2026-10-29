# A third ecosystem: npm

The npm lane plans a project from its `package-lock.json`. Like the Rust
lane, it uses only `pydd.core` and the root wrapper `lib/core/root.nix`.

    root = pydd.node.npm { project = ./.; };

The result is an ordinary derivation. It holds the project in
`lib/node_modules/<name>` and its programs in `bin`. It takes `override`
over the arguments, `overrideAttrs` and the install hooks.

## The lock is the plan

A Python lock says nothing of how to build a package, and a Cargo lock
says nothing of units. An npm lock of version 2 or 3 says almost
everything. Its `packages` map holds one entry for each path that npm
installs, with the URL, the hash, the platform (`os`, `cpu`, `libc`),
`dev`, `optional`, `bin` and `hasInstallScript`.

So the planner runs no npm. It reads the lock in the build, and decides
three things for the platform of the build:

- **Which packages install.** An optional package whose `os`, `cpu` or
  `libc` does not fit is left out, as npm leaves it out. esbuild has one
  such package for each platform.
- **What each package can `require`.** Node looks for
  `node_modules/<name>` in the directory of the package, then in each
  parent directory. The planner walks the lock's paths the same way, and
  a workspace link resolves to its member.
- **What the result leaves out.** `omit` is npm's `--omit`, `[ "dev" ]`
  by default.

The host evaluation never reads the lock. Its cost does not grow with the
number of packages.

## Other locks: pnpm, bun and Yarn v1

The planner also reads `pnpm-lock.yaml`, `bun.lock` and Yarn v1's
`yarn.lock`. A reader in `pydd.node._readers` turns each of them into
npm's map of install paths. The rest of the lane does not change: the
same nodes, the same fetches, the same root. By default the planner reads
the first lock of `package-lock.json`, `pnpm-lock.yaml`, `bun.lock` and
`yarn.lock` that the project holds. `lockfile` chooses another.

All three hash a package as npm does: the `integrity` of its tarball,
which a fixed-output fetch takes as it is.

| lock | install paths | programs | platforms | install scripts |
| --- | --- | --- | --- | --- |
| npm | in the lock | in the lock | in the lock | in the lock |
| bun | in the lock | in the lock | in the lock | those that `trustedDependencies` names |
| pnpm 9 to 11 | a graph; the reader places it | only "has programs" | in the lock | those that `allowBuilds` or `onlyBuiltDependencies` names |
| Yarn v1 | a graph; the reader places it | not recorded | not recorded | not recorded |

**The reader places a graph as npm does.** Each version goes to the top
`node_modules`, unless another version of that name is visible from its
dependent. Then it goes inside the dependent. For every project of
`tests/node`, each tool's lock gives the layout of npm's lock
(`tests/test_readers.py`).

**A node reads what the lock omits.** When a lock does not name a
package's programs, the node reads them from the package's own
`package.json`, and makes the `.bin` links at build time.

**Install scripts follow the tool.** pnpm 10 and bun run the scripts of
the packages that the project allows, and no others. The reader does the
same. pnpm 9 ran every script, so a pnpm 9 project names its scripts, as a
Yarn project does. Yarn v1 runs every script, and its lock does not say which packages
have one. So a Yarn project names them: `installScripts = [ "better-sqlite3" ];`,
or `"pydd": { "installScripts": [...] }` in `package.json`. bun also has a
built-in list of packages that it trusts. pydd does not copy that list.

**Yarn v1 records no platform.** Every package of esbuild, for each
platform, installs for the build. The result omits them as development
dependencies, or keeps them all.

**Yarn 2 and newer are refused.** Their `checksum` is the hash of Yarn's
own zip of the package, not of the tarball. Nix cannot check a fetch
against it. This is the problem of Go's `go.sum` (nixidae/nixidae#806).

**`out` reads npm's lock only.** In `out` the host makes every node, in
Nix. Nix reads no YAML, and the host would need each reader a second
time.

**pnpm's patches apply in the package's node.** pnpm's lock gives only
the hash of each patch, in `patchedDependencies`. The project names the
file, in `pnpm-workspace.yaml` or in `"pnpm"` of `package.json`. The node
runs `patch --strip=1` after it unpacks the package, before any install
script. A patched version whose file the project does not name is
refused.

## One node per package

| entry | nodes | output |
| --- | --- | --- |
| a package | a fixed-output fetch, `ms-2.1.3.tgz` | the tarball |
|  | a node, `ms-2.1.3` | the unpacked package |
| a package with an install script | the same two | the package after `preinstall`, `install` and `postinstall` |
| the project | `<name>` | the project, its tree and its programs |

**The planner writes the fetches.** The Python and Cargo lanes fetch on
the host. A fetch here is a `Fixed` node from the planner, with the hash
of the lock. Its path is known before it builds (`fixed_output_path` in
`pydd.core`), so the next node names the path itself, not a placeholder.

**A node holds no dependency.** Node resolves a module from its real
path. A package linked from the store looks for its dependencies beside
the store path, and finds none. So a node holds only its own package,
and the root copies each node to the path that the lock gives it. Node
then resolves every `require` as it would after `npm ci`, with nested
versions too. The cost is disk space at build time; the store's
deduplication shares the files.

The other layout gives each node links to its dependencies, as pnpm
does. npm's trees have cycles, and a derivation cannot depend on itself,
so that layout needs one node for each cycle. This layout needs none.

## Install scripts

A package with `hasInstallScript` runs `preinstall`, `install` and
`postinstall` in its own node. A package with `binding.gyp` and no such
script runs `node-gyp rebuild`, as npm does. The node first copies the
part of the tree that the script can `require`: every package that the
lock resolves from the package, transitively, with each program in
`node_modules/.bin`.

The script gets what npm's run-script gives it, without a network:

| variable | value |
| --- | --- |
| `PATH` | each `node_modules/.bin` above the package, `node-gyp`, Node, stdenv's tools, a C compiler, Python |
| `npm_config_nodedir` | Node of the menu, so node-gyp downloads no headers |
| `npm_config_build_from_source` | `true`, so prebuild-install and node-pre-gyp download no binary |
| `npm_package_name`, `npm_package_version`, `npm_lifecycle_event` | as npm sets them |

`tests/node/native-npm` builds better-sqlite3. Its script tries
`prebuild-install`, which refuses, and then node-gyp compiles the SQLite
that the package bundles.

Install scripts that depend on each other in a cycle are refused, with
the cycle in the message. npm runs them in an order of its own choice.

## Other sources

| `resolved` in the lock | source |
| --- | --- |
| `https://...` with `integrity` | a fixed-output fetch of the tarball |
| `git+ssh://git@github.com/...#<commit>`, GitLab, sourcehut | a fixed-output fetch of the commit's archive, unpacked |
| a path in the project, `file:...` | the project's own file |
| a workspace member, with `link: true` | a link in the root, to the member |

**A git dependency needs its hash.** The lock names the commit and no
hash. The call gives `outputHashes."<name>-<version>"`, the hash of the
unpacked archive, as `fetchFromGitHub` takes it. The planner's error
names the URL, and `nix store prefetch-file --unpack <url>` prints the
hash. nixpkgs' `prefetch-npm-deps` maps the URLs of the three hosts the
same way. A git dependency with a `prepare` script is refused: npm runs
it with the package's development dependencies, and the lock does not
name those.

**`overrides.<name>.src` replaces a package**, with a tarball or a
directory, wherever the lock installs it. Its fetch is not written. This
is how a patched or a prebuilt package goes in, as nixpkgs'
`importNpmLock` takes `packageSourceOverrides`. `tests/node/git-dep`
replaces `ms`, and fetches `isarray` from a commit on GitHub.

## Native inputs

nixpkgs has no table of npm packages and their inputs, as
`defaultCrateOverrides` is for crates. So the lane has no hints. Two
sources give a package its tools, and the later one wins:

| source | example |
| --- | --- |
| the project | `"pydd": { "buildTools": { "canvas": ["pkg-config", "cairo.dev", "cairo"] } }` in `package.json` |
| the call | `buildTools.canvas = [ pkgs.pkg-config pkgs.cairo.dev pkgs.cairo ];`, `overrides.<package>.env` |

A tool reaches only the install script of its package, the same way as in
the Rust lane (`tool_env` of `pydd.core`).

## The project

The root copies the project, places every node, and links each workspace
member where the lock names it. It runs each script of `scripts`
(`[ "build" ]`) with `npm run --if-present`, in the project and in each
workspace member. With a pnpm, bun or Yarn lock it runs them in the
project only, as those tools do. The project builds in the build directory, and not in
`$out/lib/node_modules`: TypeScript, and api-extractor over it, skip
every file under a `node_modules`, the project's own included.

**The scripts get the tool of the lock.** A bun project's `build` runs
`bun run typecheck && bun --bun vite build`. So the root has
`packageManager` on `PATH`: pnpm, bun or Yarn, by the lock, and nothing
more for npm. Only the root gets it; a package's install script runs as
npm runs it. `scriptEnv` sets the environment of the scripts, as
`{ VITE_API_URL = "/api"; }`.

The root then removes what `omit` leaves out, and the directories that
this leaves empty. It links each program of the project and of its
workspace members into `bin`.

**`members` installs part of a workspace**, as `pnpm --filter
<member>...` does: `members = [ "apps/server" ]` keeps the project, that
member, each member it links to, and the packages these reach. Every
other member and its packages are left out of the build and the result.
With a pnpm or Yarn lock the reader places only what it keeps.

A program's `#!/usr/bin/env node` becomes the path of Node, as stdenv's
`patchShebangs` does. The packages' programs get the same.

## The modes

Each project of `tests/node` gives one result in five modes:
`nix run --file . internal.test-node`.

| mode | Nix | writes through |
| --- | --- | --- |
| `cli` | master, `builder-rpc-v0` | `nix derivation add` |
| `huggorm` | master, `builder-rpc-v0` | huggorm |
| `drv` | master, `builder-rpc-v0` | `pydd-drv` |
| `recursive` | 2.35, recursive-nix | `nix derivation add`, root to `$out` |
| `out` | 2.35, no socket | the host makes every node; the planner writes the root to `$out` |

## `out`: the host makes the graph

**`out` exists for npm, and not for Cargo.** On released Nix with no
socket, a planner writes one derivation. Every node that its root names
must exist before the planner runs, as an input of it. Only cargo, in a
build, knows Rust's units, so the host cannot make them. The npm lock
names every node, so the host can.

`lib/node/graph.nix` is the planner's resolution a second time, in Nix:
the platform, Node's walk, the closure of an install script, the `.bin`
links, and the spec of each node. It must give the `.drv` that the
planner gives, byte for byte. `OutWriter` refuses a root that names any
other, so a difference fails the build and does not pass unseen. Three
rules make the two agree:

- **JSON as `builtins.toJSON` writes it**: sorted keys, no spaces, no
  escaped Unicode. Every list in a spec is sorted.
- **A tool is an input derivation.** A string with a package's path
  carries that derivation in Nix. So the menu gives the planner `refs`,
  the derivation and output of each such path, and a node names the
  derivation and not the path.
- **A fetch is named by its URL**, and not by the first path that needs
  it, so the order of a walk cannot change a name.

The planner then only chooses the root. The graph is static, as
`importNpmLock` makes it, and the evaluation reads the lock:

| nodemon, 807 lock entries | evaluator CPU | function calls |
| --- | --- | --- |
| `cli`, the planner reads the lock | 1.01 s | 0.27 M |
| `out`, the host reads the lock | 1.31 s | 1.99 M |

The cost of `cli` does not grow with the lock. The cost of `out` does.
Whether a lock is the whole plan decides whether an ecosystem has `out`
at all.

A variable in `overrides.<name>.env` that names a store path differs
between the two in `out`: the menu gives the planner the text, without
the derivation.

## What is missing

- A git dependency outside GitHub, GitLab and sourcehut, and one with a
  `prepare` script. The planner and the node refuse them by name.
- `npm test` as `root.tests`.
- Yarn 2 and newer, whose checksums Nix cannot check.
- `out` for pnpm, bun and Yarn.
