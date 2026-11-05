# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable destinations shared by scoped and explicit global input."""

from dataclasses import InitVar, dataclass, field, replace

from computer_use.diagnostics import local_failure
from computer_use.interaction.agent.requests import ActOnElement, UIPointer
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.ui.pipeline import input_image
from computer_use.native.contracts import Binding, Intent


@dataclass(frozen=True, eq=False, repr=False)
class Target:
    """A retained element or point in one delivered image.

    Obtain it from Element.target or Computer/App.point().
    It records a destination independently of the input transport. Capture
    coordinates remain bound to their original image when later reads arrive.
    """

    _controller: object = field(repr=False)
    _owner: object = field(repr=False)
    _binding: Binding = field(repr=False)
    _snapshot: object = field(default=None, repr=False)
    _point: tuple | None = field(default=None, repr=False)
    _image: object = field(default=None, repr=False)
    _internal: InitVar[bool] = False

    def __post_init__(self, _internal):
        if not _internal:
            raise TypeError("Use Element.target or Computer/App.point() to obtain a Target")

    def __repr__(self):
        kind = "point" if self._point is not None else "element"
        return f"Target({kind})"


def element_target(element):
    return Target(
        element._controller,
        element._owner,
        element._snapshot["binding"],
        _snapshot=dict(element._snapshot),
        _internal=True,
    )


def image_target(controller, owner, point):
    if owner is None:
        controller.desktop.check_coordinates(*point, current=False)
        ref = controller.desktop.target(True, current=False)
        state = controller.desktop.observed(ref)
        mapped = controller.desktop.map_parameters("click", {"x": point[0], "y": point[1]}, current=False)
        target = Target(
            controller,
            None,
            Binding(state["binding"]["session"], None, None, None, capture=ref),
            _point=(mapped["x"], mapped["y"]),
            _internal=True,
        )
        controller.interfaces.windows.retain_target_capture(target, ref)
        return target
    image = replace(input_image(controller, owner, current=False))
    piece, _ = image.hit(point, scope_required=False)
    target = Target(controller, owner, piece.image.target, _point=point, _image=image, _internal=True)
    controller.interfaces.windows.retain_target_capture(target, piece.image.target.capture)
    return target


def as_target(controller, value):
    # Import locally to keep the public handle definitions independent.
    from .computer import Element

    if isinstance(value, Element):
        value = element_target(value)
    if not isinstance(value, Target):
        raise local_failure("target expects an Element or retained Target")
    if value._controller is not controller:
        raise local_failure("target belongs to another Computer session", "stale_target")
    return value


def target_request(controller, value, intent, parameters, *, global_input=False, owner=None, end=None):
    target = as_target(controller, value)
    scope = target._owner if owner is None else owner
    if intent == "drag" and target._point is None:
        raise local_failure("drag requires two image point targets")
    if target._point is not None:
        if intent not in ("click", "scroll", "drag"):
            raise local_failure("an image point accepts pointer input; use App or Element for keyboard input")
        points = (target._point,)
        if end is not None:
            last = as_target(controller, end)
            if (
                last._point is None
                or last._binding != target._binding
                or (
                    target._image is not None
                    and (
                        last._image is None
                        or last._image.png is not target._image.png
                        or last._image.delivered_coordinates != target._image.delivered_coordinates
                    )
                )
            ):
                raise local_failure("drag targets must use the same captured window or desktop image")
            if target._point == last._point:
                raise local_failure("drag start and end must differ")
            points += (last._point,)
        if target._image is not None:
            return UIPointer(scope, intent, points, parameters, global_input=global_input, image=target._image)
        if owner is not None:
            raise local_failure("a desktop point requires a Computer input method")
        coordinates = (
            {"fromX": points[0][0], "fromY": points[0][1], "toX": points[1][0], "toY": points[1][1]}
            if intent == "drag"
            else {"x": points[0][0], "y": points[0][1]}
        )
        return ExecuteAction(None, Intent(intent, dict(parameters, **coordinates), global_input), target._binding)
    if owner is not None:
        raise local_failure("at expects image coordinates or a Target obtained from point()")
    return ActOnElement(scope, target._snapshot, intent, parameters, global_input)
