# numpy-sdist

numpy, built from its sdist, with BLAS and LAPACK chosen through PEP 517
`config_settings`.

The backend is meson-python, and the `build` group puts it and Cython in
the lock. numpy carries its own meson.

`pyproject.toml` declares one node override (nixidae/nixidae#786):

    [tool.pydd.overrides.numpy]
    config-settings = { setup-args = ["-Dblas=blas", "-Dlapack=lapack", ...] }

meson-python passes each `setup-args` entry to `meson setup`. The names
`blas` and `lapack` are the pkg-config names of the nixpkgs `blas` and
`lapack` packages. The nixpkgs hints give those two, the compilers,
pkg-config and Ninja.

`../scripts/check-numpy.py` checks that numpy reports that BLAS and
LAPACK, and that a matrix inverse runs.
