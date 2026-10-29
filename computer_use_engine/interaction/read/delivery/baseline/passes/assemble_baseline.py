# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble baseline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.json import dumps, utf8
from computer_use.interaction.read.delivery.baseline.model import PURE, BaselineDraft, BaselineInput
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def assemble_baseline(value: BaselineInput, control: Control) -> BaselineDraft:
    """Retain the delivered document, bindings, and coverage within the cache budget.

    Structured observations retain their frozen document without requesting full text.
    Text-only observations retain their already materialized historical presentation.
    """
    observation = value.observation
    document = observation.get("document")
    text = observation["state"]["text"] if document is None else None
    size = len(utf8(text)) if text is not None else document.estimated_bytes
    size += len(utf8(dumps(observation["refs"]))) + len(utf8(dumps(observation.get("coverage"))))
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
