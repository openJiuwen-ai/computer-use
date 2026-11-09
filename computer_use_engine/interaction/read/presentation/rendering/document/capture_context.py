# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Identify newly captured system-sharing companions from complete source evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from computer_use.interaction.read.facts.capture_companion import sharing_control_signature
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.headers import ImageHeader, InterfaceHeader, MemberHeading
from computer_use.protocol.observation import Geometry, NativeNode


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
    if value is None or value.valid is not True:
        return None
    for component in (value.x, value.y, value.width, value.height):
        component_type = type(component)
        if component_type not in (int, float) or not math.isfinite(component):
            return None
    if value.width <= 0 or value.height <= 0:
        return None
    return value


def _body(document: DisplayDocument, position: int, window: MemberHeading) -> DisplayTree | None:
    """Find this exact member's retained root without crossing a following heading."""
    for part in document.parts[position + 1:]:
        if isinstance(part, DisplayText) and part.kind in ("interface_header", "member_heading", "captured_metadata"):
            break
        if not isinstance(part, DisplayTree):
            continue
        binding = tuple(
            (window.target or {}).get(key) for key in ("session", "instance", "root_window", "surface", "node")
        )
        request = part.prepared.request
        if not (part.scope == binding and len(binding) == 5 and all(binding[:4])):
            break
        if part.details or not request.public_api or not request.compact:
            break
        if request.share_descriptions:
            return part
        break
    return None


def _observation(directory: Mapping[str, object] | None) -> tuple[str, float, float] | None:
    """Read complete directory timing without treating missing metadata as evidence."""
    value = directory.get("observed") if directory is not None else None
    if not isinstance(value, Mapping):
        return None
    identity_type = type(value.get("id"))
    if identity_type is not str or not value["id"]:
        return None
    start, end = value.get("startedAt"), value.get("completedAt")
    start_type, end_type = type(start), type(end)
    if start_type not in (int, float) or end_type not in (int, float):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start > end:
        return None
    return value["id"], start, end


def _directory_members(directory: Mapping[str, object] | None) -> tuple[Mapping[str, object], ...] | None:
    """Use only complete macOS surface directories with independently typed entries."""
    if directory is None or directory.get("platform") != "macos" or directory.get("kind") != "surfaces":
        return None
    if directory.get("stopReasons") != () and directory.get("stopReasons") != []:
        return None
    members = directory.get("surfaces")
    if not isinstance(members, (tuple, list)) or any(not isinstance(item, Mapping) for item in members):
        return None
    windows = tuple(item.get("window") for item in members)
    for identity in windows:
        identity_type = type(identity)
        if identity_type is not str or not identity:
            return None
    if len(set(windows)) != len(windows):
        return None
    return tuple(members)


def _signature(tree: DisplayTree, window: MemberHeading) -> tuple[str, str] | None:
    """Check the complete small AppKit sharing-control signature without application names."""
    snapshot = tree.prepared.snapshot
    native = snapshot.native
    if native.backend != "macos" or native.session != (window.target or {}).get("session") or len(native.roots) != 1:
        return None
    token = native.roots[0]
    signature = sharing_control_signature(native, token)
    if signature is None or token != (window.target or {}).get("node"):
        return None
    _, button_token = signature
    root, button = native.nodes[token], native.nodes[button_token]
    if root.name != "Window":
        return None
    for issue in native.issues:
        path = tuple(issue.path)
        if len(path) < 2 or path[0] != "nodes":
            return None
        if path[1] not in (token, button_token):
            continue
        relative = path[2:]
        if not relative or relative[0] in ("name", "geometry", "actions"):
            return None
    frame, hit = _bounds(root), _bounds(button)
    if frame is None or hit is None or frame.space != hit.space:
        return None
    if not 20 <= frame.width <= 180 or not 12 <= frame.height <= 48:
        return None
    frame_values = (frame.x, frame.y, frame.width, frame.height)
    hit_values = (hit.x, hit.y, hit.width, hit.height)
    if any(abs(a - b) > 3 for a, b in zip(frame_values, hit_values)):
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
    if window.main or window.current or window.reported_input:
        return None
    if window.modal is not False or window.parent_state != "known" or window.parent is None:
        return None
    if not window.in_scope or not window.expanded:
        return None
    if image.kind != "composite" or not image.mapping_available:
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
    if left is None or right is None:
        return None
    if earlier is None or later is None:
        return None
    if before.get("session") != after.get("session") or before.get("application") != after.get("application"):
        return None
    if before.get("session") != (window.target or {}).get("session"):
        return None
    if any(item["window"] == window.native_window for item in left):
        return None
    matches = tuple(item for item in right if item["window"] == window.native_window)
    if len(matches) != 1:
        return None
    (member,) = matches
    if member.get("parent") != parent.native_window or member.get("parentState") != "known":
        return None
    if member.get("modal") is not False or member.get("kind") != "dialog":
        return None
    if member.get("nativeWindow") is not True or member.get("accessibility") is not True:
        return None
    if any(member.get(key) != (window.target or {}).get(key) for key in ("surface", "node")):
        return None

    def displayed_captures(owner: MemberHeading) -> list[Mapping[str, object]]:
        captures = []
        for item in image.captures:
            if item.get("ref") != owner.ref or item.get("window") != owner.native_window:
                continue
            if item.get("displayed") is True:
                captures.append(item)
        return captures

    parent_captures = displayed_captures(parent)
    child_captures = displayed_captures(window)
    if len(parent_captures) != 1 or len(child_captures) != 1 or not {parent.ref, window.ref} <= set(image.windows):
        return None
    for entry, owner in ((parent_captures[0], parent), (child_captures[0], window)):
        target, content = entry.get("target"), entry.get("capture")
        if not isinstance(target, Mapping) or not isinstance(content, Mapping):
            return None
        if (
            any(
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
    if not isinstance(capture, Mapping) or capture.get("kind") != "window":
        return None
    capture_id_type = type(capture.get("id"))
    if capture_id_type is not str:
        return None
    start, end = capture.get("startedAt"), capture.get("completedAt")
    start_type, end_type = type(start), type(end)
    if start_type not in (int, float) or end_type not in (int, float):
        return None
    if not earlier[2] <= start <= end <= later[1] or earlier[0] == later[0]:
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
    if frame is None or enclosing is None or frame.space != enclosing.space:
        return None
    if not 0 <= frame.x - enclosing.x <= min(180, enclosing.width / 2) or not 0 <= frame.y - enclosing.y <= 48:
        return None
    if (
        frame.x + frame.width > enclosing.x + enclosing.width
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
            positions = {}
            for position, item in enumerate(document.parts[start:], start):
                if not isinstance(item, DisplayText) or item.kind != "member_heading":
                    continue
                heading = item.values[0]
                if isinstance(heading, MemberHeading) and heading in header.windows:
                    positions[heading.ref] = position
            evidence = _evidence(document, header, image, index, window, positions)
            if evidence is not None:
                result[index] = evidence
    return result
