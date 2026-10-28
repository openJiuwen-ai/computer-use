# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan exact native action declarations under their nearest retained common parent."""

from __future__ import annotations

from collections import defaultdict

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..helpers import action_saving, append_groups, canonical, declaration_parent, member_scope
from ..model import SharedActionGroup, SharingDraft


def execute(draft: SharingDraft, control: Control) -> SharingDraft:
    """Share complete equal native actions while retaining every executable owner."""
    request = draft.request
    groups: dict[tuple[object, ...], list[str]] = defaultdict(list)
    for ref in request.atoms.order:
        if ref in request.excluded_sources:
            continue
        atom = request.atoms.atoms[ref]
        source = request.evidence.actions.get(ref)
        if (
            source is None
            or not atom.actions
            or atom.actions_unavailable
            or ref in request.evidence.contested_sources
            or any(action.source_ref != ref for action in atom.actions)
            or tuple(action.name for action in atom.actions) != source.names
        ):
            continue
        signature = tuple(
            (action.name, action.label, action.ambiguous, canonical(action.source)) for action in atom.actions
        )
        groups[(member_scope(request, ref), source.key, signature)].append(ref)
    selected: dict[str, list[SharedActionGroup]] = defaultdict(list)
    for refs in groups.values():
        key = declaration_parent(request, refs)
        if key is None:
            continue
        actions = request.atoms.atoms[refs[0]].actions
        saved = action_saving(refs, actions)
        if saved > 0:
            selected[key].append(SharedActionGroup(tuple(refs), actions, saved))
    return append_groups(draft, actions=selected)


stage = Pass.define(
    name="share_interface_native_actions",
    function=execute,
    contract=Contract(
        SharingDraft,
        SharingDraft,
        {Effect.PURE},
        requires=("complete exact native action catalogues", "known common source parent"),
        writes=("local action declarations", "per-owner action exclusions"),
    ),
)
