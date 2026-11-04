# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan one section-local index convention for repeated structural indices."""

from __future__ import annotations

from dataclasses import replace

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ...model import AtomDocument


def execute(document: AtomDocument, control: Control) -> AtomDocument:
    """Share index-base spelling when repeated fields outweigh a single declaration.

    Values and units stay in their original slots. The terminal serializer emits
    the declared convention in both full and incremental sections.
    """
    indices = sum(slot.key == "index" for atom in document.atoms.values() for slot in atom.slots)
    return replace(document, shared_index_basis=indices >= 3)


stage = Pass.define(
    name="share_interface_index_convention",
    function=execute,
    contract=Contract(
        AtomDocument,
        AtomDocument,
        {Effect.PURE},
        requires=("typed index and logical span slots",),
        writes=("section-local index convention",),
    ),
)
