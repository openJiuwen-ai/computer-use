"""Conservation and production-boundary tests for the AT-SPI graph wire.

The provider substitutes native OS objects. Request validation, acquisition,
reference retention and the publication pipeline are the production versions.
"""

import base64
import math
import unittest
from copy import deepcopy

import backends.linux.test_linux_native as base
from computer_use.protocol.observation import decode_observation, encode_observation as encode_graph
from computer_use.protocol.observation.adapters import decode_driver_observation
from computer_use_linux.contracts.native_observation import value
from computer_use_linux.read.native_observation import encode_observation


def untyped(encoded):
    """Inspect wire payload content in tests without decoding graph identity."""
    if "issue" in encoded:
        return encoded
    payload = encoded["data"]
    if isinstance(payload, dict):
        if "bytes" in payload:
            return base64.b64decode(payload["bytes"], validate=True)
        if "integer" in payload:
            return int(payload["integer"])
        if "float" in payload:
            return -0.0 if payload["float"] == "-0" else float(payload["float"])
        if "fields" in payload:
            return {key: untyped(item) for key, item in payload["fields"]}
        if "entries" in payload:
            return [(untyped(key), untyped(item)) for key, item in payload["entries"]]
        if "items" in payload:
            return [untyped(item) for item in payload["items"]]
    return payload


def qualifiers(read):
    return {key: untyped(item) for key, item in read.get("details", [])}


class LinuxNativeObservationTests(unittest.TestCase):
    setUp = base.LinuxNativeFactsTests.setUp
    tearDown = base.LinuxNativeFactsTests.tearDown
    begin = base.LinuxNativeFactsTests.begin
    inspect = base.LinuxNativeFactsTests.inspect
    surfaces = base.LinuxNativeFactsTests.surfaces
    target = base.LinuxNativeFactsTests.target

    def test_production_graph_has_one_registry_and_declared_budget_stubs(self):
        target = self.target()
        result = self.inspect(target, dict(kind="nodes", fields=["role", "name"], maxNodes=1, maxDepth=0))
        self.assertEqual(result["kind"], "observation")
        self.assertFalse({"relations", "unread", "requestedRelations", "observed"} & result.keys())
        self.assertEqual(result["roots"], [target["node"]])
        index = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(len(index), len(result["nodes"]))
        self.assertEqual(index[target["node"]]["read"]["details"][0][0], "requested_fields")
        self.assertTrue(any(node["properties"] is None for node in result["nodes"]))
        self.assertFalse(result["read"]["complete"])
        self.assertTrue(result["read"]["issues"])
        root = index[target["node"]]
        children = next(prop for prop in root["properties"] if prop["key"][1] == "children")
        self.assertEqual(len(children["value"]["data"]["items"]), 3)
        self.assertTrue(all(item["data"]["ref"] in index for item in children["value"]["data"]["items"]))
        self.assertEqual(qualifiers(result["read"])["surface"], target["surface"])
        graph = decode_driver_observation(result)
        self.assertEqual(graph.roots[0], target["node"])
        self.assertIn(target["node"], graph.nodes)
        canonical = encode_graph(graph)
        self.assertEqual(encode_graph(decode_observation(canonical)), canonical)

    def test_relation_query_uses_same_graph_and_preserves_shared_order(self):
        self.backend.window.children = [self.backend.editor, self.backend.editor, self.backend.window]
        target = self.target()
        result = self.inspect(target, dict(kind="relation", property="children", cursor=None, limit=2))
        self.assertEqual(result["kind"], "observation")
        index = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(len(index), 2)
        (relation,) = index[target["node"]]["properties"]
        refs = [item["data"]["ref"] for item in relation["value"]["data"]["items"]]
        self.assertEqual(refs[0], refs[1])
        info = qualifiers(relation["read"])
        self.assertEqual((info["state"], info["total"], info["certainty"]), ("partial", 3, "confirmed"))
        self.assertIn("cursor", info)
        self.assertEqual(untyped(relation["read"]["issues"][0]["continuation"])["cursor"], info["cursor"])
        self.assertIsNone(index[refs[0]]["properties"])

    def test_wire_conserves_status_payloads_text_continuation_and_raw_null(self):
        original = dict(
            kind="nodes",
            session="session",
            platform="linux",
            surface="surface",
            observed=dict(id="capture", startedAt=5, completedAt=6),
            roots=["root"],
            nodes=[
                dict(
                    node="root",
                    requestedFields=["null", "zero", "false", "empty", "missing", "Text.text"],
                    values={"null": None, "zero": 0, "false": False, "empty": "", "Text.text": "a😀"},
                    fieldStatus={
                        "missing": dict(state="unsupported"),
                        "Text.text": dict(
                            state="truncated", textRef="immutable", totalUnits=9000, detail="Native preview"
                        ),
                    },
                    actions=dict(
                        state="partial",
                        items=[
                            dict(
                                name="click",
                                description="说明",
                                descriptionStatus=dict(state="truncated", detail="Action description budget"),
                            )
                        ],
                        total=2,
                    ),
                )
            ],
            requestedRelations=[],
            relations=[],
            unread=[],
            stopReasons=[],
        )
        before = deepcopy(original)
        result = encode_observation(original)
        self.assertEqual(original, before, "Publishing must not mutate retained selection evidence")
        props = {prop["key"][1]: prop for prop in result["nodes"][0]["properties"]}
        self.assertIsNone(props["null"]["value"]["data"])
        self.assertEqual(props["zero"]["value"]["data"], 0)
        self.assertIs(props["false"]["value"]["data"], False)
        self.assertEqual(props["empty"]["value"]["data"], "")
        self.assertIsNone(props["missing"]["value"])
        self.assertEqual(props["missing"]["state"], "unsupported")
        (issue,) = props["Text.text"]["read"]["issues"]
        self.assertEqual(untyped(issue["nativeStatus"]), original["nodes"][0]["fieldStatus"]["Text.text"])
        self.assertEqual(untyped(issue["continuation"]), dict(textRef="immutable", totalUnits=9000))
        self.assertEqual(untyped(props["actions"]["value"]), original["nodes"][0]["actions"]["items"])
        self.assertFalse(props["actions"]["read"]["complete"])

    def test_geometry_and_array_positions_survive_encoding(self):
        original = dict(
            kind="nodes",
            session="s",
            platform="linux",
            surface="surface",
            observed=dict(id="o", startedAt=0, completedAt=1),
            roots=["root"],
            nodes=[
                dict(
                    node="root",
                    requestedFields=["Component.extents", "Text.selections", "attributes"],
                    values={
                        "Component.extents": dict(x=-1920, y=-80, width=-1, height=0),
                        "Text.selections": [dict(startOffset=2, endOffset=2), dict(startOffset=5, endOffset=8)],
                        "attributes": {"empty": "", "unicode": "a😀"},
                    },
                    fieldStatus={},
                    actions=dict(state="complete", items=[], total=0),
                )
            ],
            requestedRelations=[],
            relations=[],
            unread=[],
            stopReasons=[],
        )
        result = encode_observation(original)
        props = {prop["key"][1]: prop for prop in result["nodes"][0]["properties"]}
        rectangle = props["Component.extents"]["value"]
        self.assertEqual(untyped(rectangle), original["nodes"][0]["values"]["Component.extents"])
        self.assertEqual(rectangle["nativeType"], "AtspiRect")
        self.assertEqual(dict((key, untyped(item)) for key, item in rectangle["metadata"])["coordinate_type"], "SCREEN")
        self.assertEqual(untyped(props["Text.selections"]["value"]), original["nodes"][0]["values"]["Text.selections"])
        self.assertEqual(untyped(props["attributes"]["value"]), [("empty", ""), ("unicode", "a😀")])

    def test_scalar_wire_primitives_preserve_values_json_cannot_represent(self):
        self.assertEqual(value(2**100)["data"], {"integer": str(2**100)})
        self.assertEqual(value(b"\x00\xff")["data"], {"bytes": "AP8="})
        self.assertEqual(value(-0.0)["data"], {"float": "-0"})
        for number, expected in ((math.nan, "nan"), (math.inf, "inf"), (-math.inf, "-inf")):
            self.assertEqual(value(number)["data"], {"float": expected})
        self.assertEqual(value("\ud800")["data"], "\ud800")

    def test_already_collected_gi_states_preserve_unknown_enum_values(self):
        from types import SimpleNamespace

        self.backend.ax.live = lambda obj: SimpleNamespace(
            contains=obj.states.__contains__, get_states=lambda: [1, 31, 127]
        )
        target = self.target()
        result = self.inspect(target, dict(kind="nodes", fields=["name"], maxNodes=1, maxDepth=0))
        root = next(node for node in result["nodes"] if node["id"] == result["roots"][0])
        states = next(prop for prop in root["properties"] if prop["key"][1] == "Accessible.stateSet")["value"]
        self.assertEqual(states["nativeType"], "AtspiStateSet")
        self.assertEqual(untyped(states), [1, 31, 127])
        self.assertEqual(
            dict((key, untyped(item)) for key, item in states["metadata"])["representation"], "enum_sequence"
        )
        self.assertFalse(
            any(name == "get_state_set" for name, _, _ in self.backend.ax.calls),
            "Publishing an acquired StateSet must not query the provider again",
        )


if __name__ == "__main__":
    unittest.main()
