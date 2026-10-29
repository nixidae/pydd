# hello

The smallest planner. Read this example first.

`plan.py` writes two derivations that do not exist before it runs. The
root reads the output of the leaf. The planner writes the leaf first,
then the root, then submits the root.

Read `plan.py`, then `default.nix`. `menu.bash` gives the planner a store
path that the nodes may use.

From the top of the repository:

    nix run --file . internal.run -- examples.hello.cli
    nix run --file . internal.run -- examples.hello.huggorm

Both print:

    root read: hello from a node the plan wrote

Both writers submit the same root, `nxi07rms…-hello.drv`. The `cli`
writer runs `nix derivation add`. The `huggorm` writer calls the Nix
store in process.
