# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select read isolation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.model import PURE, ComponentRequest
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.app import NativeAppReader
    from computer_use.pipeline.core import Control


def create(*, services: NativeAppReader) -> Callable[[ComponentRequest, Control], bool]:
    def execute(value: ComponentRequest, control: Control) -> bool:
        """Select separate collection leases when the driver declares native read isolation."""
        return "nativeReadIsolation" in services.port.connection.capabilities

    return execute


stage = Pass.factory(
    name="select_read_isolation",
    factory=create,
    contract=Contract(ComponentRequest, bool, PURE),
)
