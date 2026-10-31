# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select exact standard macOS menu mechanisms for same-capture details."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import secondary_actions

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import NodeView, SnapshotFacts
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class MenuMechanism:
    """One original action occurrence selected from a complete source catalogue."""

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


def capture_menu_mechanisms(snapshot: SnapshotFacts, projection: ProjectionResult) -> tuple[MenuMechanism, ...]:
    """Select menu-only AX records without changing actions or control semantics.

    Require a complete role/catalogue, exact standard selectors and local targets.
    Pick is incidental only when this same source also exposes a real AXPress
    or a confirmed SDK click. Unknown sources keep their original labels.
    """
    if snapshot.native.backend != "macos":
        return ()
    facts = {node["node"]: node for node in snapshot.facts["nodes"]}
    result = []
    for ref, node in projection["nativeNodes"].items():
        token = snapshot.refs.get(ref)
        native = snapshot.native.nodes.get(token)
        if (
            native is None
            or native.role not in ("menu", "menu_bar", "menu_item", "menu_bar_item")
            or native.actions is None
            or node.get("actionsUnavailable")
            or facts.get(token, {}).get("actions", {}).get("state") != "complete"
        ):
            continue
        # A node-wide failure or any role/action issue leaves the mechanism
        # uncertain. Collection-wide partial membership does not invalidate an
        # already captured source's complete role and catalogue.
        if any(
            len(issue.path) >= 2
            and issue.path[:2] == ("nodes", token)
            and (len(issue.path) == 2 or issue.path[2] in ("role", "actions"))
            for issue in snapshot.native.issues
        ):
            continue
        secondary = {(item["name"], item.get("kind", item["name"])) for item in secondary_actions(node)}
        primary = node.get("capabilities", {}).get("click") == "available" or any(
            action.key == "AXPress"
            and action.kind == "invoke"
            and action.available is not False
            and action.target is None
            for action in native.actions
        )
        for index, action in enumerate(native.actions):
            if (
                (action.key, action.kind) not in secondary
                or action.target is not None
                or action.shortcuts
                or action.key == "AXCancel"
                and action.kind != "cancel"
                or action.key == "AXPick"
                and (
                    action.kind not in ("custom", "pick", "select")
                    or native.role not in ("menu_item", "menu_bar_item")
                    or not primary
                )
                or action.key not in ("AXCancel", "AXPick")
            ):
                continue
            # Normalized action lines do not carry an ordinal. A reused key with
            # another target or semantic kind must therefore remain fully inline.
            if any(
                other.key == action.key and (other.kind != action.kind or other.target is not None or other.shortcuts)
                for other in native.actions
            ):
                continue
            result.append(
                MenuMechanism(snapshot.native.session, snapshot.native.id, ref, token, index, action.key, action.kind)
            )
    return tuple(result)


def menu_action_selected(node: NodeView, action: Record, selected: tuple[MenuMechanism, ...]) -> bool:
    """Match only the explicit source occurrence plan supplied by snapshot assembly."""
    return any(item.matches(node, action) for item in selected)
