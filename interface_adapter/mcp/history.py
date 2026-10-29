# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host history-cursor handshake; authorizes incremental delivery without owning baselines."""

import uuid

from computer_use.protocol.primitives import utf16_length

HISTORY_KEY = "computer-use-history"


def history_request(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"previous", "version"}
        or type(value["version"]) not in (int, float)
    ):
        return None
    if value["version"] != 1:
        return None
    previous = value["previous"]
    if previous is not None and (not isinstance(previous, str) or not 0 < utf16_length(previous) <= 128):
        return None
    return dict(version=1, previous=previous)


class HistoryGate:
    def __init__(self):
        self._cursor = None
        self._managed = False

    def begin(self, request=None):
        continued = request is not None and request["previous"] is not None and request["previous"] == self._cursor
        reset_images = not continued and (self._managed or request is not None)
        self._managed = request is not None
        return dict(incremental=request is not None, resetText=not continued, resetImages=reset_images)

    def complete(self):
        self._cursor = str(uuid.uuid4())
        return dict(version=1, cursor=self._cursor)
