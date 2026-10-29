# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native argument units, not a text-input fallback policy.

libatspi forwards InsertText.length as a byte count. Qt 5/6's AT-SPI adaptor
instead resizes a QString to that count, padding non-ASCII payloads. Adapt this
known provider ABI at the native boundary, never by editing a whole document.
Sources: GNOME atspi-editabletext.c; Qt qtbase atspiadaptor.cpp (InsertText).
"""

import re
from dataclasses import dataclass

from computer_use_linux.contracts.protocol import Failure


@dataclass(frozen=True)
class TextCodec:
    toolkit: str
    version: str
    qt: bool = False

    def insertion_length(self, text):
        if self.qt:
            parts = re.match(r"^(\d+)\.(\d+)(?:\.|$)", self.version)
            # The actual 6.10 provider and the maintained 5.15/6.x source use
            # QString::resize. Unknown future ABIs need evidence, not a guess.
            known = parts and (int(parts[1]) == 5 and int(parts[2]) == 15 or int(parts[1]) == 6 and int(parts[2]) <= 10)
            if not known and not text.isascii():
                raise Failure(
                    "This Qt AT-SPI version has an unverified Unicode insertion length contract.", "input_unavailable"
                )
            return len(text.encode("utf-16-le")) // 2
        return len(text.encode("utf-8"))


def text_codec(ax, obj):
    app = ax.call(ax.atspi.Accessible.get_application, obj)
    if app is None:
        raise Failure("The native text provider identity is unavailable.", "input_unavailable")
    toolkit = ax.call(ax.atspi.Accessible.get_toolkit_name, app)
    version = ax.call(ax.atspi.Accessible.get_toolkit_version, app)
    if not isinstance(toolkit, str) or not toolkit.strip() or not isinstance(version, str):
        raise Failure("The native text provider did not declare its argument contract.", "input_unavailable")
    return TextCodec(toolkit, version, toolkit.casefold() == "qt")
