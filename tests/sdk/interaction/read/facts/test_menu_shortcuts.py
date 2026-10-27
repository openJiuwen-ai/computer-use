"""Declared menu chords survive the v5 raw-facts/read boundary without input."""

import copy
import unittest
from types import SimpleNamespace

from observation_fixture import normalize_fixture as normalizeFacts, reply

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.facts.shortcuts import MACOS_MENU_SHORTCUT_FIELDS
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.contracts import Binding


def source(**changes):
    values = dict(
        AXRole="AXMenuItem",
        AXTitle="Save",
        AXMenuItemCmdChar="S",
        AXMenuItemCmdModifiers=0,
        AXMenuItemCmdVirtualKey=1,
        AXMenuItemCmdGlyph=0,
    )
    values.update(changes)
    return dict(
        node="save",
        requestedFields=list(values),
        values=values,
        fieldStatus={},
        actions=dict(state="complete", items=[]),
    )


def batch(item):
    return dict(
        kind="nodes",
        session="session",
        surface="surface",
        platform="macos",
        observed=dict(id="observation", startedAt=1, completedAt=2),
        roots=["save"],
        nodes=[item],
        requestedRelations=[],
        relations=[],
        unread=[],
        stopReasons=[],
    )


def macos_menu_shortcut(item):
    shortcuts = reply(batch(item)).observation.nodes["save"].shortcuts
    return shortcuts[0].text if shortcuts else None


class MenuShortcutTests(unittest.TestCase):
    def test_ax_modifier_mask_is_not_a_cgevent_mask(self):
        expected = (
            "⌘S",
            "⇧⌘S",
            "⌥⌘S",
            "⌥⇧⌘S",
            "⌃⌘S",
            "⌃⇧⌘S",
            "⌃⌥⌘S",
            "⌃⌥⇧⌘S",
            "S",
            "⇧S",
            "⌥S",
            "⌥⇧S",
            "⌃S",
            "⌃⇧S",
            "⌃⌥S",
            "⌃⌥⇧S",
        )
        for mask, label in enumerate(expected):
            with self.subTest(mask=mask):
                self.assertEqual(macos_menu_shortcut(source(AXMenuItemCmdModifiers=mask)), label)

    def test_native_characters_not_keyboard_layout_guesses(self):
        for key, expected in [
            ("a", "⌘a"),
            (",", "⌘,"),
            (" ", "⌘Space"),
            ("\uf700", "⌘↑"),
            ("\uf703", "⌘→"),
            ("\uf704", "⌘F1"),
            ("\uf70f", "⌘F12"),
        ]:
            with self.subTest(key=key):
                self.assertEqual(macos_menu_shortcut(source(AXMenuItemCmdChar=key)), expected)

    def test_unknown_missing_and_failed_facts_never_invent_a_chord(self):
        for value in (None, True, -1, 16, 24, 0.0, "0"):
            with self.subTest(modifiers=value):
                self.assertIsNone(macos_menu_shortcut(source(AXMenuItemCmdModifiers=value)))
        for character in ("", "SS", "\n", "\t", "\x00", "\u200d", "\uf8ff", "\ud800", None):
            with self.subTest(character=repr(character)):
                self.assertIsNone(macos_menu_shortcut(source(AXMenuItemCmdChar=character)))
        for key in ("AXRole", "AXMenuItemCmdChar", "AXMenuItemCmdModifiers"):
            item = source()
            del item["values"][key]
            item["fieldStatus"][key] = dict(state="absent")
            self.assertIsNone(macos_menu_shortcut(item))
            for state in ("error", "timeout", "truncated", "unsupported"):
                item = source()
                item["fieldStatus"][key] = dict(state=state)
                if state == "truncated":
                    item["fieldStatus"][key]["detail"] = "provider returned incomplete shortcut metadata"
                else:
                    item["values"].pop(key)
                self.assertIsNone(macos_menu_shortcut(item))
        self.assertIsNone(macos_menu_shortcut(source(AXRole="AXStaticText")))

    def test_projection_preserves_native_facts_reference_and_capabilities(self):
        raw = batch(source())
        before = copy.deepcopy(raw)
        reference = DisplayReferences("session")
        snapshot = normalizeFacts(raw, reference)
        rendered = snapshot.render(compact=True)
        self.assertIn("Shortcut: ⌘S", rendered["text"])
        self.assertEqual(snapshot.native.nodes["save"].shortcuts[0].text, "⌘S")
        self.assertEqual(snapshot.native.nodes["save"].shortcuts[0].kind, "accelerator")
        self.assertEqual(raw, before)
        self.assertEqual(snapshot.native, __import__("observation_fixture").reply(before).observation)
        self.assertEqual(rendered["refs"], {"0": "save"})
        # Declaring a shortcut is not evidence of click/keyboard readiness.
        without = source()
        for key in MACOS_MENU_SHORTCUT_FIELDS:
            without["values"].pop(key)
            without["requestedFields"].remove(key)
        baseline = normalizeFacts(batch(without), reference)
        self.assertEqual(snapshot.roots[0]["capabilities"], baseline.roots[0]["capabilities"])
        self.assertEqual(baseline.render(compact=True)["refs"], rendered["refs"])

    def test_noncharacter_glyph_is_retained_without_unicode_or_layout_guess(self):
        raw = batch(source(AXMenuItemCmdChar="", AXMenuItemCmdGlyph=27, AXMenuItemCmdVirtualKey=51))
        rendered = normalizeFacts(raw, DisplayReferences("session")).render(compact=True)
        self.assertNotIn("Shortcut:", rendered["text"])
        self.assertIn('"key":"provider.macos.shortcut.AXMenuItemCmdGlyph","value":27', rendered["details"])
        self.assertIn('"key":"provider.macos.shortcut.AXMenuItemCmdVirtualKey","value":51', rendered["details"])


class Provider:
    def __init__(self, features):
        self.connection = SimpleNamespace(platform="macos", capabilities=set(features))

    async def nodes(self, operation, target, fields, max_nodes, max_depth, timeout, **options):
        self.fields, self.options = fields, options
        item = source()
        item["requestedFields"] = fields
        item["values"] = {k: v for k, v in item["values"].items() if k in fields}
        item["fieldStatus"] = {k: dict(state="unsupported") for k in fields if k not in item["values"]}
        return reply(batch(item))


class MenuShortcutReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_read_uses_declared_fields_only_when_negotiated(self):
        for features in (
            (),
            ("nativeMenuShortcuts",),
            ("nativeAdvertisedFields",),
            ("nativeMenuShortcuts", "nativeAdvertisedFields"),
        ):
            with self.subTest(features=features):
                port = Provider(features)
                read = SimpleNamespace(
                    target=Binding("session", "app", "window", "surface", "save"),
                    token="operation",
                    remaining=lambda: 1000,
                )
                result = await NativeReader(port).collect(read)
                supported = len(features) == 2
                self.assertEqual(set(MACOS_MENU_SHORTCUT_FIELDS) <= set(port.fields), supported)
                self.assertEqual(
                    set(MACOS_MENU_SHORTCUT_FIELDS) <= set(port.options.get("advertised_fields", ())), supported
                )
                self.assertEqual("Shortcut: ⌘S" in result.render(compact=True)["text"], supported)
                await NativeReader(port).collect(read, fields=["AXRole"])
                self.assertEqual(port.fields, ["AXRole"])
                self.assertNotIn("advertised_fields", port.options)
