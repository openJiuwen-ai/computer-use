"""The production encoder and native decoder share request conformance cases."""

import asyncio
import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from repository import ROOT as REPO_ROOT

from computer_use.native.contracts import (
    Actuator,
    ApplicationBinding,
    Binding,
    FocusMember,
    FocusScope,
    HostPolicy,
    ScrollActuator,
    SelectionDependency,
    VirtualReceiver,
)
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.execution import decodeInspectQuery

ROOT = REPO_ROOT
CASES = json.loads((ROOT / "tests/fixtures/native-inspect-queries.json").read_text())
PROBE = os.environ.get("COMPUTER_USE_NATIVE_QUERY_PROBE")


class NativeQueriesTests(unittest.TestCase):
    def test_shared_request_cases(self):
        for case in CASES:
            with self.subTest(case["name"]):
                if case["valid"]:
                    result = decodeInspectQuery(case["query"])
                    self.assertEqual(result, case["query"])
                    self.assertIsNot(result, case["query"])
                else:
                    with self.assertRaises(ValueError):
                        decodeInspectQuery(case["query"])

    def test_constraints_are_bounded_and_owned(self):
        with self.assertRaises(ValueError):
            decodeInspectQuery(dict(kind="inputContext", within=[str(n) for n in range(17)]))
        original = dict(kind="inputContext", within=["root"])
        result = decodeInspectQuery(original)
        result["within"].append("group")
        self.assertEqual(original["within"], ["root"])

    @unittest.skipUnless(PROBE, "run via test-macos for the compiled native decoder")
    def test_actual_port_queries_pass_the_native_decoder(self):
        class Captured(Exception):
            pass

        class RecordingConnection:
            protocol_version = 6
            sessionIdentity = ("test-session", 1)
            platform = "macos"
            capabilities = frozenset(
                (
                    "nativeControlParts",
                    "nativeSelectionBasis",
                    "nativeAdvertisedFields",
                    "nativeFollowRelations",
                    "nativeRelationPages",
                    "nativeVirtualFocus",
                    "nativeFocusMembers",
                    "nativeFocusScopes",
                )
            )

            async def request(self, method, params, options):
                self.query = params["query"]
                raise Captured

        async def encoded_queries():
            connection = RecordingConnection()
            port = ConnectionPort(connection, HostPolicy())
            port._operations["operation"] = (connection.sessionIdentity, "read-only query probe", None)
            queries = []
            for node, within, actuator in (
                ("node", (), None),
                (None, (), None),
                ("node", ("root", "group"), None),
                ("node", (), Actuator("editor")),
                ("node", (), Actuator("label", "controlPart")),
                ("node", (), ScrollActuator("page", "area", "bar")),
            ):
                try:
                    await port.inspect(
                        "operation",
                        Binding("test-session", "instance", "root", "surface", node),
                        1000,
                        within=within,
                        actuator=actuator,
                        basis=(SelectionDependency("observed", "label", ("AXRole",), True),)
                        if actuator is not None and actuator.relation == "controlPart"
                        else (),
                    )
                except Captured:
                    queries.append(connection.query)
            try:
                await port.inspect("operation", ApplicationBinding("test-session", "installation-observed"), 1000)
            except Captured:
                queries.append(connection.query)
            try:
                await port.captured_text(
                    "operation", Binding("test-session", None, None, None), "captured-field", 3, 4096, 1000
                )
            except Captured:
                queries.append(connection.query)
            node_target = Binding("test-session", "instance", "root", "surface", "node")
            try:
                await port.inspect(
                    "operation",
                    node_target,
                    1000,
                    receiver=FocusScope("container"),
                    basis=(SelectionDependency("observed", "node", ("AXRole",), False, ("AXParent",)),),
                )
            except Captured:
                queries.append(connection.query)
            try:
                await port.inspect(
                    "operation",
                    node_target,
                    1000,
                    receiver=FocusMember("label"),
                    basis=(SelectionDependency("observed", "label", ("AXRole",), True, ("AXParent",)),),
                )
            except Captured:
                queries.append(connection.query)
            for actuator in (None, Actuator("editor")):
                try:
                    await port.inspect(
                        "operation",
                        node_target,
                        1000,
                        actuator=actuator,
                        receiver=VirtualReceiver("popup", "owns"),
                        basis=(SelectionDependency("observed", "node", ("AXRole", "AXHasPopup"), False, ("AXOwns",)),),
                    )
                except Captured:
                    queries.append(connection.query)
            port._continuations["child-page"] = ("test-session", "surface", "node", "AXChildren", 128, 300)
            for page in (None, dict(cursor=None, limit=128), dict(cursor="child-page", limit=128)):
                try:
                    await port.nodes("operation", node_target, ["AXRole"], 1000, 40, 1000, children_page=page)
                except Captured:
                    queries.append(connection.query)
            port._continuations["row-page"] = ("test-session", "surface", "node", "AXRows", 128, 300)
            for cursor in (None, "row-page"):
                try:
                    await port.nodes(
                        "operation",
                        node_target,
                        ["AXRole"],
                        1000,
                        40,
                        1000,
                        relation_page=dict(property="AXRows", cursor=cursor, limit=128),
                    )
                except Captured:
                    queries.append(connection.query)
            try:
                await port.nodes(
                    "operation", node_target, ["AXRole", "AXRowCount"], 1000, 40, 1000, advertised_fields=["AXRowCount"]
                )
            except Captured:
                queries.append(connection.query)
            for kind in ("surfaces", "roots"):
                try:
                    await port.directory(
                        "operation",
                        Binding("test-session", None, None, None),
                        dict(kind=kind, application="running:app"),
                        1000,
                    )
                except Captured:
                    queries.append(connection.query)
            try:
                await port.nodes(
                    "operation", node_target, ["AXRole"], 40, 3, 1000, follow_relations=["AXVisibleCells", "AXRows"]
                )
            except Captured:
                queries.append(connection.query)
            return queries

        queries = asyncio.run(encoded_queries())
        encoded_count = len(queries)
        queries += [case["query"] for case in CASES]
        result = subprocess.run(
            [PROBE, "--stdio"],
            input="".join(json.dumps(q) + "\n" for q in queries),
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(
            [json.loads(line) for line in result.stdout.splitlines()],
            [True] * encoded_count + [case["valid"] for case in CASES],
        )


class NativeQueryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_operation_does_not_consume_query_inputs_or_send_a_request(self):
        consumed = []

        def fields():
            consumed.append("fields")
            yield "AXRole"

        peer = SimpleNamespace(protocol_version=6, sessionIdentity="session", request=AsyncMock())
        port = ConnectionPort(peer, HostPolicy())
        target = Binding("session", "instance", "window", "surface", "node")
        for method in (port.nodes, port.query_nodes):
            with self.assertRaisesRegex(RuntimeError, "connection ended"):
                await method("expired", target, fields(), 4, 2, 1000)
        self.assertEqual(consumed, [])
        peer.request.assert_not_awaited()

    async def test_collection_profile_keeps_one_wire_request_and_its_original_options(self):
        class CapturedQuery(Exception):
            pass

        trace = []

        async def request(method, params, options):
            if method == "begin":
                return dict(operation="read-operation", target=params["target"])
            if method == "finish":
                return dict(cleanup="complete")
            trace.append((method, params, options))
            raise CapturedQuery()

        def fields():
            trace.append("fields")
            yield from ("AXRole", "AXValue", "AXHelp", "AXSelectedText")

        peer = SimpleNamespace(
            protocol_version=6,
            platform="macos",
            sessionIdentity="session",
            capabilities=frozenset(
                ("nativeUiaTextLines", "nativeUiaTextContext", "nativeFollowRelations", "nativeAdvertisedFields")
            ),
            request=request,
        )
        port = ConnectionPort(peer, HostPolicy())
        target = Binding("session", "instance", "window", "surface", "node")
        opened = await port.begin_read(target, 1000)
        try:
            with self.assertRaises(CapturedQuery):
                await port.nodes(
                    opened.token,
                    target,
                    fields(),
                    4,
                    2,
                    900,
                    children_page=dict(cursor=None, limit=3),
                    advertised_fields=["AXHelp"],
                    follow_relations=["AXRows"],
                    focused_fields=["AXSelectedText"],
                    primary_fields=["AXValue"],
                )
        finally:
            await port.finish(opened.token)
        self.assertEqual(trace[0], "fields")
        self.assertEqual(len(trace), 2)
        method, payload, options = trace[1]
        self.assertEqual((method, payload["operation"], options), ("inspect", opened.token, {"timeoutMs": 900}))
        self.assertEqual(
            payload["query"],
            dict(
                kind="nodes",
                fields=["AXRole", "AXValue", "AXHelp", "AXSelectedText"],
                maxNodes=4,
                maxDepth=2,
                childrenPage=dict(cursor=None, limit=3),
                advertisedFields=["AXHelp"],
                followRelations=["AXRows"],
                focusedFields=["AXSelectedText"],
                primaryFields=["AXValue"],
            ),
        )
