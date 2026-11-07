"""App content is observed and bound independently of document windows."""

import asyncio
from types import MappingProxyType

from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline
from computer_use.native.contracts import Binding, Cleanup, OpenedOperation
from computer_use.protocol.execution import decode_inspect_query
from computer_use.protocol.observation import NativeNode, NativeObservation, Relation
from computer_use.protocol.observation.adapters.records import DriverObservationReply, DriverReadMetadata
from computer_use.session.events import observation_delivered
from unit_tests.test_d34_read_contracts import directory, setup


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
