# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Action receipt identity and captured-text delivery callbacks."""

from computer_use.identity import FrozenRecord, WeakIdentityMap, freeze_result

_actions = WeakIdentityMap()
# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_text_handler = None


def action_result(receipt, *, observation=None):
    values = dict(
        status=receipt["status"], detail=receipt["detail"], target=receipt["target"], intent=receipt.get("intent")
    )
    values["impact"] = freeze_result(receipt.get("impact"))
    if observation is not None:
        values.update(
            observation=observation.observation,
            observation_status=observation.observation_status,
            observation_error=observation.observation_error,
            window_lifecycle=observation.window_lifecycle,
            choices=observation.choices,
        )
    result = FrozenRecord(values)
    _actions.set(result, receipt)
    if observation is not None and observation.observation_error is not None:
        from computer_use.session.events import action_observation_completed

        action_observation_completed(result)
    return result


def result_receipt(value):
    return _actions.get(value)


def set_text_page_handler(handler):
    global _text_handler
    _text_handler = handler


def text_page_completed(source, result):
    if _text_handler:
        _text_handler(source, result)
