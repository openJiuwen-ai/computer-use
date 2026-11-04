# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Publish terminal interface text and its exact field-emission evidence."""

from __future__ import annotations

from computer_use.interaction.read.presentation.accounting.fields import RenderedResult, SourceEmission
from computer_use.interaction.read.presentation.interface.text.model import FormattedInterface
from computer_use.interaction.read.presentation.interface.text.shared_content import slice_destination
from computer_use.interaction.read.presentation.interface.disclosure.groups.formatting import (
    format_group_directory,
    format_group_payload,
)
from computer_use.interaction.read.presentation.ir import thaw
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedSection
from computer_use.interaction.read.presentation.records import RenderedTree
from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control


def execute(formatted: FormattedInterface, control: Control) -> RenderedTree:
    """Assemble source-accounted output for the existing observation publication boundary."""
    request = formatted.request
    atoms, interface = request.atoms, request.interface
    text = "\n".join((*((formatted.prefix,) if formatted.prefix else ()), *formatted.lines, *formatted.suffix))
    excerpts = request.disclosure.excerpts if request.disclosure else {}
    field_plans = tuple(request.fields.fields.values()) if request.fields is not None else ()
    composed = {member.ref for plan in field_plans for member in plan.members if member.mode != "complete"}
    dispositions = tuple(item for item in atoms.dispositions if item.target_ref not in composed) + tuple(
        item for plan in field_plans for item in plan.dispositions
    )
    field_claims = tuple(item for item in atoms.field_claims if item.ref not in composed) + tuple(
        item for plan in field_plans for item in plan.field_claims
    )
    slices = request.text_slices.plans if request.text_slices is not None else {}
    grouped = (
        frozenset(
            ref
            for group in request.groups.groups
            for ref in group.projected_refs
            if ref not in getattr(group, "important_refs", ())
        )
        if request.groups
        else frozenset()
    )
    shared_fields = (
        {(ref, key) for ref, keys in request.sharing.omitted_slots.items() for key in keys}
        if request.sharing is not None
        else set()
    )
    structural_details = (
        {(item.ref, item.slot.key) for table in request.collections.tables.values() for item in table.detail_fields}
        if request.collections is not None
        else set()
    )

    def representation(target: str, slot: str, original: str) -> str:
        """Identify the actual full-view destination chosen for this exact source slot."""
        key = (target, slot)
        return (
            "details"
            if key in structural_details
            else "continuation"
            if target in grouped or key in excerpts
            else "text-slice"
            if key in slices
            else "shared"
            if key in shared_fields
            else original
        )

    emitted = tuple(
        SourceEmission(
            item.fact.key,
            item.fact.value,
            item.target_ref,
            item.slot,
            representation(item.target_ref, item.slot, "equivalent" if item.kind == "equivalent" else "display"),
        )
        for item in dispositions
        if item.kind != "details"
    )
    claims = [
        dict(
            ref=item.ref,
            field=item.field,
            value=thaw(item.value),
            target=item.target,
            kind=representation(item.target, item.field, item.kind),
        )
        for item in field_claims
    ]
    nodes = atoms.atoms
    result = dict(
        text=text,
        refs=list(interface.target_refs),
        labels={
            ref: next(
                (
                    slot.value
                    for slot in nodes[ref].slots
                    if slot.key in ("name", "description", "value") and slot.value
                ),
                nodes[ref].role.label,
            )
            for ref in interface.target_refs
        },
        roots=request.projection["roots"],
        contextRefs={node.ref: list(node.related) for node in formatted.nodes if node.related},
        projection=request.projection,
        fieldCoverage=claims,
        inlineChildRefs=[
            ref
            for ref in interface.target_refs
            if nodes[ref].coverage.unexpanded_menu or nodes[ref].coverage.next_offset is not None
        ],
        childCoverageRefs=tuple(
            ref
            for ref in interface.target_refs
            if nodes[ref].coverage.partial and not nodes[ref].coverage.unexpanded_menu
        ),
        interfaceLayout=dict(version=interface.format_version, applied=list(interface.decisions)),
    )
    if slices:
        result["compactTextSlices"] = tuple(slice_destination(plan) for plan in slices.values())
    if shared_fields:
        result["sharedTextFields"] = tuple(
            dict(block=key, refs=group.refs, field=group.slot_key, value=thaw(group.slot.value))
            for key, groups in request.sharing.by_block.items()
            for group in groups.fields
        )
    if structural_details:
        result["collectionStructureDetails"] = tuple(
            dict(ref=item.ref, field=item.slot.key, value=thaw(item.slot.value))
            for table in request.collections.tables.values()
            for item in table.detail_fields
        )
    if excerpts:
        result["disclosedTextFields"] = tuple(
            dict(
                ref=excerpt.source.ref,
                field=excerpt.source.native_field,
                attribute=excerpt.source.attribute,
                value=excerpt.source.value,
                ranges=[(item.start, item.end) for item in excerpt.fragments],
            )
            for excerpt in excerpts.values()
            if excerpt.source.ref not in grouped
        )
    if request.groups is not None and request.groups.groups:
        if request.snapshot is None:
            raise ValueError("Content overview requires its captured recovery source")
        result["capturedContentDirectory"] = format_group_directory(request.groups)
        result["capturedContentPayload"] = format_group_payload(
            request.groups, request.snapshot, formatted.background_content
        )
        result["contentOverviews"] = tuple(
            dict(
                section=group.section_key,
                refs=group.projected_refs,
                characters=group.captured_characters,
                fields=tuple(dict(ref=entry.ref, slot=entry.slot_key) for entry in group.fields),
            )
            for group in request.groups.groups
        )
    for output, attribute in (
        ("deferredNativeFields", "deferred_native_fields"),
        ("deferredMenuActions", "deferred_menu_actions"),
        ("deferredControlRaises", "deferred_control_raises"),
        ("deferredNativeUrls", "deferred_native_urls"),
        ("deferredNativeUrlPeers", "deferred_native_url_peers"),
    ):
        entries = getattr(atoms, attribute)
        if entries:
            result[output] = entries
    document = RenderedDocument(
        (RenderedSection(text, formatted.nodes, prefix=formatted.prefix, suffix="\n".join(formatted.suffix)),)
    )
    return RenderedResult(result, emitted, request.projection.source, document=document)


stage = Pass.define(
    name="assemble_interface_text",
    function=execute,
    contract=Contract(
        FormattedInterface,
        dict,
        {Effect.PURE},
        requires=("serialized blocks", "explicit source field destinations"),
        writes=("root text", "same-capture disclosure catalogue", "source emissions"),
    ),
)
