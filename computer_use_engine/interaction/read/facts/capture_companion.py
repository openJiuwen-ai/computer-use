# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Positive native sharing-control facts, independent of display and window geometry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.protocol.observation import NativeNode, NativeObservation, Relation

# Actions and child relations retain their own complete query contracts.
SHARING_CONTROL_FIELDS = (
    "AXRole",
    "AXSubrole",
    "AXTitle",
    "AXDescription",
    "AXHelp",
    "AXURL",
    "AXValue",
    "AXEnabled",
    "AXFocused",
    "AXModal",
    "AXSelectedText",
    "AXPlaceholderValue",
)
_ABSENT = {"absent", "unsupported"}
_PAYLOAD_FIELDS = ("description", "help", "placeholder", "url", "control_value", "numeric_value", "text")
_ABSENCE_PATHS = (
    ("description",),
    ("help",),
    ("placeholder",),
    ("url",),
    ("control_value", "value"),
    ("text", "selected_text"),
)


def _complete_children(relation: Relation | None, count: int) -> bool:
    return (
        relation is not None
        and relation.kind == "children"
        and relation.evidence in ("reported", "verified")
        and relation.enumeration == "enumerated"
        and relation.offset in (None, 0)
        and relation.total == count
        and len(relation.targets) == count
        and relation.positions in (None, tuple(range(count)))
    )


def _empty_payload(native: NativeObservation, token: str, node: NativeNode) -> bool:
    if any(getattr(node, key) is not None for key in _PAYLOAD_FIELDS):
        return False
    # A silent missing value is unknown. Each queried content channel needs
    # explicit absent/unsupported evidence; errors cannot stand in for absence.
    return all(
        any(issue.state in _ABSENT for issue in native.issue_index.applicable(("nodes", token, *path)))
        for path in _ABSENCE_PATHS
    )


def sharing_control_signature(native: NativeObservation, root_token: str) -> tuple[str, str] | None:
    """Identify a complete, nonmodal sharing control by original native identities.

    This is a control signature, not proof that a window belongs to the SDK.
    Capture/parent timing and exact window bindings remain the caller's evidence.
    Incomplete facts return None without refusing observation or input.
    """
    if native.backend != "macos" or native.roots != (root_token,):
        return None
    root = native.nodes.get(root_token)
    if root is None or root.role != "dialog":
        return None
    if root.states.modal is not False or root.states.focused is not False:
        return None
    if not _empty_payload(native, root_token, root) or not _complete_children(root.relations.get("children"), 1):
        return None
    (button_token,) = root.relations["children"].targets
    button = native.nodes.get(button_token)
    if button is None or button.role != "button" or button.name != "WindowSharingSessionButton":
        return None
    if button.states.enabled is not True or button.states.focused is not False:
        return None
    if not _empty_payload(native, button_token, button) or not _complete_children(button.relations.get("children"), 0):
        return None
    if not any(
        action.kind == "invoke" and action.key == "AXPress" and action.available is not False
        for action in button.actions or ()
    ):
        return None
    required = {
        root_token: (("role",), ("states", "modal"), ("states", "focused"), ("relations", "children")),
        button_token: (
            ("role",),
            ("name",),
            ("actions",),
            ("states", "enabled"),
            ("states", "focused"),
            ("relations", "children"),
        ),
    }
    for issue in native.issues:
        path = issue.path
        if path in ((), ("nodes",)):
            return None
        if len(path) < 2 or path[0] != "nodes" or path[1] not in required:
            continue
        relative = path[2:]
        if not relative or any(
            relative[: len(key)] == key or key[: len(relative)] == relative for key in required[path[1]]
        ):
            return None
        if relative[0] in _PAYLOAD_FIELDS and issue.state not in _ABSENT:
            return None
    return root_token, button_token
