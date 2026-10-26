"""Narrow execution service backed by the single composable call lifetime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.native.contracts import Intent, OperationTarget
from computer_use.native.port import NativePort

from .call.model import EngineRequest
from .call.pipeline import EngineCallPipeline

if TYPE_CHECKING:
    from computer_use.interaction.records import ElementPath as LookupPath, Remaining
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


class Engine:
    """Expose write execution with either a new call budget or a borrowed budget."""

    def __init__(self, native: NativePort) -> None:
        self.native = native
        self.pipeline = EngineCallPipeline(native)

    async def execute(
        self,
        intent: Intent,
        target: OperationTarget,
        timeout_ms: int = 30000,
        *,
        path: LookupPath | None = None,
        query_timeout_ms: int = 0,
        forward_current: bool = False,
        _control: Control | None = None,
    ) -> Record:
        return await self.pipeline.arun(
            EngineRequest(intent, target, timeout_ms, path, query_timeout_ms, forward_current=forward_current), _control
        )

    async def execute_in_call(
        self,
        intent: Intent,
        target: OperationTarget,
        remaining: Remaining,
        *,
        path: LookupPath | None = None,
        query_timeout_ms: int = 0,
        forward_current: bool = False,
        _control: Control | None = None,
    ) -> Record:
        return await self.pipeline.arun(
            EngineRequest(
                intent,
                target,
                path=path,
                query_timeout_ms=query_timeout_ms,
                remaining=remaining,
                forward_current=forward_current,
            ),
            _control,
        )
