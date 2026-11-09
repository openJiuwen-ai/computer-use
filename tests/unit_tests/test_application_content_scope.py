"""App content is observed and bound independently of document windows."""

import asyncio
from contextlib import nullcontext
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.client.computer import computer_for_controller
from computer_use.client.factory import session_controller
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline
from computer_use.native.contracts import Binding, Cleanup, OpenedOperation
from computer_use.protocol.execution import decode_inspect_query
from computer_use.protocol.observation import NativeNode, NativeObservation, Relation
from computer_use.protocol.observation.adapters.records import DriverObservationReply, DriverReadMetadata
from computer_use.session import operations
from computer_use.session.events import observation_delivered
from computer_use.session.interfaces import WindowIdentity
from unit_tests.test_d34_read_contracts import directory, setup, window
from unit_tests.test_dynamic_observation import pixels
from unit_tests.test_observation_budget_delivery import Port


def test_windowless_app_publishes_its_own_content_and_exact_element_refs():
    current = directory(reasons=("AX window catalogue unavailable",))
    controller, app, state = setup(current)
    port = controller.port
    port.connection.platform = "macos"
    port.connection.capabilities = {"nativeApplicationRoots", "nativeApplicationContent"}
    requests = []

    async def begin_read(target, timeout):
        port.calls.check_begin("read", "read", target)
        return OpenedOperation("operation", target)

    async def connect(timeout):
        return Binding("session", None, None, None)

    async def roots(token, target, query, timeout):
        requests.append(decode_inspect_query(query))
        return {
            "kind": "roots",
            "session": "session",
            "application": "application",
            "platform": "macos",
            "roots": [{"kind": "applicationContent", "instance": "app-root", "surface": "content", "node": "root"}],
            "stopReasons": [],
            "observed": current["observed"],
        }

    async def nodes(token, target, fields, max_nodes, max_depth, timeout, **options):
        requests.append(target)
        observed = NativeObservation(
            "macos", "session", "content-read", (1, 2), ("root",),
            {
                "root": NativeNode(
                    role="application", name="Fixture", actions=(),
                    relations={"children": Relation("children", ("item",), total=1)},
                ),
                "item": NativeNode(role="button", name="Owned item", actions=()),
            },
        )
        return DriverObservationReply(observed, DriverReadMetadata("content", None, {}, {}, (), {}))

    async def finish(token):
        return Cleanup.COMPLETE

    port.connect, port.begin_read, port.finish = connect, begin_read, finish
    port.directory, port.nodes = roots, nodes
    launch = MappingProxyType({"detail": "Started the selected application."})
    result = asyncio.run(
        InterfaceReadPipeline(controller)._collect(app, "both", False, None, current, launch, None, None)
    )
    assert "Owned item" in str(result)
    assert "Started the selected application." in str(result)
    assert result.screenshot is None
    assert state.input_context is None
    assert requests == [
        {"kind": "roots", "application": "application", "applicationContent": True},
        Binding("session", "app-root", None, "content", "root"),
    ]
    snapshot = next(value for value in state.refs.values() if value["binding"].node == "item")
    controller.interfaces.validate_snapshot(app, snapshot)
    assert snapshot["binding"] == Binding("session", "app-root", None, "content", "item")
    observation_delivered(result)
    assert state.lookup_context.application_content is not None
    assert state.lookup_context.window is None


@pytest.mark.parametrize(("view", "unique"), [("tree", True), ("both", True), ("tree", False)])
def test_public_read_reconciles_windows_that_appear_during_windowless_fallback(monkeypatch, view, unique):
    monkeypatch.setattr(operations, "_guard", None)
    original = directory()
    working = window("A", title="Ready window")
    current = directory(working, focused="A") if unique else directory(working, window("B"))
    port = Port(SimpleNamespace(now=100), expired_at="none")
    port.capabilities.add("nativeApplicationContent")
    port.connection.retain_session = lambda _: nullcontext()
    port.connect = AsyncMock(return_value=Binding("session", None, None, None))
    requests = []

    async def catalogue(_token, _target, query, _timeout):
        requests.append(query["kind"])
        if query["kind"] == "applications":
            return {
                "kind": "applications",
                "session": "session",
                "platform": "macos",
                "applications": [{
                    "id": "application", "displayName": "Fixture", "isRunning": True, "identifier": None, "path": None,
                }],
                "stopReasons": [],
                "observed": original["observed"],
            }
        assert query["application"] == "application"
        if query["kind"] == "roots":
            return {
                "kind": "roots",
                "session": "session",
                "application": "application",
                "platform": "macos",
                "roots": [],
                "stopReasons": [],
                "observed": original["observed"],
            }
        assert query["kind"] == "surfaces"
        return original if requests.count("surfaces") == 1 else current

    port.directory = catalogue
    controller = session_controller(port)
    controller.reader.images.collect = AsyncMock(return_value=pixels(SimpleNamespace(directory=current), working))
    with computer_for_controller(controller) as computer:
        app = computer.app("Fixture")
        observed = app.read(view=view)
        observation_delivered(observed)
        if unique:
            assert "Search" in observed.text
            assert app._instance.input_context == WindowIdentity.of(working)
            assert (observed.screenshot is not None) == (view == "both")
            assert len(port.queries) == 1
            assert port.queries[0][0] == Binding("session", "instance:A", "A", "surface:A", "node:A")
        else:
            assert "no unique observation window" in observed.text
            assert "window=0" in observed.text and "window=1" in observed.text
            assert app._instance.input_context is None
            assert not port.queries
    assert requests[:4] == ["applications", "surfaces", "roots", "surfaces"]
    assert requests.count("applications") == 1
