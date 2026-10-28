# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep composite controls' repeated caption facts on their retained descendants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.display_boundaries import display_boundary_reasons
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.tree import has_incomplete_children, VALUE_PREVIEW_LIMIT
from computer_use.protocol.primitives import utf16_length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import (
        FactKey,
        ProjectionEditor,
        ProjectionView,
        ViewNode,
    )
    from computer_use.protocol.records import Record


_PRIMARY_ACTIONS = frozenset(("invoke", "select", "toggle"))


def _complete_path(view: ProjectionView, owner: ViewNode, descendant: ViewNode) -> bool:
    """Require a complete original containment path inside the owner's scope."""
    evidence = view.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence) or owner["ref"] not in evidence.complete_children:
        return False
    current, seen = descendant["ref"], set()
    while current not in seen:
        seen.add(current)
        original = view.native_nodes.get(current)
        projected = view.nodes.get(current)
        if (
            original is None
            or projected is None
            or view.scopes.get(current) != view.scopes[owner["ref"]]
            or current in evidence.incomplete_children
            or has_incomplete_children(original)
            or has_incomplete_children(projected)
        ):
            return False
        if current == owner["ref"]:
            return True
        parent = view.parents.get(current)
        if parent is None or current not in evidence.complete_children.get(parent, ()):
            return False
        current = parent
    return False


def _primary_targets(view: ProjectionView, owner: ViewNode) -> tuple[str, ...]:
    """Find separate retained primary targets without counting one nested chain twice."""
    result: list[str] = []
    pending = list(reversed(owner["children"]))
    while pending:
        node = pending.pop()
        if not _complete_path(view, owner, node):
            continue
        original = view.native_nodes[node["ref"]]
        if (
            original.get("capabilities", {}).get("click") == "available"
            and original.get("enabled") is not False
            and not original.get("blockedBy")
            and not original.get("actionsUnavailable")
            and any(action.get("kind") in _PRIMARY_ACTIONS for action in original.get("actions", ()))
        ):
            result.append(node["ref"])
            # A child inside this control is not another independent sibling target.
            continue
        pending.extend(reversed(node["children"]))
    return tuple(result)


def _retained_sources(
    view: ProjectionView,
    owner: ViewNode,
    evidence: CompressionEvidence,
) -> list[Record] | None:
    """Locate every caption fact on its original, fully displayed text descendant."""
    caption = view.field_value(owner["ref"], "name")
    if caption is None or not caption.parts or caption.separator is None:
        return None
    retained: dict[str, ViewNode] = {}
    pending = list(owner["children"])
    while pending:
        node = pending.pop()
        retained[node["ref"]] = node
        pending.extend(node["children"])
    result: list[Record] = []
    seen: set[FactKey] = set()
    for part in caption.parts:
        if (
            type(part.value) is not str
            or not part.value
            or not part.facts
            or utf16_length(part.value) > VALUE_PREVIEW_LIMIT
            or ";base64," in part.value.lower()
        ):
            return None
        for fact in part.facts:
            source = retained.get(fact.source)
            if (
                source is None
                or source["role"] not in ("staticText", "heading")
                or source["children"]
                or not _complete_path(view, owner, source)
                or fact.attribute not in ("name", "description", "value")
                or (fact.source, fact.attribute) not in evidence.complete_text
                or source.get(fact.attribute) != part.value
                or view.native_nodes[fact.source].get(fact.attribute) != part.value
                or source.get("valueIsNumber")
                or source.get("valueKind") == "document"
            ):
                return None
            current = view.field_value(fact.source, fact.attribute)
            if (
                current is None
                or len(current.parts) != 1
                or current.parts[0].value != part.value
                or fact not in current.parts[0].facts
            ):
                return None
            if fact not in seen:
                seen.add(fact)
                result.append(
                    dict(
                        sourceRef=fact.source,
                        attribute=fact.attribute,
                        value=part.value,
                        targetRef=fact.source,
                        field=fact.attribute,
                    )
                )
    return result


def _single_child_value_caption(
    view: ProjectionView,
    owner: ViewNode,
    evidence: CompressionEvidence,
    primary_targets: tuple[str, ...],
) -> bool:
    """Locate one retained actionable text leaf supplying the parent's entire name.

    The child keeps its complete original Value and independent action boundary through
    later caption folds. Only ordinary public whole views use this narrower alternative
    to the existing composite-control rule.
    """
    if not view.context.get("publicApi") or not view.context.get("shareDescriptions"):
        return False
    original = view.native_nodes[owner["ref"]]
    if len(owner["children"]) != 1 or len(original["children"]) != 1:
        return False
    child = owner["children"][0]
    ref = child["ref"]
    if (
        child["role"] != "staticText"
        or child["children"]
        or original["children"][0]["ref"] != ref
        or view.native_nodes[ref]["role"] != "staticText"
        or view.native_nodes[ref]["children"]
        or evidence.complete_children.get(owner["ref"]) != (ref,)
        or evidence.complete_children.get(ref) != ()
        or view.parents.get(ref) != owner["ref"]
        or primary_targets != (ref,)
        or not _complete_path(view, owner, child)
        or any(display_boundary_reasons(view, node) != frozenset(("action",)) for node in (owner, child))
    ):
        return False
    caption = view.field_value(owner["ref"], "name")
    value = view.field_value(ref, "value")
    if (
        caption is None
        or caption.separator is None
        or len(caption.parts) != 1
        or value is None
        or len(value.parts) != 1
    ):
        return False
    part, retained = caption.parts[0], value.parts[0]
    return (
        type(part.value) is str
        and bool(part.value)
        and part.value == owner["name"] == child.get("value") == retained.value
        and len(part.facts) == 1
        and part.facts == retained.facts
        and part.facts[0].source == ref
        and part.facts[0].attribute == "value"
    )


def deduplicate_derived_container_captions(view: ProjectionView, edit: ProjectionEditor) -> None:
    """Keep repeated generated captions on their original retained text sources.

    Composite groups require at least two independent primary controls. Ordinary public
    whole views also accept one complete actionable text leaf supplying the entire name.
    Preserve the group, its actions and all child content; suppress only the repeated
    Python-composed name backed by complete, still-displayed descendant fields.

    Args:
        view: Final compressed tree with immutable source fields and containment.
        edit: Editor for the local name field and its recorded display destinations.
    """
    if not view.context.get("compact") or view.context.get("subtreeRef"):
        return
    evidence = view.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence):
        return
    pending = list(view.roots)
    while pending:
        node = pending.pop()
        pending.extend(node["children"])
        original = view.native_nodes[node["ref"]]
        if (
            node["role"] != "group"
            or not node.get("_factBacked")
            or not node.get("name")
            or node["ref"] not in evidence.unlabelled_groups
            or original.get("control", {}).get("kind") != "button"
            or any(
                original.get(field) != "" or (node["ref"], field) not in evidence.complete_text
                for field in ("name", "description")
            )
            or any(node.get(field) for field in ("linkedLabel", "titleRef", "labelTargets", "incompleteLabelRelation"))
            or any(node["ref"] in targets for targets in view.targets.values())
            or not _complete_path(view, node, node)
        ):
            continue
        primary_targets = _primary_targets(view, node)
        single_child = len(primary_targets) < 2 and _single_child_value_caption(view, node, evidence, primary_targets)
        if len(primary_targets) < 2 and not single_child:
            continue
        destinations = _retained_sources(view, node, evidence)
        if not destinations:
            continue
        previous = node["name"]
        edit.suppress(node, "name")
        kind = "repeatedDerivedSingleChildName" if single_child else "repeatedDerivedContainerName"
        edit.event(
            sourceRef=node["ref"],
            kind=kind,
            field="name",
            previous=previous,
            primaryTargets=list(primary_targets),
            destinations=destinations,
        )
