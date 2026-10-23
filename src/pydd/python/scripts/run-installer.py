"""Run `python -m installer` in a venv, with the node's interpreter in each shebang.

    run-installer.py PYTHON INSTALLER-ARGS...

`installer` writes `sys.executable` into the shebang of each console script,
and in a scratch venv that names the venv, which dies with the build. So this
sets `sys.executable` to `PYTHON` first. Not `--executable`: the installer
1.0.1 that the lock files pin has no such option.
"""

from __future__ import annotations

import runpy
import sys

sys.executable = sys.argv[1]
sys.argv = ["python -m installer", *sys.argv[2:]]
runpy.run_module("installer", run_name="__main__", alter_sys=True)
