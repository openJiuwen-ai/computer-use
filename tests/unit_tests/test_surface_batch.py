# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Batched discovery preserves application order, instance bindings and failures."""

from copy import deepcopy
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.pipeline import ApplicationDiscoveryPipeline
from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.query.directory import SurfaceReadProfile
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.directory import decode_directory
from computer_use.protocol.execution import decode_inspect_query
from computer_use.session.interfaces import ApplicationReferences


class DirectoryFixture:
    def __init__(self, capabilities=(), count=3):
        self.port = SimpleNamespace(connection=SimpleNamespace(capabilities=capabilities))
        self.apps = [dict(id=f"app:{i}", displayName=f"Editor {i}", isRunning=True) for i in range(count)]
        self.calls = []
        self.directories = {
            item["id"]: {**directory(window(f"window:{i}")), "application": item["id"]}
            for i, item in enumerate(self.apps)
        }
        self.failure = None

    @staticmethod
    def assert_visual(profile):
        if profile is not SurfaceReadProfile.VISUAL:
            raise AssertionError("batch lost visual intent")

    async def applications(self):
        return dict(session="session", applications=deepcopy(self.apps), stopReasons=[])

    async def _query(self, query, timeout):
        self.calls.append(query)
        result = self.directories[query["application"]]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)

    async def surface_batch(self, applications, *, profile=None):
        query = dict(kind="surfaceBatch", applications=list(applications))
        if "nativeVisualSurfaceBatch" in self.port.connection.capabilities:
            self.assert_visual(profile)
            query["profile"] = profile.value
        self.calls.append(query)
        if self.failure is not None:
            raise self.failure
        entries = []
        for app in applications:
            item = self.directories[app]
            entries.append(
                dict(
                    application=app,
                    **(
                        dict(error={key: getattr(item, key) for key in ("code", "target", "effect", "detail", "next")})
                        if isinstance(item, ComputerUseError)
                        else dict(result=deepcopy(item))
                    ),
                )
            )
        return dict(entries=entries)


def failure(effect="none"):
    return ComputerUseError(
        dict(
            code="observation_incomplete",
            target="request",
            effect=effect,
            detail="provider unavailable",
            next="read again",
        )
    )


class ApplicationDiscoveryTests(IsolatedAsyncioTestCase):
    async def compare(self, sequential, batched):
        first, second = ApplicationReferences(), ApplicationReferences()
        a = await ApplicationDiscoveryPipeline(sequential, first).run()
        b = await ApplicationDiscoveryPipeline(batched, second).run()
        self.assertEqual(a, b)
        for entry in a:
            self.assertEqual(first.resolve(entry["id"], "session"), second.resolve(entry["id"], "session"))
        return b

    async def test_batch_keeps_root_choices_unknown_ownership_and_provider_gaps(self):
        sequential, batched = DirectoryFixture(), DirectoryFixture(("nativeSurfaceBatch",))
        for fixture in (sequential, batched):
            fixture.apps.insert(1, dict(id="installed", displayName="Installed", isRunning=False))
            fixture.directories["app:0"]["surfaces"] += [
                window("child", parent="window:0"),
                {**window("unknown"), "parentState": "unknown"},
                {**window("menu"), "kind": "menu"},
            ]
            fixture.directories["app:1"] = failure()
        await self.compare(sequential, batched)
        self.assertEqual(len(sequential.calls), 3)
        self.assertEqual(batched.calls, [dict(kind="surfaceBatch", applications=["app:0", "app:1", "app:2"])])

    async def test_batches_are_bounded_and_keep_the_original_publication_order(self):
        a, b = DirectoryFixture(count=130), DirectoryFixture(("nativeSurfaceBatch",), count=130)
        await self.compare(a, b)
        self.assertEqual([len(call["applications"]) for call in b.calls], [64, 64, 2])

    async def test_failed_read_only_batch_uses_the_existing_sequential_route(self):
        a, b = DirectoryFixture(), DirectoryFixture(("nativeSurfaceBatch",))
        b.failure = failure()
        await self.compare(a, b)
        self.assertEqual([call["kind"] for call in b.calls], ["surfaceBatch", "surfaces", "surfaces", "surfaces"])

    async def test_possible_effect_is_never_downgraded_to_an_empty_directory(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch",))
        fixture.failure = failure("possible")
        with self.assertRaises(ComputerUseError):
            await ApplicationDiscoveryPipeline(fixture, ApplicationReferences()).run()
        self.assertEqual(len(fixture.calls), 1)

    async def test_isolated_native_discovery_keeps_its_existing_query_contract(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch", "nativeReadIsolation"))
        await ApplicationDiscoveryPipeline(fixture, ApplicationReferences()).run()
        self.assertEqual(len(fixture.calls), 3)
        self.assertTrue(
            all(call["kind"] == "surfaces" and call["includeAccessibility"] is False for call in fixture.calls)
        )

    async def test_uncertain_per_application_failure_is_propagated(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch",))
        fixture.directories["app:1"] = failure("possible")
        with self.assertRaises(ComputerUseError):
            await ApplicationDiscoveryPipeline(fixture, ApplicationReferences()).run()


def window(id, parent=None, modal=False, accessible=True):
    return dict(
        window=id,
        instance="app:" + id,
        surface="scope:" + id,
        node="node:" + id if accessible else None,
        title="Same title",
        kind="window",
        parent=parent,
        parentState="known" if parent else "none",
        modal=modal,
        nativeWindow=True,
        accessibility=accessible,
    )


def directory(*surfaces, reasons=()):
    return dict(
        kind="surfaces",
        session="session",
        platform="macos",
        application="running:1",
        observed=dict(id="obs", startedAt=1, completedAt=2),
        stopReasons=list(reasons),
        surfaces=list(surfaces),
        provider=dict(status="alreadyEnabled", attribute=None, writeStatus=None),
    )


class VisualBatchProfileTests(IsolatedAsyncioTestCase):
    async def test_visual_batches_keep_the_established_visual_route(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch", "nativeVisualSurfaceQuery", "nativeVisualSurfaceBatch"))
        baseline = DirectoryFixture(("nativeVisualSurfaceQuery",))
        await ApplicationDiscoveryTests.compare(self, baseline, fixture)
        self.assertEqual(
            fixture.calls, [dict(kind="surfaceBatch", applications=["app:0", "app:1", "app:2"], profile="visual")]
        )
        self.assertTrue(all(call.get("profile") == "visual" for call in baseline.calls))

    async def test_legacy_batch_does_not_replace_a_supported_visual_query(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch", "nativeVisualSurfaceQuery"))
        await ApplicationDiscoveryPipeline(fixture, ApplicationReferences()).run()
        self.assertEqual([call["kind"] for call in fixture.calls], ["surfaces"] * 3)
        self.assertTrue(all(call.get("profile") == "visual" for call in fixture.calls))

    async def test_batch_failure_returns_to_visual_queries(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch", "nativeVisualSurfaceQuery", "nativeVisualSurfaceBatch"))
        fixture.failure = failure()
        await ApplicationDiscoveryPipeline(fixture, ApplicationReferences()).run()
        self.assertEqual([call["kind"] for call in fixture.calls], ["surfaceBatch"] + ["surfaces"] * 3)
        self.assertTrue(all(call.get("profile") == "visual" for call in fixture.calls))

    async def test_batch_profile_requires_its_own_capability(self):
        for caps in ((), ("nativeSurfaceBatch", "nativeVisualSurfaceQuery"), ("nativeVisualSurfaceBatch",)):
            port = SimpleNamespace(connection=SimpleNamespace(capabilities=caps))
            adapter = NativeDirectory(port)
            adapter._query = AsyncMock(return_value={})
            await adapter.surface_batch(["a"], profile=SurfaceReadProfile.VISUAL)
            expected = dict(kind="surfaceBatch", applications=["a"])
            if "nativeVisualSurfaceBatch" in caps:
                expected["profile"] = "visual"
            adapter._query.assert_awaited_once_with(expected, 30000)
        with self.assertRaises(ValueError):
            await adapter.surface_batch(["a"], profile=False)

    def test_batch_query_bounds_and_profiles_are_strict(self):
        query = dict(kind="surfaceBatch", applications=["a", "b"])
        for profile in (None, "visual"):
            valid = query if profile is None else {**query, "profile": profile}
            self.assertEqual(decode_inspect_query(valid), valid)
        for bad in (
            {**query, "applications": []},
            {**query, "applications": ["a", "a"]},
            {**query, "applications": [str(i) for i in range(65)]},
            {**query, "profile": "accessibility"},
            {**query, "profile": None},
        ):
            with self.subTest(query=bad), self.assertRaises(ValueError):
                decode_inspect_query(bad)


class BatchScopeTests(IsolatedAsyncioTestCase):
    async def test_transport_requires_exact_application_order_and_coverage(self):
        for returned in (["a", "b"], ["b", "a"], ["a"], ["a", "other"]):
            payload = decode_directory(
                dict(
                    kind="surfaceBatch",
                    session="session",
                    platform="macos",
                    stopReasons=[],
                    observed=dict(id="batch", startedAt=1, completedAt=2),
                    entries=[dict(application=app, result={**directory(), "application": app}) for app in returned],
                )
            )
            connection = SimpleNamespace(
                protocol_version=6,
                platform="macos",
                session_identity=(1, "session"),
                request=AsyncMock(return_value=payload),
            )
            port = ConnectionPort(connection, HostPolicy("exclusive"))
            port._operations["read"] = (connection.session_identity, None)
            call = port.directory(
                "read", Binding("session", None, None, None), dict(kind="surfaceBatch", applications=["a", "b"]), 30000
            )
            if returned == ["a", "b"]:
                self.assertEqual(await call, payload)
            else:
                with self.subTest(returned=returned), self.assertRaisesRegex(ValueError, "order or coverage"):
                    await call


class BatchDirectoryDecodeTests(TestCase):
    def test_batch_preserves_independent_surface_contracts_and_rejects_cross_scope_results(self):
        from copy import deepcopy

        payload = dict(
            kind="surfaceBatch",
            session="session",
            platform="macos",
            observed=dict(id="batch", startedAt=1, completedAt=3),
            stopReasons=[],
            entries=[dict(application="running:1", result=directory(window("a")))],
        )
        decoded = decode_directory(payload)
        self.assertEqual(decoded["entries"][0]["result"], decode_directory(payload["entries"][0]["result"]))
        decoded["entries"][0]["result"]["surfaces"][0]["title"] = "changed"
        self.assertEqual(payload["entries"][0]["result"]["surfaces"][0]["title"], "Same title")
        for field, value in [("session", "another"), ("platform", "windows"), ("application", "other")]:
            bad = deepcopy(payload)
            bad["entries"][0]["result"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_directory(bad)
        for entries in (
            payload["entries"] * 2,
            [dict(application="running:1")],
            [dict(application="running:1", result=directory(), error={})],
        ):
            with self.assertRaises(ValueError):
                decode_directory({**payload, "entries": entries})
        nested = dict(application="running:1", result=payload)
        with self.assertRaisesRegex(ValueError, "surface directory"):
            decode_directory({**payload, "entries": [nested]})
