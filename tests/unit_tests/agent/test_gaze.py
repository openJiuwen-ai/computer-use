"""Gaze service boundary and real agent delivery, without a camera or paid model."""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from computer_use_agent import AgentConfig, ComputerUseToolkit, McpConfig, create_agent
from .conftest import answer

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Gaze integration requires local macOS")


@pytest.fixture
def gaze_service(tmp_path):
    class Service:
        receipt = dict(
            selection_id="selection-1",
            pid=321,
            window_id=456,
            source="camera",
            confirmation_method="thumb",
            read_only=True,
            expires_in_seconds=120,
        )
        requests = []
        leased = False
        changed = False
        claim_started = threading.Event()
        finish_claim = threading.Event()
        delay_claim = False
        lose_claim = False
        redirect = False
        oversized = False
        refuse = False

    service = Service()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/api/target"
            assert self.headers["X-Session-Token"] == "private-test-token"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            service.requests.append(request)
            action = request["action"]
            status = 200
            if action == "claim":
                service.claim_started.set()
                if service.delay_claim:
                    service.finish_claim.wait(3)
                service.leased = True
                if service.lose_claim:
                    self.close_connection = True
                    return
                result = dict(service.receipt)
            elif action == "release":
                service.leased = False
                result = {"released": True}
            elif action == "observed":
                result = {"observed": True}
            else:
                result = dict(service.receipt)
                if service.changed:
                    result["window_id"] = 999
            if service.refuse:
                status, result, service.leased = 400, {"error": "private-test-token"}, False
            if service.redirect:
                status = 307
            self.send_response(status)
            self.send_header("Location", "/do-not-follow")
            self.end_headers()
            payload = b"x" * 17000 if service.oversized else json.dumps(result).encode()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    service.path = tmp_path / "connection.json"
    service.path.write_text(json.dumps(dict(url=f"http://127.0.0.1:{server.server_port}", token="private-test-token")))
    yield service
    service.finish_claim.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def toolkit_for(service, monkeypatch, capture=None):
    toolkit = ComputerUseToolkit(AgentConfig(gaze_connection_file=str(service.path)))
    calls = []

    async def call(name, inputs):
        calls.append((name, inputs))
        if capture:
            await capture()
        return CallToolResult(content=[TextContent(type="text", text="confirmed window snapshot")])

    monkeypatch.setattr(toolkit.connection, "call", call)
    return toolkit, toolkit.tools[-1], calls


def test_gaze_is_opt_in_and_host_local(gaze_service):
    assert [tool.card.name for tool in ComputerUseToolkit().tools] == ["computer_use", "computer_use_reset"]
    with pytest.raises(ValueError, match="local"):
        AgentConfig(gaze_connection_file=str(gaze_service.path), mcp=McpConfig(command="ssh"))


def test_cli_passes_gaze_configuration(gaze_service, tmp_path):
    from computer_use_agent.cli.config import build

    _, config = build(
        dict(
            model="test-only",
            api_key="test-only",
            api_base="http://127.0.0.1:1/v1",
            gaze_connection_file=str(gaze_service.path),
        ),
        workspace=tmp_path,
    )
    assert config.gaze_connection_file == str(gaze_service.path)


def test_confirmed_target_read_uses_fixed_code_and_releases(gaze_service, monkeypatch):
    _, tool, calls = toolkit_for(gaze_service, monkeypatch)
    result = asyncio.run(tool.invoke({}))
    assert result.success
    assert "confirmed window snapshot" in result.data["content"]
    assert len(calls) == 1 and calls[0][0] == "computer_use"
    code = calls[0][1]["code"]
    assert "app_for_window(computer, process_id=321, window_id=456)" in code
    assert 'read(view="both")' in code
    assert [r["action"] for r in gaze_service.requests] == ["claim", "validate", "validate", "release"]
    assert not gaze_service.leased
    assert len({r["owner"] for r in gaze_service.requests}) == 1


def test_read_requests_a_read_only_lease_and_refuses_a_legacy_mutable_claim(gaze_service, monkeypatch):
    gaze_service.receipt = {**gaze_service.receipt, "read_only": False}
    _, tool, calls = toolkit_for(gaze_service, monkeypatch)
    result = asyncio.run(tool.invoke({}))
    assert not result.success and not calls
    assert gaze_service.requests[0]["read_only"] is True


@pytest.mark.parametrize(
    "inputs", [{"code": "anything"}, {"expected_selection_id": ""}, {"expected_selection_id": True}, None]
)
def test_bad_inputs_never_claim(gaze_service, monkeypatch, inputs):
    _, tool, calls = toolkit_for(gaze_service, monkeypatch)
    assert not asyncio.run(tool.invoke(inputs)).success
    assert not gaze_service.requests and not calls


@pytest.mark.parametrize(
    "change",
    [
        dict(pid=True),
        dict(window_id=-1),
        dict(source="simulated_rehearsal"),
        dict(expires_in_seconds=0),
        dict(read_only="yes"),
        dict(confirmation_method="unknown"),
        dict(confirmation_method="voice", read_only=False),
        dict(confirmation_method="thumb", read_only=False),
    ],
)
def test_invalid_receipt_never_reads_and_releases(gaze_service, monkeypatch, change):
    gaze_service.receipt = {**gaze_service.receipt, **change}
    _, tool, calls = toolkit_for(gaze_service, monkeypatch)
    assert not asyncio.run(tool.invoke({"expected_selection_id": "selection-1"})).success
    assert not calls and not gaze_service.leased


def test_changed_selection_discards_capture(gaze_service, monkeypatch):
    async def capture():
        gaze_service.changed = True

    _, tool, calls = toolkit_for(gaze_service, monkeypatch, capture)
    result = asyncio.run(tool.invoke({}))
    assert not result.success and len(calls) == 1
    assert "confirmed window snapshot" not in str(result)
    assert not gaze_service.leased


def test_voice_requires_exact_id_and_acknowledges_only_after_capture(gaze_service, monkeypatch):
    gaze_service.receipt = {**gaze_service.receipt, "read_only": True, "confirmation_method": "voice"}
    _, tool, calls = toolkit_for(gaze_service, monkeypatch)
    assert not asyncio.run(tool.invoke({})).success
    assert not calls
    assert asyncio.run(tool.invoke({"expected_selection_id": "selection-1"})).success
    assert [r["action"] for r in gaze_service.requests][-5:] == ["claim", "validate", "validate", "observed", "release"]


def test_cancellation_during_delayed_claim_cleans_up_without_read(gaze_service, monkeypatch):
    gaze_service.delay_claim = True
    _, tool, calls = toolkit_for(gaze_service, monkeypatch)

    async def run():
        task = asyncio.create_task(tool.invoke({}))
        await asyncio.to_thread(gaze_service.claim_started.wait, 2)
        task.cancel()
        gaze_service.finish_claim.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not calls and not gaze_service.leased
    assert gaze_service.requests[-1]["action"] == "release"


def test_cancellation_during_capture_releases_and_parallel_read_is_refused(gaze_service, monkeypatch):
    started = asyncio.Event()

    async def capture():
        started.set()
        await asyncio.Event().wait()

    _, tool, calls = toolkit_for(gaze_service, monkeypatch, capture)

    async def run():
        task = asyncio.create_task(tool.invoke({}))
        await started.wait()
        assert not (await tool.invoke({})).success
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(calls) == 1 and not gaze_service.leased


@pytest.mark.parametrize("failure", ["lose_claim", "redirect", "oversized"])
def test_unknown_claim_outcome_blocks_retry_without_leaking_secrets(gaze_service, monkeypatch, failure):
    setattr(gaze_service, failure, True)
    _, tool, calls = toolkit_for(gaze_service, monkeypatch)
    first = asyncio.run(tool.invoke({}))
    second = asyncio.run(tool.invoke({}))
    assert not first.success and not second.success
    assert "private-test-token" not in str(first)
    assert len(gaze_service.requests) == 1 and not calls


def test_definite_refusal_can_be_retried_after_fresh_confirmation(gaze_service, monkeypatch):
    gaze_service.refuse = True
    _, tool, _ = toolkit_for(gaze_service, monkeypatch)
    result = asyncio.run(tool.invoke({}))
    assert not result.success and "private-test-token" not in str(result)
    gaze_service.refuse = False
    assert asyncio.run(tool.invoke({})).success


def test_engine_error_details_are_not_delivered_as_gaze_evidence(gaze_service, monkeypatch):
    async def capture():
        raise ValueError("private-test-token and private window text")

    _, tool, _ = toolkit_for(gaze_service, monkeypatch, capture)
    result = asyncio.run(tool.invoke({}))
    assert not result.success and "private-test-token" not in str(result)
    assert not gaze_service.leased


def test_toolkit_close_joins_pending_gaze_cleanup(gaze_service, monkeypatch):
    gaze_service.delay_claim = True
    toolkit, tool, calls = toolkit_for(gaze_service, monkeypatch)

    async def run():
        task = asyncio.create_task(tool.invoke({}))
        await asyncio.to_thread(gaze_service.claim_started.wait, 2)
        close = asyncio.create_task(toolkit.close())
        await asyncio.sleep(0)
        gaze_service.finish_claim.set()
        await close
        assert task.cancelled()
        assert not (await tool.invoke({})).success

    asyncio.run(run())
    assert not calls and not gaze_service.leased


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:5000",
        "http://localhost:5000",
        "http://example.com:5000",
        "http://127.0.0.1",
        "http://x@127.0.0.1:5000",
        "http://127.0.0.1:5000/x",
    ],
)
def test_connection_rejects_nonlocal_or_ambiguous_urls(gaze_service, url):
    gaze_service.path.write_text(json.dumps(dict(url=url, token="private-test-token")))
    with pytest.raises(ValueError):
        ComputerUseToolkit(AgentConfig(gaze_connection_file=str(gaze_service.path)))


def test_real_deepagent_receives_gaze_snapshot(gaze_service, monkeypatch, model_server, tmp_path):
    reply = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "gaze-call", "type": "function", "function": {"name": "read_gaze_target", "arguments": "{}"}}
        ],
    }
    server = model_server([reply, answer("Read the confirmed window")])

    async def run():
        async with create_agent(
            server.model(), config=AgentConfig(gaze_connection_file=str(gaze_service.path), workspace=str(tmp_path))
        ) as agent:

            async def capture(name, inputs):
                return CallToolResult(
                    content=[
                        TextContent(type="text", text="gaze-delivered-marker"),
                        ImageContent(
                            type="image",
                            mimeType="image/png",
                            data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9x8AAAAASUVORK5CYII=",
                        ),
                    ]
                )

            monkeypatch.setattr(agent.toolkit.connection, "call", capture)
            await agent.invoke({"query": "Read the window I confirmed with gaze"})

    asyncio.run(run())
    assert not server.replies
    follow_up = json.dumps(server.requests[1]["messages"])
    assert "gaze-delivered-marker" in follow_up and "data:image/png;base64" in follow_up
    assert "private-test-token" not in json.dumps(server.requests)
