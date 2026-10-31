# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain standard non-window Raise mechanisms in same-capture action details."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import secondary_actions

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import NodeView, SnapshotFacts
    from computer_use.protocol.observation import NativeNode
    from computer_use.protocol.records import Record


_ATOMIC_ROLES = frozenset(
    (
        "button",
        "checkbox",
        "radio_button",
        "switch",
        "popup_button",
        "menu_button",
        "static_text",
        "image",
        "link",
        "text_field",
        "combo_box",
    )
)


@dataclass(frozen=True)
class ControlRaise:
    """One standard Raise occurrence, independently retained under its original source."""

    session: str
    observation: str
    ref: str
    native: str
    index: int
    key: str
    kind: str

    def matches(self, node: NodeView, action: Record) -> bool:
        return (
            node["ref"] == self.ref and action["name"] == self.key and action.get("kind", action["name"]) == self.kind
        )


def _ordinary_context(native: NativeNode) -> bool:
    """Select primitive controls whose structure adds no independent interaction.

    A source's positional index and orientation retain their own fact destinations.
    Their presence does not make its standard Raise operation a business action.
    Collection, document, table and scroll semantics keep the operation inline.
    """
    if native.role not in _ATOMIC_ROLES or native.states.modal is True:
        return False
    structure = native.structure
    if structure is not None and any(
        getattr(structure, item.name) is not None and not (item.name == "level" and structure.level == 0)
        for item in fields(structure)
        if item.name not in ("orientation", "index")
    ):
        return False
    focus = [action for action in native.actions or () if action.key is None and action.kind == "focus"]
    return bool(focus) and all(
        action.available is True
        and action.target is None
        and action.label is None
        and action.description is None
        and not action.shortcuts
        for action in focus
    )


def capture_control_raises(snapshot: SnapshotFacts, projection: ProjectionResult) -> tuple[ControlRaise, ...]:
    """Select atomic-control Raise records without equating Raise with focus.

    Complete native catalogues remain callable by their original key. Window,
    viewport and unknown roles keep their Raise label; generic focus is only
    context for this display choice and never accounts for the Raise action.
    """
    if snapshot.native.backend != "macos":
        return ()
    facts = {node["node"]: node for node in snapshot.facts["nodes"]}
    blocked = {
        issue.path[1]
        for issue in snapshot.native.issues
        if len(issue.path) >= 2
        and issue.path[0] == "nodes"
        and (len(issue.path) == 2 or issue.path[2] in ("role", "actions"))
    }
    result = []
    for ref, node in projection["nativeNodes"].items():
        token = snapshot.refs.get(ref)
        native = snapshot.native.nodes.get(token)
        if (
            native is None
            or native.actions is None
            or token in blocked
            or node.get("actionsUnavailable")
            or facts.get(token, {}).get("actions", {}).get("state") != "complete"
            or not _ordinary_context(native)
        ):
            continue
        actions = [action for action in native.actions if action.key == "AXRaise"]
        if (
            not actions
            or any(
                action.kind != "raise" or action.target is not None or action.shortcuts or action.available is False
                for action in actions
            )
            or not any(
                action["name"] == "AXRaise" and action.get("kind", action["name"]) == "raise"
                for action in secondary_actions(node)
            )
        ):
            continue
        result.extend(
            ControlRaise(snapshot.native.session, snapshot.native.id, ref, token, index, action.key, action.kind)
            for index, action in enumerate(native.actions)
            if action.key == "AXRaise"
        )
    return tuple(result)


def control_raise_selected(node: NodeView, action: Record, selected: tuple[ControlRaise, ...]) -> bool:
    """Match only a supplied same-source standard mechanism plan."""
    return any(item.matches(node, action) for item in selected)
