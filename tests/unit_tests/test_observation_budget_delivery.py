# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read deadlines stop new acquisition without erasing completed observations."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.read.acquisition.components.support import collect_component
from computer_use.interaction.read.acquisition.directory.model import DirectoryRequest
from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryReadPipeline
from computer_use.interaction.read.acquisition.images.capture import NativeImages
from computer_use.interaction.read.acquisition.images.desktop.model import DesktopReadRequest
from computer_use.interaction.read.acquisition.images.desktop.pipeline import DesktopReadPipeline
from computer_use.interaction.read.app import NativeAppReader
from computer_use.native.contracts import Binding, Cleanup, OpenedOperation
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.scopes import FixedWindowInvalidated, WorkingWindow
from computer_use.protocol.observation import NativeNode, NativeObservation, ReadIssue, Relation
from computer_use.protocol.observation.adapters.records import DriverObservationReply, DriverReadMetadata
from test_bounded_image_composition import captured_part
from test_d34_read_contracts import directory, window


class Port:
    def __init__(self, clock, *, isolated=False, expired_at="query"):
        features = {"nativeCapture", "nativeApplicationRoots"}
        if isolated:
            features.add("nativeReadIsolation")
        self.clock, self.expired_at = clock, expired_at
        self.connection = SimpleNamespace(
            session="session", session_identity=(1, "session"), platform="macos", capabilities=features
        )
        self.capabilities = features
        self.calls = CallLifecycle(self)
        self.opened, self.finished, self.queries = [], [], []

    async def connect(self, timeout):
        raise AssertionError("Expired suffix must not connect for another directory read")

    async def begin_read(self, target, timeout):
        self.calls.check_begin("read", "read", target)
        self.opened.append((target, timeout))
        return OpenedOperation(str(len(self.opened)), target)

    async def finish(self, token):
        self.finished.append(token)
        return Cleanup.COMPLETE

    async def nodes(self, token, target, fields, max_nodes, max_depth, timeout, **options):
        self.queries.append((target, timeout))
        if self.expired_at == "query":
            self.clock.now += 5.1
        native = NativeObservation(
            "macos", "session", "read", (1, 2), ("node:A",),
            {
                "node:A": NativeNode(
                    role="window", name="Open", actions=(),
                    relations={"children": Relation("children", ("search",), total=1)},
                ),
                "search": NativeNode(role="text_field", name="Search", actions=()),
            },
            issues=(ReadIssue(("nodes",), "partial", "collection time budget"),),
        )
        return DriverObservationReply(
            native, DriverReadMetadata("surface:A", None, {}, {}, (), {}),
        )


@pytest.mark.parametrize("isolated", [False, True])
def test_first_partial_batch_survives_expired_suffix_through_app_delivery(monkeypatch, isolated):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, isolated=isolated)
    reader = NativeAppReader(port)
    # These collectors receive the original budget; none may gain another
    # allowance after facts consumed it.
    reader.images.collect = AsyncMock(side_effect=lambda read, **kw: read.remaining())

    async def menus(application, remaining, **kwargs):
        remaining()
        raise AssertionError("Expired suffix must not start menu acquisition")

    reader.menus.collect = AsyncMock(side_effect=menus)
    selected = WorkingWindow("application", "Fixture", Binding("session", "instance:A", "A", "surface:A"))
    current = directory(window("A"))

    async def run():
        with port.calls.limit(5000):
            return await reader.read(selected, view="both", overview=False, source_directory=current)

    observed = asyncio.run(run())
    assert observed.tree is not None and "Search" in observed.document.text
    assert observed.tree.native.issues[0].detail == "collection time budget"
    assert observed.target.root_window == "A" and observed.source_directory is current
    assert observed.directory is current and not observed.scope_confirmed
    assert {gap.component for gap in observed.gaps} >= {"image", "menus", "directory"}
    assert len(port.queries) == 1 and len(port.opened) == 1 and port.finished == ["1"]
    assert not port.calls.retired and not port.calls.busy


def test_completed_read_exits_normally_but_next_admission_keeps_original_deadline(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock)
    target = Binding("session", "instance:A", "A", "surface:A")

    async def run():
        with port.calls.claim(5000) as remaining:
            async with port.calls.operation(target, remaining):
                clock.now += 5.1
            with pytest.raises(TimeoutError, match="budget exhausted"):
                async with port.calls.operation(target, remaining):
                    pytest.fail("A new read cannot start after the original deadline")

    asyncio.run(run())
    assert len(port.opened) == 1 and port.finished == ["1"]
    assert not port.calls.retired


def test_directory_identity_failure_retains_its_new_catalogue(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, expired_at="none")
    reader = NativeAppReader(port)
    selected = WorkingWindow("application", "Fixture", Binding("session", "instance:A", "A", "surface:A"))
    replacement = directory(window("New"))
    failure = FixedWindowInvalidated(selected.root, "Original window closed", directory=replacement)
    reader._discover = AsyncMock(side_effect=failure)

    with pytest.raises(FixedWindowInvalidated) as caught:
        asyncio.run(reader.read(
            selected, view="tree", overview=False, include_menus=False,
            source_directory=directory(window("A")),
        ))

    assert caught.value is failure and caught.value.directory is replacement
    assert len(port.queries) == 1 and not port.calls.retired


def test_overview_timeout_does_not_erase_window_content(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, expired_at="none")
    reader = NativeAppReader(port)
    selected = WorkingWindow("application", "Fixture", Binding("session", "instance:A", "A", "surface:A"))
    current = directory(window("A"))
    reader.overviews.collect = AsyncMock(side_effect=TimeoutError("preview deadline"))
    reader._discover = AsyncMock(return_value=(selected, current))

    observed = asyncio.run(reader.read(
        selected, view="tree", include_menus=False, source_directory=current,
    ))

    assert observed.tree is not None and "Search" in observed.document.text
    assert observed.scope_confirmed and observed.directory is current
    assert any(gap.component == "overview" and "preview deadline" in gap.detail for gap in observed.gaps)
    assert len(port.queries) == 1 and not port.calls.retired


def test_returned_window_capture_keeps_pixels_identity_and_geometry_after_deadline(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock)
    source = captured_part("A").image

    async def capture(*args, **kwargs):
        clock.now += 5.1
        return {"image": source.image, "observed": source.observed, "coordinates": source.coordinates}

    port.capture = AsyncMock(side_effect=capture)
    observed = asyncio.run(NativeImages(port).window(replace(source.target, capture=None), timeout_ms=5000))
    assert observed.image is source.image and observed.target == source.target
    assert observed.coordinates == source.coordinates and port.finished == ["1"]


@pytest.mark.parametrize("component", ["directory", "desktop"])
def test_returned_session_observation_is_preserved_after_native_budget(component, monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock)
    port.connect = AsyncMock(return_value=Binding("session", None, None, None))
    source = (
        directory(window("NewModal"), reasons=("collection time budget",))
        if component == "directory" else {"observation": {"screens": []}, "screenshot": None}
    )

    async def collect(*args, **kwargs):
        clock.now += 5.1
        return source

    if component == "directory":
        port.directory = AsyncMock(side_effect=collect)
        observed = asyncio.run(DirectoryReadPipeline(port).arun(DirectoryRequest({"kind": "surfaces"}, 5000)))
        assert observed is source and observed["surfaces"][0]["window"] == "NewModal"
    else:
        port.capture = AsyncMock(side_effect=collect)
        observed = asyncio.run(DesktopReadPipeline(port).arun(DesktopReadRequest(timeout_ms=5000)))
        assert observed["observation"] is source["observation"]
    assert port.finished == ["1"] and not port.calls.retired


@pytest.mark.parametrize("failure", ["cancel", "changed_session", "retired"])
def test_component_recovery_does_not_hide_cancellation_or_lost_generation(failure):
    port = Port(SimpleNamespace(now=100.0))
    error = asyncio.CancelledError() if failure == "cancel" else TimeoutError("component deadline")

    async def collect():
        if failure == "changed_session":
            port.connection.session_identity = (2, "replacement")
        if failure == "retired":
            port.calls.retired = True
        raise error

    with pytest.raises(type(error)) as caught:
        asyncio.run(collect_component(port, "image", collect))
    assert caught.value is error
