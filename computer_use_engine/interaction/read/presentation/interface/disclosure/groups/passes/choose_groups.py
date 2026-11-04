# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose proven blocked backgrounds or passive runs with independently protected siblings."""

from __future__ import annotations

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..evidence import permits_descendants, recovery_sources
from ..model import ClassifiedGroups, GroupAssessment, SelectedGroups
from ..planning import candidate
from ..background import candidate as background_candidate


def execute(classified: ClassifiedGroups, control: Control) -> SelectedGroups:
    """Prefer a beneficial outer run, then explore remaining child scopes in source order."""
    request = classified.request
    if not request.enabled or (request.window_context is not None and request.window_context.modal is True):
        return SelectedGroups(classified, (), classified.assessments)
    groups, assessments = [], list(classified.assessments)
    recovery_prefix = request.recovery_prefix_units

    def safe(key: str) -> bool:
        refs = classified.block_refs[key]
        return bool(refs) and not any(ref in classified.protections for ref in recovery_sources(request, refs))

    def visit(key: str) -> None:
        nonlocal recovery_prefix
        if not permits_descendants(request, key, classified.protections):
            return
        children = request.interface.blocks[key].children
        offset = 0
        while offset < len(children):
            end = offset
            while end < len(children) and safe(children[end]):
                end += 1
            if end > offset:
                group, reason = candidate(classified, key, children[offset:end], recovery_prefix)
                assessments.append(GroupAssessment(key, children[offset:end], reason, group is not None))
                if group is not None:
                    groups.append(group)
                    recovery_prefix = group.recovery_units
                else:
                    for child in children[offset:end]:
                        visit(child)
                offset = end
            else:
                visit(children[offset])
                offset += 1

    for root in request.interface.roots:
        background, reason = background_candidate(classified, root, recovery_prefix)
        if background is not None:
            groups.append(background)
            recovery_prefix = background.recovery_units
            assessments.append(GroupAssessment(root, background.child_keys, reason, True))
        else:
            visit(root)
    return SelectedGroups(classified, tuple(groups), tuple(assessments))


stage = Pass.define(
    name="choose_group_disclosure",
    function=execute,
    contract=Contract(
        ClassifiedGroups,
        SelectedGroups,
        {Effect.PURE},
        requires=("source protections", "exact captured field values", "recovery cost allowance"),
        writes=("nonoverlapping content overviews", "benefit assessments"),
    ),
)
