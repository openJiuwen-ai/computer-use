# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose reversible multiline forms using source completeness and structured cost."""

from __future__ import annotations

from dataclasses import replace
import re
from types import MappingProxyType

from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.interface.atoms.model import AtomFinalizeRequest


def _bytes(value: str) -> int:
    return len(value.encode("utf-8", errors="surrogatepass"))


def _quoted_size(value: str) -> int:
    """Count the UTF-8 JSON scalar spelling without building presentation text."""
    total = 2
    for char in value:
        code = ord(char)
        if char in '"\\\n\r\t\b\f':
            total += 2
        elif code < 32 or 127 <= code <= 159 or code in (0x2028, 0x2029) or 0xD800 <= code <= 0xDFFF:
            total += 6
        else:
            total += _bytes(char)
    return total


def _field_size(value: str) -> int:
    quote = (
        not value
        or value != value.strip()
        or any(char in value for char in ',">\\|;{}')
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 or ord(char) in (0x2028, 0x2029) for char in value)
        or value.startswith(("<", "+", "~", "|"))
        or "(partial:" in value
        or "(showing " in value
    )
    return _quoted_size(value) if quote else _bytes(value)


def prefers_inline(value: str, *, label: str | None, depth: int) -> bool:
    """Compare complete scalar and paragraph costs before either form is serialized.

    Args:
        value: Complete source string with its original paragraph separators.
        label: Public field label, or None for an unlabelled text value.
        depth: Native presentation depth used to account for paragraph indentation.

    Returns:
        Whether the JSON scalar saves at least eight UTF-8 bytes.
    """
    if not any(character in value for character in "\r\n"):
        return False
    paragraphs = re.split(r"\r\n|\r|\n", value)
    block_cost = (
        1
        + depth
        + 1
        + _bytes(label or "Value")
        + 2
        + sum(depth + 3 + _field_size(paragraph) for paragraph in paragraphs)
        + len(paragraphs)
        - 1
    )
    scalar_cost = _quoted_size(value) + 2 + (_bytes(label) + 2 if label else 0)
    return block_cost - scalar_cost >= 8


def select_multiline(request: AtomFinalizeRequest) -> AtomFinalizeRequest:
    """Select complete scalar fields only when they improve the whole field's representation."""
    document = request.atoms
    evidence = document.source.context.get("compressionEvidence")
    if not isinstance(evidence, CompressionEvidence):
        return request
    atoms = dict(document.atoms)
    depths = {}
    for ref in document.order:
        atom = atoms[ref]
        depth = depths[ref] = depths.get(atom.parent, -1) + 1
        slots = []
        for slot in atom.slots:
            value = slot.value
            complete = (
                slot.provenance is not None
                and bool(slot.provenance.parts)
                and all(
                    part.facts and all((fact.source, fact.attribute) in evidence.complete_text for fact in part.facts)
                    for part in slot.provenance.parts
                )
            )
            if (
                slot.placement == "block"
                and slot.preview is None
                and isinstance(value, str)
                and ("\n" in value or "\r" in value)
                and complete
                and (request.disclosure is None or request.disclosure.get(ref, slot.key) is None)
            ):
                if prefers_inline(value, label=slot.label, depth=depth):
                    slot = replace(slot, placement="inline")
            slots.append(slot)
        atoms[ref] = replace(atom, slots=tuple(slots))
    return replace(request, atoms=replace(document, atoms=MappingProxyType(atoms)))
