# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan fact profile."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.helpers import plan_fact_profile
from computer_use.interaction.read.acquisition.facts.model import (
    PURE,
    FactProfile,
    FactScope,
    NativeReadCapabilities,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.pipeline.core import Control


def create(*, reader: NativeReader) -> Callable[[FactScope, Control], FactProfile]:
    def profile(scope: FactScope, control: Control) -> FactProfile:
        """Select native fields, graph depth, and read options from the provider platform and
        capabilities.
        """
        connection = reader.port.connection
        return plan_fact_profile(
            scope, NativeReadCapabilities(connection.platform, getattr(connection, "capabilities", ()))
        )

    return profile


stage = Pass.factory(
    name="plan_fact_profile",
    factory=create,
    contract=Contract(FactScope, FactProfile, PURE),
)
