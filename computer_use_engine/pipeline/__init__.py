# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Static, domain-independent composition of typed read/write stages.

The runner owns control flow only. Domain facts travel as explicit values;
reference stores, native ports and attempt histories stay with their owners.

Consumers include interaction flows and native.lifecycle, which uses Control
and Effect for call budgets and resource diagnostics. Keep this package above
both layers so native lifecycle management does not depend on interaction.
"""

from .core import (
    Continue,
    Contract,
    Control,
    Done,
    Effect,
    Event,
    Pass,
    PassDefinition,
    Pipeline,
    Repeat,
    Scope,
    Select,
    Sequence,
    Step,
)

__all__ = [
    "Contract",
    "Continue",
    "Control",
    "Done",
    "Effect",
    "Event",
    "Pass",
    "PassDefinition",
    "Pipeline",
    "Repeat",
    "Scope",
    "Select",
    "Sequence",
    "Step",
]
