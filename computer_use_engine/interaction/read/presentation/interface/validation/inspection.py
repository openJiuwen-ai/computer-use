# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate original identities independently of optional layout shape choices."""

from __future__ import annotations

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft
from computer_use.interaction.read.presentation.interface.helpers import prefix_signature
from computer_use.interaction.read.presentation.interface.validation.model import LayoutIssue
from computer_use.interaction.read.presentation.interface.geometric.units_support import inline_unit_refs


def validate_sources(draft: InterfaceDraft) -> InterfaceDraft:
    """Reject invented, duplicated or missing objects and invalid field target ownership."""
    visited: set[str] = set()
    sources: set[str] = set()
    pending = list(reversed(draft.roots))
    while pending:
        key = pending.pop()
        if key in visited:
            raise ValueError("An interface block has multiple parents or forms a cycle")
        if key not in draft.blocks:
            raise ValueError("An interface child block is missing")
        visited.add(key)
        block = draft.blocks[key]
        if block.key != key or block.primary_ref is not None and block.primary_ref not in block.source_refs:
            raise ValueError("An interface primary target differs from its source")
        for ref in block.source_refs:
            if ref in sources or ref not in draft.projected_nodes:
                raise ValueError("An interface source was duplicated or invented")
            sources.add(ref)
        if block.label_ref is not None and block.label_ref not in draft.projected_nodes:
            raise ValueError("An interface label is outside this projection")
        if block.field_target_ref is not None or block.member_roles:
            members: set[str] = set()
            descendants = [key]
            checked: set[str] = set()
            while descendants:
                descendant = descendants.pop()
                if descendant in checked or descendant not in draft.blocks:
                    raise ValueError("A composed field contains a repeated or missing block")
                checked.add(descendant)
                member = draft.blocks[descendant]
                members.update(member.source_refs)
                descendants.extend(member.children)
            roles = dict(block.member_roles)
            if (
                block.kind != "field"
                or len(roles) != len(block.member_roles)
                or not set(roles) <= members
                or block.field_target_ref is not None
                and (block.field_target_ref not in members or roles.get(block.field_target_ref) != "control")
            ):
                raise ValueError("A composed field lost its original member or input target")
        pending.extend(reversed(block.children))
    expected = set(draft.projected_nodes)
    if len(set(draft.order)) != len(draft.order) or set(draft.order) != expected or sources != expected:
        raise ValueError("The interface layout lost a projected real reference")
    if visited != set(draft.blocks):
        raise ValueError("Unreachable interface blocks were retained")
    for ref, node in draft.projected_nodes.items():
        if node["ref"] != ref:
            raise ValueError("Projected fields changed their real source identity")
        if any(child not in draft.projected_nodes for child in node.get("children", ())):
            raise ValueError("An original projected child reference was lost")
    return draft


def layout_issues(draft: InterfaceDraft) -> tuple[LayoutIssue, ...]:
    """Report invalid optional layouts while keeping source identity errors fatal."""
    from computer_use.interaction.read.presentation.interface.geometric.patterns.validation import pattern_issues

    issues = list(pattern_issues(draft))
    for key, block in draft.blocks.items():
        reason = None
        members = tuple(draft.blocks[child] for child in block.children)
        if block.shared_prefix_ref is not None and block.shared_prefix_ref not in draft.projected_nodes:
            reason = "shared prefix has no captured source"
        elif block.layout == "source-only":
            from computer_use.interaction.read.presentation.interface.geometric.frontier import transparent_group

            if (
                block.primary_ref is None
                or block.source_refs != (block.primary_ref,)
                or block.children
                or not transparent_group(block.primary_ref, draft)
            ):
                reason = "source-only wrapper lacks complete passive source evidence"
        elif block.layout == "compound-control":
            from computer_use.interaction.read.presentation.interface.geometric.compound.validation import (
                compound_issue,
            )

            reason = compound_issue(block, draft)
        elif block.kind == "field" and block.field_target_ref is None:
            labels = tuple(member for member in members if member.primary_ref == block.label_ref)
            if (
                block.primary_ref is not None
                or block.layout not in ("fields", "field-inline")
                or block.label_ref is None
                or len(labels) != 1
                or labels[0].children
                or len(members) < 2
            ):
                reason = "visual field lacks its original atomic label and independent members"
        elif block.layout == "control-unit":
            if not block.visual_unit or block.field_target_ref is None or block.kind != "field":
                reason = "control unit lacks its original control target and membership evidence"
        elif block.layout in ("chain", "record-path"):
            child = members[0] if len(members) == 1 else None
            if (
                block.primary_ref is None
                or child is None
                or child.primary_ref is None
                or tuple(draft.projected_nodes[block.primary_ref].get("children", ())) != (child.primary_ref,)
                or draft.projected_parents.get(child.primary_ref) != block.primary_ref
            ):
                reason = "path differs from its unique original source edge"
            elif (
                block.layout == "chain"
                and draft.projected_nodes[block.primary_ref].get("visualChainChild") != child.primary_ref
            ):
                reason = "visual chain has no source evidence"
            elif block.layout == "record-path" and draft.source.parents.get(child.primary_ref) != block.primary_ref:
                reason = "record path crosses original native parents"
        elif block.layout == "compact-list":
            if (
                block.primary_ref is not None
                or block.shared_prefix_ref is None
                or len(members) < 3
                or any(member.primary_ref is None or member.children for member in members)
                or block.shared_prefix_ref != members[0].primary_ref
            ):
                reason = "compact list lacks its independent leaf targets"
        elif block.layout in ("records", "visual-grid"):
            slots = tuple(member.column_slots or tuple(range(len(member.children))) for member in members)
            if (
                len(members) < 2
                or not block.columns
                or len(block.column_prefix_refs) != len(block.columns)
                or block.column_alignments
                and (
                    len(block.column_alignments) != len(block.columns)
                    or any(kind not in ("left", "right", "center") for kind in block.column_alignments)
                )
                or any(
                    len(indices) != len(member.children)
                    or not indices
                    or any(
                        index is not None and (type(index) is not int or index < 0 or index >= len(block.columns))
                        for index in indices
                    )
                    or tuple(sorted({index for index in indices if type(index) is int}))
                    != tuple(index for index in indices if type(index) is int)
                    or not member.column_slots
                    and len(indices) != len(block.columns)
                    for member, indices in zip(members, slots)
                )
            ):
                reason = "rows differ from their typed column plan"
            elif block.layout == "visual-grid" and (
                block.primary_ref is not None
                or block.source_refs
                or any(prefix is not None for prefix in block.column_prefix_refs)
                or any(
                    member.layout != "visual-row" or member.primary_ref is not None or member.source_refs
                    for member in members
                )
                or any(inline_unit_refs(child, draft.blocks) is None for member in members for child in member.children)
            ):
                reason = "visual grid lacks complete independently rendered units"
            else:
                for index, prefix in enumerate(block.column_prefix_refs):
                    refs = tuple(
                        draft.blocks[child].primary_ref
                        for member, indices in zip(members, slots)
                        for child, column in zip(member.children, indices)
                        if column == index
                    )
                    if (
                        not refs
                        or prefix is not None
                        and (
                            prefix not in refs
                            or any(
                                ref is None or prefix_signature(draft, ref) != prefix_signature(draft, prefix)
                                for ref in refs
                            )
                        )
                    ):
                        reason = "column prefix differs from original member roles or states"
                        break
        elif block.layout == "record-blocks":
            refs = tuple(member.primary_ref for member in members)
            if (
                block.kind != "collection"
                or block.primary_ref is not None
                or len(members) < 2
                or any(ref is None or draft.projected_nodes[ref]["role"] not in ("group", "row") for ref in refs)
                or len({draft.source.scopes[ref] for ref in refs if ref is not None}) != 1
            ):
                reason = "record blocks lack separate original rows in one scope"
        elif block.layout == "visual-row":
            if any(
                inline_unit_refs(member.key, draft.blocks) is None
                or member.visual_unit
                and member.layout != "control-unit"
                for member in members
            ):
                reason = "visual row lacks complete single-line units"
        elif block.layout == "inline":
            if any(member.primary_ref is None or member.children for member in members):
                reason = "inline layout has a non-atomic member"
        elif block.layout == "rows":
            if block.shared_prefix_ref is None or any(member.layout != "inline" for member in members):
                reason = "row group lacks its typed inline children"
        if reason is None and block.shared_prefix_ref is not None:
            refs = (
                tuple(member.primary_ref for member in members)
                if block.layout in ("inline", "compact-list", "record-blocks")
                else tuple(draft.blocks[key].primary_ref for member in members for key in member.children)
                if block.layout == "rows"
                else ()
            )
            if any(
                ref is None or prefix_signature(draft, ref) != prefix_signature(draft, block.shared_prefix_ref)
                for ref in refs
            ):
                reason = "shared prefix differs from original member roles or states"
        if reason is not None:
            issues.append(LayoutIssue(key, reason))
    return tuple(issues)
