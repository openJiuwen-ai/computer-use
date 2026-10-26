"""Assemble public handles and presentation callbacks for one native session."""

from computer_use.interaction.controller import SessionController
from computer_use.interaction.model import ControllerAdapters

from .computer import App, Window, computerForController, observationState
from .images import imageDeliveryEpoch, imageDeliveryRequired, registerImage
from .result_presentation import actionResult, capturedText


def controllerAdapters():
    return ControllerAdapters(
        create_app=lambda controller, scope: App(controller, scope, _internal=True),
        create_window=lambda controller, scope, app, anchor: Window(
            controller, scope, _internal=True, _app=app, _anchor=anchor
        ),
        observation_state=observationState,
        action_result=actionResult,
        captured_text=capturedText,
        register_image=registerImage,
        image_delivery_epoch=imageDeliveryEpoch,
        image_delivery_required=imageDeliveryRequired,
    )


def sessionController(port=None):
    return SessionController(port, adapters=controllerAdapters())


def nativeComputer(port=None):
    return computerForController(sessionController(port))
