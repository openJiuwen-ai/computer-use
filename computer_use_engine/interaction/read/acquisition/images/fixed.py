# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One fixed-window image acquisition and its explicit bounded refresh Gate."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.components.support import ObservationGap, collect_component
from computer_use.interaction.read.acquisition.images.refresh import (
    IMAGE_REFRESH_ATTEMPTS,
    can_refresh_image,
    settle_capture_geometry,
)
from computer_use.native.contracts import Binding
from computer_use.pipeline import Continue, Contract, Done, Effect, Pass, Repeat, Sequence
from computer_use.pipeline.core import Control, Node

if TYPE_CHECKING:
    from computer_use.interaction.read.acquisition.images.capture import NativeImages, WindowImage
    from computer_use.native.port import NativePort


@dataclass(frozen=True)
class FixedImageAttempt:
    """The immutable target and local attempt state for this capture only."""

    target: Binding
    generation: tuple[int, str] | None
    attempt: int = 0
    image: WindowImage | None = None
    gap: ObservationGap | None = None
    admitted: bool = False
    expired: bool = False


def _expired(value: FixedImageAttempt, error: TimeoutError) -> FixedImageAttempt:
    return replace(
        value,
        image=None,
        expired=True,
        gap=ObservationGap(
            "image",
            "observation_incomplete",
            f"Image observation budget exhausted: {error}",
        ),
    )


def _check_cancelled(control: Control) -> None:
    if control.cancelled is not None and control.cancelled():
        raise asyncio.CancelledError()


async def _wait_selected_refresh(value: FixedImageAttempt, control: Control) -> FixedImageAttempt:
    _check_cancelled(control)
    if value.attempt:
        try:
            await settle_capture_geometry()
            control.remaining()
        except TimeoutError as error:
            return _expired(value, error)
    return value


def _image_refresh_gate(value: FixedImageAttempt, control: Control):
    if value.expired:
        return Done(value)
    try:
        refresh = can_refresh_image(
            attempt=value.attempt,
            scope_confirmed=value.admitted,
            image=value.image,
            gaps=(value.gap,) if value.gap is not None else (),
            remaining=control.remaining,
        )
    except TimeoutError as error:
        return Done(_expired(value, error))
    if refresh:
        return Continue(replace(value, attempt=value.attempt + 1))
    return Done(value)


class _CaptureFixedImage(Node[FixedImageAttempt, FixedImageAttempt]):
    """Admit and collect one exact window, keeping cleanup outside the Gate."""

    def __init__(self, port: NativePort, images: NativeImages):
        self.port, self.images = port, images
        super().__init__(
            "CaptureFixedWindowImage", Contract(FixedImageAttempt, FixedImageAttempt, {Effect.QUERY, Effect.RESOURCE})
        )

    def _validate_generation(self, value: FixedImageAttempt) -> None:
        if self.port.calls.retired or self.port.connection.session_identity != value.generation:
            raise ComputerUseError(
                dict(
                    code="stale_target",
                    effect="none",
                    target="window image",
                    detail="The image observation's native session is retired or changed.",
                )
            )

    async def _arun(self, value: FixedImageAttempt, control: Control) -> FixedImageAttempt:
        _check_cancelled(control)
        self._validate_generation(value)
        if value.expired:
            return value
        admitted = False

        async def collect():
            nonlocal admitted
            async with self.port.calls.operation(value.target, control.remaining) as read:
                admitted = True
                return await self.images.collect(read)

        try:
            image, gap = await collect_component(self.port, "image", collect)
        except TimeoutError as error:
            if self.port.calls.retired or self.port.connection.session_identity != value.generation:
                raise
            return _expired(replace(value, admitted=admitted), error)
        self._validate_generation(value)
        return replace(value, image=image, gap=gap, admitted=admitted)

    def _run(self, value, control):
        raise TypeError("Image collection requires arun()")


class FixedWindowImagePipeline(Node[Binding, FixedImageAttempt]):
    """Reuse image refresh policy without nesting the application read loop.

    Both native admissions retain the same target and claim deadline. Native
    begin and capture validate identity and geometry again; no modal selection,
    focus operation, input dispatch, or modification of an older PNG occurs.
    """

    def __init__(self, port: NativePort, images: NativeImages):
        self.port = port
        self.loop = Repeat(
            name="FixedWindowImageRefreshLoop",
            body=Sequence(
                name="FixedWindowImageAttempt",
                steps=[
                    Pass(
                        "wait_fixed_image_refresh",
                        _wait_selected_refresh,
                        Contract(FixedImageAttempt, FixedImageAttempt, {Effect.QUERY}),
                    ),
                    _CaptureFixedImage(port, images),
                ],
            ),
            gate=Pass(
                "FixedWindowImageRefreshGate", _image_refresh_gate, Contract(FixedImageAttempt, object, {Effect.PURE})
            ),
            max_iterations=IMAGE_REFRESH_ATTEMPTS,
        )
        self.children = (self.loop,)
        super().__init__(
            "FixedWindowImagePipeline",
            Contract(Binding, FixedImageAttempt, self.loop.contract.effects | {Effect.RESOURCE}),
        )

    async def _arun(self, target: Binding, control: Control) -> FixedImageAttempt:
        _check_cancelled(control)
        target = replace(target, node=None, capture=None)
        value = FixedImageAttempt(target, self.port.connection.session_identity)
        if control.remaining is not None and self.port.calls.busy:
            self.port.calls.validate_claim(control.remaining)
            return await self.loop.arun(value, control)
        try:
            available = control.remaining() if control.remaining is not None else None
            timeout = min(30000, available) if available is not None else 30000
        except TimeoutError as error:
            if self.port.calls.retired or self.port.connection.session_identity != value.generation:
                raise
            return _expired(value, error)
        with self.port.calls.claim(timeout) as remaining:
            claimed = self.port.calls.control(remaining)
            nested = replace(
                control.with_remaining(remaining),
                diagnostic=control.diagnostic or claimed.diagnostic,
                call_id=control.call_id or claimed.call_id,
            )
            return await self.loop.arun(value, nested)

    def _run(self, value, control):
        raise TypeError("Image collection requires arun()")
