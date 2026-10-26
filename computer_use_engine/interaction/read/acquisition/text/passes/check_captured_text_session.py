"""Check captured text session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.text.model import (
    PURE,
    CapturedTextPlan,
    CapturedTextRequest,
)
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, port: NativePort) -> Callable[[CapturedTextRequest, Control], CapturedTextPlan]:
    def check_session(request: CapturedTextRequest, control: Control) -> CapturedTextPlan:
        """Pair the request with its driver session after confirming the retained capture still
        belongs to it.
        """
        session = request.source.session
        if port.connection.session is None or port.connection.session != session:
            raise RuntimeError("The text capture's driver session ended")
        return CapturedTextPlan(request, session)

    return check_session


stage = Pass.factory(
    name="check_captured_text_session",
    factory=create,
    contract=Contract(CapturedTextRequest, CapturedTextPlan, PURE),
)
