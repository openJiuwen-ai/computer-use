"""Raw AT-SPI geometry and range facts through the production read pipeline.

The substituted native provider makes these portable collection checks. It
does not establish Linux desktop or compositor support.
"""

import unittest
from types import SimpleNamespace as NS

from linux_native_provider import NativeError

import backends.linux.test_linux_native as base
from computer_use_linux.runtime.native import CAPABILITIES

METADATA = [
    "Component.extents",
    "selectable",
    "Value.minimumValue",
    "Value.maximumValue",
    "Value.minimumIncrement",
    "Value.text",
]


class LinuxNativeMetadataTests(unittest.TestCase):
    begin = base.LinuxNativeFactsTests.begin
    inspect = base.LinuxNativeFactsTests.inspect
    surfaces = base.LinuxNativeFactsTests.surfaces
    target = base.LinuxNativeFactsTests.target
    collect = base.LinuxNativeFactsTests.collect
    tearDown = base.LinuxNativeFactsTests.tearDown

    def setUp(self):
        base.LinuxNativeFactsTests.setUp(self)
        self.obj = self.backend.editor
        self.obj.interfaces += ["Component", "Value"]
        self.obj.extents = NS(x=-1920, y=-80, width=400, height=0)
        self.obj.minimum, self.obj.maximum, self.obj.increment = -10.5, 90.25, 0
        self.obj.value_text = "Medium"
        self.backend.ax.A.CoordType = NS(SCREEN="screen")
        self.backend.ax.A.StateType.SELECTABLE = "SELECTABLE"
        self.obj.states.add("SELECTABLE")

        def native(name, getter):
            def read(obj, *args):
                self.backend.ax.calls.append((name, obj, args))
                if name in obj.errors:
                    result = obj.errors[name]
                    if isinstance(result, BaseException):
                        raise result
                    return result
                return getter(obj, *args)

            return read

        self.backend.ax.A.Component.get_extents = native("get_extents", lambda obj, coord: obj.extents)
        self.backend.ax.A.Value.get_minimum_value = native("get_minimum_value", lambda obj: obj.minimum)
        self.backend.ax.A.Value.get_maximum_value = native("get_maximum_value", lambda obj: obj.maximum)
        self.backend.ax.A.Value.get_minimum_increment = native("get_minimum_increment", lambda obj: obj.increment)
        self.backend.ax.A.Value.get_text = native("Value.get_text", lambda obj: obj.value_text)

    def editor(self, fields=None):
        result = self.collect(fields=["name", *(METADATA if fields is None else fields)])
        return next(node for node in result["nodes"] if node["values"].get("name") == "Input")

    def test_raw_metadata_round_trips_without_changing_actions_or_native_identity(self):
        self.assertIn("nativeRawNodeMetadata", CAPABILITIES)
        target = self.target()
        first = self.editor()
        second = self.editor()
        self.assertEqual(first["node"], second["node"])
        self.assertEqual(first["fieldStatus"], {})
        self.assertEqual(
            first["values"],
            {
                "name": "Input",
                "Component.extents": dict(x=-1920, y=-80, width=400, height=0),
                "selectable": True,
                "Value.minimumValue": -10.5,
                "Value.maximumValue": 90.25,
                "Value.minimumIncrement": 0,
                "Value.text": "Medium",
            },
        )
        self.assertEqual(first["actions"], dict(state="complete", items=[], total=0))
        calls = [(obj, args) for name, obj, args in self.backend.ax.calls if name == "get_extents"]
        self.assertTrue(calls)
        self.assertTrue(all(obj is self.obj and args == ("screen",) for obj, args in calls))
        self.assertEqual(target["instance"], self.target()["instance"])

    def test_geometry_remains_raw_when_hidden_and_handles_signed_native_limits(self):
        self.obj.states.clear()
        for rect in (NS(x=-(2**31), y=2**31 - 1, width=0, height=0), NS(x=-90, y=-80, width=-1, height=-1)):
            with self.subTest(rect=rect):
                self.obj.extents = rect
                self.assertEqual(self.editor(["Component.extents"])["values"]["Component.extents"], vars(rect))
        self.assertFalse(
            any(name in ("get_accessible_at_point", "contains", "grab_focus") for name, _, _ in self.backend.ax.calls)
        )

    def test_missing_interface_is_unsupported_without_calling_its_getters(self):
        self.obj.interfaces.remove("Component")
        self.obj.interfaces.remove("Value")
        result = self.editor()
        for name in METADATA:
            if name != "selectable":
                self.assertEqual(result["fieldStatus"][name]["state"], "unsupported")
                self.assertNotIn(name, result["values"])
        self.assertFalse(
            any(
                name
                in ("get_extents", "get_minimum_value", "get_maximum_value", "get_minimum_increment", "Value.get_text")
                for name, _, _ in self.backend.ax.calls
            )
        )

    def test_missing_library_methods_and_state_constant_remain_unsupported(self):
        del self.backend.ax.A.Component.get_extents
        del self.backend.ax.A.Value.get_minimum_increment
        del self.backend.ax.A.Value.get_text
        del self.backend.ax.A.StateType.SELECTABLE
        result = self.editor(["Component.extents", "Value.minimumIncrement", "Value.text", "selectable"])
        self.assertTrue(all(status["state"] == "unsupported" for status in result["fieldStatus"].values()))
        self.assertEqual(
            set(result["fieldStatus"]), {"Component.extents", "Value.minimumIncrement", "Value.text", "selectable"}
        )

    def test_absent_geometry_and_value_text_do_not_become_synthetic_values(self):
        self.obj.errors.update(get_extents=None, **{"Value.get_text": None})
        result = self.editor(["Component.extents", "Value.text"])
        self.assertEqual(result["values"], {"name": "Input"})
        self.assertEqual(
            result["fieldStatus"],
            {
                "Component.extents": {"state": "absent", "rawResult": None},
                "Value.text": {"state": "absent", "rawResult": None},
            },
        )
        self.obj.errors["Value.get_text"] = ""
        result = self.editor(["Value.text"])
        self.assertEqual(result["values"]["Value.text"], "")
        self.assertEqual(result["fieldStatus"], {})

    def test_malformed_or_failed_geometry_does_not_fail_other_fields(self):
        for value in (
            object(),
            NS(x=0, y=0, width=1),
            NS(x=0, y=0, width=float("inf"), height=1),
            NS(x=True, y=0, width=1, height=1),
            NS(x=2**31, y=0, width=1, height=1),
            NativeError("geometry provider failed"),
        ):
            with self.subTest(value=value):
                self.obj.errors["get_extents"] = value
                result = self.editor(["Component.extents", "Text.text", "Value.minimumValue"])
                self.assertEqual(result["fieldStatus"]["Component.extents"]["state"], "error")
                self.assertNotIn("Component.extents", result["values"])
                self.assertEqual(result["values"]["Text.text"], "a😀bc")
                self.assertEqual(result["values"]["Value.minimumValue"], -10.5)

    def test_missing_coordinate_enum_does_not_query_or_fabricate_geometry(self):
        del self.backend.ax.A.CoordType.SCREEN
        result = self.editor(["Component.extents"])
        self.assertEqual(result["fieldStatus"]["Component.extents"]["state"], "unsupported")
        self.assertNotIn("Component.extents", result["values"])
        self.assertFalse(any(name == "get_extents" for name, _, _ in self.backend.ax.calls))

    def test_range_provider_errors_and_nonfinite_values_are_isolated(self):
        cases = [
            ("Value.minimumValue", "get_minimum_value", float("nan")),
            ("Value.maximumValue", "get_maximum_value", float("inf")),
            ("Value.minimumIncrement", "get_minimum_increment", True),
            ("Value.minimumIncrement", "get_minimum_increment", NativeError("range provider failed")),
            ("Value.text", "Value.get_text", 42),
        ]
        for field, getter, value in cases:
            with self.subTest(field=field, value=value):
                self.obj.errors = {getter: value}
                result = self.editor([field, "Text.text"])
                self.assertEqual(result["fieldStatus"][field]["state"], "error")
                self.assertNotIn(field, result["values"])
                self.assertEqual(result["values"]["Text.text"], "a😀bc")

    def test_selectable_and_selected_are_independent_raw_states(self):
        for selected, selectable in ((False, True), (True, False), (False, False), (True, True)):
            with self.subTest(selected=selected, selectable=selectable):
                self.obj.states = {"SELECTED"} if selected else set()
                if selectable:
                    self.obj.states.add("SELECTABLE")
                result = self.editor(["selected", "selectable"])
                self.assertEqual(result["values"]["selected"], selected)
                self.assertEqual(result["values"]["selectable"], selectable)

    def test_unavailable_state_set_does_not_become_unselectable(self):
        self.obj.errors["get_state_set"] = None
        result = self.editor(["selectable", "Component.extents"])
        self.assertNotIn("selectable", result["values"])
        self.assertEqual(result["fieldStatus"]["selectable"]["state"], "error")
        self.assertEqual(result["values"]["Component.extents"], dict(x=-1920, y=-80, width=400, height=0))


if __name__ == "__main__":
    unittest.main()
