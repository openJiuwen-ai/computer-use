"""Native values must have a visible destination after normal projection."""

import copy
import json
import re
import unittest

from observation_fixture import normalize_fixture as normalizeFacts, reply
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.numbering.references import DisplayReferences


def observe(nodes, relations=(), **kwargs):
    return normalizeFacts(batch(nodes, relations), DisplayReferences("session"), **kwargs)


class FactAccountingTests(unittest.TestCase):
    def test_named_control_keeps_identity_subrole_and_explicit_negative_state(self):
        snapshot = observe(
            [
                node(
                    "save",
                    AXRole="AXButton",
                    AXTitle="Save",
                    AXIdentifier="save.draft",
                    AXSubrole="AXAlternateButton",
                    AXSelected=False,
                    AXFocused=False,
                    AXEnabled=True,
                )
            ]
        )
        result = snapshot.render()
        self.assertIn("save.draft", result["text"])
        self.assertIn("AXAlternateButton", result["text"])
        self.assertIn("states.selected: false", result["text"])
        self.assertIn("states.focused: false", result["text"])

    def test_long_selection_is_not_lost_behind_a_value_only_read_text_entry(self):
        selection = "a" * 250 + "unique middle selection" + "z" * 250
        result = observe(
            [node("field", AXRole="AXTextField", AXValue="different full value", AXSelectedText=selection)]
        ).render()
        self.assertIn("unique middle selection", result["text"])
        self.assertIn("different full value", result["text"])

    def test_primary_action_description_is_not_replaced_by_clickable(self):
        button = node("button", AXRole="AXButton", AXTitle="Action")
        button["actions"]["items"] = [dict(name="AXPress", description="Publish the selected revision")]
        result = observe([button], driver_features=("nativeActionIntents",)).render()
        self.assertIn("Publish the selected revision", result["text"])
        self.assertIn("AXPress", result["text"])

    def test_label_composition_records_the_original_value_not_a_new_target(self):
        button = node("button", AXRole="AXButton")
        button["actions"]["items"] = [dict(name="AXPress", description="Press")]
        snapshot = observe(
            [
                button,
                node("layout", AXRole="AXGroup"),
                node("label", AXRole="AXStaticText", AXValue="Wi-Fi", AXIdentifier="wifi-label"),
            ],
            [edge("button", "layout"), edge("layout", "label")],
            driver_features=("nativeActionIntents",),
        )
        before = copy.deepcopy(snapshot.facts)
        result = snapshot.render()
        self.assertIn("0 button (clickable) Wi-Fi", result["text"])
        self.assertIn('2 identifier: "wifi-label"', result["text"])
        self.assertNotIn('control_value.value: "Wi-Fi"', result["text"], "A proven label must not be printed twice")
        destinations = result["projections"][0]["factAccounting"]["values"]
        value = next(d for d in destinations if (d["ref"], d["property"]) == ("2", "control_value.value"))
        self.assertEqual((value["kind"], value["target"]), ("equivalent", "0"))
        self.assertEqual(result["refs"]["2"], "label", "Composition cannot transfer the native action target")
        self.assertEqual(snapshot.facts, before)

    def test_long_value_uses_real_capture_but_uncaptured_preview_keeps_all_acquired_text(self):
        value = "a" * 1200 + "unique middle value" + "z" * 1200
        field = node("field", AXRole="AXTextField", AXValue=value)
        for available in (True, False):
            with self.subTest(captured=available):
                source = copy.deepcopy(field)
                if not available:
                    source["fieldStatus"]["AXValue"] = dict(
                        state="truncated", detail="provider supplied a preview only"
                    )
                result = observe([source]).render()
                destination = next(
                    d
                    for d in result["projections"][0]["factAccounting"]["values"]
                    if d["property"] == "control_value.value"
                )
                self.assertEqual(destination["kind"], "continuation" if available else "supplement")
                if available:
                    self.assertIn(".readText()", result["text"])
                else:
                    self.assertIn("unique middle value", result["text"])
                    self.assertIn("provider supplied a preview only", result["text"])
                    self.assertNotIn('use element("0").readText()', result["text"])

    def test_captured_non_value_fields_do_not_advertise_the_wrong_public_continuation(self):
        source = node("field", AXRole="AXTextField", AXValue="Value content", AXSelectedText="selection preview")
        source["fieldStatus"]["AXSelectedText"] = dict(state="truncated", textRef="selection:original", totalUnits=6000)
        result = observe([source]).render()
        self.assertIn("selection preview", result["text"])
        self.assertIn("no public continuation for this field; 6000 UTF-16 units", result["text"])
        self.assertEqual(result["textRefs"][("0", "text.selected_text")]["textRef"], "selection:original")

    def test_repeated_metadata_and_actions_are_grouped_without_dropping_empty_or_false(self):
        controls = []
        for i in range(30):
            item = node(
                "b" + str(i),
                AXRole="AXButton",
                AXTitle="Button " + str(i),
                AXEnabled=True,
                AXFocused=False,
                AXDescription="",
            )
            item["actions"]["items"] = [dict(name="AXPress", description="Publish revision")]
            controls.append(item)
        result = observe(
            [node("window", AXRole="AXWindow"), *controls],
            [edge("window", *(n["node"] for n in controls))],
            driver_features=("nativeActionIntents",),
        ).render()
        self.assertIn("1–30 states.focused: false", result["text"])
        self.assertIn('1–30 description: ""', result["text"])
        self.assertEqual(result["text"].count("Publish revision"), 1)
        raw_records = result["projections"][0]["factAccounting"]["values"]
        self.assertEqual(len(raw_records), 1 + 30 * 5)
        self.assertEqual(len({(d["ref"], d["property"]) for d in raw_records}), len(raw_records))

    def test_shared_help_and_same_control_description_have_actual_visible_destinations(self):
        nodes = [node("group", AXRole="AXGroup", AXTitle="Controls")]
        nodes += [
            node(
                str(i),
                AXRole="AXButton",
                AXTitle="Open " + str(i),
                AXDescription="Open " + str(i),
                AXHelp="Shared useful help",
            )
            for i in range(3)
        ]
        result = observe(nodes, [edge("group", "0", "1", "2")]).render()
        self.assertEqual(result["text"].count("Shared useful help"), 1)
        destinations = result["projections"][0]["factAccounting"]["values"]
        self.assertTrue(
            all(d["kind"] == "equivalent" and d["target"] == "0" for d in destinations if d["property"] == "help")
        )
        self.assertNotIn("description:", result["text"])

    def test_grouped_output_recovers_each_original_metadata_value_and_action_record(self):
        controls = []
        for i in range(50):
            source = node(
                "b" + str(i),
                AXRole="AXButton",
                AXTitle="Button " + str(i),
                AXFocused=False,
                AXDescription="",
                VendorData={"value": (False, 0, "", "x")[i % 4]},
            )
            source["actions"] = dict(
                state="complete", total=1, items=[dict(name="AXPress", description="Activate variant " + str(i % 2))]
            )
            controls.append(source)
        snapshot = observe(
            [node("window", AXRole="AXWindow"), *controls],
            [edge("window", *(source["node"] for source in controls))],
            driver_features=("nativeActionIntents",),
        )
        rendered = snapshot.render()
        restored = {}
        for line in rendered["text"].splitlines():
            match = re.fullmatch(r"\t([0-9, –]+) (states.focused|description|Native actions): (.+)", line)
            if match is None:
                continue
            aliases, key, encoded = match.groups()
            if encoded == "none (complete)":
                continue
            value = json.loads(encoded)
            for group in aliases.split(", "):
                ends = group.split("–")
                for ref in range(int(ends[0]), int(ends[-1]) + 1):
                    self.assertNotIn((str(ref), key), restored)
                    restored[(str(ref), key)] = value
        for ref, token in snapshot.refs.items():
            if token == "window":
                continue
            source = next(source for source in controls if source["node"] == token)
            for original, key in (("AXFocused", "states.focused"), ("AXDescription", "description")):
                self.assertEqual(restored[(ref, key)], source["values"][original])
            normalized = next(row for row in snapshot.facts["nodes"] if row["node"] == token)
            self.assertEqual(restored[(ref, "Native actions")], normalized["actions"])
        from sdk.interaction.read.facts.test_fact_groups import expand

        values = expand(line for line in snapshot.annotations if re.match(r"^[0-9, –]+ ", line))
        vendor_values = {
            (ref, json.dumps(json.loads(value)["value"], sort_keys=True))
            for ref, key, value in values
            if key.startswith("supplements[") and json.loads(value)["key"] == "provider.macos.VendorData"
        }
        expected = {
            (
                ref,
                json.dumps(
                    next(source["values"]["VendorData"] for source in controls if source["node"] == token),
                    sort_keys=True,
                ),
            )
            for ref, token in snapshot.refs.items()
            if token != "window"
        }
        self.assertEqual(vendor_values, expected)
        self.assertEqual(rendered["text"].count("provider.macos.VendorData"), 4)
        self.assertEqual(rendered["text"].count("Activate variant"), 2)

    def test_supplement_values_cannot_inject_nodes_and_platform_properties_are_not_guessed(self):
        for platform, role, name, identity in (
            ("macos", "AXRole", "AXTitle", "AXIdentifier"),
            ("windows", "ControlType", "Name", "AutomationId"),
            ("linux", "role", "name", "accessibleId"),
        ):
            with self.subTest(platform=platform):
                value = "x\n999 button\u2028not a node"
                source = node(
                    "target",
                    **{
                        role: dict(macos="AXButton", windows="ControlType.Button", linux="push button")[platform],
                        name: "Visible",
                        identity: value,
                        "VendorState": {"zero": 0, "false": False},
                    },
                )
                raw = batch([source])
                raw["platform"] = platform
                snapshot = normalizeFacts(raw, DisplayReferences("session"))
                result = snapshot.render()
                line = next(line for line in result["text"].splitlines() if "identifier: " in line)
                self.assertEqual(json.loads(line.split("identifier: ", 1)[1]), value)
                self.assertNotIn("\n999 button", result["text"])
                self.assertEqual(result["refs"], {"0": "target"})
                self.assertEqual(snapshot.native, reply(raw).observation)


if __name__ == "__main__":
    unittest.main()
