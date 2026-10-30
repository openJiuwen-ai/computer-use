# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observation reuse preserves native evidence, immutable sources and byte budgets."""

from unittest import TestCase
from unittest.mock import patch

from computer_use.interaction.read.facts.normalize import normalize_facts, renumber_snapshot
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import CapturedSnapshot, prepare_snapshot
from computer_use.json import dumps, utf8
from computer_use.protocol.execution import decode_execution_result
from computer_use.protocol.observation import NativeNode, NativeObservation
from computer_use.protocol.observation.adapters import decode_driver_reply, decode_query_facts


def observation():
    return NativeObservation(
        backend="macos",
        session="session",
        id="capture",
        interval_ns=(1000000, 2000000),
        roots=("root",),
        nodes={"root": NativeNode(role="window", name="中文🙂\n\ud800")},
    )


def wire(url=None):
    properties = [dict(key=["macos.ax", "AXRole"], value=dict(nativeType="CFString", data="AXWindow"))]
    if url is not None:
        properties.append(dict(key=["macos.ax", "AXURL"], value=url))
    return dict(
        kind="observation",
        session="session",
        platform="macos",
        roots=["root"],
        nodes=[dict(id="root", source="macos.ax", kind="element", nativeType="AXUIElement", properties=properties)],
        read=dict(
            complete=True,
            intervalNs=[1000000, 2000000],
            details=[
                ["surface", dict(nativeType="CFString", data="surface")],
                ["observation_id", dict(nativeType="CFString", data="capture")],
            ],
        ),
    )


class ObservationReuseTests(TestCase):
    def test_query_and_semantic_consumers_share_one_validated_response(self):
        raw = wire()
        parsed = decode_execution_result("inspect", raw, retain_observation=True)
        expected = decode_driver_reply(raw)
        self.assertEqual(dict(parsed), raw)
        self.assertEqual(decode_query_facts(parsed), decode_query_facts(raw))
        with patch(
            "computer_use.protocol.observation.adapters.normalize.query_facts",
            side_effect=AssertionError("decoded again"),
        ):
            reply = decode_driver_reply(parsed)
            self.assertEqual(reply, expected)
            self.assertIs(decode_driver_reply(parsed), reply)

    def test_relative_native_urls_keep_their_distinct_semantic_resolution(self):
        raw = wire(
            dict(
                nativeType="CFURL",
                data="../target?q=1",
                metadata=[
                    ["base", dict(nativeType="CFURL", data="app://localhost/profile/current")],
                ],
            )
        )
        parsed = decode_execution_result("inspect", raw, retain_observation=True)
        self.assertEqual(decode_query_facts(parsed)["nodes"][0]["values"]["AXURL"], "../target?q=1")
        reply = decode_driver_reply(parsed)
        self.assertEqual(reply, decode_driver_reply(raw))
        self.assertEqual(reply.observation.nodes["root"].url, "app://localhost/target?q=1")

    def test_alias_rewrite_reuses_the_original_source_index(self):
        original = normalize_facts(observation(), DisplayReferences("session"))
        aliases = DisplayReferences("session")
        aliases.reference("earlier")
        with patch(
            "computer_use.interaction.read.facts.normalize.observation_index", side_effect=AssertionError("reindexed")
        ):
            numbered = renumber_snapshot(original, aliases)
        self.assertIs(numbered.facts, original.facts)
        self.assertIs(numbered.native, original.native)
        self.assertEqual(numbered.refs, {"1": "root"})
        self.assertEqual(original.refs, {"0": "root"})

    def test_shared_facts_remain_immutable_and_display_edits_are_local(self):
        snapshot = normalize_facts(observation(), DisplayReferences("session"))
        shared = {"items": ["original"]}
        snapshot.roots[0]["fixture"] = shared
        snapshot.fields[("0", "fixture")] = {"normalized": shared}
        captured = CapturedSnapshot.capture(snapshot)
        first, second = captured.materialize(read_only_facts=True), captured.materialize(read_only_facts=True)
        self.assertIs(first.facts, captured.facts)
        self.assertIs(second.facts, captured.facts)
        with self.assertRaises(TypeError):
            first.facts["nodes"][0]["values"]["name"] = "changed"
        first.roots[0]["fixture"]["items"].append("edit")
        self.assertEqual(first.fields[("0", "fixture")]["normalized"], {"items": ["original"]})
        self.assertEqual(second.roots[0]["fixture"], {"items": ["original"]})
        mutable = captured.materialize()
        mutable.facts["nodes"][0]["values"]["name"] = "local edit"
        self.assertEqual(second.facts["nodes"][0]["values"]["name"], observation().nodes["root"].name)

    def test_byte_budget_counts_repeated_records_and_unicode_exactly(self):
        snapshot = normalize_facts(observation(), DisplayReferences("session"))
        prepared = prepare_snapshot(snapshot)
        tree = DisplayDocument.tree(prepared)
        for document in (
            DisplayDocument(()),
            DisplayDocument.join(("中文🙂\n\ud800", "\ud83d\ude42")),
            tree,
            DisplayDocument.join((tree, tree, DisplayText("details_hint", (prepared, "0")))),
        ):
            with self.subTest(parts=len(document.parts)):
                self.assertEqual(document.estimated_bytes, len(utf8(dumps(document.to_data()))))
