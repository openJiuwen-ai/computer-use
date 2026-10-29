# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Client formatting of action/text results; provenance is session-owned."""

from computer_use.identity import FrozenRecord
from computer_use.interaction.read.facts.text_source import text_source_label
from computer_use.session.results import (
    action_result,
    result_receipt,
    set_text_page_handler,
    text_page_completed,
)

__all__ = [
    "action_result",
    "captured_text",
    "result_receipt",
    "set_text_page_handler",
    "text_source_label",
]


def captured_text(page, source):
    result = FrozenRecord(page, display=text_source_label(source) + "\n" + repr(dict(page)))
    text_page_completed(source, result)
    return result
