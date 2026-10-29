# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Action receipt identity and captured-text delivery callbacks."""

from computer_use.identity import FrozenRecord, WeakIdentityMap

_actions = WeakIdentityMap()
# Private variables hold mutable session state.
# pylint: disable-next=invalid-name
_text_handler = None


def action_result(receipt):
    result = FrozenRecord(dict(status=receipt["status"], detail=receipt["detail"]))
    _actions.set(result, receipt)
    return result


def result_receipt(value):
    return _actions.get(value)


def set_text_page_handler(handler):
    global _text_handler
    _text_handler = handler


def text_page_completed(source, result):
    if _text_handler:
        _text_handler(source, result)
