# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable destinations shared by scoped and explicit global input."""

from dataclasses import InitVar, dataclass, field, replace

from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import ActOnElement, UIPointer
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.ui.pipeline import input_image, note_pending_point_image
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

    @staticmethod
    def __post_init__(_internal):
        if not _internal:
            raise TypeError("Use Element.target or Computer/App.point() to obtain a Target")

    def __repr__(self):
        kind = "point" if self._point is not None else "element"
        return f"Target({kind})"

    def _request(self, controller, intent, parameters, *, global_input, owner, end):
        scope = self._owner if owner is None else owner
        if intent == "drag" and self._point is None:
            raise local_failure("drag requires two image point targets")
        if self._point is not None:
            if intent not in ("click", "scroll", "drag"):
                raise local_failure("an image point accepts pointer input; use App or Element for keyboard input")
            points = (self._point,)
            if end is not None:
                last = as_target(controller, end)
                # Compare two retained Targets using their immutable capture identity.
                # pylint: disable-next=protected-access
                same_capture = last._point is not None and last._binding == self._binding
                if same_capture and self._image is not None:
                    # The peer Target owns the original delivered image, not a refreshed image.
                    # pylint: disable-next=protected-access
                    first_image, last_image = self._image, last._image
                    same_capture = (
                        last_image is not None
                        and last_image.png is first_image.png
                        and last_image.delivered_coordinates == first_image.delivered_coordinates
                    )
                if not same_capture:
                    raise local_failure("drag targets must use the same captured window or desktop image")
                # Compare peer Target coordinates only after establishing the same capture.
                # pylint: disable-next=protected-access
                if self._point == last._point:
                    raise local_failure("drag start and end must differ")
                # Use the validated peer Target endpoint without re-resolving it.
                # pylint: disable-next=protected-access
                points += (last._point,)
            if self._image is not None:
                return UIPointer(scope, intent, points, parameters, global_input=global_input, image=self._image)
            if owner is not None:
                raise local_failure("a desktop point requires a Computer input method")
            coordinates = (
                {"fromX": points[0][0], "fromY": points[0][1], "toX": points[1][0], "toY": points[1][1]}
                if intent == "drag"
                else {"x": points[0][0], "y": points[0][1]}
            )
            return ExecuteAction(None, Intent(intent, dict(parameters, **coordinates), global_input), self._binding)
        if owner is not None:
            raise local_failure("at expects image coordinates or a Target obtained from point()")
        return ActOnElement(scope, self._snapshot, intent, parameters, global_input)


def image_target(controller, owner, point):
    if owner is None:
        state, mapped = controller.desktop.point(*point)
        ref = state["desktopRef"]
        target = Target(
            controller,
            None,
            Binding(state["binding"]["session"], None, None, None, capture=ref),
            _point=mapped,
            _internal=True,
        )
        controller.interfaces.windows.retain_target_capture(target, ref)
        return target
    image = replace(input_image(controller, owner, current=False))
    try:
        piece, _ = image.hit(point, scope_required=False)
    except ComputerUseError as error:
        if error.code == "invalid_request":
            note_pending_point_image(controller, owner, error, after=image.generation)
        raise
    target = Target(controller, owner, piece.image.target, _point=point, _image=image, _internal=True)
    controller.interfaces.windows.retain_target_capture(target, piece.image.target.capture)
    return target


def as_target(controller, value):
    # Import locally to keep the public handle definitions independent.
    from .computer import Element

    if isinstance(value, Element):
        # Internal conversion already runs on the session; do not re-enter the public property.
        # pylint: disable-next=protected-access
        value = Element._retain_target(value)
    if not isinstance(value, Target):
        raise local_failure("target expects an Element or retained Target")
    # This module owns Target creation and validates its retained session identity.
    # pylint: disable-next=protected-access
    if value._controller is not controller:
        raise local_failure("target belongs to another Computer session", "stale_target")
    return value


def target_request(controller, value, intent, parameters, *, global_input=False, owner=None, end=None):
    target = as_target(controller, value)
    # Keep request construction internal to the retained Target implementation.
    # pylint: disable-next=protected-access
    return Target._request(target, controller, intent, parameters, global_input=global_input, owner=owner, end=end)
