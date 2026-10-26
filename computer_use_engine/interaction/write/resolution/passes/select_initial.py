"""Select initial."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import ResolutionFrame
from computer_use.interaction.write.resolution.checks import parts
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.native.port import NativePort
    from computer_use.pipeline.core import Control


def create(*, native: NativePort) -> Callable[[ResolutionFrame, Control], str]:
    def select_initial(frame: ResolutionFrame, _: object) -> str:
        """Choose a resolver from the intent, capabilities, and retained pointer hit."""
        intent, target, _, _ = parts(frame)
        capabilities = getattr(native, "capabilities", ())
        if intent.kind == "click" and frame.request.memory.pointer_member is not None:
            return "pointer"
        if (
            intent.kind == "scroll"
            and {"nativeScroll", "nativeElementScroll"} & set(capabilities)
            and (cast("Binding", target).node is not None)
            and (cast("Binding", target).capture is None)
            and (not any((k in intent.parameters for k in ("x", "y"))))
        ):
            return "scroll"
        if intent.kind == "replace" and "nativeTextReplacement" in capabilities:
            return "replace"
        if (
            intent.kind in ("focus", "key", "insert", "paste")
            and (
                "nativeActuators" in capabilities
                or (intent.kind in ("insert", "paste") and "nativeTextInput" in capabilities)
            )
            and (cast("Binding", target).node is not None)
            and (not (intent.kind in ("insert", "paste") and intent.parameters.get("text") == ""))
        ):
            return "text"
        return "direct"

    return select_initial


stage = Pass.factory(
    name="select_resolver",
    factory=create,
    contract=Contract(ResolutionFrame, str),
)
if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
