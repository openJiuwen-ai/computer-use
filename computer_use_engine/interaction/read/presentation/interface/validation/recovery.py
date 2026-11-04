# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Restore the smallest closed source forest for a failed optional layout."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.helpers import element_key
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.interface.validation.model import LayoutAssessment


def recover_layout(assessment: LayoutAssessment) -> InterfaceDraft:
    """Keep unaffected regions and restore each isolated failure to its source hierarchy.

    A repair scope must contain complete original subtrees. A synthetic region
    may hold a forest of those subtrees; each real root and its descendants keep
    their original references. The search moves outward only when a subtree is
    split across the candidate scope. Source checks precede this decision.
    """
    draft = assessment.draft
    parents = {child: key for key, block in draft.blocks.items() for child in block.children}
    subtree_keys: dict[str, frozenset[str]] = {}
    subtree_refs: dict[str, frozenset[str]] = {}
    pending = [(key, False) for key in reversed(draft.roots)]
    while pending:
        key, finish = pending.pop()
        block = draft.blocks[key]
        if not finish:
            pending.append((key, True))
            pending.extend((child, False) for child in reversed(block.children))
        else:
            subtree_keys[key] = frozenset((key,)).union(*(subtree_keys[child] for child in block.children))
            subtree_refs[key] = frozenset(block.source_refs).union(*(subtree_refs[child] for child in block.children))

    original: dict[str, frozenset[str]] = {}

    def original_members(ref: str) -> frozenset[str]:
        if ref not in original:
            found: set[str] = set()
            remaining = [ref]
            while remaining:
                current = remaining.pop()
                if current in found:
                    raise ValueError("Original source hierarchy is not a tree")
                found.add(current)
                remaining.extend(draft.projected_nodes[current].get("children", ()))
            original[ref] = frozenset(found)
        return original[ref]

    def original_forest(refs: frozenset[str]) -> tuple[str, ...] | None:
        """Prove that a local member set consists of whole original subtrees."""
        roots = tuple(ref for ref in draft.order if ref in refs and draft.projected_parents.get(ref) not in refs)
        covered: set[str] = set()
        for ref in roots:
            members = original_members(ref)
            if not members <= refs:
                return None
            if covered.intersection(members):
                raise ValueError("Original recovery roots overlap")
            covered.update(members)
        return roots if roots and covered == refs else None

    selected: dict[str, list[str]] = {}
    source_roots: dict[str, tuple[str, ...]] = {}
    for issue in assessment.issues:
        key = issue.block_key
        while True:
            ref = draft.blocks[key].primary_ref
            roots = original_forest(subtree_refs[key])
            if roots is not None and (ref is None or roots == (ref,)):
                selected.setdefault(key, []).append(issue.reason)
                source_roots[key] = roots
                break
            if key not in parents:
                raise ValueError("Invalid layout has no closed original recovery scope")
            key = parents[key]
    # An outer selected scope already restores every inner candidate. Perform one
    # bounded replacement per disjoint scope rather than cascading retries.
    owners = tuple(
        key for key in selected if not any(key != other and key in subtree_keys[other] for other in selected)
    )
    blocks = dict(draft.blocks)
    replacements = {}
    decisions = []
    for key in owners:
        previous = draft.blocks[key]
        root_ref = previous.primary_ref
        refs = subtree_refs[key]
        for member in subtree_keys[key]:
            blocks.pop(member)
        for ref in draft.order:
            if ref in refs:
                node = draft.projected_nodes[ref]
                if element_key(ref) in blocks:
                    raise ValueError("A recovered source block would replace an unrelated block")
                blocks[element_key(ref)] = InterfaceBlock(
                    element_key(ref),
                    "tree",
                    ref,
                    (ref,),
                    tuple(element_key(child) for child in node.get("children", ())),
                )
        if root_ref is None:
            if key in blocks:
                raise ValueError("A recovered forest container conflicts with a real source")
            children = tuple(element_key(ref) for ref in source_roots[key])
            # A verified region still describes the same captured area. Clear
            # the failed format and sharing fields; its surrounding layout stays.
            region = previous.kind == "region"
            blocks[key] = InterfaceBlock(
                key,
                "region" if region else "tree",
                None,
                (),
                children,
                origin=previous.origin,
                label_ref=previous.label_ref if region and previous.label_ref in refs else None,
                position=previous.position if region else None,
                region_role=previous.region_role if region else None,
            )
            replacements[key] = key
        else:
            replacements[key] = element_key(root_ref)
        reasons = sorted(
            {
                reason
                for selected_key, items in selected.items()
                if selected_key in subtree_keys[key]
                for reason in items
            }
        )
        decisions.append("local-layout-fallback:" + (root_ref or key) + ": " + "; ".join(reasons))
    for key, block in tuple(blocks.items()):
        children = tuple(replacements.get(child, child) for child in block.children)
        if children != block.children:
            blocks[key] = replace(block, children=children)
    restored_keys = frozenset(member for key in owners for member in subtree_keys[key])
    restored_refs = frozenset(ref for key in owners for ref in subtree_refs[key])
    patterns = tuple(
        plan
        for plan in draft.geometric_patterns
        if plan.owner_key in draft.blocks
        and plan.owner_key not in restored_keys
        and plan.owner_ref not in restored_refs
    )
    return replace(
        draft,
        roots=tuple(replacements.get(key, key) for key in draft.roots),
        blocks=MappingProxyType(blocks),
        decisions=(*draft.decisions, *decisions),
        geometric_patterns=patterns,
    )
