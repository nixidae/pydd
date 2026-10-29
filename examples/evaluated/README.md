# evaluated

The graph is a Nix file, and the Nix evaluator runs inside the planner.

`graph.nix` is a function. It returns a root that reads a leaf, and the
leaf reads one of two candidates. A candidate is a derivation that Nix
builds only if a node uses it. Nobody reads `unread`, so Nix never builds
it.

Read `graph.nix`, then `default.nix`.

From the top of the repository:

    nix run --file . internal.run -- examples.evaluated.huggorm

It prints:

    root read: leaf read: hello from a candidate

Only huggorm can do this. It attaches store context from Python, so
`graph.nix` needs no `builtins.storePath`. That primop calls
`EnsurePath`, and a `builder-rpc-v0` build refuses it.
