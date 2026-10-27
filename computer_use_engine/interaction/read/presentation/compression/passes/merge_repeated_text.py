"""Merge repeated text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.captions import passive_caption
from computer_use.interaction.read.presentation.compression.policy import CONTROLS, REGIONS, sources
from computer_use.interaction.read.presentation.policy import editing
from computer_use.interaction.read.presentation.tree import VALUE_PREVIEW_LIMIT, has_incomplete_children
from computer_use.protocol.primitives import required_item, utf16_length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.compression.model import Scope
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def merge_repeated_text(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, scope: Scope) -> list[ViewNode]:
    """Fold passive text children that repeat a complete displayed field.

    For eligible fact-backed nodes outside tables and editing scopes, match each child caption
    against the parent's whole textual fields. Adopt the child's source evidence into those
    fields and record its folded destination.
    """
    if not node.get("_factBacked") or scope.in_table or editing(node):
        return [node]
    if (
        node["role"] not in REGIONS | CONTROLS | {"heading", "staticText"}
        or node.get("preserveGroup")
        or has_incomplete_children(node)
    ):
        return [node]

    # Adopt only whole, already displayed fields, not substrings or combined
    # captions with several different components. Keeping this invariant lets
    # later passes safely propagate the same textSources through another fold.
    destinations = {}
    for field in ("name", "description", "value"):
        # A checkbox's native "1" may render as "on"; a heading's numeric
        # value is its level. Neither is a duplicate textual caption.
        if field == "value" and (node.get("valueIsNumber") or node["role"] in ("checkBox", "switch", "radioButton")):
            continue
        value = node.get(field)
        parts = [part for part in node["textSources"] if part["field"] == field]
        matches_value = (
            True
            if (
                isinstance(value, str)
                and value
                and utf16_length(value) <= VALUE_PREVIEW_LIMIT
                and (not parts or all(part["text"] == value for part in parts))
            )
            else False
        )
        if matches_value:
            destinations.setdefault(value, field)

    retained = []
    for child in node["children"]:
        parts = sources(child)
        matches_child = (
            True
            if (
                child["role"] != "staticText"
                or child["children"]
                or not passive_caption(state, edit, child, node)
                or any(child.get(key) for key in ("labelTargets", "titleRef", "valueIsNumber"))
                or not parts
                or any(part["text"] not in destinations for part in parts)
            )
            else False
        )
        if matches_child:
            retained.append(child)
            continue
        for part in parts:
            field = required_item(destinations, part["text"])
            if not any(p["field"] == field for p in node["textSources"]):
                # Value was previously recorded on its own ref. Include it as
                # well as the caption source, so neither field loses coverage.
                edit.text_source(node, dict(sourceRef=node["ref"], attribute=field, field=field, text=node[field]))
            edit.adopt(node, [part], field)
        edit.fold(child, node, "label")
    edit.children(node, retained)
    return [node]
