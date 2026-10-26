"""Share identical secondary-action lists without sharing execution targets."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.compression.policy import secondaryActions

if TYPE_CHECKING:
    from typing import Callable, Sequence

    from computer_use.interaction.read.presentation.records import NodeView


def action_fields(
    owner: NodeView, parts: Sequence[NodeView], render_actions: Callable[[NodeView], list[str]]
) -> list[str]:
    """Format available actions and retain their native action references.

    Args:
        owner: Source/projected node in this exact view; its native identity is unchanged.
        parts: Ordered source parts whose provenance remains available after composition.
        render_actions: Bound dependency for render actions; invoked only at its existing stage.
    """
    if not parts:
        actions = render_actions(owner)
        return ["Secondary Actions: " + "; ".join(actions)] if actions else []
    groups = {}
    for source in (owner, *parts):
        actions = secondaryActions(source)
        if not actions or not render_actions(source):
            continue
        # Names as well as descriptions must agree. Equal display labels alone
        # do not mean that performAction accepts the same native action name.
        key = json.dumps([actions, render_actions(source)], ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        groups.setdefault(key, []).append(source)
    fields, ordinary = [], []
    for sources in groups.values():
        if len(sources) > 1:
            fields.append(
                "Secondary Actions [elements "
                + reference_set(s["ref"] for s in sources)
                + "]: "
                + "; ".join(render_actions(sources[0]))
            )
        else:
            source = sources[0]
            suffix = "" if source is owner else " [element " + source["ref"] + "]"
            ordinary.extend(action + suffix for action in render_actions(source))
    if ordinary:
        fields.insert(0, "Secondary Actions: " + "; ".join(ordinary))
    return fields
