# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read deadlines stop new acquisition without erasing completed observations."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from computer_use.client.images import image_label
from computer_use.interaction.read.acquisition.components.support import collect_component
from computer_use.interaction.read.acquisition.directory.model import DirectoryRequest
from computer_use.interaction.read.acquisition.directory.pipeline import DirectoryReadPipeline
from computer_use.interaction.read.acquisition.images.capture import NativeImages
from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline
from computer_use.interaction.read.acquisition.images.desktop.model import DesktopReadRequest
from computer_use.interaction.read.acquisition.images.desktop.pipeline import DesktopReadPipeline
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.views.pipeline import WindowPart
from computer_use.native.contracts import Binding, Cleanup, OpenedOperation
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.scopes import FixedWindowInvalidated, FixedWindowUnconfirmed, WorkingWindow
from computer_use.protocol.observation import NativeNode, NativeObservation, ReadIssue, Relation
from computer_use.protocol.observation.adapters.records import DriverObservationReply, DriverReadMetadata
from computer_use.protocol.session import failure_error
from test_bounded_image_composition import captured_part
from test_d34_read_contracts import directory, setup, window


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
def test_sequential_facts_and_pixels_can_use_the_original_deadline_without_repeated_slices(monkeypatch, isolated):
    """Both components fit the call; neither may be denied by an unrelated fraction."""
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, isolated=isolated, expired_at="none")
    original_nodes = port.nodes

    async def nodes(token, target, fields, max_nodes, max_depth, timeout, **options):
        # Model a provider that needs 140 ms for this unchanged two-node tree.
        # A 400/3 ms slice fails even though tree+pixels fit the original call.
        if timeout < 140:
            raise TimeoutError("The provider cannot complete this tree within the artificial slice")
        clock.now += 0.140
        returned = await original_nodes(token, target, fields, max_nodes, max_depth, timeout, **options)
        return replace(returned, observation=replace(returned.observation, issues=()))

    port.nodes = nodes
    reader = NativeAppReader(port)
    image = captured_part("A").image
    image_budgets = []

    async def capture(read, **options):
        image_budgets.append(read.remaining())
        if read.remaining() < 180:
            raise TimeoutError("The window needs 180 ms for its pixels")
        clock.now += 0.180
        return image

    reader.images.collect = AsyncMock(side_effect=capture)
    selected = WorkingWindow("application", "Fixture", Binding("session", "instance:A", "A", "surface:A"))
    current = directory(window("A"))
    reader._discover = AsyncMock(return_value=(selected, current))

    async def run():
        with port.calls.limit(400):
            return await reader.read(
                selected, view="both", overview=False, include_menus=False, source_directory=current,
            )

    observed = asyncio.run(run())

    assert observed.tree is not None and "Search" in observed.document.text
    assert observed.image is image and not observed.gaps
    assert 398 <= port.queries[0][1] <= 400
    assert 258 <= image_budgets[0] <= 260
    assert clock.now == pytest.approx(100.320)
    assert not port.calls.busy and not port.calls.retired


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

    observed = asyncio.run(reader.read(
        selected, view="tree", overview=False, include_menus=False,
        source_directory=directory(window("A")),
    ))

    assert observed.directory is replacement and not observed.scope_confirmed
    assert observed.target.root_window == "A" and "Search" in observed.document.text
    assert any(gap.component == "scope" and gap.code == "window_closed" for gap in observed.gaps)
    assert len(port.queries) == 1 and not port.calls.retired


@pytest.mark.parametrize("failure", ["deadline", "other", "possible_effect", "retired", "changed_session"])
def test_acknowledged_native_recheck_deadline_preserves_capture_with_unconfirmed_scope(monkeypatch, failure):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, expired_at="none")
    reader = NativeAppReader(port)
    binding = Binding("session", "instance:A", "A", "surface:A")
    selected = WorkingWindow("application", "Fixture", binding)
    original = directory(window("A"))
    port.connect = AsyncMock(return_value=Binding("session", None, None, None))
    error = failure_error(
        "inspect",
        {
            "code": "execution_failed" if failure == "other" else "deadline_exceeded",
            "effect": "possible" if failure == "possible_effect" else "none",
            "detail": "request deadline exceeded",
            "target": "native request",
        },
    )

    async def recheck(*_args):
        clock.now += 5.1
        if failure == "retired":
            port.calls.retired = True
        elif failure == "changed_session":
            port.connection.session_identity = (2, "replacement")
        raise error

    port.directory = AsyncMock(side_effect=recheck)

    async def read():
        with port.calls.limit(5000):
            return await reader.read(
                selected, view="tree", overview=False, include_menus=False, source_directory=original, scope_managed=True,
            )

    assert error.code == "execution_failed"
    if failure == "deadline":
        observed = asyncio.run(read())
        assert observed.source_directory is original and observed.directory is original
        assert observed.target.root_window == "A" and observed.target.surface == "surface:A"
        assert observed.tree.native.interval_ns == (1, 2)
        assert any(node.name == "Search" for node in observed.tree.native.nodes.values())
        assert not observed.scope_confirmed
        assert any(gap.component == "directory" and "request deadline exceeded" in gap.detail for gap in observed.gaps)
        assert not port.calls.retired
    else:
        with pytest.raises(type(error)) as caught:
            asyncio.run(read())
        assert caught.value is error
    assert len(port.queries) == port.directory.await_count == 1 and port.finished == ["1", "2"]


@pytest.mark.parametrize("scope_managed", [False, True])
def test_postcapture_unconfirmed_window_keeps_its_exact_facts_pixels_and_intervals(monkeypatch, scope_managed):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    port = Port(clock, expired_at="none")
    reader = NativeAppReader(port)
    reader.window_aliases = Mock(wraps=reader.window_aliases)
    selected = WorkingWindow("application", "Fixture", Binding("session", "instance:A", "A", "surface:A"))
    original, later = directory(window("A")), directory()
    image = captured_part("A").image
    reader.images.collect = AsyncMock(return_value=image)
    reader._discover = AsyncMock(side_effect=FixedWindowUnconfirmed(selected.root, directory=later))

    observed = asyncio.run(reader.read(
        selected, view="both", overview=False, include_menus=False, source_directory=original,
        scope_managed=scope_managed,
    ))

    assert observed.target.root_window == "A" and observed.target.surface == "surface:A"
    assert observed.source_directory is original and observed.directory is later
    assert observed.tree.native.interval_ns == (1, 2)
    assert any(node.name == "Search" for node in observed.tree.native.nodes.values())
    assert observed.image is image and not observed.scope_confirmed
    if scope_managed:
        reader.window_aliases.assert_not_called()
        assert not observed.refs and not observed.windows and observed.tree.refs
    else:
        reader.window_aliases.assert_called_once_with(later)
        assert observed.refs
    assert any(gap.component == "scope" and "original facts, pixels" in gap.detail for gap in observed.gaps)
    assert len(port.queries) == 1 and reader.images.collect.await_count == 1


@pytest.mark.parametrize("later_evidence", [
    "same", "replaced", "ended", "unknown_parent", "timeout", "native_timeout", "capture_scope_changed",
    "final_parent_changed",
])
def test_exact_window_pixels_do_not_require_a_complete_directory(monkeypatch, later_evidence):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("computer_use.native.lifecycle.time.monotonic", lambda: clock.now)
    reader = NativeAppReader(Port(clock, expired_at="none"))
    modal = {**window("A"), "modal": True}
    owner = window("Owner")
    reasons = ("attached surface children unavailable",)
    initial = directory(modal, owner, focused="A", reasons=reasons)
    latest = dict(modal)
    if later_evidence == "replaced":
        latest["instance"] = "instance:replacement"
    if later_evidence == "unknown_parent":
        latest["parentState"] = "unknown"
    incomplete = directory(
        latest, owner, focused="A", reasons=reasons,
        ended=(modal,) if later_evidence == "ended" else (),
    )
    initial["rootDiscoveryComplete"] = incomplete["rootDiscoveryComplete"] = True
    root = Binding("session", "instance:A", "A", "surface:A")
    selected = WorkingWindow("application", "Fixture", root)
    image = replace(
        captured_part("A").image, target=replace(root, capture="capture-A"),
        scope_changed=later_evidence == "capture_scope_changed",
    )
    reader.images.collect = AsyncMock(return_value=image)
    if later_evidence == "timeout":
        reader._discover = AsyncMock(side_effect=TimeoutError("recheck deadline"))
    elif later_evidence == "native_timeout":
        reader._discover = AsyncMock(side_effect=failure_error(
            "inspect", {
                "code": "deadline_exceeded", "effect": "none", "detail": "recheck deadline", "target": "native request",
            },
        ))
    else:
        reader._discover = AsyncMock(return_value=(selected, incomplete))

    observed = asyncio.run(reader.read(
        selected, view="both", overview=False, include_menus=False, source_directory=initial, scope_managed=True,
    ))

    assert observed.image.image is image.image and not observed.scope_confirmed
    assert observed.directory["stopReasons"] == list(reasons)
    final_directory, source = observed.directory, observed
    if later_evidence == "final_parent_changed":
        final_directory = directory({**latest, "parent": "Owner", "parentState": "known"}, owner, focused="A")
        # A later tree upgrade can replace the source without replacing the pixels.
        source = replace(observed, scope_confirmed=True, directory=final_directory)
    controller, app, _ = setup(final_directory)
    part = WindowPart(observed.target, image=observed.image, source=source)
    canvas, notes = InterfaceImagePipeline(controller).run(app, [part])
    assert canvas.png is image.image
    assert notes[-1].values[0].mapping_available == (later_evidence == "same")
    assert ("Image point for window=" in image_label(canvas.png)) == (later_evidence == "same")
    if later_evidence == "same":
        assert canvas.for_window("A").hit((20, 30))[0].image.target == image.target
    else:
        assert canvas.for_window("A") is None
    assert reader.images.collect.await_count == reader._discover.await_count == 1


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
