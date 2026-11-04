# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Evidence for complex records with real owners, content and independent controls."""

from __future__ import annotations

from dataclasses import dataclass

from computer_use.interaction.read.presentation.interface.helpers import CONTROL_ROLES, FIELD_ROLES, same_scope
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.spatial.geometry import Box


_CONTENT = frozenset(("staticText", "heading", "image"))
_CONTROLS = (CONTROL_ROLES | FIELD_ROLES) - {"link"}
_BOUNDARIES = frozenset(
    (
        "window",
        "sheet",
        "table",
        "outline",
        "list",
        "row",
        "cell",
        "column",
        "document",
        "webArea",
        "scrollArea",
        "menu",
        "menuBar",
    )
)


@dataclass(frozen=True)
class RecordCandidate:
    """One complete native record and the local proof for grouping its peers.

    Attributes:
        key: Existing layout block retaining the record's real target.
        ref: Original group or row reference.
        parent: Confirmed native owner shared by neighboring candidate records.
        shape: Ordered content roles and caption wrappers, excluding optional controls.
        bounds: Complete record rectangle in a declared comparable coordinate space.
        complex: Whether long text or nested content needs a record block.
    """

    key: str
    ref: str
    parent: str
    shape: tuple[object, ...]
    bounds: Box
    complex: bool


def confirmed_children(draft: InterfaceDraft, parent: str, children: tuple[str, ...]) -> bool:
    """Require one uncontested, complete native membership in the captured order."""
    matches = tuple(
        relation for relation in draft.relations if relation.source == parent and relation.kind == "children"
    )
    return bool(matches) and all(
        relation.complete and relation.confirmed and relation.targets == children for relation in matches
    )


def known_control(draft: InterfaceDraft, ref: str) -> bool:
    """Recognize a reported control operation without guessing from its geometry."""
    node = draft.projected_nodes[ref]
    atom = draft.atoms.atoms[ref]
    reported = any(
        node.get("capabilities", {}).get(operation) == "available" for operation in ("click", "typeText", "replaceText")
    )
    return (
        node["role"] in _CONTROLS
        and (bool(atom.actions) or reported)
        or not node["children"]
        and node["role"] not in _BOUNDARIES | {"link"}
        and reported
    )


def record_candidate(draft: InterfaceDraft, block: InterfaceBlock) -> RecordCandidate | None:
    """Find a bounded native group whose repeated content can organize real controls.

    Text paragraphs and ordinary text-only lists provide no control evidence.
    Optional controls are kept outside the content-shape comparison so an extra
    button never needs to be discarded to fit a record template.
    """
    ref = block.primary_ref
    if (
        draft.atoms is None
        or ref is None
        or block.origin != "native"
        or block.layout != "tree"
        or block.kind not in ("tree", "record")
        or draft.projected_nodes[ref]["role"] not in ("group", "row")
        or not block.children
    ):
        return None
    parent = draft.source.parents.get(ref)
    if (
        parent is None
        or draft.projected_parents.get(ref) != parent
        or draft.source.native_nodes[parent]["role"] in ("table", "outline")
        or sum(child["role"] in ("group", "row") for child in draft.source.native_nodes[parent]["children"]) < 2
        or not confirmed_children(
            draft, parent, tuple(child["ref"] for child in draft.source.native_nodes[parent]["children"])
        )
    ):
        return None
    bounds = draft.source.geometry.boxes.get(ref)
    outer = draft.source.geometry.boxes.get(parent)
    if bounds is None or outer is None or not outer.contains(bounds):
        return None
    refs: list[str] = []
    seen: set[str] = set()
    content: dict[str, tuple[object, ...]] = {}
    has_control = False
    complex_content = False
    pending = [ref]
    while pending:
        current = pending.pop()
        if current in seen:
            return None
        seen.add(current)
        refs.append(current)
        node = draft.projected_nodes[current]
        evidence = draft.evidence[current]
        box = draft.source.geometry.boxes.get(current)
        if (
            evidence.incomplete
            or evidence.hidden
            or box is None
            or not bounds.contains(box)
            or current != ref
            and node["role"] in _BOUNDARIES
            or node.get("modal") is True
            or node.get("blockedBy")
        ):
            return None
        native_children = tuple(child["ref"] for child in draft.source.native_nodes[current]["children"])
        if native_children and not confirmed_children(draft, current, native_children):
            return None
        has_control |= known_control(draft, current)
        atom = draft.atoms.atoms[current]
        complex_content |= current != ref and bool(node["children"])
        complex_content |= any(
            isinstance(slot.value, str) and (len(slot.value) > 160 or "\n" in slot.value or "\r" in slot.value)
            for slot in atom.slots
        )
        pending.extend(reversed(node["children"]))
    if not has_control or not same_scope(draft, (parent, *refs)):
        return None
    for current in reversed(refs):
        node = draft.projected_nodes[current]
        if known_control(draft, current):
            content[current] = ()
            continue
        children = tuple(content[child] for child in node["children"] if content[child])
        named = any(isinstance(node.get(key), str) and node[key] for key in ("name", "value", "description"))
        role = node["role"]
        if named and role in _CONTENT:
            content[current] = (role, children)
        elif named and role == "group":
            content[current] = ("caption", children)
        else:
            content[current] = children
    # The record owner's title alone cannot turn unrelated controls into records.
    shape = tuple(content[child] for child in draft.projected_nodes[ref]["children"] if content[child])
    if not shape:
        return None
    return RecordCandidate(block.key, ref, parent, shape, bounds, complex_content)


def peer_axis(left: RecordCandidate, right: RecordCandidate) -> str | None:
    """Recognize a short local row or column in the existing source order."""
    a, b = left.bounds, right.bounds
    if left.parent != right.parent or left.shape != right.shape or not a.comparable(b) or a.intersection_area(b) > 0:
        return None
    if b.y >= a.bottom and a.horizontal_overlap(b) >= 0.6 and b.y - a.bottom <= max(a.height, b.height):
        return "vertical"
    if b.x >= a.right and a.vertical_overlap(b) >= 0.6 and b.x - a.right <= max(a.width, b.width):
        return "horizontal"
    return None
