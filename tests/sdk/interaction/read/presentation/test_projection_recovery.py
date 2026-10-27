"""A broken display transform must not erase acquired facts or input targets."""

import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.reader import capturedField
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.rendering.passes import render_semantic_lines as render_pass
from computer_use.interaction.read.presentation.semantics.passes import compose_display_semantics as semantics_pass


class ProjectionRecoveryTests(unittest.TestCase):
    @staticmethod
    def snapshot():
        field = node(
            "field",
            AXRole="AXTextField",
            AXTitle="输入\n777 native element",
            AXValue="preview🙂",
            AXEnabled=False,
            AXSelected=False,
            VendorNumber=0,
            VendorEmpty="",
            VendorObject={"key": [False, 0, "", None]},
            **{"settable.AXValue": True},
        )
        field["requestedFields"].extend(["AXHelp", "AXDescription"])
        field["fieldStatus"].update(
            AXValue=dict(state="truncated", textRef="text:original", totalUnits=100),
            AXHelp=dict(state="timeout", detail="provider slow"),
            AXDescription=dict(state="absent"),
        )
        field["actions"] = dict(
            state="partial",
            total=2,
            items=[dict(name="AXShowMenu", descriptionStatus=dict(state="error", detail="description unavailable"))],
        )
        other = node("menu", AXRole="AXMenuBar", AXTitle="Independent menu")
        wire = batch(
            [node("window", AXRole="AXWindow", AXTitle="Window"), field, other],
            [
                edge("window", "field"),
                edge(
                    "field",
                    "window",
                    "unread",
                    property="AXLinkedUIElements",
                    state="partial",
                    total=4,
                    cursor="original:cursor",
                ),
            ],
            roots=["window", "menu"],
        )
        wire["unread"] = ["unread"]
        return normalizeFacts(wire, DisplayReferences("session"))

    def test_failed_root_retains_every_semantic_record_and_other_roots_stay_compressed(self):
        snapshot = self.snapshot()
        before = copy.deepcopy(snapshot.facts)
        compose = semantics_pass.composeControls

        def broken(root, **kwargs):
            if root["role"] == "window":
                raise ValueError("bad projection\nnot a new node")
            return compose(root, **kwargs)

        with patch.object(semantics_pass, "composeControls", side_effect=broken):
            result = snapshot.render()
        recovered, normal = result["projections"]
        self.assertEqual(recovered["presentation"], "uncompressed")
        self.assertEqual(recovered["issue"]["error"], "ValueError")
        self.assertNotIn("presentation", normal)
        self.assertIn("Independent menu", normal["text"])
        self.assertEqual(result["refs"], snapshot.refs)
        self.assertEqual(snapshot.facts, before)
        self.assertEqual(capturedField(snapshot, "1", "control_value.value").text_ref, "text:original")
        field = snapshot.native.nodes["field"]
        self.assertEqual(field.role, "text_field")
        self.assertEqual(field.control_value.value, "preview🙂")
        self.assertIs(field.states.enabled, False)
        self.assertIs(field.states.selected, False)
        extras = {item.key: item.value for item in field.supplements}
        self.assertEqual(extras["provider.macos.VendorNumber"], 0)
        self.assertEqual(extras["provider.macos.VendorEmpty"], "")
        self.assertEqual(extras["provider.macos.VendorObject"]["key"][:3], (False, 0, ""))
        self.assertTrue(
            any(issue.state == "absent" and issue.path[-2:] == ("key", 3) for issue in snapshot.native.issues)
        )
        self.assertEqual(field.actions[0].key, "AXShowMenu")
        self.assertEqual(field.relations["linked"].targets, ("window", "unread"))
        # Independently decode the fallback, checking exact scalar values,
        # statuses, actions and ordered relation references, including missing
        # and cyclic targets. No assertion depends on its JSON spacing.
        records, current = {}, None
        for line in recovered["text"].splitlines()[1:]:
            if not line.startswith("\t"):
                current = line.split(" ", 1)[0]
                records[current] = dict(values={}, statuses={}, relations={})
                continue
            key, value = line[1:].split(": ", 1)
            if key == "Native actions":
                records[current]["actions"] = json.loads(value)
            elif key.startswith("Relation "):
                records[current]["relations"][json.loads(key[9:])] = json.loads(value)
            elif key.endswith(" status"):
                records[current]["statuses"][json.loads(key[:-7])] = json.loads(value)
            else:
                records[current]["values"][json.loads(key)] = json.loads(value)
        reverse = {token: reference for reference, token in snapshot.refs.items()}
        for source in before["nodes"][:2]:
            record = records[reverse[source["node"]]]
            self.assertEqual(record["values"], source["values"])
            self.assertEqual(record["statuses"], source["fieldStatus"])
            self.assertEqual(record["actions"], source["actions"])
            for relation in before["relations"]:
                if relation["source"] == source["node"]:
                    expected = {k: v for k, v in relation.items() if k not in ("source", "property", "targets")}
                    expected["targets"] = [reverse[token] for token in relation["targets"]]
                    self.assertEqual(record["relations"][relation["property"]], expected)

    def test_formatter_failure_uses_unified_graph_and_keeps_current_capture_metadata(self):
        snapshot = self.snapshot()
        before = copy.deepcopy(snapshot.facts)
        with patch.object(render_pass, "renderProjectionLines", side_effect=TypeError("format error")):
            result = snapshot.render()
        self.assertEqual(len(result["projections"]), 2)
        self.assertTrue(all(p["presentation"] == "uncompressed" for p in result["projections"]))
        self.assertEqual(result["textRefs"], snapshot.text_refs)
        self.assertEqual(
            {(s["source"], s["property"]) for s in result["relationSources"]},
            {(s["source"], s["property"]) for s in snapshot.facts["relations"]},
        )
        self.assertTrue(all(s["representation"] == "uncompressed" for s in result["relationSources"]))
        self.assertEqual(snapshot.facts, before)
        self.assertNotIn("\n777 native element", result["text"])

    def test_cancellation_exhaustion_and_operation_errors_are_not_presentation_failures(self):
        failure = ComputerUseError(
            dict(code="stale_target", effect="none", target="test", detail="wrong identity", next="read again")
        )
        for error in (asyncio.CancelledError(), MemoryError(), TimeoutError(), failure):
            with self.subTest(error=type(error).__name__):
                with patch.object(render_pass, "renderProjectionLines", side_effect=error):
                    with self.assertRaises(type(error)):
                        self.snapshot().render()

    def test_protocol_and_source_identity_failures_cannot_publish_a_fallback(self):
        snapshot = self.snapshot()
        malformed = copy.deepcopy(snapshot.facts)
        malformed["relations"][0]["targets"] = ["unregistered"]
        with patch.object(render_pass, "renderProjectionLines") as render:
            with self.assertRaises(ValueError):
                normalizeFacts(malformed, DisplayReferences("session"))
            del snapshot.refs["1"]
            with self.assertRaisesRegex(ValueError, "identity before presentation"):
                snapshot.render()
            render.assert_not_called()

    def test_same_fallback_preserves_windows_and_linux_semantic_properties(self):
        for platform, values in (
            (
                "windows",
                {"ControlType": "ControlType.Edit", "Name": "字段", "Value.Value": "text", "Value.IsReadOnly": False},
            ),
            ("linux", {"role": "entry", "name": "字段", "value": "text", "editable": True}),
        ):
            with self.subTest(platform=platform):
                wire = batch([node("field", **values)])
                wire["platform"] = platform
                snapshot = normalizeFacts(wire, DisplayReferences("session"))
                with patch.object(semantics_pass, "composeControls", side_effect=ValueError("fixture")):
                    output = snapshot.render()
                lines = output["text"].splitlines()
                restored = {
                    json.loads(k): json.loads(v)
                    for line in lines
                    if line.startswith('\t"')
                    for k, v in (line[1:].split(": ", 1),)
                }
                self.assertEqual(snapshot.native.nodes["field"].role, "text_field")
                self.assertEqual(snapshot.native.nodes["field"].name, "字段")
                if platform == "windows":
                    self.assertEqual(
                        restored,
                        {
                            "role": "text_field",
                            "name": "字段",
                            "control_value.value": "text",
                            "control_value.read_only": False,
                        },
                    )
                    self.assertIs(snapshot.native.nodes["field"].control_value.read_only, False)
                else:
                    # This fixture deliberately uses a provider-specific value
                    # outside AT-SPI Text.text: retain it as a typed supplement.
                    self.assertEqual(
                        restored,
                        {
                            "role": "text_field",
                            "name": "字段",
                            "states.editable": True,
                            "supplements[0]": {"key": "provider.linux.value", "value": "text"},
                        },
                    )
                    self.assertIs(snapshot.native.nodes["field"].states.editable, True)
                self.assertEqual(output["refs"], {"0": "field"})


if __name__ == "__main__":
    unittest.main()
