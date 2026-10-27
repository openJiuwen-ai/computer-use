"""Scalar and element-valued facts retain their meaning after normalization."""

import unittest
from datetime import datetime, timezone

from computer_use.interaction.read.facts.normalize import normalizeFacts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.protocol.observation import (
    ControlValue,
    ElementValue,
    NativeNode,
    NativeObservation,
    ReadIssue,
    Relation,
    Supplement,
)


def observation(nodes, *, issues=()):
    return NativeObservation("macos", "session", "capture", (0, 1), (next(iter(nodes)),), nodes, issues=issues)


class NativeValueTests(unittest.TestCase):
    @staticmethod
    def render(value):
        snapshot = normalizeFacts(value, DisplayReferences("session"))
        return snapshot, snapshot.render(compact=True)

    def test_aware_datetime_remains_the_same_instant_and_has_no_native_type_wrapper(self):
        instant = datetime(2000, 12, 31, 23, 59, 59, 750000, tzinfo=timezone.utc)
        value = observation({"date": NativeNode(role="group", control_value=ControlValue(instant))})
        snapshot, rendered = self.render(value)
        self.assertIs(snapshot.native, value)
        self.assertEqual(snapshot.native.nodes["date"].control_value.value, instant)
        self.assertIn("2000-12-31T23:59:59.750000+00:00", rendered["text"])
        self.assertNotIn("CFDate", rendered["details"])

    def test_unrepresentable_provider_date_has_an_explicit_semantic_supplement(self):
        node = NativeNode(role="group", supplements=(Supplement("date_seconds_since_2001", 1e100),))
        value = observation(
            {"date": node},
            issues=(ReadIssue(("nodes", "date", "control_value", "value"), "error", "Date cannot be represented"),),
        )
        _, rendered = self.render(value)
        self.assertIn("1e+100", rendered["details"])
        self.assertIn("Date cannot be represented", rendered["details"])

    def test_object_value_retains_exact_target_without_inventing_selection(self):
        value = observation(
            {
                "group": NativeNode(
                    role="radio_group",
                    control_value=ControlValue(ElementValue("choice")),
                    relations={"children": Relation("children", ("choice",))},
                ),
                "choice": NativeNode(role="radio_button", name="Month", control_value=ControlValue(1)),
            }
        )
        snapshot, rendered = self.render(value)
        self.assertIn("Value: element 1", rendered["text"])
        self.assertIn("Month", rendered["text"])
        self.assertEqual(rendered["refs"]["1"], "choice")
        self.assertNotIn("selected", snapshot.roots[0]["children"][0])
        self.assertIs(snapshot.native, value)

    def test_unread_value_reference_remains_bound_without_inventing_containment(self):
        value = observation(
            {
                "group": NativeNode(role="radio_group", control_value=ControlValue(ElementValue("outside"))),
                "outside": NativeNode(),
            },
            issues=(ReadIssue(("nodes", "outside"), "not_read"),),
        )
        snapshot, rendered = self.render(value)
        self.assertIn("Value: element 1", rendered["text"])
        self.assertEqual(rendered["refs"]["1"], "outside")
        self.assertEqual(snapshot.roots[0]["children"], [])
        self.assertIn("outside", snapshot.facts["unread"])


if __name__ == "__main__":
    unittest.main()
