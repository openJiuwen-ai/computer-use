# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Assemble public handles and presentation callbacks for one native session."""

from computer_use.interaction.controller import SessionController
from computer_use.interaction.model import ControllerAdapters

from .computer import computer_for_controller, observation_state
from .images import image_delivery_epoch, image_delivery_required, register_image
from .result_presentation import action_result, captured_text


def controller_adapters():
    return ControllerAdapters(
        observation_state=observation_state,
        action_result=action_result,
        captured_text=captured_text,
        register_image=register_image,
        image_delivery_epoch=image_delivery_epoch,
        image_delivery_required=image_delivery_required,
    )


def session_controller(port=None, *, allow_global_input=True):
    return SessionController(port, adapters=controller_adapters(), allow_global_input=allow_global_input)


def native_computer(port=None):
    return computer_for_controller(session_controller(port))
