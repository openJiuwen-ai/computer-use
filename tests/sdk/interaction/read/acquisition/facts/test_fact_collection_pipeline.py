"""Fact/text/relation stages preserve existing queries, scopes and alias effects."""

import unittest
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from types import SimpleNamespace

from observation_fixture import reply
from sdk.interaction.read.facts.test_native_facts import batch, node

from computer_use.interaction.read.acquisition.facts.helpers import plan_fact_profile, resolve_fact_scope
from computer_use.interaction.read.acquisition.facts.model import FactReadRequest, NativeReadCapabilities
from computer_use.interaction.read.acquisition.facts.pipeline import FactCollectionPipeline
from computer_use.interaction.read.acquisition.reader import CapturedField, NativeReader
from computer_use.interaction.read.facts.index import observation_index
from computer_use.native.contracts import Binding
from computer_use.pipeline import Control, Effect

TARGET = Binding("session", "instance", "root", "surface", "field")


class Calls:
    def __init__(self, trace, canonical=None):
        self.trace, self.canonical = trace, canonical

    @contextmanager
    def claim(self, timeout):
        self.trace.append(("claim", timeout))
        try:
            yield lambda: 700
        finally:
            self.trace.append(("release_claim",))

    @asynccontextmanager
    async def open(self, target, timeout):
        self.trace.append(("open", target, timeout))
        try:
            yield SimpleNamespace(token="read-op", target=self.canonical or target, remaining=lambda: self.budget())
        finally:
            self.trace.append(("finish",))

    def budget(self):
        self.trace.append(("budget",))
        return 700


class Port:
    def __init__(self, *, platform="macos", features=()):
        self.trace = []
        self.connection = SimpleNamespace(platform=platform, capabilities=features, session="session")
        self.calls = Calls(self.trace)
        self.result = reply(batch([node("field", AXRole="AXTextField", AXValue="hello")]))
        self.error = None

    async def nodes(self, token, target, fields, max_nodes, max_depth, timeout, **options):
        self.trace.append(("nodes", token, target, fields, max_nodes, max_depth, timeout, options))
        if self.error is not None:
            raise self.error
        return self.result

    async def relation(self, token, target, property, cursor, limit, timeout):
        self.trace.append(("relation", token, target, property, cursor, limit, timeout))
        if self.error is not None:
            raise self.error
        return self.result

    async def captured_text(self, token, target, text_ref, offset, length, timeout):
        self.trace.append(("captured_text", token, target, text_ref, offset, length, timeout))
        if self.error is not None:
            raise self.error
        return self.result


class FactCollectionPipelineTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def read(port, target=TARGET):
        return SimpleNamespace(token="read-op", target=target, remaining=port.calls.budget)

    async def test_stages_keep_exact_query_and_existing_lease_budget(self):
        port = Port(features=("nativeNodeDepthBudget",))
        reader = NativeReader(port)
        events = []
        fields = ["AXRole", "AXValue"]
        children = dict(cursor="next-children", limit=3)
        relation = dict(property="AXVisibleCells", cursor="next-members", limit=2)
        request = FactReadRequest(
            self.read(port), fields=fields, max_nodes=8, children_page=children, relation_page=relation
        )
        result = await FactCollectionPipeline(reader).arun(
            request, Control(remaining=port.calls.budget, diagnostic=events.append)
        )
        self.assertEqual([item[0] for item in port.trace], ["budget", "nodes", "budget"])
        query = port.trace[1]
        self.assertIs(query[3], fields, "Explicit profiles remain unmodified")
        self.assertEqual(query[4:7], (8, 7, 700))
        self.assertEqual(query[7], dict(children_page=children, relation_page=relation, scope_kind=None))
        self.assertEqual(result.refs, {"0": "field"})
        entered = [event for event in events if event.phase == "enter"]
        self.assertEqual(
            [event.path[-1] for event in entered],
            [
                "FactCollectionPipeline",
                "resolve_fact_scope",
                "plan_fact_profile",
                "collect_native_facts",
                "normalize_native_facts",
                "check_fact_budget",
            ],
        )
        normalize = next(event for event in entered if event.path[-1] == "normalize_native_facts")
        self.assertEqual(normalize.effects, {Effect.RESOURCE})
        for name in ("resolve_fact_scope", "plan_fact_profile"):
            self.assertEqual(next(event for event in entered if event.path[-1] == name).effects, {Effect.PURE})

    async def test_internal_trace_cannot_replace_the_existing_lease_budget(self):
        port = Port()
        reader, events = NativeReader(port), []

        def outer_budget():
            self.fail("Fact collection must retain its bounded native read lease")

        await reader.collect(
            self.read(port),
            fields=["AXRole"],
            _control=Control(remaining=outer_budget, diagnostic=events.append, path=("outer",)),
        )
        self.assertTrue(all(event.path[0] == "outer" for event in events))
        self.assertEqual([entry[0] for entry in port.trace], ["budget", "nodes", "budget"])

    async def test_query_failure_does_not_register_or_reset_references(self):
        port = Port()
        reader = NativeReader(port)
        await reader.collect(self.read(port))
        previous = reader._references
        port.error = RuntimeError("native query failed")
        with self.assertRaisesRegex(RuntimeError, "native query failed"):
            await reader.collect(self.read(port, replace(TARGET, session="other-session")))
        self.assertIs(reader._references, previous)

    async def test_normalization_and_final_budget_failure_retain_alias_owner_timing(self):
        port = Port()
        reader = NativeReader(port)
        port.result = reply({**batch([node("field", AXRole="AXTextField")]), "session": "other-session"})
        with self.assertRaises(ValueError):
            await reader.collect(self.read(port))
        self.assertEqual(reader._references.session, "session")
        port.result = reply(batch([node("field", AXRole="AXTextField")]))
        counts = 0

        def remaining():
            nonlocal counts
            counts += 1
            if counts == 2:
                raise TimeoutError("original budget expired")
            return 700

        with self.assertRaisesRegex(TimeoutError, "original budget expired"):
            await reader.collect(SimpleNamespace(token="read-op", target=TARGET, remaining=remaining))
        self.assertEqual(counts, 2)
        self.assertEqual(reader._references.reference("field"), "0")

    async def test_scope_refusals_precede_profile_and_native_queries(self):
        port, events = Port(), []
        reader = NativeReader(port)
        for target, override in ((replace(TARGET, capture="image"), None), (TARGET, "sibling")):
            with self.subTest(target=target, override=override), self.assertRaises(ValueError):
                await FactCollectionPipeline(reader).arun(
                    FactReadRequest(self.read(port, target), node=override), Control(diagnostic=events.append)
                )
        self.assertEqual(port.trace, [])
        self.assertFalse(any(event.path[-1] == "plan_fact_profile" for event in events))
        self.assertIsNone(reader._references)

    def test_profile_planner_is_independent_of_port_and_preserves_explicit_fields(self):
        read = SimpleNamespace(target=TARGET)
        fields = ["VendorProperty"]
        scope = resolve_fact_scope(FactReadRequest(read, fields=fields, max_nodes=4), None)
        result = plan_fact_profile(scope, NativeReadCapabilities("windows", ("nativeUiaReadV2",)))
        self.assertIs(result.fields, fields)
        self.assertEqual(result.options, {})
        self.assertEqual(result.max_depth, 40)
        with self.assertRaisesRegex(ValueError, "explicit depth of at most 40"):
            scope = resolve_fact_scope(FactReadRequest(read, max_depth=41), None)
            plan_fact_profile(scope, NativeReadCapabilities("macos", ()))


class TextRelationPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_relation_uses_only_its_native_scope_and_preserves_captured_result(self):
        port = Port()
        port.result = reply(
            dict(
                kind="relation",
                session="session",
                surface="surface",
                platform="macos",
                observed=dict(id="relation-capture", startedAt=1, completedAt=2),
                relation=dict(
                    source="field",
                    property="AXLinkedUIElements",
                    targets=["related"],
                    state="partial",
                    certainty="confirmed",
                    offset=2,
                    total=8,
                    cursor="next-prefix",
                ),
            )
        )
        canonical = replace(TARGET, root_window="canonical-root")
        port.calls.canonical = canonical
        reader = NativeReader(port)
        output = await reader.read_relation(
            TARGET, "AXLinkedUIElements", cursor="observed-prefix", limit=2, timeout_ms=900
        )
        self.assertEqual(output["relation"], observation_index(port.result.observation)["relations"][0])
        self.assertEqual(output["observed"]["id"], "relation-capture")
        self.assertEqual(
            port.trace,
            [
                ("open", TARGET, 900),
                ("budget",),
                ("relation", "read-op", canonical, "AXLinkedUIElements", "observed-prefix", 2, 700),
                ("finish",),
            ],
        )
        port.trace.clear()
        with self.assertRaises(ValueError):
            await reader.read_relation(TARGET, "AXChildren", limit=True)
        self.assertEqual(port.trace, [])

    async def test_relation_failure_releases_once_and_never_collects_a_tree(self):
        port = Port()
        error = port.error = RuntimeError("relation provider stopped")
        with self.assertRaises(RuntimeError) as result:
            await NativeReader(port).read_relation(TARGET, "AXChildren")
        self.assertIs(result.exception, error)
        self.assertEqual([entry[0] for entry in port.trace], ["open", "budget", "relation", "finish"])

    async def test_local_empty_capture_only_claims_and_never_opens_native_scope(self):
        port = Port()
        output = await NativeReader(port).read_captured_text(CapturedField("session", value=""), timeout_ms=500)
        self.assertEqual(output, dict(text="", offset=0, total=0, next=None))
        self.assertEqual(port.trace, [("claim", 500), ("release_claim",)])

    async def test_native_text_uses_session_scope_and_validates_before_cleanup(self):
        port = Port()
        port.result = dict(text="hello", offset=0, total=5, next=None)
        source = CapturedField("session", text_ref="immutable-text", total_units=5)
        output = await NativeReader(port).read_captured_text(source, length=7, timeout_ms=800)
        target = Binding("session", None, None, None)
        self.assertIs(output, port.result)
        self.assertEqual(
            port.trace,
            [
                ("open", target, 800),
                ("budget",),
                ("captured_text", "read-op", target, "immutable-text", 0, 7, 700),
                ("finish",),
            ],
        )
        port.trace.clear()
        port.result = dict(text="changed", offset=0, total=7, next=None)
        with self.assertRaisesRegex(ValueError, "immutable capture range"):
            await NativeReader(port).read_captured_text(source)
        self.assertEqual([entry[0] for entry in port.trace], ["open", "budget", "captured_text", "finish"])

    async def test_invalid_or_expired_capture_never_claims_or_reconnects(self):
        port = Port()
        reader = NativeReader(port)
        source = CapturedField("ended", value="text")
        with self.assertRaisesRegex(ValueError, "range or budget"):
            await reader.read_captured_text(source, length=1)
        with self.assertRaisesRegex(RuntimeError, "driver session ended"):
            await reader.read_captured_text(source)
        with self.assertRaisesRegex(ValueError, "no immutable full-text capture"):
            await reader.read_captured_text(CapturedField("session"))
        self.assertEqual(port.trace, [])
