"""Plan the build of one source distribution, from inside a build.

**This is the step that no evaluation can take.** A source distribution
declares its build system inside the archive, and the archive is a file that
some derivation has to download first. So the backend is not known when the
outer plan runs, and `uv.lock` and `poetry.lock` do not record it either.
`docs/PACKAGING-FACTS.md` gives the measurement behind that statement.

The outer planner therefore registers **this planner** as a node of its graph,
rather than a build. The archive is an input of this derivation, so Nix
downloads it before this script runs. This script reads `pyproject.toml` out of
the archive, selects the backend from the same menu of artefacts that the
outer planner used, and submits a graph that builds the distribution.

`examples/nested` shows two levels of planners. This script is the second
level of that chain, and the graph that it
submits may hold a third: a backend that is itself a source distribution
becomes another node of kind `sdist`, which is another planner.
"""

from __future__ import annotations

import json
import os
import sys

from packaging.utils import canonicalize_name

from pydd.core import writer_from_environ
from pydd.modes import submit
from pydd.python import (
    Artifact,
    Plan,
    build_system,
    build_tools_from,
    choose,
    edits_from,
    env,
    has_native_sources,
    overrides_from,
    read_pyproject,
    target_from_environ,
)
from pydd.python import _build_systems as build_systems
from pydd.python._hook import requires_for_build, sites_of
from pydd.python._poetrylock import PoetryProject
from pydd.python._pylock import PylockProject
from pydd.python._script import ScriptProject


def main() -> int:
    store = writer_from_environ()

    inputs = json.loads(env("PYDD_INPUTS"))
    artifacts = [Artifact.from_json(row) for row in inputs["artifacts"]]
    target = target_from_environ()

    # The archive itself. It is an input of this derivation, so it is present,
    # and the node that builds it needs no fetch.
    archive = env("PYDD_SDIST")
    # **The row of this archive travels apart from the menu, and that is a
    # measurement and not a taste.** Every planner of one level reads the same
    # menu, so `default.nix` serialises that menu one time for the level. A row
    # inside the menu made each document different, and Nix then wrote a
    # quarter of a megabyte of JSON for each of the planners.
    subject: dict[str, str] = json.loads(env("PYDD_SDIST_ROW"))
    # **The project is not always at the top of the archive.** A URL source of
    # `uv.lock` names a subdirectory, and the archive of `pypa/hatch` holds more
    # than one project. The declaration to read, and the directory that the
    # backend runs in, are both below that subdirectory.
    subdirectory = subject.get("subdirectory", "")
    sdist = Artifact(
        package=subject["package"],
        version=subject["version"],
        filename=subject["filename"],
        kind="sdist",
        path=archive,
        subdirectory=subdirectory,
    )

    backend, requirements = build_system(read_pyproject(archive, subdirectory))
    print(f"plan-sdist: {sdist.filename} builds through {backend}", file=sys.stderr)

    # **A backend needs its own dependencies, and `requires` names only the top
    # of that closure.** `flit_core` needs nothing, and `hatchling` needs four
    # packages to import at all. A `uv.lock` holds the edges of every package
    # that it lists, so it expands the list when there is one.
    reader: build_systems.Reader | None = None
    edits = edits_from(inputs)
    if (spec := inputs.get("uv")) is not None:
        reader = build_systems.UvLockReader(spec["lock"], edits)
    elif (spec := inputs.get("poetry")) is not None:
        reader = PoetryProject.from_json(spec, edits)
    elif (spec := inputs.get("pylock")) is not None:
        reader = PylockProject.from_json(spec, edits)
    elif (spec := inputs.get("script")) is not None:
        reader = ScriptProject.from_json(spec, edits)
    requirements = build_systems.backend(reader, requirements, target, sdist.filename, inputs.get("buildSystems"))

    # **The tools travel with the inputs, so a nested planner has them too.**
    # A source distribution that holds C code needs a compiler wherever its
    # build node is, and this planner is what makes that node.
    plan = Plan(build_tools=build_tools_from(inputs), overrides=overrides_from(inputs))
    if has_native_sources(archive):
        plan.compile_with(sdist.package, inputs["tools"].get("compiler"))
    no_binary = frozenset(inputs.get("noBinary", []))

    # The installer, on the same terms as in the outer graph. The node is the
    # same wheel unpacked by the same script, so its content is the same and
    # content addressing gives it the same output path in both graphs.
    bootstrap = build_systems.installer(artifacts, inputs["installer"], target)
    if bootstrap is None:
        raise RuntimeError(f"the installer '{inputs['installer']}' is not in the menu")
    plan.installer = plan.artifact_node(bootstrap, "unpack")

    backend_path: list[str] = []
    for requirement in requirements:
        dependency = choose(artifacts, requirement, target, no_binary)
        if dependency is None:
            raise RuntimeError(f"{sdist.filename}: no artefact of the menu satisfies '{requirement}'")
        # **A backend that is itself a source distribution needs no special
        # case.** Its node is of kind `sdist`, and `graph.nix` turns that into
        # another planner, which is one more level of the same chain.
        backend_path.append(plan.artifact_node(dependency, dependency.kind))
        print(f"plan-sdist:   backend {dependency.filename} for {requirement}", file=sys.stderr)

    # **Two planners build one archive, and this one is either.** `backend`
    # stops at the backend's environment. `build` takes that environment, built,
    # asks the backend what more it needs, and adds that to the path.
    if os.environ.get("PYDD_ROLE") == "backend":
        root = plan.backend_node(f"{sdist.node_name}-backend", backend_path=backend_path)
    else:
        override = plan.overrides.get(canonicalize_name(sdist.package), {})
        extra = requires_for_build(
            hook="wheel",
            python=env("PYDD_PYTHON"),
            source=archive,
            subdirectory=subdirectory,
            backend=backend,
            sites=sites_of(env("PYDD_BACKEND")),
            config_settings=override.get("configSettings"),
            path=os.environ.get("PYDD_HOOK_PATH", ""),
            env=override.get("env"),
        )
        extra = [
            requirement
            for requirement in extra
            if target.accepts(str(requirement.marker) if requirement.marker else None)
        ]
        if extra:
            print(f"plan-sdist:   {backend} asks for {[str(requirement) for requirement in extra]}", file=sys.stderr)
            extra = build_systems.backend(reader, extra, target, sdist.filename, inputs.get("buildSystems"))
        for requirement in extra:
            dependency = choose(artifacts, requirement, target, no_binary)
            if dependency is None:
                raise RuntimeError(
                    f"{sdist.filename}: {backend} asks for '{requirement}' through get_requires_for_build_wheel, "
                    "and no artefact of the menu satisfies it. Add it to a dependency group of the project."
                )
            name = plan.artifact_node(dependency, dependency.kind)
            if name not in backend_path:
                backend_path.append(name)
        root = plan.artifact_node(sdist, "sdist-build", backend=backend, backend_path=backend_path)

    print(f"==> {len(plan.nodes)} nodes for {sdist.filename}", file=sys.stderr)

    submit(
        store,
        plan.document(
            root=root,
            system=env("PYDD_SYSTEM"),
            inputs=inputs,
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
