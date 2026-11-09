# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from computer_use.diagnostics import record_structured_observations, set_diagnostic_handler
from computer_use.native.preview import set_preview_handler
from computer_use.session.events import set_action_handler, set_observation_handler
from computer_use.session.lookup_continuation import lookup_continuation_scope
from computer_use.session.operations import set_execution_guard
from computer_use.session.results import set_text_page_handler

from .handles import set_handle_handler

__all__ = [
    "computer_host",
    "lookup_continuation_scope",
    "record_structured_observations",
    "set_action_handler",
    "set_diagnostic_handler",
    "set_execution_guard",
    "set_handle_handler",
    "set_observation_handler",
    "set_preview_handler",
    "set_text_page_handler",
]


def computer_host(factory=None):
    """Own one fresh public Computer and its session lifecycle for a host task."""
    from ..identity import Record

    if factory is None:
        from .. import Computer

        factory = Computer
    computer = factory()

    return Record(computer=computer, close=computer.close)
