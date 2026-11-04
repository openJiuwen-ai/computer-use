# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble baseline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.delivery.baseline.model import PURE, BaselineDraft, BaselineInput
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.sizing import DocumentSize
from computer_use.json import utf8
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def assemble_baseline(value: BaselineInput, control: Control) -> BaselineDraft:
    """Capture the delivered comparison facts, bindings, coverage and retained size.

    Structured observations retain immutable field signatures, not the capture
    and rendering graphs used to create the delivered document.
    Text-only observations retain their already materialized historical presentation.
    """
    observation = value.observation
    document = observation.get("document")
    if isinstance(document, DisplayDocument):
        document = ComparisonDocument.capture(document, checkpoint=control.remaining)
    text = observation["state"]["text"] if document is None else None
    measurement = DocumentSize(checkpoint=control.remaining)
    size = len(utf8(text)) if text is not None else measurement.data(document)
    size += measurement.data(observation["refs"]) + measurement.data(observation.get("coverage"))
    baseline = dict(
        source=observation["source"],
        scope=observation["scope"],
        view=observation["view"],
        refs=observation["refs"],
        coverage=observation.get("coverage"),
        document=document,
        bytes=size,
    )
    if document is None:
        baseline["text"] = text
    return BaselineDraft(value.key, baseline, size)


stage = Pass.define(
    name="assemble_baseline",
    function=assemble_baseline,
    contract=Contract(BaselineInput, BaselineDraft, PURE),
)
