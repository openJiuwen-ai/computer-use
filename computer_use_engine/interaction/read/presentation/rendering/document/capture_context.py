# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Identify newly captured system-sharing companions from complete source evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.headers import ImageHeader, InterfaceHeader, MemberHeading
from computer_use.protocol.observation import Geometry, NativeNode, Relation


@dataclass(frozen=True)
class CaptureCompanion:
    """Evidence for one new macOS sharing control accompanying a captured window.

    Attributes:
        window: Published number of the retained companion window.
        parent: Published number of the independently captured parent.
        root: Original element reference of the companion's complete root.
        button: Original reference of its sole sharing-session control.
        capture: Parent capture between the before and after window directories.
        before: Original directory observation preceding that capture.
        after: Original directory observation where the companion first appears.
    """

    window: int
    parent: int
    root: str
    button: str
    capture: str
    before: str
    after: str


def _bounds(node: NativeNode) -> Geometry | None:
    """Require a complete finite native frame in its original coordinate space."""
    value = node.geometry.get("bounds")
    if (
        value is None
        or value.valid is not True
        or any(
            type(item) not in (int, float) or not math.isfinite(item)
            for item in (value.x, value.y, value.width, value.height)
        )
        or value.width <= 0
        or value.height <= 0
    ):
        return None
    return value


def _complete(relation: Relation | None, count: int) -> bool:
    """Require a full reported child list rather than a candidate or sampled view."""
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


def _body(document: DisplayDocument, position: int, window: MemberHeading) -> DisplayTree | None:
    """Find this exact member's retained root without crossing a following heading."""
    for part in document.parts[position + 1 :]:
        if isinstance(part, DisplayText) and part.kind in ("interface_header", "member_heading", "captured_metadata"):
            break
        if not isinstance(part, DisplayTree):
            continue
        binding = tuple(
            (window.target or {}).get(key) for key in ("session", "instance", "root_window", "surface", "node")
        )
        request = part.prepared.request
        if (
            part.scope == binding
            and len(binding) == 5
            and all(binding[:4])
            and not part.details
            and request.public_api
            and request.compact
            and request.share_descriptions
        ):
            return part
        break
    return None


def _observation(directory: Mapping[str, object] | None) -> tuple[str, float, float] | None:
    """Read complete directory timing without treating missing metadata as evidence."""
    value = directory.get("observed") if directory is not None else None
    if not isinstance(value, Mapping) or type(value.get("id")) is not str or not value["id"]:
        return None
    start, end = value.get("startedAt"), value.get("completedAt")
    if (
        type(start) not in (int, float)
        or type(end) not in (int, float)
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start > end
    ):
        return None
    return value["id"], start, end


def _directory_members(directory: Mapping[str, object] | None) -> tuple[Mapping[str, object], ...] | None:
    """Use only complete macOS surface directories with independently typed entries."""
    if (
        directory is None
        or directory.get("platform") != "macos"
        or directory.get("kind") != "surfaces"
        or directory.get("stopReasons") != ()
        and directory.get("stopReasons") != []
    ):
        return None
    members = directory.get("surfaces")
    if not isinstance(members, (tuple, list)) or any(not isinstance(item, Mapping) for item in members):
        return None
    windows = tuple(item.get("window") for item in members)
    if any(type(item) is not str or not item for item in windows) or len(set(windows)) != len(windows):
        return None
    return tuple(members)


def _empty_payload(node: NativeNode) -> bool:
    """Leave business text, URLs and independently valued dialogs in their normal scope."""
    return all(
        getattr(node, key) is None
        for key in ("description", "help", "placeholder", "url", "control_value", "numeric_value", "text")
    )


def _signature(tree: DisplayTree, window: MemberHeading) -> tuple[str, str] | None:
    """Check the complete small AppKit sharing-control signature without application names."""
    snapshot = tree.prepared.snapshot
    native = snapshot.native
    if native.backend != "macos" or native.session != (window.target or {}).get("session") or len(native.roots) != 1:
        return None
    token = native.roots[0]
    root = native.nodes.get(token)
    if (
        root is None
        or token != (window.target or {}).get("node")
        or root.role != "dialog"
        or root.name != "Window"
        or root.states.modal is not False
        or root.states.focused is not False
        or not _empty_payload(root)
        or not _complete(root.relations.get("children"), 1)
    ):
        return None
    (button_token,) = root.relations["children"].targets
    button = native.nodes.get(button_token)
    if (
        button is None
        or button.role != "button"
        or button.name != "WindowSharingSessionButton"
        or button.states.enabled is not True
        or button.states.focused is not False
        or not _empty_payload(button)
        or not _complete(button.relations.get("children"), 0)
        or not any(
            action.kind == "invoke" and action.key == "AXPress" and action.available is not False
            for action in button.actions or ()
        )
    ):
        return None
    for issue in native.issues:
        path = tuple(issue.path)
        if len(path) < 2 or path[0] != "nodes":
            return None
        if path[1] not in (token, button_token):
            continue
        relative = path[2:]
        if not relative or relative[0] in ("role", "name", "geometry", "actions"):
            return None
        required_states = ("focused", "modal") if path[1] == token else ("focused", "enabled")
        if (
            relative[0] == "states"
            and (len(relative) == 1 or relative[1] in required_states)
            or relative[0] == "relations"
            and (len(relative) == 1 or relative[1] == "children")
        ):
            return None
    frame, hit = _bounds(root), _bounds(button)
    if (
        frame is None
        or hit is None
        or frame.space != hit.space
        or not 20 <= frame.width <= 180
        or not 12 <= frame.height <= 48
        or any(
            abs(a - b) > 3
            for a, b in zip((frame.x, frame.y, frame.width, frame.height), (hit.x, hit.y, hit.width, hit.height))
        )
    ):
        return None
    aliases = {value: ref for ref, value in snapshot.refs.items()}
    if token not in aliases or button_token not in aliases:
        return None
    return aliases[token], aliases[button_token]


def _evidence(
    document: DisplayDocument,
    header: InterfaceHeader,
    image: ImageHeader,
    position: int,
    window: MemberHeading,
    positions: Mapping[int, int],
) -> CaptureCompanion | None:
    """Correlate original directories, exact bindings, image pieces and native facts."""
    if (
        window.main
        or window.current
        or window.reported_input
        or window.modal is not False
        or window.parent_state != "known"
        or window.parent is None
        or not window.in_scope
        or not window.expanded
        or image.kind != "composite"
        or not image.mapping_available
    ):
        return None
    parents = tuple(item for item in header.windows if item.ref == window.parent)
    if len(parents) != 1:
        return None
    (parent,) = parents
    if not parent.in_scope or not parent.expanded or parent.scope_confirmed is not True:
        return None
    before, after = parent.source_directory, parent.rechecked_directory
    left, right = _directory_members(before), _directory_members(after)
    earlier, later = _observation(before), _observation(after)
    if (
        left is None
        or right is None
        or earlier is None
        or later is None
        or before.get("session") != after.get("session")
        or before.get("application") != after.get("application")
        or before.get("session") != (window.target or {}).get("session")
        or any(item["window"] == window.native_window for item in left)
    ):
        return None
    matches = tuple(item for item in right if item["window"] == window.native_window)
    if len(matches) != 1:
        return None
    (member,) = matches
    if (
        member.get("parent") != parent.native_window
        or member.get("parentState") != "known"
        or member.get("modal") is not False
        or member.get("kind") != "dialog"
        or member.get("nativeWindow") is not True
        or member.get("accessibility") is not True
        or any(member.get(key) != (window.target or {}).get(key) for key in ("surface", "node"))
    ):
        return None
    parent_captures = tuple(
        item
        for item in image.captures
        if item.get("ref") == parent.ref
        and item.get("window") == parent.native_window
        and item.get("displayed") is True
    )
    child_captures = tuple(
        item
        for item in image.captures
        if item.get("ref") == window.ref
        and item.get("window") == window.native_window
        and item.get("displayed") is True
    )
    if len(parent_captures) != 1 or len(child_captures) != 1 or not {parent.ref, window.ref} <= set(image.windows):
        return None
    for entry, owner in ((parent_captures[0], parent), (child_captures[0], window)):
        target, content = entry.get("target"), entry.get("capture")
        if (
            not isinstance(target, Mapping)
            or not isinstance(content, Mapping)
            or any(
                target.get(key) != (owner.target or {}).get(key)
                for key in ("session", "instance", "root_window", "surface")
            )
            or content.get("scope") != target.get("surface")
            or content.get("id") != target.get("capture")
        ):
            return None
    pieces = tuple(key for key, _ in image.pieces)
    if (
        pieces.count(parent.native_window) != 1
        or pieces.count(window.native_window) != 1
        or (window.native_window, (window.target or {}).get("surface"), parent.native_window, "known", False)
        not in image.membership
    ):
        return None
    capture = parent_captures[0].get("capture")
    if not isinstance(capture, Mapping) or capture.get("kind") != "window" or type(capture.get("id")) is not str:
        return None
    start, end = capture.get("startedAt"), capture.get("completedAt")
    if (
        type(start) not in (int, float)
        or type(end) not in (int, float)
        or not earlier[2] <= start <= end <= later[1]
        or earlier[0] == later[0]
    ):
        return None
    tree = _body(document, position, window)
    parent_position = positions.get(parent.ref)
    parent_tree = _body(document, parent_position, parent) if parent_position is not None else None
    if tree is None or parent_tree is None or not parent_tree.prepared.snapshot.native.roots:
        return None
    signature = _signature(tree, window)
    if signature is None:
        return None
    native = parent_tree.prepared.snapshot.native
    parent_node = native.nodes.get(native.roots[0])
    child_node = tree.prepared.snapshot.native.nodes[tree.prepared.snapshot.native.roots[0]]
    frame, enclosing = _bounds(child_node), _bounds(parent_node) if parent_node is not None else None
    if (
        frame is None
        or enclosing is None
        or frame.space != enclosing.space
        or not 0 <= frame.x - enclosing.x <= min(180, enclosing.width / 2)
        or not 0 <= frame.y - enclosing.y <= 48
        or frame.x + frame.width > enclosing.x + enclosing.width
        or frame.y + frame.height > enclosing.y + enclosing.height
    ):
        return None
    return CaptureCompanion(window.ref, parent.ref, *signature, capture["id"], earlier[0], later[0])


def capture_companions(document: DisplayDocument) -> Mapping[int, CaptureCompanion]:
    """Return member-heading positions with complete same-observation capture evidence.

    The private macOS control name is corroborating evidence, never a deletion
    rule. Incomplete directories, preexisting names, modals and ordinary popups
    retain their original presentation. The plan only adds a capture-status label.
    """
    result: dict[int, CaptureCompanion] = {}
    header: InterfaceHeader | None = None
    image: ImageHeader | None = None
    start = 0
    for index, part in enumerate(document.parts):
        if not isinstance(part, DisplayText):
            continue
        if part.kind == "interface_header":
            header = part.values[0] if isinstance(part.values[0], InterfaceHeader) else None
            image, start = None, index
        elif part.kind == "image_header":
            image = part.values[0] if isinstance(part.values[0], ImageHeader) else None
        elif part.kind == "member_heading" and header is not None and image is not None:
            window = part.values[0]
            if not isinstance(window, MemberHeading) or window not in header.windows:
                continue
            positions = {
                item.values[0].ref: position
                for position, item in enumerate(document.parts[start:], start)
                if isinstance(item, DisplayText)
                and item.kind == "member_heading"
                and isinstance(item.values[0], MemberHeading)
                and item.values[0] in header.windows
            }
            evidence = _evidence(document, header, image, index, window, positions)
            if evidence is not None:
                result[index] = evidence
    return result
