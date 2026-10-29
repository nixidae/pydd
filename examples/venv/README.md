# venv

A Python environment from a `uv.lock`. This is the case that pydd is for.

`fixtures/myapp` is a uv project, and `uv lock` wrote its `uv.lock`. The
lock has 122 wheels and sdists, for many platforms. The planner reads the
lock the way uv installs it: which fork, which extras, which groups. It
then chooses one artefact for each package with `packaging`. Only the
chosen artefacts are downloaded.

`[tool.uv] no-binary-package` makes `idna` build from its sdist. **No
evaluation can know how to build an sdist**, because its build backend is
declared inside the archive. So the graph holds a second planner for
`idna`. That planner reads `pyproject.toml` out of the archive, finds
`flit_core.buildapi`, and submits the build.

uv does not lock build backends. The `build` group of `pyproject.toml`
puts `flit_core` and the installer in the lock, and neither is installed.
`myapp` itself is installed as a PEP 660 editable.

Read `default.nix`, then `lib/python/uv.nix` and `lib/python/graph.nix`.
From the top of the repository:

    nix run --file . internal.run -- examples.venv.huggorm
    nix run --file . internal.run -- examples.venv.huggorm check

`examples.venv.cli` builds the same environment with no huggorm.
`pydd.modes.render` writes the derivations that `graph.nix` makes, so
both attributes print the same store path.

The first lists the environment. `check` runs `check.py` in it:

    idna         3.20 xn--eckwd4c7c.xn--zckzah
    ninja        1.13.2
    editable     /nix/store/…-myapp
      hello from the tree that the check derivation named

The build log shows both planners:

    plan: idna==3.20: idna-3.20.tar.gz (sdist)
    plan-sdist: idna-3.20.tar.gz builds through flit_core.buildapi

To change the dependencies, edit `fixtures/myapp/pyproject.toml` and run
`uv lock` in that folder.
