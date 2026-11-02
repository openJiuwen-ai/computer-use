# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session discovery hint shared by keyboard units; never an input authority."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class NativeKeyboardRecipients:
    native_key: Callable[[object], object]
    hint: tuple | None = None

    def clear(self):
        self.hint = None

    def focus_lost(self, source):
        """Invalidate only a loss notification for the captured native object."""
        hint = self.hint
        if hint is not None and self.native_key(source) == self.native_key(hint[2]):
            self.clear()
