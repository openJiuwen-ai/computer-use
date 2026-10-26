"""Immutable captured-text reads: local values and native pages are distinct.

A text capture outlives its live element, but not its driver session. Neither
branch looks up an AX object, refreshes a tree or restarts a connection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

from computer_use.interaction.control import NativeScopeBody
from computer_use.interaction.read.acquisition.text.model import (
    LocalCapturedTextRead,
    OpenCapturedTextRead,
)
from computer_use.interaction.read.acquisition.text.passes.check_captured_text_range import (
    stage as check_captured_text_range,
)
from computer_use.interaction.read.acquisition.text.passes.check_captured_text_session import (
    stage as check_captured_text_session,
)
from computer_use.interaction.read.acquisition.text.passes.collect_captured_text_page import (
    stage as collect_captured_text_page,
)
from computer_use.interaction.read.acquisition.text.passes.read_local_captured_text import (
    stage as read_local_captured_text,
)
from computer_use.interaction.read.acquisition.text.passes.require_native_text_reference import (
    stage as require_native_text_reference,
)
from computer_use.interaction.read.acquisition.text.passes.select_captured_text_source import (
    stage as select_captured_text_source,
)
from computer_use.interaction.read.acquisition.text.passes.validate_captured_text_range import (
    stage as validate_captured_text_range,
)
from computer_use.native.contracts import Binding
from computer_use.pipeline import Pipeline, Scope, Select, Sequence

if TYPE_CHECKING:
    from typing import AsyncIterator, Iterator

    from computer_use.interaction.read.acquisition.text.model import CapturedTextPlan, CapturedTextRequest
    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control, Step
    from computer_use.protocol.records import TextPage as NativeTextPage


class CapturedTextPipeline(Pipeline["CapturedTextRequest", "NativeTextPage"]):
    """Read a bounded page from an immutable text capture.

    Validate the range and captured driver session, then select a retained local value or
    native text reference. Return the requested page after checking native range metadata
    when a driver read is needed.

    Attributes:
        port: Native port and lifecycle service used for capture-session checks and text-
            reference reads.
    """

    def __init__(self, port: NativePort) -> None:
        self.port = port
        super().__init__(root=self.build())

    def build(self) -> Step[CapturedTextRequest, NativeTextPage]:
        port = self.port

        @contextmanager
        def local_claim(plan: CapturedTextPlan, control: Control) -> Iterator[LocalCapturedTextRead]:
            with port.calls.claim(plan.request.timeout_ms) as remaining:
                yield LocalCapturedTextRead(plan, remaining)

        @asynccontextmanager
        async def native_scope(plan: CapturedTextPlan, control: Control) -> AsyncIterator[OpenCapturedTextRead]:
            target = Binding(plan.session, None, None, None)
            async with port.calls.open(target, plan.request.timeout_ms) as read:
                yield OpenCapturedTextRead(plan, read)

        local = Scope(
            name="LocalCapturedTextScope",
            using=local_claim,
            body=NativeScopeBody(port, read_local_captured_text(), lambda value: value.remaining),
        )
        native = Sequence(
            name="NativeCapturedTextPipeline",
            steps=[
                require_native_text_reference(),
                Scope(
                    name="NativeCapturedTextReadScope",
                    using=native_scope,
                    body=NativeScopeBody(
                        port,
                        Sequence(
                            name="CollectCapturedTextPage",
                            steps=[collect_captured_text_page(port=port), check_captured_text_range()],
                        ),
                        lambda value: value.read.remaining,
                    ),
                ),
            ],
        )
        return Sequence(
            name="CapturedTextPipeline",
            steps=[
                validate_captured_text_range(),
                check_captured_text_session(port=port),
                Select(
                    name="CapturedTextSource",
                    selector=select_captured_text_source(),
                    branches={True: local, False: native},
                ),
            ],
        )
