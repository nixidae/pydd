"""Released Nix: a planner writes one derivation, so other planners write the rest.

The environment under an application is the root of a planner that the host
made (`lib/modes/out.nix`). This planner makes the same plan as every other
mode, and puts a node of kind `planned` in the place of that environment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...python._plan import Plan


def root(plan: Plan, inputs: dict[str, Any], name: str, application: str | Sequence[str] | None) -> str:
    """Name the node that this planner submits, with the environment planned."""
    submitted = plan.root(name, application=application)
    venv_planner: dict[str, str] | None = inputs.get("venvPlanner")
    if application is not None and venv_planner is not None:
        plan.replace(f"{name}-env", plan.planned_entry(f"{name}-env", venv_planner))
    return submitted
