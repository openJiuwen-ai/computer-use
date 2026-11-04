# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A source-preserving presentation of native display facts when projection fails.

This formatter deliberately has no dependency on the semantic tree renderer.
It does not acquire new facts, infer containment, or create operation targets.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedNode, RenderedSection

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Any

    from computer_use.interaction.read.presentation.records import NodeView, RenderedTree
    from computer_use.protocol.records import FactCollection, FactNode


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


def branch_references(
    root: NodeView, refs: Mapping[str, str], *, checkpoint: Callable[[], object] | None = None
) -> tuple[str, ...]:
    """Capture membership before calling a fallible presentation transform.

    Args:
        root: Root of the requested semantic view; native bindings remain separate.
        refs: Display-to-native identity map owned by the observation/session service.
        checkpoint: Original call-budget check, consulted within bounded work batches.
    """
    if checkpoint is not None:
        checkpoint()
    work = 0
    members, seen, pending = [], set(), [root]
    while pending:
        work += 1
        if checkpoint is not None and work % 128 == 0:
            checkpoint()
        node = pending.pop()
        reference = node["ref"]
        if reference not in refs or reference in seen:
            raise ValueError("Invalid source-tree identity before presentation")
        seen.add(reference)
        members.append(reference)
        for child in reversed(node["children"]):
            work += 1
            if checkpoint is not None and work % 128 == 0:
                checkpoint()
            pending.append(child)
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

    def render(self, members: Iterable[str], error: BaseException, *, refresh: bool = False) -> RenderedTree:
        """Describe a real projection failure and retain its complete captured branch.

        Args:
            members: Source references belonging to this exact branch or declared control.
            error: Original failure, including its uncertainty and effect information.
        """
        from computer_use.interaction.read.presentation.accounting.fields import RenderedResult

        members = tuple(members)
        issue = dict(root=members[0], error=type(error).__name__, detail=str(error))
        prefix = (
            f"Presentation fallback for {members[0]}: uncompressed collected facts "
            f"({issue['error']}: {literal(issue['detail'])}). "
            "Relations: element references."
        )
        nodes = tuple(
            RenderedNode(ref, None, 0, "\n".join(self.records((ref,))), ref + " native element") for ref in members
        )
        text = "\n".join((prefix, *(node.text for node in nodes)))
        return RenderedResult(
            dict(text=text, refs=list(members), presentation="uncompressed", issue=issue),
            document=RenderedDocument((RenderedSection(text, nodes, prefix=prefix, refresh=refresh),)),
        )
