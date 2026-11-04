# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Current delivery references travel with capture, without another driver call."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.transport import ConnectionPort, encode_target
from computer_use.protocol.execution import decode_capture_request


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("supported", [False, True])
async def test_capture_piggybacks_current_references_only_with_native_support(supported, monkeypatch):
    target = Binding("session", "instance", "window", "surface")

    async def capture(method, parameters, options):
        assert method == "capture"
        # Exercise the exact pre-send schema used by DriverConnection, so a
        # negotiated field cannot pass this test but fail before the native RPC.
        decoded = decode_capture_request(parameters)
        assert decoded["target"] == encode_target(target)
        return {"kind": "capture", "binding": decoded["target"], "image": {}}

    connection = SimpleNamespace(
        protocol_version=6,
        capabilities=("nativeCaptureRetention",) if supported else (),
        session_identity=(1, target.session),
        request=AsyncMock(side_effect=capture),
    )
    port = ConnectionPort(connection, HostPolicy())
    port._operations["read"] = (connection.session_identity, "read", None)
    monkeypatch.setattr("computer_use.native.transport.capture_metadata", lambda _: {"width": 10, "height": 20})
    for references in (frozenset({"shown-window", "pending-window"}), frozenset()):
        port.capture_references = references
        await port.capture("read", target, 1024, 500)
        method, parameters, options = connection.request.await_args.args
        assert method == "capture" and options == {"timeoutMs": 500}
        assert parameters["target"] == encode_target(target)
        if supported:
            assert parameters["retainedCaptures"] == sorted(references)
        else:
            assert "retainedCaptures" not in parameters
    assert connection.request.await_count == 2


@pytest.mark.parametrize("references", [[None], [1], "capture", [""]])
def test_capture_schema_rejects_invalid_retention_references(references):
    target = Binding("session", "instance", "window", "surface")
    with pytest.raises(ValueError):
        decode_capture_request({
            "operation": "read",
            "target": encode_target(target),
            "kind": "window",
            "maxDimension": 1024,
            "retainedCaptures": references,
        })
