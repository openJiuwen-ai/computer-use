# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Issue lookup preserves capture order while avoiding cross-node rescans."""

import random
import unittest
from types import SimpleNamespace

from computer_use.interaction.read.facts.index import _IssueIndex
from computer_use.interaction.read.presentation.interface.collections.planning import _Source
from computer_use.protocol.observation import ReadIssue
from computer_use.protocol.observation.issues import ReadIssueIndex


class CountingIssues:
    def __init__(self, items):
        self.items = items
        self.visits = 0

    def __iter__(self):
        for item in self.items:
            self.visits += 1
            yield item


class IssueIndexTests(unittest.TestCase):
    def test_prefix_restrictions_preserve_original_order_and_scope(self):
        issues = (
            ReadIssue(("nodes", "a", "text", "content"), "partial"),
            ReadIssue((), "error"),
            ReadIssue(("nodes", "a"), "stale"),
            ReadIssue(("nodes", "a", "text"), "timeout"),
            ReadIssue(("nodes",), "partial"),
            ReadIssue(("nodes", "b"), "unavailable"),
            ReadIssue(("nodes", "a", "text", "content"), "error"),
        )
        index = _IssueIndex(issues)
        self.assertEqual(
            index.applicable(("nodes", "a", "text", "content")), [issues[0], issues[2], issues[3], issues[6]]
        )
        self.assertEqual(index.applicable(("nodes", "a", "actions")), [issues[2]])
        self.assertEqual(index.applicable(("nodes", "c", "name")), [])
        self.assertEqual(index.applicable(()), [issues[1]])
        self.assertEqual(index.applicable(("nodes",)), [issues[4]])

    def test_mixed_paths_match_linear_reference_semantics(self):
        randomizer = random.Random(42)
        paths = [(), ("nodes",)] + [
            ("nodes", str(node), *field)
            for node in range(8)
            for field in (
                (),
                ("name",),
                ("text",),
                ("text", "selections"),
                ("text", "selections", 0),
                ("relations", "a.b"),
            )
        ]
        issues = tuple(
            ReadIssue(randomizer.choice(paths), randomizer.choice(("partial", "error", "absent"))) for _ in range(200)
        )
        index = _IssueIndex(issues)
        for path in paths:
            expected = [
                issue
                for issue in issues
                if path[: len(issue.path)] == issue.path and (issue.path == path or len(issue.path) >= 2)
            ]
            self.assertEqual(index.applicable(path), expected)

    def test_repeated_field_queries_do_not_retraverse_unrelated_issues(self):
        issues = CountingIssues(
            [
                ReadIssue(("nodes", str(node), "field", field), "unsupported")
                for node in range(300)
                for field in range(30)
            ]
        )
        index = _IssueIndex(issues)
        self.assertEqual(issues.visits, 9000)
        for node in range(300):
            for field in range(30):
                self.assertEqual(len(index.applicable(("nodes", str(node), "field", field))), 1)
        self.assertEqual(issues.visits, 9000, "Each observation issue is indexed once, not scanned per field")
        self.assertEqual(len(index.nodes["42"]), 30)


class OverlappingIssueIndexTests(unittest.TestCase):
    def test_overlapping_paths_preserve_original_order_and_duplicates(self):
        issues = (
            ReadIssue(("nodes", "other", "name"), "error"),
            ReadIssue(("nodes", "target", "structure", "row_index"), "error"),
            ReadIssue(("nodes",), "partial"),
            ReadIssue(("nodes", "target"), "unavailable"),
            ReadIssue((), "conflict"),
            ReadIssue(("nodes", "target", "name"), "unsupported"),
            ReadIssue(("nodes", "target", "name"), "unsupported"),
            ReadIssue(("roots",), "not_read"),
        )
        index = ReadIssueIndex(issues)
        for path in (
            (),
            ("nodes",),
            ("roots",),
            ("nodes", "missing"),
            ("nodes", "target"),
            ("nodes", "target", "name"),
            ("nodes", "target", "structure"),
            ("nodes", "target", "structure", "column_index"),
        ):
            with self.subTest(path=path):
                expected = tuple(
                    issue
                    for issue in issues
                    if issue.path[: len(path)] == path or path[: len(issue.path)] == issue.path
                )
                self.assertEqual(index.overlapping(path), expected)
        self.assertEqual(
            index.for_node("target"), tuple(issue for issue in issues if issue.path[:2] == ("nodes", "target"))
        )
        self.assertEqual(index.for_node("missing"), ())

    def test_collection_queries_consume_the_issue_sequence_once(self):
        class CountedIssues:
            reads = 0

            def __iter__(self):
                self.reads += 1
                return iter(
                    (
                        ReadIssue(("nodes",), "partial"),
                        *(ReadIssue(("nodes", str(i), "name"), "unsupported") for i in range(200)),
                    )
                )

        issues = CountedIssues()
        request = SimpleNamespace(
            refs={str(i): str(i) for i in range(200)},
            interface=SimpleNamespace(projected_nodes={}),
            atoms=SimpleNamespace(atoms={}),
            native=SimpleNamespace(issues=issues),
        )
        source = _Source(request)
        for ref in request.refs:
            self.assertTrue(source.clean(ref, "role"))
            self.assertTrue(source.clean(ref, "structure", "row_index"))
            self.assertFalse(source.clean(ref, "name"))
        self.assertEqual(issues.reads, 1)

    def test_collection_wide_uncertainty_still_blocks_exact_field_use(self):
        for state in ("error", "unavailable", "conflict", "not_read"):
            with self.subTest(state=state):
                source = _Source(
                    SimpleNamespace(
                        refs={"0": "native"},
                        interface=SimpleNamespace(projected_nodes={}),
                        atoms=SimpleNamespace(atoms={}),
                        native=SimpleNamespace(issues=(ReadIssue(("nodes",), state),)),
                    )
                )
                self.assertFalse(source.clean("0", "role"))
                self.assertFalse(source.clean("missing", "role"))


class FrozenPresentationTests(unittest.TestCase):
    def test_shared_capture_is_detached_and_materializations_stay_independent(self):
        from computer_use.interaction.read.presentation.preparation import _copy_collections

        child = {"values": ["original"]}
        source = {"first": child, "second": child}
        captured = _copy_collections(source, immutable=True)
        self.assertIs(captured["first"], captured["second"])
        child["values"].append("later")
        first = _copy_collections(captured, immutable=False)
        second = _copy_collections(captured, immutable=False)
        first["first"]["values"].append("local")
        self.assertEqual(second["first"]["values"], ["original"])
        self.assertEqual(captured["first"]["values"], ("original",))

    def test_deep_collection_and_cycles_keep_their_original_boundaries(self):
        from computer_use.interaction.read.presentation.preparation import _copy_collections

        source = []
        cursor = source
        for _ in range(3000):
            cursor.append([])
            cursor = cursor[0]
        frozen = _copy_collections(source, immutable=True)
        for _ in range(3000):
            frozen = frozen[0]
        self.assertEqual(frozen, ())
        cursor.append(source)
        with self.assertRaisesRegex(ValueError, "collection cycle"):
            _copy_collections(source, immutable=True)

    def test_passive_group_planning_keeps_compact_text_and_source_evidence(self):
        from computer_use.interaction.read.facts.normalize import normalize_facts
        from computer_use.interaction.read.numbering.references import DisplayReferences
        from computer_use.interaction.read.presentation.preparation import prepare_snapshot, render_prepared_snapshot
        from computer_use.protocol.observation import NativeNode, NativeObservation, Relation, encode_observation

        nodes = {
            str(i): NativeNode(
                role="static_text",
                name=f"Paragraph {i} " + "captured text " * 300,
                actions=(),
                relations={"children": Relation("children", (), total=0)},
            )
            for i in range(4)
        }
        nodes["window"] = NativeNode(
            role="window",
            name="Fixture",
            actions=(),
            relations={"children": Relation("children", tuple(nodes), total=4)},
        )
        native = NativeObservation("macos", "session", "capture", (1, 2), ("window",), nodes)
        original = encode_observation(native)
        snapshot = normalize_facts(native, DisplayReferences("session"))
        prepared = prepare_snapshot(snapshot, compact=True, public_api=True)
        rendered = render_prepared_snapshot(prepared)
        self.assertNotIn("Presentation fallback", rendered["text"])
        self.assertTrue(all(root.groups is not None for root in prepared.roots))
        self.assertEqual(encode_observation(native), original)


class ResolvedDirectoryTests(unittest.TestCase):
    def request(self, **directory):
        from computer_use.interaction.read.app_read.model import ReadRequest
        from computer_use.native.contracts import Binding
        from computer_use.native.scopes import WorkingWindow

        scope = WorkingWindow("application", "Fixture", Binding("session", "instance", "root", "surface", "node"))
        return ReadRequest(scope, source_directory={"session": "session", "application": "application", **directory})

    def test_current_directory_remains_the_source_of_the_fixed_selection(self):
        from computer_use.interaction.read.app_read.helpers import parse_read_request
        from computer_use.pipeline import Control

        request = self.request()
        parsed = parse_read_request(request, Control())
        self.assertIs(parsed.source_directory, request.source_directory)
        self.assertIs(parsed.selector, request.selector)

    def test_directory_from_another_application_or_session_cannot_be_reused(self):
        from computer_use.interaction.read.app_read.helpers import parse_read_request
        from computer_use.pipeline import Control

        for field in ("session", "application"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_read_request(self.request(**{field: "another"}), Control())


class ApplicationListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_listing_reads_fresh_apps_without_querying_windows(self):
        from unittest.mock import AsyncMock

        from computer_use.interaction.query.instances.pipeline import ApplicationDiscoveryPipeline
        from computer_use.session.interfaces import ApplicationReferences

        catalogue = {
            "session": "session",
            "applications": [
                {"id": "one", "displayName": "One", "isRunning": True},
                {"id": "helper", "displayName": "Helper", "isRunning": True},
                {"id": "installed", "displayName": "Installed", "isRunning": False},
            ],
            "stopReasons": [],
        }
        directory = SimpleNamespace(
            applications=AsyncMock(return_value=catalogue),
            surface_batch=AsyncMock(side_effect=AssertionError("Unexpected window query")),
            surfaces=AsyncMock(side_effect=AssertionError("Unexpected window query")),
            _query=AsyncMock(side_effect=AssertionError("Unexpected window query")),
            port=SimpleNamespace(
                connection=SimpleNamespace(
                    capabilities={
                        "nativeApplicationListing",
                        "nativeSurfaceBatch",
                        "nativeVisualSurfaceQuery",
                        "nativeVisualSurfaceBatch",
                    }
                )
            ),
        )
        references = ApplicationReferences()
        pipeline = ApplicationDiscoveryPipeline(directory, references)
        first = await pipeline.run()
        catalogue["applications"][0]["displayName"] = "Updated"
        second = await pipeline.run()
        self.assertEqual(second[0].id, first[0].id)
        self.assertEqual(second[0].display_name, "Updated")
        self.assertEqual(directory.applications.await_count, 2)
        for item in second:
            self.assertIsNone(references.resolve(item.id, "session").window)
        directory.surface_batch.assert_not_awaited()
        directory.surfaces.assert_not_awaited()
        directory._query.assert_not_awaited()
