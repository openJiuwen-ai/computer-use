# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compose complete source-backed form units without merging their input targets."""

from __future__ import annotations

from dataclasses import replace

from computer_use.interaction.read.presentation.interface.helpers import revise, same_scope, synthetic_key
from computer_use.interaction.read.presentation.interface.composition.membership import field_memberships
from computer_use.interaction.read.presentation.interface.composition.placement import member_span
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Arrange unique labels and native descriptions with their existing input field."""
    if not draft.enabled:
        return draft
    blocks = dict(draft.blocks)
    decisions = []
    for membership in field_memberships(draft):
        target, label = membership.target, membership.label
        descriptions = tuple(ref for ref in membership.descriptions if ref not in (target, label))
        refs = tuple(dict.fromkeys((target, *((label,) if label is not None else ()), *descriptions)))
        if (
            len(refs) < 2
            or not same_scope(draft, refs)
            or any(draft.evidence[ref].incomplete or draft.evidence[ref].hidden for ref in refs)
        ):
            continue
        span = member_span(refs, draft, blocks)
        if span is None:
            continue
        parent = blocks[span.parent]
        if parent.kind == "field":
            continue
        key = synthetic_key("field-row", span.parent, span.children)
        member_roles = ((target, "control"),) + (((label, "label"),) if label is not None else ())
        member_roles += tuple((ref, "help") for ref in descriptions)
        simple = len(span.children) == 2 and all(not blocks[child].children for child in span.children)
        blocks[key] = InterfaceBlock(
            key,
            "field",
            None,
            (),
            span.children,
            layout="inline" if simple and not descriptions else "fields",
            origin=membership.origin,
            label_ref=label,
            field_target_ref=target,
            member_roles=member_roles,
        )
        blocks[span.parent] = replace(
            parent, children=(*parent.children[: span.start], key, *parent.children[span.stop :])
        )
        decisions.append("field-label:" + target if label else "field-description:" + target)
    return revise(draft, blocks, *decisions)
