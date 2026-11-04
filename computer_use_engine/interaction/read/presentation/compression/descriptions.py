# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed public destinations for complete shared empty description fields."""

from __future__ import annotations

from collections.abc import Mapping
from collections import defaultdict
from typing import Any

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.ir import (
    FactKey,
    ProjectionView,
    SharedDescription,
    SharedDescriptionDestination,
    SourceKey,
    SourceRoleDeclaration,
)

DESCRIPTION_LABELS = {"valueDescription": "Value description", "rangeDescription": "Range description"}


def shared_description(node: Mapping[str, Any], attribute: str) -> bool:
    """Check that this exact empty source field has a typed scope destination."""
    destination = node.get("sharedDescriptionFields", {}).get(attribute)
    return (
        isinstance(destination, SharedDescriptionDestination)
        and destination.fact == FactKey(SourceKey(node["ref"]), attribute)
        and type(node.get(attribute)) is str
        and node[attribute] == destination.value == ""
    )


def description_declaration(group: SharedDescription) -> str:
    """Name every source and its exact field value in a reversible scope declaration."""
    return (
        DESCRIPTION_LABELS[group.attribute]
        + " [elements "
        + reference_set(fact.source for fact in group.facts)
        + ']: ""'
    )


def folded_source_roles(view: ProjectionView) -> tuple[SourceRoleDeclaration, ...]:
    """Group exact role fields of sources newly eligible to fold after field sharing."""
    visible: set[str] = set()
    pending = list(view.roots)
    while pending:
        node = pending.pop()
        visible.add(node["ref"])
        pending.extend(node["children"])
    groups: dict[tuple[str, str, str], list[FactKey]] = defaultdict(list)
    for ref in view.order:
        if ref in visible or not view.nodes[ref].get("sharedDescriptionFields"):
            continue
        native = view.native_nodes[ref]
        attribute = "roleDescription" if native.get("roleDescription") else "role"
        value = native[attribute]
        if type(value) is str:
            groups[view.scopes[ref], attribute, value].append(FactKey(SourceKey(ref), attribute))
    return tuple(
        SourceRoleDeclaration(SourceKey(scope), attribute, value, tuple(facts))
        for (scope, attribute, value), facts in groups.items()
    )
