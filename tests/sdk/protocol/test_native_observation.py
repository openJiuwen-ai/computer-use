"""Driver carrier validation precedes platform normalization of observation facts."""

import unittest
from copy import deepcopy

from computer_use.protocol.execution import decodeExecutionResult
from computer_use.protocol.observation import ElementValue, NativeNode
from computer_use.protocol.observation.adapters import decode_driver_observation, decode_query_facts


def value(data, native_type="CFString"):
    return {"nativeType": native_type, "data": data}


def prop(name, data=None, *, state="value", read=None):
    result = {"key": ["macos.ax", name], "value": data, "state": state}
    if read is not None:
        result["read"] = read
    return result


def carrier():
    return {
        "kind": "observation",
        "session": "generation",
        "platform": "macos",
        "roots": ["42"],
        "nodes": [
            {
                "id": "42",
                "source": "macos.ax",
                "kind": "element",
                "nativeType": "AXUIElement",
                "properties": [
                    prop("AXRole", value("AXTextField")),
                    prop("AXTitle", value("Search")),
                    prop("AXValue", value("")),
                ],
            },
            {"id": "43", "source": "macos.ax", "kind": "element", "nativeType": "AXUIElement", "properties": None},
        ],
        "read": {
            "intervalNs": [1, 2],
            "complete": True,
            "details": [
                ["surface", value("surface")],
                ["observation_id", value("capture")],
            ],
        },
    }


class NativeObservationTests(unittest.TestCase):
    def test_driver_carrier_becomes_unified_fields(self):
        graph = decode_driver_observation(carrier())
        self.assertEqual(graph.backend, "macos")
        self.assertEqual(graph.roots, ("42",))
        self.assertEqual(graph.nodes["42"].role, "text_field")
        self.assertEqual(graph.nodes["42"].name, "Search")
        self.assertEqual(graph.nodes["42"].control_value.value, "")
        self.assertEqual(graph.nodes["43"], NativeNode())
        self.assertFalse(hasattr(graph.nodes["42"], "native_type"))
        self.assertFalse(hasattr(graph.nodes["42"], "properties"))

    def test_raw_query_evidence_remains_separate_from_presentation(self):
        facts = decode_query_facts(carrier())
        node = next(node for node in facts["nodes"] if node["node"] == "42")
        self.assertEqual(node["values"]["AXRole"], "AXTextField")
        self.assertEqual(node["values"]["AXTitle"], "Search")
        self.assertEqual(node["values"]["AXValue"], "")

    def test_element_value_does_not_become_a_child_or_new_identity(self):
        raw = carrier()
        raw["nodes"][0]["properties"][-1] = prop("AXValue", value({"ref": "43"}, "AXUIElement"))
        graph = decode_driver_observation(raw)
        self.assertEqual(graph.nodes["42"].control_value.value, ElementValue("43"))
        self.assertFalse(any(relation.kind == "children" for relation in graph.nodes["42"].relations.values()))

    def test_shared_and_cyclic_relationship_targets_keep_their_identifiers(self):
        raw = carrier()
        refs = [value({"ref": identity}, "AXUIElement") for identity in ("43", "42", "43")]
        raw["nodes"][0]["properties"].append(
            prop(
                "AXOwns",
                value({"items": refs}, "CFArray"),
                read={
                    "complete": True,
                    "details": [
                        ["relation", value(True, "CFBoolean")],
                        ["offset", value(0, "CFNumber")],
                        ["total", value(3, "CFNumber")],
                        ["certainty", value("confirmed")],
                        ["state", value("complete")],
                    ],
                },
            )
        )
        graph = decode_driver_observation(raw)
        relation = next(relation for relation in graph.nodes["42"].relations.values() if relation.kind == "owns")
        self.assertEqual(relation.targets, ("43", "42", "43"))
        self.assertEqual(set(graph.nodes), {"42", "43"})

    def test_payload_free_read_states_survive_as_sparse_issues(self):
        for state in ("absent", "unsupported", "not_read", "error", "timeout", "unavailable", "redacted"):
            with self.subTest(state=state):
                raw = carrier()
                raw["nodes"][0]["properties"][1] = prop("AXTitle", state=state)
                graph = decode_driver_observation(raw)
                self.assertIsNone(graph.nodes["42"].name)
                self.assertTrue(
                    any(issue.state == state and issue.path == ("nodes", "42", "name") for issue in graph.issues)
                )

    def test_old_table_formats_are_rejected_at_the_driver_boundary(self):
        for raw in (
            {"kind": "nodes"},
            {"kind": "relation"},
            {"kind": "nativeObservation"},
            {"kind": "native_observation"},
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_driver_observation(raw)
            with self.assertRaises(ValueError):
                decodeExecutionResult("inspect", raw)

    def test_malformed_driver_registry_references_and_payloads_are_rejected(self):
        for mutate in (
            lambda x: x["nodes"].append(deepcopy(x["nodes"][0])),
            lambda x: x["roots"].append("missing"),
            lambda x: x["nodes"][0]["properties"][-1].update(value=value({"ref": "missing"}, "AXUIElement")),
            lambda x: x["nodes"][0]["properties"][0].update(state="unsupported"),
            lambda x: x["nodes"][0].update(extra="unknown"),
            lambda x: x["read"].update(intervalNs=[5, 2]),
            lambda x: x["nodes"][0]["properties"][0].update(key=["macos.ax", True]),
        ):
            raw = carrier()
            mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_driver_observation(raw)

    def test_invalid_raw_value_tags_and_bytes_are_rejected(self):
        for encoded in (
            {"nativeType": "CFData", "data": {"bytes": "!!!"}},
            {"nativeType": "CFNumber", "data": {"integer": "--1"}},
            {"nativeType": "CFNumber", "data": {"float": "unknown"}},
            {"nativeType": "CFString", "data": "x", "raw": "not base64!"},
            {"nativeType": "CFString", "data": {"unknown": "x"}},
            {"nativeType": "CFNumber", "data": float("nan")},
        ):
            raw = carrier()
            raw["nodes"][0]["properties"][-1] = prop("AXValue", encoded)
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                decode_driver_observation(raw)

    def test_reference_validation_in_arguments_metadata_and_diagnostics(self):
        missing = value({"ref": "missing"}, "AXUIElement")
        for mutate in (
            lambda x: x["nodes"][0].update(identity=missing),
            lambda x: x["nodes"][0]["properties"][0].update(arguments=[missing]),
            lambda x: x["nodes"][0]["properties"][0]["value"].update(metadata=[["reference", missing]]),
            lambda x: x["nodes"][0]["properties"][0].update(
                read={"issues": [{"reason": "failed", "nativeStatus": missing}]}
            ),
            lambda x: x["read"].update(details=x["read"]["details"] + [["reference", missing]]),
        ):
            raw = carrier()
            mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_driver_observation(raw)

    def test_empty_driver_graph_is_a_valid_empty_observation(self):
        raw = carrier()
        raw["nodes"], raw["roots"] = [], []
        graph = decode_driver_observation(raw)
        self.assertEqual(graph.roots, ())
        self.assertEqual(dict(graph.nodes), {})


if __name__ == "__main__":
    unittest.main()
