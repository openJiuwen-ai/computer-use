"""Associate labels."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.captions import passive_label
from computer_use.interaction.read.presentation.compression.policy import represented, sources
from computer_use.interaction.read.presentation.tree import VALUE_PREVIEW_LIMIT
from computer_use.protocol.primitives import utf16_length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode
    from computer_use.interaction.read.presentation.records import NodeView
    from computer_use.native.contracts import Binding


def associate_labels(state: ProjectionView, edit: ProjectionEditor) -> None:
    """Associate explicit native label relationships with their display targets.

    Compose eligible label text into the target's name or description within the same source
    scope. Fold an exclusively represented passive label into that target and retain any link
    children alongside the target's display children.
    """
    targets, labels = {}, {}

    def add(label: ViewNode, target: Binding) -> None:
        target_set = targets.setdefault(label, {})
        target_set[target] = None

    for node in state.nodes.values():
        if node["role"] == "staticText":
            for target in node.get("labelTargets", []):
                add(node["ref"], target)
        if node.get("titleRef") and state.nodes.get(node["titleRef"], {}).get("role") == "staticText":
            add(node["titleRef"], node["ref"])
    for label, ids in targets.items():
        if label in state.nodes:
            edit.annotate(state.nodes[label], "labelTargets", list(ids))
    for label, ids in targets.items():
        for identifier in ids:
            target = state.nodes.get(identifier)
            matches_identifier = (
                True
                if (
                    identifier != label
                    and target is not None
                    and state.scopes.get(identifier) == state.scopes.get(label)
                    and target["role"] not in ("staticText", "application", "window", "sheet", "menuBar")
                )
                else False
            )
            if matches_identifier:
                labels.setdefault(identifier, []).append(label)
    edit.targets(targets)
    consumed, additions = set(), {}

    for identifier in state.order:
        target, linked = state.nodes[identifier], labels.get(identifier)
        if not linked:
            continue
        primary = target["titleRef"] if target.get("titleRef") in linked else linked[0]
        if not target.get("name") and utf16_length(state.nodes[primary].get("value", "")) <= VALUE_PREVIEW_LIMIT:
            edit.adopt(target, sources(state.nodes[primary]), "name", " ")
        for label in linked:
            node = state.nodes[label]
            if utf16_length(node.get("value", "")) > VALUE_PREVIEW_LIMIT:
                continue
            parts = [
                part
                for part in sources(node)
                if not any(existing["text"] == part["text"] for existing in target["textSources"])
            ]
            if parts:
                edit.adopt(target, parts, "description", " ")
        edit.annotate(target, "linkedLabel", True)
        for label in linked:
            node = state.nodes[label]
            matches_passive_label = (
                True
                if (
                    len(state.targets.get(label, {})) == 1
                    and not state.ancestor_of(label, identifier)
                    and passive_label(state, edit, node, target, True)
                    and represented(node, target)
                    and all(child["role"] == "link" and child["ref"] != identifier for child in node["children"])
                )
                else False
            )
            if matches_passive_label:
                consumed.add(label)
                edit.fold(node, target, "label")
                additions.setdefault(identifier, []).extend(node["children"])

    def remove_consumed(node: NodeView) -> None:
        edit.children(
            node, [child for child in node["children"] if child["ref"] not in consumed] + additions.get(node["ref"], [])
        )
        for child in node["children"]:
            remove_consumed(child)

    remove_consumed(state.view)
