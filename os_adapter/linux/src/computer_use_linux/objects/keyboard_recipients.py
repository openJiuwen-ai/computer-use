# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session discovery hint shared by keyboard units; never an input authority."""

from dataclasses import dataclass


@dataclass
class NativeKeyboardRecipients:
    hint: tuple | None = None

    def clear(self):
        self.hint = None
