# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve form membership from captured relations before arranging source blocks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.spatial.policy import form_control


@dataclass(frozen=True)
class FieldMembership:
    """An input and its unambiguous source-backed caption and description objects.

    Attributes:
        target: Existing input reference, never a new layout identity.
        label: Unique caption source, when one is confirmed locally.
        descriptions: Complete native descriptive-relation targets in source order.
        origin: Whether the caption depends on the prepared visual relation.
    """

    target: str
    label: str | None
    descriptions: tuple[str, ...]
    origin: Literal["native", "visual"]


def field_memberships(draft: InterfaceDraft) -> tuple[FieldMembership, ...]:
    """Resolve complete native relationships before using unique visual captions.

    Shared labels or descriptions retain independent placement. Conflicting and
    incomplete native relationships stop the corresponding composition, including
    when a nearby visual caption would otherwise look plausible.
    """
    native_labels: dict[str, set[str]] = {}
    label_users: dict[str, set[str]] = {}
    descriptions: dict[str, set[str]] = {}
    description_users: dict[str, set[str]] = {}
    uncertain_labels: set[str] = set()
    uncertain_descriptions: set[str] = set()

    def add(mapping: dict[str, set[str]], reverse: dict[str, set[str]], target: str, member: str) -> None:
        mapping.setdefault(target, set()).add(member)
        reverse.setdefault(member, set()).add(target)

    for ref, node in draft.projected_nodes.items():
        label = node.get("titleRef")
        if isinstance(label, str):
            add(native_labels, label_users, ref, label)
        if node.get("incompleteLabelRelation"):
            uncertain_labels.add(ref)
            uncertain_labels.update(node.get("labelTargets", ()))
        for target in node.get("labelTargets", ()):
            add(native_labels, label_users, target, ref)
    for relation in draft.relations:
        label_relation = relation.kind in ("labelled_by", "labels")
        description_relation = relation.kind in ("described_by", "describes")
        if not label_relation and not description_relation:
            continue
        inverse = relation.kind in ("labels", "describes")
        mapping, reverse = (native_labels, label_users) if label_relation else (descriptions, description_users)
        uncertain = uncertain_labels if label_relation else uncertain_descriptions
        if not relation.complete or not relation.confirmed:
            uncertain.update(relation.targets if inverse else (relation.source,))
        for endpoint in relation.targets:
            target, member = (endpoint, relation.source) if inverse else (relation.source, endpoint)
            add(mapping, reverse, target, member)

    visual_users: dict[str, set[str]] = {}
    for ref, node in draft.projected_nodes.items():
        label = node.get("visualLabelRef")
        if isinstance(label, str):
            visual_users.setdefault(label, set()).add(ref)

    result = []
    positions = {ref: index for index, ref in enumerate(draft.order)}
    for target in draft.order:
        if not form_control(
            draft.projected_nodes[target], related=bool(native_labels.get(target) or descriptions.get(target))
        ):
            continue
        label: str | None = None
        origin: Literal["native", "visual"] = "native"
        native = native_labels.get(target, set())
        if target not in uncertain_labels:
            if len(native) == 1:
                candidate = next(iter(native))
                if label_users[candidate] == {target}:
                    label = candidate
            elif not native:
                candidate = draft.projected_nodes[target].get("visualLabelRef")
                if (
                    isinstance(candidate, str)
                    and visual_users[candidate] == {target}
                    and not label_users.get(candidate, set()).difference({target})
                ):
                    label, origin = candidate, "visual"
        if label not in draft.projected_nodes or (label is not None and not _caption(draft.projected_nodes[label])):
            label = None
        related = (
            ()
            if target in uncertain_descriptions
            else tuple(
                sorted(
                    (
                        ref
                        for ref in descriptions.get(target, ())
                        if ref in draft.projected_nodes and description_users[ref] == {target}
                    ),
                    key=positions.__getitem__,
                )
            )
        )
        if label is not None or related:
            result.append(FieldMembership(target, label, related, origin))
    return tuple(result)


def _caption(node: Mapping[str, object]) -> bool:
    """Accept a real text-bearing label while keeping its own actions and state."""
    return node["role"] in ("staticText", "heading", "image") and any(
        isinstance(node.get(name), str) and node[name] for name in ("name", "value", "description")
    )
