# graph

A planner with no Python and no pydd library. A shell script prints the
graph as JSON, and `pydd-drv graph` writes it (nixidae/nixidae#782).

A node is Nix's derivation JSON. It names another node by its key in
`inputs.drvs`, and reads its output as `{{key^out}}`. The tool writes
the nodes leaves first, computes each placeholder, and submits the root.
A path in `inputs` may be absolute, and a floating output needs no
`$out` in `env`.

    nix run --file . run -- examples.graph.drv
    nix run --file . run-released -- examples.graph.released

The first prints `root read: hello from a node the planner wrote`. On
released Nix a planner writes only its root, so the host makes the
leaf, and the second prints `root read: hello from a node the host
made`.
