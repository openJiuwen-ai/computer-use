"""Grouped metadata must expand to the exact acquired property/ref facts."""

import copy
import json
import random
import re
import unittest

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.facts.groups import render_properties
from computer_use.interaction.read.numbering.references import DisplayReferences


def expand(lines):
    result = set()
    for line in lines:
        refs, body = re.fullmatch(r"([0-9, –]+) (.+)", line).groups()
        if body.startswith("Properties "):
            properties, end_value = json.JSONDecoder().raw_decode(body[len("Properties ") :])
            value = body[len("Properties ") + end_value + 2 :]
        elif body.startswith('"'):
            property_name, end_value = json.JSONDecoder().raw_decode(body)
            properties, value = [property_name], body[end_value + 2 :]
        else:
            property_name, value = body.split(": ", 1)
            properties = [property_name]
        for interval in refs.split(", "):
            limits = interval.split("–")
            for ref in range(int(limits[0]), int(limits[-1]) + 1):
                for property_name in properties:
                    item = (str(ref), property_name, value)
                    assert item not in result, item
                    result.add(item)
    return result


class FactGroupTests(unittest.TestCase):
    def test_complete_round_trip_preserves_reference_sets_types_states_and_details(self):
        rng = random.Random(52026)
        payloads = [
            "false",
            "0",
            '""',
            '"absent"',
            "absent",
            "unsupported",
            'timeout ("provider timed out")',
            'error ("other failure")',
            '{"a":false}',
            '{"a":0}',
            "truncated; captured text available; 32 UTF-16 units",
        ]
        groups = {}
        for index in range(120):
            value = payloads[index % len(payloads)]
            refs = rng.sample([str(i) for i in range(30)], index % 7 + 1)
            # Equal sets in a different order may share a record. Overlapping
            # sets may not: their Cartesian product would invent observations.
            groups[("Vendor." + str(index), value)] = refs
            groups[("Peer." + str(index), value)] = list(reversed(refs))
            groups[("Subset." + str(index), value)] = refs[:1]
        before = copy.deepcopy(groups)
        lines = render_properties(groups)
        self.assertEqual(
            expand(lines), {(ref, property, value) for (property, value), refs in groups.items() for ref in refs}
        )
        self.assertEqual(groups, before)
        self.assertLess(len(lines), len(groups))

    def test_property_names_cannot_be_confused_with_group_syntax_or_values(self):
        groups = {(name, "false"): ["1", "2"] for name in ("AXHelp", "Properties []", "a: b", 'quoted"\nname')}
        groups[('single: "odd"', "unsupported")] = ["3"]
        self.assertEqual(
            expand(render_properties(groups)),
            {(ref, property, value) for (property, value), refs in groups.items() for ref in refs},
        )

    def test_native_graph_bindings_and_distinct_statuses_survive_projection(self):
        first = node("first", AXRole="AXButton", VendorA=False, VendorB=False, VendorC=0)
        second = node("second", AXRole="AXButton", VendorA=False)
        for source in (first, second):
            source["requestedFields"] += ["AXHelp", "AXDescription"]
            source["fieldStatus"] = dict(
                AXHelp=dict(state="absent", detail="AXError -25212"),
                AXDescription=dict(state="absent", detail="AXError -25212"),
            )
        second["fieldStatus"]["AXHelp"] = dict(state="timeout", detail="provider busy")
        raw = batch([first, second], [edge("first", "second")])
        before = copy.deepcopy(raw)
        snapshot = normalizeFacts(raw, DisplayReferences("session"))
        rendered = snapshot.render()
        lines = [line for line in snapshot.annotations if re.match(r"^[0-9, –]+ ", line)]
        restored = expand(lines)
        status_expected = {
            ("0", "help", "absent: AXError -25212"),
            ("0", "description", "absent: AXError -25212"),
            ("1", "description", "absent: AXError -25212"),
            ("1", "help", "timeout: provider busy"),
        }
        self.assertTrue(status_expected <= restored)
        extras = {
            (ref, json.loads(value)["key"], json.dumps(json.loads(value)["value"]))
            for ref, key, value in restored
            if key.startswith("supplements[")
        }
        self.assertEqual(
            extras,
            {
                ("0", "provider.macos.VendorA", "false"),
                ("0", "provider.macos.VendorB", "false"),
                ("0", "provider.macos.VendorC", "0"),
                ("1", "provider.macos.VendorA", "false"),
            },
        )
        from observation_fixture import reply

        self.assertEqual(snapshot.native, reply(before).observation)
        self.assertEqual(raw, before)
        self.assertEqual(rendered["refs"], {"0": "first", "1": "second"})
        self.assertIn("timeout: provider busy", rendered["text"])
        self.assertEqual(len(rendered["projections"][0]["factAccounting"]["values"]), 6)


if __name__ == "__main__":
    unittest.main()
