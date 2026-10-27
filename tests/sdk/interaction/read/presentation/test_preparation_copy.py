"""Fact-copy performance shortcuts retain generic container and detachment contracts."""

import unittest
from collections import UserDict
from types import MappingProxyType

from computer_use.interaction.read.presentation.preparation import _copy_collections


class PreparationCopyTests(unittest.TestCase):
    def test_freeze_and_materialize_preserve_values_aliases_and_source_detachment(self):
        shared = {"text": "测试", "range": [0, 3], "flags": {"focused"}}
        source = {"first": shared, "second": shared, "optional": None, "count": 2, "enabled": False}
        captured = _copy_collections(source, immutable=True)
        self.assertIsInstance(captured, MappingProxyType)
        self.assertIs(captured["first"], captured["second"])
        source["first"]["range"][1] = 99
        self.assertEqual(captured["first"]["range"], (0, 3))
        with self.assertRaises(TypeError):
            captured["first"]["text"] = "changed"
        first = _copy_collections(captured, immutable=False)
        second = _copy_collections(captured, immutable=False)
        self.assertIs(first["first"], first["second"])
        first["first"]["range"][1] = 20
        self.assertEqual(second["first"]["range"], [0, 3])
        self.assertEqual(captured["first"]["range"], (0, 3))

    def test_custom_mappings_and_container_subclasses_keep_existing_conversion(self):
        class CustomList(list):
            pass

        class CustomDict(dict):
            pass

        source = UserDict({"custom": CustomList([CustomDict(value=3)]), "frozen": frozenset({2})})
        captured = _copy_collections(source, immutable=True)
        self.assertIsInstance(captured, MappingProxyType)
        self.assertIsInstance(captured["custom"], tuple)
        self.assertIsInstance(captured["custom"][0], MappingProxyType)
        self.assertEqual(captured["custom"][0]["value"], 3)
        self.assertEqual(_copy_collections(captured, immutable=False), {"custom": [{"value": 3}], "frozen": {2}})

    def test_deep_collections_remain_iterative_and_cycles_are_rejected(self):
        source = "leaf"
        for _ in range(2000):
            source = [source]
        copied = _copy_collections(source, immutable=True)
        for _ in range(2000):
            self.assertIsInstance(copied, tuple)
            copied = copied[0]
        self.assertEqual(copied, "leaf")
        cycle = []
        cycle.append(cycle)
        with self.assertRaisesRegex(ValueError, "collection cycle"):
            _copy_collections(cycle, immutable=True)


if __name__ == "__main__":
    unittest.main()
