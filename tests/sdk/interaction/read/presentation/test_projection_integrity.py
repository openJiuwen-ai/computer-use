"""Each full projection pass has an input/output contract independent of I/O."""

import copy
import unittest
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node
from sdk.interaction.read.presentation.fixtures import n, state

from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.compression.projection import projectTree


class PipelineIntegrityTests(unittest.TestCase):
    def test_passes_cannot_rebind_refs_transfer_capabilities_or_mutate_sources(self):
        projection = state(n(0, "group", n(1, "button", capabilities=dict(click="unknown"))))
        child = projection.nodes["1"]
        for item, key, value in (
            (child, "ref", "999"),
            (child["capabilities"], "click", "available"),
            (child["native"], "role", "window"),
        ):
            with self.assertRaises(TypeError):
                item[key] = value
        for field in ("role", "capabilities", "actions", "control"):
            with self.assertRaises(ValueError):
                projection.edit.set_field(child, field, "invented")
        with self.assertRaises(ValueError):
            projection.edit.adopt(child, [dict(sourceRef="0", attribute="name", text="invented")], "name")
        projection.edit.children(projection.view, [])
        with self.assertRaisesRegex(ValueError, "without recording"):
            projection.assert_integrity()
        projection.edit.children(projection.view, [child])
        projection.edit.children(child, [projection.view])
        with self.assertRaisesRegex(ValueError, "repeated"):
            projection.assert_integrity()

    def test_display_fields_are_private_and_pass_audit_does_not_change_output(self):
        source = n(
            0,
            "button",
            n(1, "staticText", value="Save"),
            capabilities=dict(click="available"),
            actions=[dict(name="AXPress")],
            control=dict(kind="button", members=[dict(ref="1", part="label")]),
        )
        before, projection = copy.deepcopy(source), state(source)
        with self.assertRaises(TypeError):
            projection.view["actions"][0]["name"] = "altered"
        with self.assertRaises(AttributeError):
            projection.view["control"]["members"].clear()
        self.assertEqual(source, before)
        plain = projectTree(source)
        traced = projectTree(source, dict(recordStages=True))
        records = traced.pop("stages")
        self.assertEqual(traced, plain)
        labels = next(record for record in records if record["name"] == "merge_control_labels")
        self.assertEqual(set(labels["affectedRefs"]), {"0", "1"})

    def test_stage_contract_failure_falls_back_to_raw_facts_and_keeps_refs(self):
        snapshot = normalizeFacts(
            batch(
                [node("window", AXRole="AXWindow"), node("button", AXRole="AXButton", AXTitle="Save")],
                [edge("window", "button")],
            ),
            DisplayReferences("session"),
        )
        with patch(
            "computer_use.interaction.read.presentation.ir.validate_projection", side_effect=ValueError("test contract")
        ):
            result = snapshot.render()
        self.assertEqual(result["refs"], {"0": "window", "1": "button"})
        self.assertEqual(result["projections"][0]["presentation"], "uncompressed")
        self.assertIn("Save", result["text"])
