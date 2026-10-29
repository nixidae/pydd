# The modes, compared

pydd builds one Python environment in nine modes. The plan is the same in
each mode. A mode decides what the planner may do, how the plan becomes
derivations, and how a source distribution is built.

**Modes that build a source node the same way give the same environment,
and a test proves it.** `nix run --file . internal.test-projects` builds each of the
19 test projects in every mode, in two groups. It fails when two modes of a
group give different store paths for one environment.

- **The plain group:** `cli`, `huggorm`, `drv`, `recursive`,
  `recursive-eval` and `out`. A source node runs pydd's own build scripts.
- **The stdenv group:** `recursive-nixpkgs`, `stdenv` and `out-stdenv`. A
  source node is the derivation `stdenv.mkDerivation` writes, and stdenv
  strips and patches what it builds, so these environments differ from the
  plain ones.

## The levels of restriction

**This repository exists to show what a planner can do at each level.**
From the most restricted to the least:

| level | Nix | a planner may | modes |
| --- | --- | --- | --- |
| `$out` | any release | write one derivation, its root, as ATerm to `$out` | `out`, `out-stdenv` |
| `builder-rpc-v0` | master | add derivations through a socket, and submit the root | `cli`, `huggorm`, `drv`, `stdenv` |
| `builder-rpc-v0` with EnsurePath | master with Lillecarl/nix#371 | also `builtins.storePath` and `builtins.appendContext` | none built (nixidae/nixidae#789); `recursive-eval` shows what it allows |
| recursive-nix | any release, with `recursive-nix` | also `QueryPathInfo`, and building | `recursive`, `recursive-eval`, `recursive-nixpkgs` |

**pydd uses recursive-nix as a `builder-rpc-v0` with more allowed, and
never builds through it.** recursive-nix lets a build build, and a build
that waits on another build holds a build slot. So a pydd planner only adds
derivations and evaluates. Every planner sets
`allow-import-from-derivation = false`, so an evaluator in it cannot build
to import. `.override { allowImportFromDerivation = true; }` on the root, or
`overrideAttrs` on `mkPlanner`'s result, changes that.

**On a release, recursive-nix gives the whole graph to the planner.** A
planner writes every node through the socket, and its root to `$out`. Nix
lets an output name a path that the build added. So the host makes no node,
as with `builder-rpc-v0`, and the environments are the same paths.

### The Nix: `builder-rpc-v0`, recursive-nix, or `$out`

**`builder-rpc-v0` is on Nix master, and in no release.** Nix gives the
planner a restricted daemon socket. The planner writes each derivation of
its graph through that socket, and then submits the root as its output.

**recursive-nix is in every release, as an experimental feature.** The
planner gets a daemon socket, as above. A release has no `SubmitOutput`,
so the planner writes its root as ATerm to `$out`.

**On a release with neither, a planner writes one derivation: its root.**
It writes the root as ATerm text to `$out`, and Nix reads that text as a
derivation. The planner cannot add any other derivation, so the host makes
every other node before the planner runs.

### The writer: `nix`, huggorm, or `pydd-drv`

- **`cli` is the stock `nix` binary.** The planner runs
  `nix derivation add` once for each derivation, and
  `nix store submit-output` for the root.
- **`huggorm` is Python bindings to Nix's own C++ libraries**, libnixstore
  and libnixexpr. It is not a second implementation of Nix: the code that
  runs is Nix's. The planner writes in process, and it can evaluate Nix
  code: this mode evaluates `lib/python/graph.nix`.
- **`drv` is `pydd-drv`**, a small C++ tool over libnixstore
  (`tools/pydd-drv`, nixidae/nixidae#782). It is the specialised CLI.
  `add` and `submit` do what the two `nix` commands do, and `graph`
  writes a whole graph of JSON derivations in one process.
- **`out` writes through huggorm** today. huggorm stages each derivation
  in an in-memory store, and the planner copies the root's ATerm to
  `$out`. `pydd-drv graph --out` does the same for a planner in any
  language (`examples/graph`). pydd's own planners cannot use it yet,
  because they add one derivation per call, and a new process does not
  keep the in-memory store (nixidae/nixidae#796).

### The nine modes

| mode | Nix | writer | plan to derivations | a source node |
| --- | --- | --- | --- | --- |
| `cli` | master, `builder-rpc-v0` | `nix` | `pydd.modes.render`, in Python | pydd's scripts |
| `huggorm` | master, `builder-rpc-v0` | huggorm | `graph.nix`, in huggorm's evaluator | pydd's scripts |
| `drv` | master, `builder-rpc-v0` | `pydd-drv` | `pydd.modes.render`, in Python | pydd's scripts |
| `stdenv` | master, `builder-rpc-v0` | `nix` | `pydd.modes.render`, in Python | stdenv, as data |
| `recursive` | 2.35, recursive-nix | `nix` | `pydd.modes.render`, in Python | pydd's scripts |
| `recursive-eval` | 2.35, recursive-nix | `nix` | `graph.nix`, in `nix eval` | pydd's scripts |
| `recursive-nixpkgs` | 2.35, recursive-nix | `nix` | `graph.nix`, in `nix eval` | stdenv, from nixpkgs in the planner |
| `out` | 2.35, `$out` | huggorm | `pydd.modes.render`, in Python | pydd's scripts |
| `out-stdenv` | 2.35, `$out` | huggorm | `pydd.modes.render`, in Python | stdenv, as data |

**`recursive-eval` is what EnsurePath buys.** A stock `nix eval` in the
planner evaluates `graph.nix` over `lib/modes/nix-eval.nix`, which attaches
the context with `builtins.storePath` and `builtins.appendContext`. It
needs no huggorm and no renderer, and it writes the same `.drv` for every
node kind (`checks.tests`). On `builder-rpc-v0` the same needs
Lillecarl/nix#371.

## Three ways to give a node stdenv

**A node of pydd runs no stdenv, so pydd does stdenv's work by hand**:
`-g0`, `CPPFLAGS` and `LDFLAGS`, and `scripts/fixup-elf.py` in place of
`autoPatchelfHook`. Three ways give a node stdenv instead:

| | nixpkgs in the planner | stdenv as data | host builders |
| --- | --- | --- | --- |
| modes | `recursive-nixpkgs` | `stdenv`, `out-stdenv`, any other | any (`builders`, nixidae/nixidae#787) |
| how | the planner imports nixpkgs and calls `stdenv.mkDerivation` | the host evaluates `stdenv.mkDerivation` once, and the planner lays a node's attributes over the result | the host calls a function per artefact, before planning |
| needs | recursive-nix | nothing more | nothing more |
| keeps | all of nixpkgs, at plan time | stdenv's phases and setup hooks | all of nixpkgs |
| loses | overlays and config, except as files and JSON | `mkDerivation`'s logic at evaluation: `overrideAttrs`, `passthru`, inputs it derives | the planner's choice: the host decides per artefact |

**The first two write the same derivation.** `checks.stdenvAsData` holds
them to one `.drv` for every source node, and `test-projects` to one
environment for every project. A planner with no nixpkgs, on
`builder-rpc-v0` or on a release with no socket, so writes exactly what a
planner that imports nixpkgs writes.

**nixpkgs is evaluated only where a node forces it.** A wheel node never
does, so a planner of a wheel-only lock never imports nixpkgs. Each sdist
planner and local-project planner that builds from source evaluates
stdenv once.

:::{warning}
**An overlay does not reach a planner.** An overlay is a Nix function, and
no function crosses into a sandbox. `recursive-nixpkgs` takes overlays only
as files (`plannerNixpkgsOverlays`) and config only as JSON
(`plannerNixpkgsConfig`). A package set that the caller changed in memory
is not the one the planner imports.

**`"${pkgs.path}"` copies nixpkgs a second time**, because a path value is
copied on interpolation even when it lies in the store. pydd names the
path with `builtins.storePath` instead, in an impure evaluation.
:::

### How `out` builds a graph from one derivation

**The host is the evaluation that the user starts**, such as
`nix build --file . env`. It runs before any build, outside every
builder, the way the evaluation of a nixpkgs package does. It
*instantiates* derivations: it writes each `.drv` to the store, and
builds nothing. A planner is a builder, which runs later, in a sandbox.

`lib/modes/out.nix` makes the host's nodes. It evaluates `graph.nix` for
each one, takes its `.drv` path with `unsafeDiscardOutputDependency`, and
passes the list to every planner as `hostNodes`. Each `.drv` is then an
input of the planner, present in its sandbox, while no output of it is
built.

**A planner on a release writes one derivation, and that derivation may
name others that already exist.** A root names its inputs by `.drv`
path. Nix accepts any input that is in the store, and the planner's
sandbox holds every input of the planner. So the host makes each `.drv`
that a root could need before the planner runs, and the planner decides
which of them the root names. Three things make that work:

1. **The host makes a node for every wheel of the lock**, for every
   platform, with the same `graph.nix` and the same document that the
   planner would write. So the host's `.drv` and the planner's are one
   path. The host only instantiates them: Nix builds the ones that the
   root names, and no other one.
2. **A node that only a build can shape gets a planner of its own.** The
   backend of an sdist is declared inside the archive, and no evaluation
   has read the archive. The host makes one planner per sdist, one per
   local project, and one for the environment under an application. Each
   writes one derivation, its node, as its own `$out`. The root names
   that node as a dynamic output (`builtins.outputOf`), a node of kind
   `planned`. So one node per package holds: each package's derivation
   is either one the host made, or the one derivation of its own
   planner.
3. **The planner computes every path exactly, and writes only the
   root.** huggorm stages each derivation in an in-memory store, so the
   paths match the other modes byte for byte. The planner then copies the
   root's ATerm to `$out`. Nix registers no reference to a path that the
   sandbox lacks, and the root then silently becomes another derivation.
   So the writer refuses a root that names such a path.

**So `out` chooses at build time, and it does not construct.** Its
planners run in builders, and make each decision that needs a build:
which wheel fits the target, and what an sdist's archive declares. Every
node they name is one the host made before. That suffices when the host
can list the candidates, as one node per package can. It fails when the
number or the shape of the nodes is known only after a build, such as a
node per source file of an archive, or nesting deeper than the host
prepared (`sdistPlannerDepth`). `builder-rpc-v0` constructs: a planner
writes any derivation it decides on.

**The cost is on the host.** It instantiates a node for every wheel it
might need, and a planner wherever only a build can decide. With
`builder-rpc-v0` a planner writes what it chose, and nothing more.

**"Plan to derivations" is a third, smaller choice.** `graph.nix` says how
a node is built. `pydd.modes.render` makes the same derivations in Python,
for a planner with no evaluator. `tests/test_render.py` holds the two to
the same `.drv` for every node kind.

## Measured

```{include} _generated/measurements.md
```

`tools/measure-modes.py` measures this, after `test-projects` built every
node. It gives every planner a nonce, so Nix runs each planner of an
environment again, nested ones included, and builds no node. So a time is
all the planning of one environment.

**At this scale the mode decides little, except on a release with no
socket.** A build that does nothing takes 1.4 to 2.6 s. Each mode with a
socket, `builder-rpc-v0` or recursive-nix, is within about a second of the
others on every project. `out` and `out-stdenv` take 6 to 12 s where a
project has a local project under an application or a workspace, against
about 3 s elsewhere: the host-made planner of the local project runs
before the planner of the environment.

**nixpkgs in the planner costs little.** Measured alone, the sdist planner
of psutil in `native-app` takes 3.19 s in `recursive-nixpkgs`, where it
imports nixpkgs and writes a `stdenv.mkDerivation` node, against 2.87 s
in `recursive-eval`. A planner that writes no source node never imports
nixpkgs, so a wheel-only lock pays nothing.

**The closure follows the writer.** A planner that writes through huggorm
(`huggorm`, `out`, `out-stdenv`) carries about 509 MiB; one that writes
through `nix` or `pydd-drv` carries 305 to 323 MiB.

The test projects lock at most a few packages. The ddrn lab measured lock
files of up to 1911 packages (`PACKAGING-FACTS.md`), and a large lock is
where the modes can differ: one process per node in `cli` and `drv`, one
evaluator in `huggorm`, and host evaluation of every wheel in `out`. That
measurement is still to do.

- **Planner closure** is what one planner needs in its sandbox: an
  interpreter, pydd, and the tool that writes.
- **Lines of the mode** count the files that only that mode needs, beside
  the core and the Python layer that every mode shares.

## What each mode cannot do

**`cli` and `drv` run no evaluator in the planner.** So `graph.nix` exists
twice: in Nix, and as `pydd.modes.render` in Python. `tests/test_render.py`
holds the two to the same `.drv` for every node kind.

**`huggorm` cannot call `builtins.storePath` or `builtins.appendContext`.**
Both call `EnsurePath`, and `builder-rpc-v0` refuses it. huggorm attaches
the string context from Python instead
(`pydd.modes.evaluate._document.document_value`). A plain `nix eval` in
the planner has no such way.

**No `builder-rpc-v0` mode can evaluate nixpkgs in a planner.** Measured in
nixidae/nixidae#790: a planner that imports nixpkgs fails after 33 s with
`Operation 26 not allowed inside derivation`. Operation 26 is
`QueryPathInfo`. `fetchToStore` calls it after it copies a path literal
into the store, and nixpkgs has a path literal in every setup hook.
Lillecarl/nix#371 allows `EnsurePath`, and not `QueryPathInfo`. recursive-nix
allows both, so `recursive-nixpkgs` works. Stdenv as data needs neither,
so `stdenv` and `out-stdenv` get stdenv's nodes without it.

**The recursive modes could build, and refuse to.** recursive-nix gives a
planner a socket that builds. pydd's planners only add derivations, and an
evaluator in them refuses import from derivation. Nothing in Nix holds a
planner to that: it is pydd's convention, and the one setting.

**`out` makes the host do the planning that a planner cannot.**

- The host makes a node for every wheel of the lock, for every platform,
  because only the planner knows which wheel it chooses.
- An sdist, and a local project, become planners that the host made. A
  planner reads the output of another through `builtins.outputOf`.
- A root that names a path the sandbox does not hold silently becomes
  another derivation. `OutWriter` and `pydd-drv graph --out` refuse such a
  root.

## What this asks of Nix

Each item is a change to Nix, and each one is measured above or in the
issue it names.

1. **`EnsurePath` in `builder-rpc-v0`** (Lillecarl/nix#371). A planner can
   then run a plain `nix eval` over `graph.nix`, with no huggorm.
   `recursive-eval` shows it today, on a release, through recursive-nix.
2. **`QueryPathInfo` in `builder-rpc-v0`, or a `fetchToStore` that does
   not query.** A planner can then evaluate nixpkgs, as
   `recursive-nixpkgs` does through recursive-nix. It is the weakest
   request here: stdenv as data writes the same nodes with no nixpkgs in
   the planner, at every level.
3. **Tools over the daemon protocol, and not a second protocol.**
   `pydd-drv` (nixidae/nixidae#782) is the shape: `add` and `submit` are the two
   commands Nix has, and `graph` writes a whole graph in one process. It
   computes each placeholder and writes ATerm to `$out` on a release.
   Its closure is not much smaller than `nix`'s, because libnixstore is
   most of both. A subcommand of Nix would cost nothing extra.
4. **Nothing else.** One derivation per package works on released Nix
   today, through host-made nodes. The cost is host evaluation, and the
   table above shows what the planners cost.
