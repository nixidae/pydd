"""Node overrides, from the inputs of a planner to the node that builds a package."""

from __future__ import annotations

import pytest
from packaging.requirements import Requirement

from pydd.python import Artifact, Plan, overrides_from


def sdist(package: str) -> Artifact:
    return Artifact(package=package, version="1.0", filename=f"{package}-1.0.tar.gz", kind="sdist")


def test_the_name_is_canonical_and_config_settings_are_json() -> None:
    given = {"overrides": {"NumPy": {"configSettings": {"setup-args": ["-Db=1"], "a": "x"}, "fixup": False}}}
    assert overrides_from(given) == {"numpy": {"configSettings": '{"a":"x","setup-args":["-Db=1"]}', "fixup": False}}


@pytest.mark.parametrize(
    "override",
    [{"preBuld": "true"}, {"fixup": "no"}, {"env": {"N": 1}}],
)
def test_a_wrong_key_or_type_is_refused(override: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="override of 'pkg'"):
        overrides_from({"overrides": {"pkg": override}})


def test_only_the_named_package_gets_the_override() -> None:
    plan = Plan(overrides=overrides_from({"overrides": {"numpy": {"preBuild": "true"}}}))
    plan.artifact_node(sdist("numpy"), "sdist-build", backend="mesonpy")
    plan.artifact_node(sdist("idna"), "sdist-build", backend="flit_core.buildapi")
    assert plan.nodes[0]["overrides"] == {"preBuild": "true"}
    assert "overrides" not in plan.nodes[1]


def wheel(package: str) -> Artifact:
    filename = f"{package.replace('-', '_')}-1.0-py3-none-any.whl"
    return Artifact(package=package, version="1.0", filename=filename, kind="wheel")


def test_a_wheel_node_takes_patches_and_post_install_only() -> None:
    given = {"Weasy_Print": {"patches": [{"out": "/p"}], "postInstall": "true", "env": {"A": "1"}, "fixup": False}}
    plan = Plan(overrides=overrides_from({"overrides": given}))
    plan.artifact_node(wheel("weasy-print"), "wheel")
    assert plan.nodes[0]["overrides"] == {"patches": [{"out": "/p"}], "postInstall": "true"}


def test_a_wheel_node_with_no_wheel_key_gets_no_override() -> None:
    plan = Plan(overrides=overrides_from({"overrides": {"rich": {"env": {"A": "1"}}}}))
    plan.artifact_node(wheel("rich"), "wheel")
    assert "overrides" not in plan.nodes[0]


def test_a_tree_with_a_vcs_version_plugin_gets_the_locked_version() -> None:
    plan = Plan(overrides=overrides_from({"overrides": {"kept": {"env": {"UV_DYNAMIC_VERSIONING_BYPASS": "9"}}}}))
    plan.pretend_version("demo", "1.2.3", [Requirement("hatchling"), Requirement("uv-dynamic-versioning>=0.7")])
    plan.pretend_version("plain", "1.0", [Requirement("hatchling")])
    plan.pretend_version("kept", "0.0.0", [Requirement("uv-dynamic-versioning")])
    assert plan.overrides["demo"]["env"] == {"UV_DYNAMIC_VERSIONING_BYPASS": "1.2.3"}
    assert "plain" not in plan.overrides
    assert plan.overrides["kept"]["env"] == {"UV_DYNAMIC_VERSIONING_BYPASS": "9"}
