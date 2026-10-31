# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lock independent controls, selection, source gaps and modal scope before grouping."""

from __future__ import annotations

from types import MappingProxyType

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from computer_use.protocol.observation.issues import ReadIssueIndex

from ..evidence import block_members, source_protections
from ..model import CapturedGroupField, ClassifiedGroups, GroupAssessment, GroupDisclosureRequest


def execute(request: GroupDisclosureRequest, control: Control) -> ClassifiedGroups:
    """Capture explicit protections and assess available window-level evidence."""
    issues = ReadIssueIndex(request.snapshot.native.issues)
    protections = {
        ref: reasons for ref in request.interface.source.order if (reasons := source_protections(request, ref, issues))
    }
    # A relation endpoint may be passive by itself yet label an independent input.
    aliases = {token: ref for ref, token in request.snapshot.refs.items()}
    for node in request.snapshot.native.nodes.values():
        for relation in node.relations.values():
            if relation.kind in ("children", "parent", "window"):
                continue
            for token in relation.targets:
                if (ref := aliases.get(token)) is not None:
                    protections[ref] = (*protections.get(ref, ()), "semantic-relation-endpoint")
    context = request.window_context
    if context is None:
        reason = "background-overview-requires-confirmed-window-directory"
    elif context.current or context.modal is True:
        reason = "current-or-modal-window-kept-expanded"
    elif not context.confirmed_blockers:
        reason = "background-blocking-unconfirmed"
    else:
        reason = "background-blocking-confirmed; fixed-scope-overview-assessed-separately"
    assessment = GroupAssessment("", (), reason)
    records = {record["node"]: record for record in request.snapshot.facts["nodes"]}
    native_fields: dict[tuple[str, str], list[CapturedGroupField]] = {}
    for (ref, property), field in request.snapshot.fields.items():
        native = records.get(request.snapshot.refs.get(ref))
        if (
            native is None
            or native.get("observed") is False
            or field.get("representation") != "field"
            or property not in native["requestedFields"]
            or property not in native["values"]
            or native["fieldStatus"].get(property, {}).get("state", "complete") != "complete"
        ):
            continue
        native_fields.setdefault((ref, field["field"]), []).append(
            CapturedGroupField(ref, property, native["values"][property], field.get("normalized"), field["field"])
        )
    return ClassifiedGroups(
        request,
        MappingProxyType(protections),
        MappingProxyType(dict(block_members(request))),
        (assessment,),
        MappingProxyType({key: tuple(fields) for key, fields in native_fields.items()}),
    )


stage = Pass.define(
    name="classify_group_disclosure",
    function=execute,
    contract=Contract(
        GroupDisclosureRequest,
        ClassifiedGroups,
        {Effect.PURE},
        requires=("captured native facts", "structured object membership"),
        writes=("source protection reasons", "background evidence assessment"),
    ),
)
