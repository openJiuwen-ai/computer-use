# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve action semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.write.routes.selection.candidates import resolve_action_semantics
from computer_use.interaction.write.routes.selection.model import CandidateSet
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def execute(value: CandidateSet, control: Control) -> CandidateSet:
    """Match action names and resolve click or menu-opening semantics among native candidates."""
    return resolve_action_semantics(value)


stage = Pass.define(
    name="resolve_action_semantics",
    function=execute,
    contract=Contract(CandidateSet, CandidateSet, failures=("ambiguous action", "missing action")),
)
