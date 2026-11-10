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
    # The global locator consumes the retained Target binding and capture as one internal contract.
    # pylint: disable-next=protected-access
    if target._point is None and target._snapshot is None:
        raise local_failure("a global pointer target requires an Element or an image point")
    # The global locator consumes the retained Target binding and capture as one internal contract.
    # pylint: disable-next=protected-access
    binding, point = target._binding, target._point
    # The global locator consumes the retained Target capture without object routing.
    # pylint: disable-next=protected-access
    if target._image is not None:
        # The global locator consumes the retained Target capture without object routing.
        # pylint: disable-next=protected-access
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
    """Dispatch raw primitives where supported, otherwise use captured coordinates."""

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
            if port.platform == "macos" or (
                port.platform == "windows" and request.intent.kind in ("key", "insert", "paste")
            ):
                return await self._primitive(request, GlobalTarget(session.session), remaining)
        return await self._captured_input(request)

    async def _primitive(self, request, scope, remaining):
        port = self.controller.port
        points = tuple([await locate_point(port, point, remaining) for point in request.points])
        intent = Intent(request.intent.kind, input_parameters(request, points), global_input=True)
        step, entered = None, False
        try:
            async with port.calls.operation(scope, remaining, intent=intent) as operation:
                entered = True
                step = await port.execute_global(operation.token, scope, intent, remaining())
                empty_text = (
                    port.platform == "windows"
                    and intent.kind in ("insert", "paste")
                    and intent.parameters["text"] == ""
                    and step.dispatch == Dispatch.NOT_DISPATCHED
                    and step.confirmation == Confirmation.CONFIRMED
                )
                input_missing = step.dispatch != Dispatch.DISPATCHED and not empty_text
                if input_missing or step.error_code is not None or step.cleanup != Cleanup.COMPLETE:
                    raise failure(
                        step.error_code or "execution_unknown",
                        "Computer",
                        step.detail,
                        step.dispatch,
                        step.preparation,
                    )
        except asyncio.CancelledError as caught:
            caught.impact = global_impact(port, step, caught, entered=entered)
            raise
        except BaseException as caught:
            # Never replay: a lost or partial receipt may already have changed
            # the desktop. Cleanup errors also retain known dispatch evidence.
            if isinstance(caught, ComputerUseError):
                error = caught
                if step is not None:
                    error.effect = failure(
                        error.code, error.target, error.detail, step.dispatch, step.preparation, error.effect
                    ).effect
            else:
                error = failure(
                    "execution_unknown" if entered else "execution_failed",
                    "Computer",
                    str(caught),
                    step.dispatch if step is not None else Dispatch.UNKNOWN if entered else Dispatch.NOT_DISPATCHED,
                    step.preparation if step is not None else (),
                )
            error.impact = global_impact(port, step, error, entered=entered)
            # The execution boundary supplies the public-method name for error formatting.
            # pylint: disable-next=protected-access
            error._method = intent.kind
            if error is caught:
                raise caught
            raise error from caught
        receipt = {
            "status": "verified" if step.confirmation == Confirmation.CONFIRMED else "dispatched",
            "detail": step.detail,
            "target": "Computer",
            "intent": intent.kind,
            "impact": global_impact(port, step, entered=True),
        }
        action_completed(receipt)
        # The global route uses the session-owned result constructor without object routing.
        # pylint: disable-next=protected-access
        return self.controller._action_result(
            receipt,
            observation=ObservedAction(receipt, observation_status="not_requested", window_lifecycle="not_applicable"),
        )

    async def _captured_input(self, request):
        # Windows pointer and Linux desktop input still use captured coordinates/scope.
        from computer_use.client.targets import Target, target_request

        points, intent = request.points, request.intent
        if intent.kind == "paste":
            raise local_failure(
                "Computer.paste is unavailable on Linux; use App/Element.paste.",
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
