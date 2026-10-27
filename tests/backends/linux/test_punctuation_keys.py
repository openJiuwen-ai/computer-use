"""Shortcut syntax and X11 keysyms, without desktop input or an X server."""

import sys
import unittest
from types import SimpleNamespace

from repository import ROOT as REPO_ROOT

sys.path.append(str(REPO_ROOT / "os_adapter/linux/src"))

from computer_use.protocol.validation import validateKey
from computer_use_linux.contracts.protocol import Failure, key_parts
from computer_use_linux.system.x11 import X11


class PunctuationKeyTests(unittest.TestCase):
    def test_public_and_linux_syntax_accept_single_base_keys(self):
        for key in ("[", "]", "-", "=", "\\", ";", "'", ",", ".", "/", "`"):
            for prefix in ("", "super+", "ctrl+shift+"):
                with self.subTest(key=prefix + key):
                    validateKey(prefix + key)
                    self.assertEqual(key_parts(prefix + key), (prefix + key).split("+"))

    def test_invalid_or_ambiguous_chords_still_fail_before_native_input(self):
        for key in ("+", "super++", "super+super+[", "ctrl+[]", "ctrl+{", "ctrl+ [", "ctrl+é", "shift+", "meta+["):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validateKey(key)
                with self.assertRaises(Failure):
                    key_parts(key)

    def test_x11_uses_literal_punctuation_keysyms_and_preserves_modifier_names(self):
        symbols, names = [], []
        x11 = object.__new__(X11)

        def lookup(symbol):
            symbols.append(symbol)
            return 34

        def named(value):
            names.append(value)
            return {"Control_L": 0xFFE3, "Return": 0xFF0D}[value]

        x11.d = SimpleNamespace(keysym_to_keycode=lookup)
        x11.XK = SimpleNamespace(string_to_keysym=named)
        for key, symbol in (
            ("[", 0x5B),
            ("]", 0x5D),
            ("-", 0x2D),
            ("=", 0x3D),
            ("\\", 0x5C),
            (";", 0x3B),
            ("'", 0x27),
            (",", 0x2C),
            (".", 0x2E),
            ("/", 0x2F),
            ("`", 0x60),
        ):
            self.assertEqual(x11.keycode(key), 34)
            self.assertEqual(symbols[-1], symbol)
        self.assertEqual(names, [])
        x11.keycode("ctrl")
        x11.keycode("Return")
        self.assertEqual(names, ["Control_L", "Return"])
        self.assertEqual(symbols[-2:], [0xFFE3, 0xFF0D])
        x11.d = SimpleNamespace(keysym_to_keycode=lambda _: 0)
        with self.assertRaises(Failure) as error:
            x11.keycode("[")
        self.assertEqual(error.exception.code, "input_unavailable")


if __name__ == "__main__":
    unittest.main()
