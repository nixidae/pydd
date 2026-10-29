# nested

A planner whose graph reads the graph of a second planner. This is the
shape that an sdist needs: its build backend is inside the archive, so a
planner of its own reads it at build time.

`graph.nix` reads `outputOf (outputOf inner.drv "out") "out"`. The inner
`outputOf` is the root that the `hello` planner submits, and the outer one
is what that root builds.

**The inner planner is made here, on the host.** A `.drv` that a planner
writes is absent from its own sandbox, and `outputOf` reads it. Passed in
as `pydd.candidate`, the `.drv` is an input of the outer planner.

From the top of the repository:

    nix run --file . internal.run -- examples.nested.huggorm

It prints:

    outer read: root read: hello from a node the plan wrote
