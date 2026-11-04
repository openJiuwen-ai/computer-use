# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Choose the negotiated click branch without changing other write intents."""

from computer_use.interaction.write.routes.selection.model import CandidateSet
from computer_use.pipeline import Contract, Pass


def execute(value: CandidateSet, control: object) -> str:
    """Select atomic click offers or the ordinary native-unit pipeline."""
    return (
        "click"
        if value.request.intent.kind == "click" and any(p.click is not None for p in value.request.facts.primitives)
        else "unit"
    )


stage = Pass.define(name="select_click_contract", function=execute, contract=Contract(CandidateSet, str))
