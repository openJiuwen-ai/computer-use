# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Find mutually unique local captions without editing source semantics."""

from __future__ import annotations

from collections.abc import Sequence

from computer_use.interaction.read.presentation.records import NodeView
from computer_use.interaction.read.presentation.policy import independent_actions
from computer_use.interaction.read.presentation.compression.policy import ordinary_control
from computer_use.interaction.read.presentation.spatial.geometry import Box, GeometryIndex
from computer_use.interaction.read.presentation.spatial.model import LabelMatch
from computer_use.interaction.read.presentation.spatial.policy import (
    CONTAINED_CAPTIONS,
    FIELDS,
    caption_target,
    form_control,
)
from computer_use.protocol.primitives import utf16_length
from computer_use.interaction.read.presentation.tree import VALUE_PREVIEW_LIMIT


def caption_competitor(node: NodeView) -> bool:
    """Recognize controls that can make a nearby caption's ownership ambiguous.

    Native actions, available input routes and explicit control roles participate
    even when this presentation rule cannot assign a caption to that control.
    """
    if node["role"] in ("window", "application"):
        return False
    return (
        node["role"]
        in CONTAINED_CAPTIONS
        | {
            "slider",
            "scrollBar",
            "incrementor",
            "tab",
            "closeButton",
            "zoomButton",
            "minimizeButton",
            "disclosureTriangle",
        }
        or ordinary_control(node)
        or independent_actions(node)
        or "available" in node.get("capabilities", {}).values()
        or any(node.get(key) for key in ("focusable", "selectable", "expandable", "submenu"))
    )


def referenceable_caption(node: NodeView) -> bool:
    """Recognize complete passive text that can remain an independent visual label.

    Provider metadata and independent value fields stay on their original node.
    A reference does not require those fields to be safe for caption folding.
    """
    value = node.get("value")
    return (
        node["role"] == "staticText"
        and not node["children"]
        and isinstance(value, str)
        and bool(value.strip())
        and "value" in node.get("_completeTextValues", ())
        and utf16_length(value) <= VALUE_PREVIEW_LIMIT
        and not caption_competitor(node)
        and not any(
            node.get(key)
            for key in (
                "focused",
                "selected",
                "actionsUnavailable",
                "valueExpected",
                "incompleteLabelRelation",
                "modal",
            )
        )
        and "control" not in node
    )


def relation(
    label: Box, target: Box, role: str, *, competing_only: bool = False, field_like: bool | None = None
) -> str | None:
    """Recognize supported captions or a competing control's plausible nearby text.

    Competing-only controls can veto a match in every supported direction. They
    cannot acquire a caption through this rule.
    """
    if not label.comparable(target):
        return None
    if target.contains(label):
        return "inside"
    if (
        not (role in FIELDS if field_like is None else field_like)
        and not competing_only
        or label.intersection_area(target) > 0
    ):
        return None
    if label.vertical_overlap(target) >= 0.75:
        if 0 <= target.x - label.right <= 2 * label.height:
            return "left"
        if (
            role in ("checkBox", "radioButton", "switch") or competing_only
        ) and 0 <= label.x - target.right <= 2 * label.height:
            return "right"
    if (
        0 <= target.y - label.bottom <= 0.75 * label.height
        and abs(target.x - label.x) <= 0.5 * label.height
        and label.horizontal_overlap(target) >= 0.9
    ):
        return "above"
    return None


def unique_matches(
    labels: Sequence[NodeView], targets: Sequence[NodeView], geometry: GeometryIndex
) -> tuple[LabelMatch, ...]:
    """Return pairs with one candidate in each direction, preferring full containment.

    No distance tie-break is used: two plausible controls or captions retain their
    original entries instead of choosing a possibly unrelated input field.
    """
    by_label: dict[str, list[LabelMatch]] = {}
    by_target: dict[str, list[LabelMatch]] = {}
    for label in labels:
        box = geometry.boxes.get(label["ref"])
        if box is None:
            continue
        candidates = []
        for target in targets:
            target_box = geometry.boxes.get(target["ref"])
            if target_box is None:
                continue
            kind = relation(
                box,
                target_box,
                target["role"],
                competing_only=not caption_target(target),
                field_like=form_control(target),
            )
            if kind is not None:
                candidates.append(LabelMatch(label["ref"], target["ref"], kind))
        contained = [item for item in candidates if item.relation == "inside"]
        by_label[label["ref"]] = contained or candidates
        for item in by_label[label["ref"]]:
            by_target.setdefault(item.target, []).append(item)
    return tuple(items[0] for items in by_label.values() if len(items) == 1 and len(by_target[items[0].target]) == 1)
