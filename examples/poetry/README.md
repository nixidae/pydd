# poetry

The environment of `examples/venv`, from a `poetry.lock`. Read `venv`
first: the planner and `lib/python/graph.nix` are the same, and only the
reader of the lock file differs.

`fixtures/myapp` is a Poetry project, and `poetry lock` (Poetry 2.4) wrote
its `poetry.lock`. Lock version 2.1 gives each package its dependency
groups and its markers, already combined over the whole graph. The planner
reads `groups`, evaluates `markers` with `packaging`, and installs the
`main` group. That is what Poetry's own installer does with the same file.

**`poetry.lock` records no URL**, only a file name and a hash.
`lib/python/poetry.nix` builds the PyPI address from the file name, so this
reader takes PyPI packages only.

Poetry locks no build backend either. The `build` group of
`pyproject.toml` puts `flit_core` and the installer in the lock.

Read `default.nix`, then `lib/python/poetry.nix` and
`src/pydd/python/_poetrylock.py`. From the top of the repository:

    nix run --file . internal.run -- examples.poetry.huggorm check
    nix run --file . internal.run -- examples.poetry.cli check

The build log shows the same plan as `venv`:

    plan: colorama: markers exclude this target
    plan: poetry.lock selects 7 packages
    plan-sdist: idna-3.20.tar.gz builds through flit_core.buildapi

To change the dependencies, edit `fixtures/myapp/pyproject.toml` and run
`poetry lock` in that folder.
