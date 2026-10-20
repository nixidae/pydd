"""The name of a node, which is the name of the store path that it builds.

`graph.nix` keys a node by its name, so two nodes under one name make the
second replace the first. `Plan.add` refuses that, and this module holds the
one case that reaches the refusal in a real build.

**The installer is both the bootstrap and a member.** `uv add --group build
installer` puts the wheel installer in a dependency group of
`../projects/pure-app`, and `groups = [ "build" ]` of `../uv.nix` then selects
that group. The graph holds one node that unpacks the wheel to bootstrap every
install, and a second node that installs the same wheel into the environment.
The two build different store paths, so each one needs its own name.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

import pytest

from pydd.core import CliWriter, Derivation, RecursiveWriter, writer_from_environ
from pydd.core._aterm import aterm, drv_path
from pydd.python import Artifact, Plan


def wheel(package: str, version: str) -> Artifact:
    """One row of a lock file, as a pure wheel."""
    filename = f"{package.replace('-', '_')}-{version}-py3-none-any.whl"
    return Artifact(package=package, version=version, filename=filename, kind="wheel")


class TestTheBootstrapNode:
    """The node that unpacks, against the node that installs."""

    def test_the_bootstrap_takes_a_suffix(self) -> None:
        plan = Plan()
        assert plan.artifact_node(wheel("installer", "1.0.1"), "unpack") == "installer-1.0.1-bootstrap"

    def test_the_same_wheel_installs_under_its_own_name(self) -> None:
        plan = Plan()
        plan.artifact_node(wheel("installer", "1.0.1"), "unpack")
        assert plan.artifact_node(wheel("installer", "1.0.1"), "wheel") == "installer-1.0.1"
        assert [node["kind"] for node in plan.nodes] == ["unpack", "wheel"]

    def test_one_wheel_that_two_requirements_name_is_one_node(self) -> None:
        # The refusal is about two *different* nodes, and a repeated node is
        # ordinary: `rich` and the environment both name `pygments`.
        plan = Plan()
        plan.artifact_node(wheel("pygments", "2.20.0"), "wheel")
        plan.artifact_node(wheel("pygments", "2.20.0"), "wheel")
        assert len(plan.nodes) == 1

    def test_two_different_nodes_under_one_name_are_refused(self) -> None:
        plan = Plan()
        plan.artifact_node(wheel("demo", "1.0"), "wheel")
        with pytest.raises(ValueError, match=re.escape("two different nodes are both called 'demo-1.0'")):
            plan.artifact_node(wheel("demo", "1.0"), "sdist-build", backend="flit_core.buildapi")


class TestAPlannerRunsInsideABuildOnly:
    """The two guards that `writer_from_environ` puts before every Nix call.

    **A planner reads a restricted daemon socket that only a `builder-rpc-v0`
    build has.** A reader who runs `plan.py` by hand meets one of these two
    messages, and each one names the reason rather than the symptom. Both
    guards raise before `init_libstore`, so this module reaches them with no
    store and no build.
    """

    @pytest.mark.parametrize("remote", ["", "daemon", "/nix/store", "ssh-ng://web01"])
    def test_a_socket_that_is_not_the_restricted_one_is_refused(
        self, remote: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("out", raising=False)
        monkeypatch.setenv("NIX_REMOTE", remote)
        with pytest.raises(RuntimeError, match="runs only inside a builder-rpc-v0 or recursive-nix build"):
            writer_from_environ()

    def test_an_unset_socket_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("out", raising=False)
        monkeypatch.delenv("NIX_REMOTE", raising=False)
        with pytest.raises(RuntimeError, match="runs only inside a builder-rpc-v0 or recursive-nix build"):
            writer_from_environ()

    def test_an_output_path_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # **A `builder-rpc-v0` build gets no `$out`.** The output arrives
        # through `submit_output`, so a build that set one is not the build
        # that this script runs in.
        monkeypatch.setenv("NIX_REMOTE", "unix:///build/restricted-socket")
        monkeypatch.setenv("out", "/nix/store/aaa-demo")
        with pytest.raises(RuntimeError, match="out is set to"):
            writer_from_environ()

    def test_recursive_nix_has_both_a_socket_and_an_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYDD_PROTOCOL", "recursive")
        monkeypatch.setenv("NIX_REMOTE", "unix:///build/.nix-socket")
        monkeypatch.setenv("out", "/nix/store/aaa-demo")
        monkeypatch.delenv("PYDD_WRITER", raising=False)
        assert isinstance(writer_from_environ(), RecursiveWriter)

    def test_recursive_submit_copies_the_root_to_out(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "root.drv"
        root.write_text("Derive([])")
        monkeypatch.setenv("out", str(tmp_path / "out"))
        RecursiveWriter(CliWriter()).submit(str(root), "out")
        assert (tmp_path / "out").read_text() == "Derive([])"

    def test_a_root_at_out_is_written_and_not_added(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # **Nix keeps a realisation after a GC deletes the planner's output**,
        # and then builds with `$out` at that old path: the root's own `.drv`.
        # Added through the daemon, the root sits read-only at `$out`, and Nix
        # rejects an output the build did not write.
        class Refusing:
            def add(self, drv: Derivation) -> str:
                raise AssertionError(f"{drv.name} went to the daemon")

            def submit(self, path: str, output: str) -> None:
                raise AssertionError("unused")

        root = Derivation(name="demo", system="x86_64-linux", builder="/bin/sh")
        out = tmp_path / posixpath.basename(drv_path(root, str(tmp_path)))
        monkeypatch.setenv("NIX_STORE", str(tmp_path))
        monkeypatch.setenv("out", str(out))
        writer = RecursiveWriter(Refusing())
        writer.submit(writer.add(root), "out")
        assert out.read_text() == aterm(root, str(tmp_path))
