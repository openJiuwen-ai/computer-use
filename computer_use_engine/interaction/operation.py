# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply the host operation guard around asynchronous session branches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.pipeline.core import Node, T, U
from computer_use.session.operations import operation

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control, Step


class HostOperation(Node[T, U]):
    """Wrap an asynchronous child flow in the host operation guard.

    The guard brackets the outer operation and tracks its active lifetime.
    Nested operations share that boundary through the host's operation service.

    Attributes:
        body: Child flow whose execution is covered by the guard.
    """

    def __init__(self, body: Step[T, U]) -> None:
        self.body, self.children = body, (body,)
        super().__init__("HostOperation", body.contract)

    async def _arun(self, value: T, control: Control) -> U:
        return await operation(lambda: self.body.arun(value, control))

    def _run(self, value: T, control: Control) -> U:
        raise TypeError("System operations require arun()")


if TYPE_CHECKING:
    from computer_use.pipeline.core import T, U
