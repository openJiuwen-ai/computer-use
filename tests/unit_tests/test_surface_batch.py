# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""App discovery avoids window queries; explicit surface batches retain their native contracts."""

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
from computer_use.session.windows import WindowReferences


class DirectoryFixture:
    def __init__(self, capabilities=(), count=3):
        self.port = SimpleNamespace(connection=SimpleNamespace(capabilities=capabilities))
        self.apps = [dict(id=f"app:{i}", displayName=f"Editor {i}", isRunning=True) for i in range(count)]
        self.calls = []
        self.stop_reasons = []
        self.failure = None

    async def applications(self, *, include_ended=False):
        self.calls.append(dict(kind="applications"))
        if self.failure is not None:
            raise self.failure
        return dict(session="session", applications=deepcopy(self.apps), stopReasons=list(self.stop_reasons))

    async def _query(self, query, timeout):
        raise AssertionError("Application listing must not inspect windows")

    async def surface_batch(self, applications, *, profile=None):
        raise AssertionError("Application listing must not inspect window batches")


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
    async def test_listing_preserves_running_and_installed_application_order(self):
        fixture = DirectoryFixture()
        fixture.apps.insert(1, dict(id="installed", displayName="Installed", isRunning=False))
        references = ApplicationReferences()
        result = await ApplicationDiscoveryPipeline(fixture, references, WindowReferences()).run()
        self.assertEqual([item.display_name for item in result], ["Editor 0", "Installed", "Editor 1", "Editor 2"])
        self.assertEqual([item.is_running for item in result], [True, False, True, True])
        self.assertEqual(
            [references.resolve(item.id, "session").selector for item in result],
            ["app:0", "installed", "app:1", "app:2"],
        )
        self.assertEqual(fixture.calls, [dict(kind="applications")])

    async def test_large_listing_does_not_add_per_application_window_queries(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch",), count=130)
        references = ApplicationReferences()
        result = await ApplicationDiscoveryPipeline(fixture, references, WindowReferences()).run()
        self.assertEqual(len(result), 130)
        self.assertEqual(
            [references.resolve(item.id, "session").selector for item in result],
            [f"app:{i}" for i in range(130)],
        )
        self.assertEqual(fixture.calls, [dict(kind="applications")])

    async def test_same_application_names_do_not_merge_native_identities(self):
        fixture = DirectoryFixture(count=2)
        for item in fixture.apps:
            item["displayName"] = "Same name"
        references = ApplicationReferences()
        result = await ApplicationDiscoveryPipeline(fixture, references, WindowReferences()).run()
        self.assertNotEqual(result[0].id, result[1].id)
        self.assertEqual(
            [references.resolve(item.id, "session").selector for item in result], ["app:0", "app:1"]
        )
        self.assertEqual(fixture.calls, [dict(kind="applications")])

    async def test_application_query_failure_retains_its_original_effect(self):
        fixture = DirectoryFixture(("nativeSurfaceBatch",))
        fixture.failure = failure("possible")
        with self.assertRaises(ComputerUseError) as caught:
            await ApplicationDiscoveryPipeline(fixture, ApplicationReferences(), WindowReferences()).run()
        self.assertIs(caught.exception, fixture.failure)
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(fixture.calls, [dict(kind="applications")])

    async def test_incomplete_empty_listing_reports_failure_without_scanning_windows(self):
        fixture = DirectoryFixture(count=0)
        fixture.stop_reasons = ["provider unavailable"]
        with self.assertRaises(ComputerUseError) as caught:
            await ApplicationDiscoveryPipeline(fixture, ApplicationReferences(), WindowReferences()).run()
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertEqual(caught.exception.effect, "none")
        self.assertIn("provider unavailable", caught.exception.detail)
        self.assertEqual(fixture.calls, [dict(kind="applications")])

    async def test_window_query_capabilities_do_not_change_application_listing(self):
        capabilities = (
            (),
            ("nativeSurfaceBatch",),
            ("nativeSurfaceBatch", "nativeReadIsolation"),
            ("nativeSurfaceBatch", "nativeVisualSurfaceQuery"),
            ("nativeSurfaceBatch", "nativeVisualSurfaceQuery", "nativeVisualSurfaceBatch"),
            ("nativeApplicationListing",),
        )
        baseline = None
        for supported in capabilities:
            with self.subTest(capabilities=supported):
                fixture = DirectoryFixture(supported)
                result = await ApplicationDiscoveryPipeline(fixture, ApplicationReferences(), WindowReferences()).run()
                if baseline is None:
                    baseline = result
                self.assertEqual(result, baseline)
                self.assertEqual(fixture.calls, [dict(kind="applications")])


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
        rootDiscoveryComplete=not reasons,
        surfaces=list(surfaces),
        provider=dict(status="alreadyEnabled", attribute=None, writeStatus=None),
    )


class VisualBatchProfileTests(IsolatedAsyncioTestCase):
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
                capabilities=("nativeDirectory",),
                session_identity=(1, "session"),
                request=AsyncMock(return_value=payload),
            )
            port = ConnectionPort(connection, HostPolicy())
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
