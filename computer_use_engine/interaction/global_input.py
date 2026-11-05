# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit Computer input: optional geometric location, then one global primitive.

There is no object qualification, focus preparation, replay, or automatic read
here. Native operation lifetime and recorded dispatch remain session-owned.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.requests import ComputerInput
from computer_use.interaction.write.progress import failure
from computer_use.interaction.write.request.model import ObservedAction
from computer_use.native.contracts import Cleanup, Confirmation, Dispatch, GlobalTarget, Intent
from computer_use.pipeline import Contract, Effect
from computer_use.pipeline.core import Node
from computer_use.session.events import action_completed, observations_before_write


async def locate_point(port, target, remaining):
    """Convert an explicit locator; raw tuples are already native screen points."""
    if isinstance(target, tuple):
        return target
    if target._point is None and target._snapshot is None:
        raise local_failure("a global pointer target requires an Element or an image point")
    binding, point = target._binding, target._point
    if target._image is not None:
        piece, point = target._image.hit(point, scope_required=False)
        binding = piece.image.target
    return await port.locate_point(binding, point, remaining())


def input_parameters(request, points):
    parameters = dict(request.intent.parameters)
    if request.intent.kind == "drag":
        if points[0] == points[1]:
            raise local_failure("drag start and end must differ")
        parameters.update(fromX=points[0][0], fromY=points[0][1], toX=points[1][0], toY=points[1][1])
    elif points:
        parameters.update(x=points[0][0], y=points[0][1])
    return parameters


def global_impact(port, step=None, error=None, *, entered=False):
    """Keep actual native effects and uncertainty without inventing a selected route."""
    actual = asdict(step.impact) if step is not None and step.impact is not None else None
    if actual is not None:
        actual["foreground_changed"] = step.impact.foreground_changed.value
        actual["effects"] = tuple(actual["effects"])
    dispatch = (
        step.dispatch.value
        if step is not None
        else "notDispatched"
        if not entered or getattr(error, "effect", None) == "none"
        else "partial"
        if getattr(error, "effect", None) == "partial"
        else "unknown"
    )
    return {
        "global_input_allowed": port.policy.allow_global_input,
        "attempts": (
            {
                "route": "global_input",
                "declared_global_input": "yes",
                "preparation_potential": None,
                "foreground_requested": False,
                "preparation_only": False,
                "preparation": step.preparation if step is not None else None,
                "dispatch": dispatch,
                "cleanup": step.cleanup.value if step is not None and not port.calls.retired else "unknown",
                "actual": actual,
            },
        )
        if entered
        else (),
    }


class GlobalInput(Node):
    """Use macOS raw input while preserving other platforms' existing adapters."""

    def __init__(self, controller):
        self.controller, self.children = controller, ()
        super().__init__(
            "GlobalInput", Contract(ComputerInput, object, {Effect.QUERY, Effect.DISPATCH, Effect.RESOURCE})
        )

    async def _arun(self, request, control):
        controller, port = self.controller, self.controller.port
        observations_before_write(controller)
        with port.calls.claim(30000) as remaining:
            session = await port.connect(remaining())
            if port.platform == "macos":
                return await self._macos(request, GlobalTarget(session.session), remaining)
        return await self._other_platform(request)

    async def _macos(self, request, scope, remaining):
        port = self.controller.port
        points = tuple([await locate_point(port, point, remaining) for point in request.points])
        intent = Intent(request.intent.kind, input_parameters(request, points), global_input=True)
        step, entered = None, False
        try:
            async with port.calls.operation(scope, remaining, intent=intent) as operation:
                entered = True
                step = await port.execute_global(operation.token, scope, intent, remaining())
                if (
                    step.dispatch != Dispatch.DISPATCHED
                    or step.error_code is not None
                    or step.cleanup != Cleanup.COMPLETE
                ):
                    raise failure(
                        step.error_code or "execution_unknown",
                        "Computer",
                        step.detail,
                        step.dispatch,
                        step.preparation,
                    )
        except BaseException as caught:
            # Never replay: a lost or partial receipt may already have changed
            # the desktop. Cleanup errors also retain known dispatch evidence.
            if isinstance(caught, ComputerUseError):
                error = caught
                if step is not None:
                    error.effect = failure(
                        error.code, error.target, error.detail, step.dispatch, step.preparation, error.effect
                    ).effect
            elif isinstance(caught, asyncio.CancelledError):
                caught.impact = global_impact(port, step, caught, entered=entered)
                raise
            else:
                error = failure(
                    "execution_unknown" if entered else "execution_failed",
                    "Computer",
                    str(caught),
                    step.dispatch if step is not None else Dispatch.UNKNOWN if entered else Dispatch.NOT_DISPATCHED,
                    step.preparation if step is not None else (),
                )
                error.__cause__ = caught
            error.impact = global_impact(port, step, error, entered=entered)
            error._method = intent.kind
            raise error
        receipt = {
            "status": "verified" if step.confirmation == Confirmation.CONFIRMED else "dispatched",
            "detail": step.detail,
            "target": "Computer",
            "intent": intent.kind,
            "impact": global_impact(port, step, entered=True),
        }
        action_completed(receipt)
        return self.controller._action_result(
            receipt,
            observation=ObservedAction(receipt, observation_status="not_requested", window_lifecycle="not_applicable"),
        )

    async def _other_platform(self, request):
        # Windows/Linux keep their existing captured desktop/input support.
        # Their native raw global contract will migrate independently.
        from computer_use.client.targets import Target, target_request

        points, intent = request.points, request.intent
        if intent.kind == "paste":
            raise local_failure(
                "Computer.paste is implemented on macOS; use App/Element.paste for current Windows/Linux support.",
                "unsupported_operation",
                "Computer",
            )
        if any(isinstance(point, Target) for point in points):
            return await self.controller.run(
                target_request(
                    self.controller,
                    points[0],
                    intent.kind,
                    dict(intent.parameters),
                    global_input=True,
                    end=points[1] if len(points) == 2 else None,
                )
            )
        parameters = input_parameters(request, points)
        for x, y in points:
            self.controller.desktop.check_coordinates(x, y)
        method = {"insert": "type_text", "key": "press_key"}.get(intent.kind, intent.kind)
        parameters["desktopRef"] = self.controller.desktop.target(bool(points))
        return await self.controller.act(method, parameters)

    def _run(self, value, control):
        raise TypeError("Global input requires arun()")
