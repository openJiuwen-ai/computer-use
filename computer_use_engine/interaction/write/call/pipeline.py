"""The single Engine owner of busy/retired checks and write-call claims."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node

from .model import EngineRequest

if TYPE_CHECKING:
    from computer_use.interaction.records import Remaining
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control
    from computer_use.protocol.records import Record


class EngineCallPipeline(Node["EngineRequest", "Record"]):
    """Run one write within a native call lifetime.

    Borrow the caller's budget when supplied; otherwise check session availability
    and claim a new budget. Return the receipt from a request-local WritePipeline.

    Attributes:
        native: Driver port owning call availability, claims, and write execution.
    """

    def __init__(self, native: NativePort) -> None:
        (self.native, self.children) = (native, ())
        super().__init__("EngineCallPipeline", Contract(EngineRequest, dict, frozenset(Effect)))

    async def _arun(self, request: EngineRequest, control: Control) -> Record:
        """Borrow an existing budget or claim a new call after checking session availability."""
        native = self.native
        if request.remaining is not None:
            return await self._borrow(request, request.remaining, control)
        if native.calls.retired or native.calls.busy:
            raise ComputerUseError(
                dict(
                    code="execution_unknown" if native.calls.retired else "invalid_request",
                    effect="none",
                    target="desktop",
                    detail="The native session is retired."
                    if native.calls.retired
                    else "Another native call is in progress.",
                    next="close the old driver and observe a new session"
                    if native.calls.retired
                    else "await the active call",
                )
            )
        with native.calls.claim(request.timeout_ms) as remaining:
            return await self._borrow(request, remaining, control)

    async def _borrow(self, request: EngineRequest, remaining: Remaining, control: Control) -> Record:
        """Expose this write's child graph while it runs inside the supplied call budget."""
        pipeline = WritePipeline(self.native)
        self.children = (pipeline.intent, pipeline.main)
        try:
            return await pipeline.run_in_call(
                request.intent,
                request.target,
                remaining,
                path=request.path,
                query_timeout_ms=request.query_timeout_ms,
                forward_current=request.forward_current,
                _control=control,
            )
        finally:
            self.children = ()

    def _run(self, value: EngineRequest, control: Control) -> Record:
        raise TypeError("Writes require arun()")
