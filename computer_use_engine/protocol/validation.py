# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re

from .primitives import nonempty

"Public text and keyboard intent validation."


def _unicode(value):
    # A pair of explicit surrogate code points is also valid JS Unicode input.
    try:
        value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "strict")
        return True
    except UnicodeError:
        return False


def validate_text(value):
    if not isinstance(value, str) or re.search("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]", value):
        raise ValueError(
            "typeText expects one line of text; use paste for multiline content or tabs, "
            "and press_key for control keys."
        )
    if not _unicode(value):
        raise ValueError("typeText expects valid Unicode")


def validate_paste_text(value):
    if not isinstance(value, str) or re.search("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f]", value):
        raise ValueError("paste expects plain text; only tabs and line breaks are allowed control characters")
    if not _unicode(value):
        raise ValueError("paste expects valid Unicode")


_NAMED_KEYS = (
    "Return",
    "Tab",
    "Escape",
    "BackSpace",
    "Delete",
    "space",
    "Left",
    "Right",
    "Up",
    "Down",
    "Home",
    "End",
    "Page_Up",
    "Page_Down",
    *[f"F{i}" for i in range(1, 13)],
)
_CANONICAL_KEY_NAMES = {name.lower(): name for name in _NAMED_KEYS}
_PUNCTUATION = "-=[]\\;',./`"


def normalize_key(value, *, allow_macos_glyphs=False):
    nonempty(value, "key")
    if allow_macos_glyphs and value == "+":
        parts, name = [], "+"
    elif allow_macos_glyphs and value.endswith("++"):
        parts, name = value[:-2].split("+"), "+"
    else:
        *parts, name = value.split("+")
    key = _CANONICAL_KEY_NAMES.get(name.lower(), name)
    if (
        not (
            key in _NAMED_KEYS
            or allow_macos_glyphs
            and key in ("+", "?")
            or len(key) == 1
            and key in _PUNCTUATION
            or re.fullmatch("[a-z0-9]", key)
            or not parts
            and key == "super"
        )
        or len(set(parts)) != len(parts)
        or any(p not in ("super", "ctrl", "alt", "shift") for p in parts)
    ):
        raise ValueError(
            f"unsupported key or modifiers: {value}. Keys: a–z, 0–9, punctuation {_PUNCTUATION}"
            f", {', '.join(_NAMED_KEYS)}; modifiers: super, ctrl, alt, shift (joined with +)"
        )
    # Keep the wire chord unambiguous for native modifier and route checks.
    return "+".join((*parts, "literal-plus" if key == "+" else key))
