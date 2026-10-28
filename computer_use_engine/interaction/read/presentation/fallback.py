# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A source-preserving presentation of native display facts when projection fails.

This formatter deliberately has no dependency on the semantic tree renderer.
It does not acquire new facts, infer containment, or create operation targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Iterable

    from computer_use.interaction.read.presentation.records import NodeView, RenderedTree
    from computer_use.protocol.records import FactCollection, FactNode
import json
import re


def display_node_facts(node: FactNode) -> FactNode:
    """Return every unified field for lossless fallback presentation."""
    return node


def literal(value: Any) -> str:
    def mapping(item: object) -> dict:
        if isinstance(item, Mapping):
            return dict(item)
        raise TypeError(f"Cannot serialize {type(item).__name__}")

    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=mapping)
    # Keep each value on one physical line, including unusual provider text.
    return re.sub(r"[\x7f-\x9f\ud800-\udfff\u2028\u2029]", lambda match: f"\\u{ord(match[0]):04x}", encoded)


def branch_references(root: NodeView, refs: Mapping[str, str]) -> tuple[str, ...]:
    """Capture membership before calling a fallible presentation transform.

    Args:
        root: Root of the requested semantic view; native bindings remain separate.
        refs: Display-to-native identity map owned by the observation/session service.
    """
    members, seen, pending = [], set(), [root]
    while pending:
        node = pending.pop()
        reference = node["ref"]
        if reference not in refs or reference in seen:
            raise ValueError("Invalid source-tree identity before presentation")
        seen.add(reference)
        members.append(reference)
        pending.extend(reversed(node["children"]))
    return tuple(members)


class UncompressedFacts:
    """Index native fields and relations for source-preserving fallback rendering."""

    def __init__(self, native: FactCollection, refs: Mapping[str, str]) -> None:
        self.refs = refs
        self.aliases = {node: reference for reference, node in refs.items()}
        if len(self.aliases) != len(refs):
            raise ValueError("Native presentation has duplicate object aliases")
        self.nodes = {node["node"]: display_node_facts(node) for node in native["nodes"]}
        self.relations = {}
        for relation in native["relations"]:
            self.relations.setdefault(relation["source"], []).append(relation)

    def records(self, members: Iterable[str], *, first_fields: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Format exact collected records without invoking projection or failure handling.

        Args:
            members: Original display references whose captured records should be included.
            first_fields: Fields to place first when present, without omitting other fields.

        Returns:
            Flat records with complete values, read states, actions and ordered relations.
            Unpublished relation endpoints retain their native token without a new binding.
        """
        lines: list[str] = []
        for reference in members:
            node = self.nodes[self.refs[reference]]
            lines.append(reference + " native element")
            fields = node["requestedFields"]
            ordered = (
                *[name for name in first_fields if name in fields],
                *[name for name in fields if name not in first_fields],
            )
            for name in ordered:
                if name in node["values"]:
                    lines.append("\t" + literal(name) + ": " + literal(node["values"][name]))
                if name in node["fieldStatus"]:
                    lines.append("\t" + literal(name) + " status: " + literal(node["fieldStatus"][name]))
            # Preserve the complete action records, including description
            # failures, and all relation coverage instead of guessing defaults.
            lines.append("\tNative actions: " + literal(node["actions"]))
            for relation in self.relations.get(node["node"], ()):
                observed = {
                    key: value for key, value in relation.items() if key not in ("source", "property", "targets")
                }
                observed["targets"] = [
                    self.aliases[target] if target in self.aliases else dict(native=target, ref=None)
                    for target in relation["targets"]
                ]
                lines.append("\tRelation " + literal(relation["property"]) + ": " + literal(observed))
        return tuple(lines)

    def render(self, members: Iterable[str], error: BaseException) -> RenderedTree:
        """Describe a real projection failure and retain its complete captured branch.

        Args:
            members: Source references belonging to this exact branch or declared control.
            error: Original failure, including its uncertainty and effect information.
        """
        members = tuple(members)
        issue = dict(root=members[0], error=type(error).__name__, detail=str(error))
        lines = [
            f"Presentation fallback for {members[0]}: uncompressed collected facts "
            f"({issue['error']}: {literal(issue['detail'])}). "
            "Relations below use element references; record order is not containment.",
            *self.records(members),
        ]
        return dict(text="\n".join(lines), refs=list(members), presentation="uncompressed", issue=issue)
