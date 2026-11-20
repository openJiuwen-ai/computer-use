# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public input validation retains the original Python and wire contracts."""

import pytest
from computer_use import ClickOption
from computer_use.client import parameters
from computer_use.errors import ComputerUseError
from computer_use.protocol.validation import normalize_key, validate_paste_text, validate_text


def test_references_and_scoped_points_preserve_python_types():
    class Integer(int):
        pass

    assert parameters.reference(10**24) == "1000000000000000000000000"
    for invalid in (True, Integer(2), -1, 1.0):
        with pytest.raises(ComputerUseError, match="window reference must be a nonnegative integer"):
            parameters.reference(invalid, "window reference")

    point = (1.5, -0.0)
    assert parameters.point(point) is point
    screen = (-5.0, 3)
    assert parameters.screen_point(screen) is screen
    with pytest.raises(ComputerUseError, match="nonnegative"):
        parameters.point(screen)
    for invalid in ((True, 1), (10**500, 2), (float("inf"), 2), [1, 2]):
        with pytest.raises(ComputerUseError, match="finite numbers"):
            parameters.screen_point(invalid)


def test_click_and_scroll_preserve_qualification_and_button_identity():
    class Button(str):
        pass

    button = Button("right")
    assert parameters.click(None) == {"button": "left", "count": 1}
    assert parameters.click(ClickOption(button=button, count=2))["button"] is button
    with pytest.raises(ComputerUseError, match="expects ClickOption"):
        parameters.click(ClickOption(count=True))
    parameters.scroll("down", 0.5)
    with pytest.raises(ComputerUseError, match="finite positive page amount"):
        parameters.scroll("down", 10**500)


def test_menu_title_and_cursor_keep_distinct_whitespace_and_length_rules():
    parameters.view("tree", True, ["🚀" * 128, "\u0085"])
    for invalid in (["🚀" * 129], ["\ufeff"], ["a"] * 9, ("a",)):
        with pytest.raises(ComputerUseError, match="menu_path requires 1-8 nonempty titles"):
            parameters.view("tree", True, invalid)
    with pytest.raises(ComputerUseError, match="and a bool full"):
        parameters.view("tree", 1)
    parameters.cursor("🚀" * 256)
    parameters.cursor("\ufeff")
    for invalid in ("\u0085", "🚀" * 257):
        with pytest.raises(ComputerUseError, match="cursor must be None"):
            parameters.cursor(invalid)


@pytest.mark.parametrize(
    ("key", "macos", "expected"),
    [
        ("ctrl+BACKSPACE", False, "ctrl+BackSpace"),
        ("alt+BacKSpace", False, "alt+BackSpace"),
        ("super", False, "super"),
        ("+", True, "literal-plus"),
        ("ctrl++", True, "ctrl+literal-plus"),
        ("shift+?", True, "shift+?"),
    ],
)
def test_key_encoding_preserves_named_keys_modifiers_and_macos_glyphs(key, macos, expected):
    assert normalize_key(key, allow_macos_glyphs=macos) == expected


@pytest.mark.parametrize("key", ["CTRL+a", "ctrl+ctrl+a", "ctrl+", "?", "+", "ctrl+super", "A", " Return "])
def test_key_rejection_keeps_existing_error(key):
    with pytest.raises(ValueError, match="unsupported key or modifiers:"):
        normalize_key(key)


def test_text_rejects_control_before_invalid_unicode_and_preserves_pairs():
    validate_text("🚀")
    validate_text("\ud834\udf06")
    validate_paste_text("\t\r\n\u2028\u2029\ud834\udf06")
    with pytest.raises(ValueError, match="one line of text"):
        validate_text("\ud834\t")
    with pytest.raises(ValueError, match="typeText expects valid Unicode"):
        validate_text("\ud834")
    with pytest.raises(ValueError, match="only tabs and line breaks"):
        validate_paste_text("\ud834\0")
    with pytest.raises(ValueError, match="paste expects valid Unicode"):
        validate_paste_text("\udf06")
