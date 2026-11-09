# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native argument units, not a text-input fallback policy.

AT-SPI declares InsertText.length in UTF-8 bytes. Qt's AT-SPI adaptor instead
resizes a QString in UTF-16 units. Adapt that provider family at the native
boundary; optional toolkit metadata is not permission to edit an object.
Sources: GNOME atspi-editabletext.c; Qt qtbase atspiadaptor.cpp (InsertText).
"""

from dataclasses import dataclass
from typing import Literal

from computer_use_linux.contracts.protocol import Failure


@dataclass(frozen=True)
class AsciiTextCodec:
    """The UTF-8 and UTF-16 insertion lengths coincide for this payload."""

    @staticmethod
    def insertion_length(text: str) -> int:
        if not text.isascii():
            raise Failure("The native ASCII insertion contract cannot send non-ASCII text.", "input_unavailable")
        return len(text)


@dataclass(frozen=True)
class TextCodec:
    """The required native argument unit, independent of metadata labels."""

    unit: Literal["utf8-bytes", "utf16-units"] = "utf8-bytes"

    def insertion_length(self, text: str) -> int:
        if self.unit == "utf16-units":
            return len(text.encode("utf-16-le")) // 2
        return len(text.encode("utf-8"))


def text_codec(ax, obj, *, text: str | None = None, fallback: TextCodec | None = None) -> AsciiTextCodec | TextCodec:
    # ASCII does not depend on a provider-specific length ABI. Keep that
    # payload contract separate from provider identity, including at dispatch.
    if text is not None and text.isascii():
        return AsciiTextCodec()
    # Initial unknown providers use the standard. During the same operation,
    # temporarily missing metadata does not contradict its selected unit.
    unknown = fallback if fallback is not None else TextCodec()
    accessible = ax.atspi.Accessible
    application = getattr(accessible, "get_application", None)
    toolkit_name = getattr(accessible, "get_toolkit_name", None)
    if application is None or toolkit_name is None:
        return unknown
    try:
        app = ax.call(application, obj)
        toolkit = ax.call(toolkit_name, app) if app is not None else None
    except ax.glib.Error:
        # No provider override was established. The declared EditableText
        # interface still supplies the standard contract. Request failures from
        # ax.call, including cancellation/deadline, continue to propagate.
        return unknown
    if not isinstance(toolkit, str) or not toolkit.strip():
        return unknown
    # Qt 5.15, 6.11, 6.12 and current dev all use QString::resize(length).
    # This family compatibility adapter does not expire at a version label;
    # neither known providers nor standards-compliant new ones need a whitelist.
    qt = toolkit.strip().casefold() == "qt"
    return TextCodec("utf16-units" if qt else "utf8-bytes")
