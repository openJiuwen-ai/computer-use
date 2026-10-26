"""Desktop observation over native protocols; no input or AX subtree needed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.images.desktop.model import DesktopReadRequest
from computer_use.interaction.read.acquisition.images.desktop.pipeline import DesktopReadPipeline

if TYPE_CHECKING:
    from computer_use.native.port import NativePort
    from computer_use.protocol.records import Record


class NativeDesktopReader:
    """Read desktop pixels through the bounded desktop-capture pipeline."""

    def __init__(self, port: NativePort) -> None:
        self.port = port

    async def read(self, *, max_dimension: int = 4096, timeout_ms: int = 30000) -> Record:
        return await DesktopReadPipeline(self.port).arun(DesktopReadRequest(max_dimension, timeout_ms))
