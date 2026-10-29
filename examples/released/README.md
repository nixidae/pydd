# released

`hello`, on a released Nix that has no `builder-rpc-v0`.

A planner on released Nix cannot add a derivation. It writes exactly one,
the root, as ATerm to `$out`, and Nix builds that. So the host makes every
other node, and hands it to the planner as `pydd.candidate`. Here the host
makes the leaf, and the planner writes the root that reads it.

`mkPlanner { protocol = "out"; }` selects this. The huggorm writer stages
each derivation in a writable `dummy://` in memory, and writes the root's
ATerm. It refuses a root that names a path the planner does not have as
an input: Nix would register no reference to that path, and the root
would silently become a different derivation.

From the top of the repository:

    nix run --file . internal.run-released -- examples.released.huggorm

It builds with stock Nix 2.35, in a private store, and prints:

    root read: hello from a node the host made

The planner's output is the root it wrote, `bhvkx7f0…-released.drv`.
