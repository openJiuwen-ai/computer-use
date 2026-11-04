# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture complete native fields that are still literal public atomic slots."""

from __future__ import annotations

from types import MappingProxyType

from computer_use.interaction.read.presentation.rendering.layout.text_slices import capture_text_slice_sources
from .model import TextSliceEvidence, TextSliceField, TextSliceRequest


def capture(request: TextSliceRequest) -> TextSliceEvidence:
    """Retain unique native sources whose complete scalar is present in the final atom."""
    native = capture_text_slice_sources(
        request.native, request.refs, source_scopes=request.atoms.source.scopes, value_fields=request.value_fields
    )
    fields = {}
    for ref in request.atoms.order:
        source = native.nodes.get(ref)
        if source is None or ref in request.excluded_sources:
            continue
        for key, value, path in (("name", source.name, ("name",)), ("value", source.value, ("control_value", "value"))):
            slot = request.atoms.atoms[ref].slot(key)
            if (
                slot is None
                or not value
                or type(slot.value) is not str
                or slot.value != value
                or slot.preview is not None
                or slot.format != "text"
                or slot.protection is not None
                or any(character in value for character in "\r\n\u2028\u2029")
                or (ref, key) in request.excluded_slots
                or request.disclosure is not None
                and request.disclosure.get(ref, key) is not None
                or not slot.facts
                or any(
                    fact.key.source != ref
                    or fact.key.attribute != key
                    or type(fact.value) is not str
                    or fact.value != value
                    for fact in slot.facts
                )
            ):
                continue
            try:
                value.encode("utf-16-le")
            except UnicodeError:
                continue
            fields[(ref, key)] = TextSliceField(ref, key, source.source, path, source.scope, value, slot)
    return TextSliceEvidence(request, native, MappingProxyType(fields))
