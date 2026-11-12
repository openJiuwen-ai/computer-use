# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep Rust-owned AX replies behind the existing JSONL connection contract."""

import asyncio
import json
from io import BytesIO

import pytest

from computer_use import diagnostics
from computer_use.errors import ComputerUseError
from computer_use.native import connection as transport
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.interaction.read.accessibility._native import require_read_engine
from computer_use.interaction.read.accessibility.frame import NativeReadCapture
from computer_use.native.transport import ConnectionPort
from computer_use_mcp.worker_transport import WorkerMessageWriter, read_message


def _observation(platform="macos"):
    def string(value):
        return {"nativeType": "CFString", "data": value}

    source, element, role_key, role, title_key = {
        "macos": ("macos.ax", "AXUIElement", "AXRole", "AXWindow", "AXTitle"),
        "windows": ("windows.uia", "UIAElement", "ControlType", "ControlType.Window", "Name"),
        "linux": ("linux.atspi", "Accessible", "role", "frame", "name"),
    }[platform]
    return {
        "kind": "observation",
        "session": "session",
        "platform": platform,
        "roots": ["root"],
        "nodes": [
            {
                "id": "root",
                "kind": "element",
                "nativeType": element,
                "source": source,
                "properties": [
                    {"key": [source, role_key], "value": string(role)},
                    {"key": [source, title_key], "value": string("Settings")},
                ],
            }
        ],
        "read": {
            "intervalNs": [0, 1000000],
            "details": [["surface", string("surface")], ["observation_id", string("capture")]],
        },
    }


class ReplyConnection(transport.Connection):
    def __init__(self, payload, *, platform="macos", wrong_request=False):
        self.invalidated = False
        super().__init__(self._invalidate)
        self._hello = dict(
            version=6,
            session="session",
            platform=platform,
            maxTimeoutMs=30000,
            capabilities=("nativeOperations", "nativeUnitReadinessV2"),
        )
        self.payload = payload
        self.wrong_request = wrong_request
        self.sent = []

    def _invalidate(self):
        self.invalidated = True

    async def _connect(self):
        return None

    def _write(self, value):
        self.sent.append(value)
        response = dict(
            version=value["version"], session=value["session"], id=value["id"] + self.wrong_request, **self.payload
        )
        line = json.dumps(response, ensure_ascii=True).encode() + b"\r\n"
        split = len(line) // 2
        self._receive(line[:split])
        self._receive(line[split:])


def _request(query):
    return dict(operation="operation", target={}, query=query)


def _nodes():
    return dict(kind="nodes", fields=["AXRole"], maxNodes=1, maxDepth=0)


@pytest.fixture
def native_read():
    pytest.importorskip("computer_use.interaction.read.accessibility._ax_read")
    return require_read_engine()


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_ax_read_consumes_jsonl_in_rust_and_preserves_diagnostics(native_read, encoded, platform, monkeypatch):
    expected = _observation(platform)

    def forbid_python_tree(_line):
        pytest.fail("Python decoded the accessibility graph")

    monkeypatch.setattr(transport, "_decode_json_line", forbid_python_tree)

    async def run():
        connection = ReplyConnection(dict(result=expected), platform=platform)
        port = ConnectionPort(connection, HostPolicy())
        port._operations["operation"] = (connection.session_identity, None)
        records = []
        handler = records.append
        stream = BytesIO()
        writer = WorkerMessageWriter(stream)
        diagnostics.set_diagnostic_handler(handler)
        if encoded:
            diagnostics._register_observation_diagnostic(
                handler,
                lambda event, body: writer.send_observation(dict(diagnostic=event, executionId=1), body),
            )
        try:
            result = await port.nodes(
                "operation",
                Binding("session", None, None, "surface", node="root"),
                ["AXRole"],
                1,
                0,
                30000,
                scope_kind="window",
            )
            assert isinstance(result, NativeReadCapture)
            assert result.capture.backend == platform
            assert result.capture.node_view("root", ["role", "name"]) == dict(role="window", name="Settings")
            assert result.capture.source_tokens == ["root"]
            assert "decode_response" not in connection.sent[0]["params"]
            assert connection._pending is None
            assert not connection.invalidated
            if encoded:
                reader = asyncio.StreamReader()
                reader.feed_data(stream.getvalue())
                reader.feed_eof()
                delivered = await read_message(reader)
                assert delivered.event["kind"] == "command_end"
                assert delivered.event["executionId"] == 1
                assert json.loads(bytes(delivered.result_json)) == expected
            else:
                event = next(record for record in records if record["kind"] == "command_end")
                assert event["result"] == expected
        finally:
            diagnostics.set_diagnostic_handler(None)

    asyncio.run(run())


def test_read_engine_retains_envelope_and_backend_error_contract(native_read):
    async def run():
        connection = ReplyConnection(dict(result=_observation()), wrong_request=True)
        with pytest.raises(ComputerUseError, match="protocol: unexpected response id"):
            await connection.request("inspect", _request(_nodes()), decode_response=native_read.open_read_envelope)
        assert connection.invalidated

        malformed = ReplyConnection(dict(result=float("nan")))
        with pytest.raises(ComputerUseError, match="protocol: invalid JSON constant NaN"):
            await malformed.request("inspect", _request(_nodes()), decode_response=native_read.open_read_envelope)
        assert malformed.invalidated

        backend_error = dict(code="stale_target", target="root", effect="none", detail="root closed", next="read again")
        connection = ReplyConnection(dict(error=backend_error))
        with pytest.raises(ComputerUseError, match="root closed") as error:
            await connection.request("inspect", _request(_nodes()), decode_response=native_read.open_read_envelope)
        assert error.value.effect == "none"
        assert not connection.invalidated

    asyncio.run(run())


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_input_context_uses_ordinary_transport(platform):
    backend_error = dict(code="stale_target", target="root", effect="none", detail="root closed", next="read again")
    connection = ReplyConnection(dict(error=backend_error), platform=platform)
    with pytest.raises(ComputerUseError, match="root closed"):
        asyncio.run(connection.request("inspect", _request(dict(kind="inputContext", within=[]))))
    assert not connection.invalidated


def test_windows_snapshot_retains_outside_window_scope(native_read):
    raw = _observation("windows")
    raw["nodes"][0]["properties"].append({
        "key": ["windows.uia", "UIA.ReferenceScopeEvidence"],
        "value": {
            "nativeType": "provider",
            "data": {"fields": [
                [name, {"nativeType": "String", "data": value}]
                for name, value in (
                    ("ownership", "outside"),
                    ("input", "observationOnly"),
                    ("detail", "Outside the fixed window."),
                )
            ]},
        },
    })
    frame = native_read.open_read_frame("json", json.dumps(raw).encode(), 8192)
    capture = frame.capture("window", [])
    assert capture.node_view("root", ["supplements"])["supplements"] == [
        {"key": "observation_scope", "value": {
            "ownership": "outside", "input": "observationOnly", "detail": "Outside the fixed window.",
        }}
    ]
    rows, _ = capture.with_aliases({"root": "0"}).retained_bindings(
        ("session", None, "window", "surface", "root"), False
    )
    assert rows[0]["native_window_outside"]


def test_linux_read_only_field_remains_a_control_value_in_details(native_read):
    raw = _observation("linux")
    raw["nodes"][0]["properties"].append({
        "key": ["linux.atspi", "readOnly"], "value": {"nativeType": "Boolean", "data": False},
    })
    frame = native_read.open_read_frame("json", json.dumps(raw).encode(), 8192)
    capture = frame.capture("window", [])
    assert capture.node_view("root", ["control_value"]) == {"control_value": {"read_only": False}}
    details = capture.with_aliases({"root": "0"}).element_details(
        "0", ("session", None, "window", "surface", "root"),
    )
    assert '"control_value.read_only": false' in details
