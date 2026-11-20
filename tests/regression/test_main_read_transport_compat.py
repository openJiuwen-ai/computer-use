# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for native read transport and startup identity."""

import asyncio
import base64
import copy
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.accessibility.frame import NativeReadFrame
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.element_details import CapturedElementDetails
from computer_use.interaction.read.presentation.rendering.document import prepare_document, render_document
from computer_use.session.detail_pages import CapturedDetailPages
from computer_use.interaction.read.presentation.rendering.document.model import RustRenderedTree
from computer_use.native import connection as native_connection
from computer_use.native import driver_exec
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.schema import SchemaError
from computer_use.protocol.observation.adapters.wire import DecodedDriverObservation, DriverWire
from computer_use.runtime_provenance import startup_provenance
from computer_use.interaction.controller import SessionController
from computer_use.interaction.query.instances import scope as instance_scope
from computer_use.interaction.query.model import FindLease, FindPlan
from computer_use.interaction.query.passes.build_lookup_snapshot import stage as build_lookup_snapshot
from computer_use.interaction.query.pipeline import FindFacts
from computer_use.interaction.read.acquisition.facts.model import FactProfile, FactReadRequest, FactScope
from computer_use.interaction.read.acquisition.facts.passes.collect_native_facts import create as collect_facts
from computer_use.interaction.read.acquisition.facts.passes.normalize_native_facts import create as normalize_facts
from computer_use.interaction.read.acquisition.reader import CapturedField, ChildContinuation, NativeReader
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.element.agent import AgentElementReadPipeline
from computer_use.interaction.requests import FindElement
from computer_use.native.lifecycle import OperationLease
from computer_use.interaction.read.accessibility.frame import NativeReadCapture
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.pipeline import Control


SESSION = "00000000-0000-0000-0000-000000000001"
FINISH = {"operation": "fixture"}
CAPS = ("cancel", "requestDedup", "nativeOperations", "nativeDirectory")
HELLO = {"version": 6, "id": 0, "method": "hello", "params": {"globalInputAllowed": False}}
DRIVER = Path(__file__).resolve()


DRIVER_NAME = "driver.exe" if os.name == "nt" else "driver"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def envelope(request_id, result=None, error=None, *, version=6):
    body = {"error": error} if error is not None else {"result": result}
    return encoded({"version": version, "session": SESSION, "id": request_id, **body}) + b"\r\n"


def exchange(request_id, method, params, response, *, split=None, cancel=False, late=False):
    request = {"version": 6, "session": SESSION, "id": request_id, "method": method, "params": params}
    return {
        "request": request,
        "response": response.hex(),
        "split": split,
        "cancel": cancel,
        "replyAfterCancel": late,
    }


def prepare_driver(directory, capabilities, exchanges):
    (directory / "spec.json").write_text(
        json.dumps({"session": SESSION, "capabilities": capabilities, "exchanges": exchanges}), encoding="utf-8"
    )
    return {"command": sys.executable, "args": [str(DRIVER), str(directory)]}


def request_ledger(directory):
    return [json.loads(line) for line in (directory / "requests.jsonl").read_bytes().splitlines()]


@asynccontextmanager
async def wire_connection(directory, capabilities, exchanges, *, diagnostic=None):
    invalidations = []
    connection = native_connection.Connection(
        lambda: invalidations.append(connection.session_identity),
        driver=prepare_driver(directory, capabilities, exchanges),
        allow_global_input=False,
    )
    set_diagnostic_handler(diagnostic)
    try:
        yield connection, invalidations
    finally:
        try:
            if connection.session_identity is not None:
                connection.close()
            await connection.wait_closed()
        finally:
            set_diagnostic_handler(None)
    expected = [HELLO]
    for step in exchanges:
        expected.append(step["request"])
        if step["cancel"]:
            expected.append({"version": 6, "session": SESSION, "cancel": step["request"]["id"]})
    assert request_ledger(directory) == expected


@pytest.mark.anyio
@pytest.mark.parametrize("tail", ["tail", 7])
async def test_command_start_mutations_preserve_wire_values_and_order(tmp_path, tail):
    params = {
        "operation": "fixture",
        "reorder": "initial text",
        "nested": {"values": ["old", 2, 3], "removed": {"stale": True}},
        "removed": False,
    }
    expected = {
        "operation": "fixture",
        "nested": {"values": ["changed", {"added": None}], "added": True},
        "reorder": ["new shape"],
        str(tail): "new field",
    }
    seen = []

    def on_event(event):
        if event["kind"] == "command_start":
            actual = event["params"]
            seen.append(actual is params)
            del actual["reorder"], actual["removed"], actual["nested"]["removed"]
            actual["nested"]["values"][:] = ["changed", {"added": None}]
            actual["nested"]["added"] = True
            actual["reorder"] = ["new shape"]
            actual[tail] = "new field"

    step = exchange(1, "finish", expected, envelope(1, {"cleanup": "complete"}))
    async with wire_connection(tmp_path, CAPS, [step], diagnostic=on_event) as (connection, _):
        assert await connection.request("finish", params) == {"cleanup": "complete"}

    assert seen == [True]
    written = request_ledger(tmp_path)[-1]["params"]
    assert list(written) == list(expected)
    assert list(written["nested"]) == list(expected["nested"])


async def wait_for(path, pending=None):
    async with asyncio.timeout(8):
        while not path.exists():
            if pending is not None and pending.done():
                # Re-raise the actual transport fault instead of a fixture timeout.
                pending.result()
                raise AssertionError("Request completed before the fake driver reached its pause")
            await asyncio.sleep(0.005)


def terminal_events(events, request_id=1):
    return [
        event
        for event in events
        if event.get("id") == request_id and event["kind"] in ("command_end", "command_diagnostic_end")
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("backend_error", [False, True])
async def test_receive_reports_decode_costs_and_complete_frame_bytes(tmp_path, backend_error):
    error = {
        "code": "execution_failed",
        "target": "finish",
        "effect": "none",
        "detail": "记录的失败",
        "next": "inspect the result",
    }
    data = envelope(1, {"cleanup": "complete"}, error if backend_error else None)
    events = []
    terminal_order = []

    def handler(event):
        events.append(event)
        if event["kind"] == "command_end":
            terminal_order.append("end")
            asyncio.get_running_loop().call_soon(terminal_order.append, "next turn")
            raise RuntimeError("diagnostic sink failed")
        if event["kind"] == "command_diagnostic_end":
            terminal_order.append("metrics")

    steps = [exchange(1, "finish", FINISH, data, split=17)]
    async with wire_connection(tmp_path, CAPS, steps, diagnostic=handler) as (connection, invalidated):
        pending = asyncio.create_task(connection.request("finish", FINISH))
        try:
            await wait_for(tmp_path / "paused-1", pending)
            assert not pending.done() and not terminal_events(events)
            (tmp_path / "continue-1").touch()
            if backend_error:
                with pytest.raises(ComputerUseError, match="记录的失败") as caught:
                    await pending
                assert (caught.value.code, caught.value.effect) == ("execution_failed", "none")
            else:
                assert await pending == {"cleanup": "complete"}
        finally:
            (tmp_path / "continue-1").touch()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        assert not invalidated and connection.session_identity == (0, SESSION)
    end, metrics = terminal_events(events)
    assert [end["kind"], metrics["kind"]] == ["command_end", "command_diagnostic_end"]
    assert terminal_order == ["end", "metrics", "next turn"]
    assert end["traceId"] == metrics["traceId"] and metrics["method"] == "finish"
    assert metrics["frameBytes"] == len(data) - 2
    assert all(
        math.isfinite(metrics[key]) and metrics[key] >= 0
        for key in (
            "jsonDecodeMs",
            "protocolDecodeMs",
            "diagnosticMs",
        )
    )


@pytest.mark.anyio
async def test_invalid_backend_context_keeps_structured_schema_cause(tmp_path):
    failure = {
        "code": "execution_failed",
        "target": "finish",
        "effect": "none",
        "detail": "input interrupted",
        "next": "observe",
        "context": {"after": "OVERFLOW", "scope": "window", "window": None},
    }
    data = envelope(1, error=failure).replace(b'"OVERFLOW"', b"1e400")
    steps = [exchange(1, "finish", FINISH, data, cancel=True)]
    async with wire_connection(tmp_path, CAPS, steps) as (connection, invalidated):
        with pytest.raises(ComputerUseError) as caught:
            await connection.request("finish", FINISH)
        cause = caught.value.__cause__
        assert isinstance(cause, SchemaError)
        assert cause.issues == [
            {
                "expected": "string",
                "code": "invalid_type",
                "received": "Infinity",
                "path": ["after"],
                "message": "Invalid input: expected string, received Infinity",
            }
        ]
        assert invalidated == [None]


@pytest.mark.anyio
async def test_late_reply_cannot_complete_a_cancelled_request_when_diagnostic_raises(tmp_path):
    events = []

    def handler(event):
        events.append(event)
        if event["kind"] == "command_end":
            raise RuntimeError("diagnostic sink failed during cancellation")

    steps = [exchange(1, "finish", FINISH, envelope(1, {"cleanup": "complete"}), cancel=True, late=True)]
    async with wire_connection(tmp_path, CAPS, steps, diagnostic=handler) as (connection, invalidated):
        pending = asyncio.create_task(connection.request("finish", FINISH))
        try:
            await wait_for(tmp_path / "paused-1", pending)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await wait_for(tmp_path / "late-1")
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        assert connection.session_identity is None and invalidated == [None]
    (end,) = terminal_events(events)
    assert end["stage"] == "connection" and end["status"] == "error"


@pytest.mark.anyio
@pytest.mark.parametrize("malformed_json", [False, True])
async def test_invalid_ordinary_json_or_schema_keeps_connection_failure(tmp_path, malformed_json):
    if malformed_json:
        method, params = "inspect", {"operation": "fixture", "query": {"kind": "surfaces", "application": "fixture"}}
        body = b'{"broken":'
        with pytest.raises(json.JSONDecodeError) as original:
            json.loads(body)
        expected = ("execution_failed", "none", str(original.value))
        data = body + b"\n"
    else:
        method, params = "finish", FINISH
        expected = ("execution_unknown", "possible", "Invalid option")
        data = envelope(1, {"cleanup": "invalid"})
    events = []
    steps = [exchange(1, method, params, data, cancel=True)]
    async with wire_connection(tmp_path, CAPS, steps, diagnostic=events.append) as (connection, invalidated):
        with pytest.raises(ComputerUseError) as caught:
            await connection.request(method, params)
        code, effect, detail = expected
        assert (caught.value.code, caught.value.effect) == (code, effect)
        assert detail in caught.value.detail
        assert invalidated == [None] and connection.session_identity is None
    (end,) = terminal_events(events)
    assert end["stage"] == "connection" and end["status"] == "error"


@pytest.mark.anyio
async def test_preflight_keeps_options_and_validation_in_the_original_order(tmp_path):
    validation = []

    def diagnostic(event):
        if event["kind"] == "local_error":
            validation.append(sys.exception())

    step = exchange(1, "finish", FINISH, envelope(1, {"cleanup": "complete"}))
    async with wire_connection(tmp_path, CAPS, [step], diagnostic=diagnostic) as (connection, invalidated):
        with pytest.raises(AttributeError, match="get"):
            await connection.request("unknown", {}, object())
        assert not validation
        with pytest.raises(ComputerUseError) as failure:
            await connection.request("inspect", None)
        cause = failure.value.__cause__
        assert isinstance(cause, TypeError) and str(cause) == "'NoneType' object is not a mapping"
        assert validation == [cause]
        assert failure.value.__context__ is cause and failure.value.__suppress_context__
        assert not (tmp_path / "requests.jsonl").exists()
        assert await connection.request("finish", FINISH) == {"cleanup": "complete"}
        assert not invalidated


@pytest.mark.anyio
async def test_json_null_reply_uses_method_schema_before_closing(tmp_path):
    events = []
    step = exchange(1, "finish", FINISH, envelope(1, None), cancel=True)
    async with wire_connection(tmp_path, CAPS, [step], diagnostic=events.append) as (connection, invalidated):
        with pytest.raises(ComputerUseError) as failure:
            await connection.request("finish", FINISH)
        assert isinstance(failure.value.__cause__, SchemaError)
        assert "expected object, received null" in failure.value.detail
        assert invalidated == [None] and connection.session_identity is None
    (end,) = terminal_events(events)
    assert end["stage"] == "connection" and end["status"] == "error"


@pytest.mark.anyio
async def test_connection_hello_fields_and_retained_identity(tmp_path):
    events = []
    step = exchange(1, "finish", FINISH, envelope(1, {"cleanup": "complete"}))
    async with wire_connection(tmp_path, CAPS, [step], diagnostic=events.append) as (connection, invalidated):
        first = await connection.connect()
        second = await connection.connect()
        (hello,) = (event for event in events if event["kind"] == "protocol_hello")
        assert first is not second
        assert all(first[field] is second[field] is hello[field] for field in first)
        identity = connection.session_identity
        assert identity[1] is connection.session is first["session"]
        assert connection.platform is first["platform"]
        first_caps, second_caps = connection.capabilities, connection.capabilities
        assert first_caps == second_caps and first_caps is not second_caps
        assert all(any(cap is other for other in second_caps) for cap in first_caps)

        class EquivalentIdentity:
            def __eq__(self, value):
                return value == identity

        retained = EquivalentIdentity()
        with connection.retain_session(retained):
            assert connection._retained_session.get() is retained
            assert await connection.request("finish", FINISH) == {"cleanup": "complete"}
        assert connection._retained_session.get() is None and not invalidated


@pytest.mark.anyio
async def test_retained_context_observes_close_during_comparison(tmp_path):
    async with wire_connection(tmp_path, CAPS, []) as (connection, invalidated):
        await connection.connect()
        identity = connection.session_identity

        class ClosesConnection:
            def __eq__(self, value):
                assert value == identity
                connection.close()
                return True

        with pytest.raises(ComputerUseError) as stale, connection.retain_session(ClosesConnection()):
            pytest.fail("a scope must not enter after its driver closed during comparison")
        assert stale.value.code == "stale_target"
        assert connection.session_identity is None
        assert connection._retained_session.get() is None
        assert invalidated == [None]


@pytest.mark.anyio
async def test_retained_request_observes_context_change_during_comparison(tmp_path):
    async with wire_connection(tmp_path, CAPS, []) as (connection, invalidated):
        await connection.connect()
        identity = connection.session_identity
        comparisons = 0

        class ReplacesRetainedIdentity:
            def __eq__(self, value):
                nonlocal comparisons
                comparisons += 1
                if comparisons == 2:
                    connection._retained_session.set((identity[0] + 1, identity[1]))
                return value == identity

        with connection.retain_session(ReplacesRetainedIdentity()):
            with pytest.raises(ComputerUseError) as stale:
                await connection.request("finish", FINISH)
            assert stale.value.code == "stale_target"
            assert connection.session_identity == identity
        assert comparisons == 2
        assert connection._retained_session.get() is None and not invalidated


def test_retained_context_requires_live_identity_after_comparison():
    connection = native_connection.Connection(lambda: None)

    class MatchesMissing:
        def __eq__(self, value):
            return value is None

    try:
        raise LookupError("already handling")
    except LookupError as active:
        with pytest.raises(ComputerUseError) as stale, connection.retain_session(MatchesMissing()):
            pytest.fail("a retained scope must have a live generation")
        assert stale.value.code == "stale_target"
        assert stale.value.__context__ is active
        assert connection._retained_session.get() is None


@pytest.mark.anyio
async def test_jsonl_preserves_surrogate_session_and_reply_text(tmp_path, monkeypatch):
    session = "session\ud800"
    monkeypatch.setattr(sys.modules[__name__], "SESSION", session)
    target = {"session": session, "application": "app"}
    params = {"operation": "fixture", "target": target, "query": {"kind": "appChanges", "application": "app"}}
    raw = {
        "kind": "appChanges",
        "session": session,
        "application": "app",
        "incarnation": None,
        "token": None,
        "available": False,
        "notifications": ["雪\ud800\x00🧪", "\x00host-wire-text:literal", "\udfff"],
    }

    def wire_reply(request_id, value):
        data = {"version": 6, "session": session, "id": request_id, "result": value}
        return json.dumps(data, ensure_ascii=True).encode("ascii") + b"\n"

    steps = [
        exchange(1, "inspect", params, wire_reply(1, raw)),
        exchange(2, "finish", FINISH, wire_reply(2, {"cleanup": "complete"})),
    ]
    async with wire_connection(tmp_path, CAPS, steps) as (connection, invalidated):
        assert await connection.request("inspect", params) == raw
        assert connection.session == session and not invalidated
        with connection.retain_session(connection.session_identity):
            assert await connection.request("finish", FINISH) == {"cleanup": "complete"}


@pytest.mark.parametrize("semantic_urls", [False, True])
def test_callback_frame_preserves_original_index_and_cpython_json_spelling(semantic_urls):
    row = {"id": "root"}
    raw = {"nodes": [row]}
    native = Mock(has_semantic_urls=semantic_urls)
    native.raw_result.return_value = raw
    frame = NativeReadFrame(native)
    assert frame.raw_result() is frame.raw_result() is raw
    native.raw_result.assert_called_once()
    row["id"] = "renamed"
    row["proof"] = ["\ud800", 1e-7, -0.0]
    raw["nodes"] = [row, {"id": "injected"}]
    assert frame.capture("window", ()) is native.capture.return_value
    expected = (
        rb'{"nodes":[{"id":"renamed","proof":["\ud800",1e-07,-0.0]},{"id":"injected"}]}'
        if semantic_urls
        else rb'[{"id":"root","proof":["\ud800",1e-07,-0.0]}]'
    )
    native.capture.assert_called_once_with("window", (), semantic_body=expected)


@pytest.mark.parametrize("bad_value", [float("nan"), {2: "not a string key"}, ("tuple",), object()])
def test_callback_frame_rejects_non_json_edits_before_consuming_capture(bad_value):
    native = Mock(has_semantic_urls=False)
    native.raw_result.return_value = {"nodes": [{"id": "root", "proof": bad_value}]}
    frame = NativeReadFrame(native)
    frame.raw_result()
    with pytest.raises(ValueError, match="protocol: diagnostic observation edits are not JSON values"):
        frame.capture("window", ())
    native.capture.assert_not_called()


def checkout(tmp_path):
    root = tmp_path / "source"
    package = root / "computer_use"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text("VERSION = 1\n")
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=Provenance Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    driver = tmp_path / DRIVER_NAME
    driver.write_bytes(b"driver version one\n")
    driver.chmod(0o700)
    return module, driver


def test_startup_fingerprints_dirty_contents_even_when_size_and_mtime_stay_the_same(tmp_path):
    module, driver = checkout(tmp_path)
    run = subprocess.run

    def run_git(command, **options):
        return run(command, **(options | {"timeout": 10}))

    # Keep real Git and file reads while isolating this hash test from startup timing.
    git = SimpleNamespace(
        run=run_git,
        PIPE=subprocess.PIPE,
        DEVNULL=subprocess.DEVNULL,
        SubprocessError=subprocess.SubprocessError,
    )
    with (
        patch.object(sys.modules["computer_use"], "__file__", str(module)),
        patch("computer_use.runtime_provenance.time", SimpleNamespace(monotonic=lambda: 0.0)),
        patch("computer_use.runtime_provenance.subprocess", git),
    ):
        clean = startup_provenance(str(driver))
        assert clean["sdkModulePath"] == str(module)
        assert clean["source"]["status"] == "complete" and clean["source"]["dirty"] is False
        module.write_text("VERSION = 2\n")
        first = startup_provenance(str(driver))
        stamp = module.stat()
        module.write_text("VERSION = 3\n")
        os.utime(module, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        second = startup_provenance(str(driver))
        assert first["source"]["head"] == second["source"]["head"] == clean["source"]["head"]
        assert first["source"]["dirty"] and second["source"]["dirty"]
        assert first["source"]["dirtyContentSha256"] != second["source"]["dirtyContentSha256"]
        untracked = module.parent / "new_module.py"
        untracked.write_text("new source\n")
        assert startup_provenance(str(driver))["source"]["dirtyContentSha256"] != second["source"]["dirtyContentSha256"]


def test_driver_hash_is_a_startup_snapshot_and_missing_git_does_not_block_it(tmp_path):
    module = tmp_path / "computer_use" / "__init__.py"
    module.parent.mkdir()
    module.write_text("# installed package without Git metadata\n")
    driver = tmp_path / DRIVER_NAME
    original = b"original executable\n"
    driver.write_bytes(original)
    driver.chmod(0o700)
    with patch.object(sys.modules["computer_use"], "__file__", str(module)):
        started = startup_provenance(str(driver))
        driver.write_bytes(b"subsequent executable\n")
        later = startup_provenance(str(driver))
    assert started["source"]["status"] == "unavailable"
    assert started["driver"]["captureStage"] == "beforeSpawn"
    assert started["driver"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert started["driver"]["sha256"] != later["driver"]["sha256"]
    assert started["driver"]["path"] == str(driver.resolve())


def test_installed_sdk_in_ignored_venv_cannot_claim_its_enclosing_checkout_head(tmp_path):
    module, driver = checkout(tmp_path)
    root = module.parent.parent
    (root / ".gitignore").write_text(".venv/\n")
    installed = root / ".venv" / "computer_use" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("# different installed SDK\n")
    with patch.object(sys.modules["computer_use"], "__file__", str(installed)):
        evidence = startup_provenance(str(driver))
    assert evidence["sdkModulePath"] == str(installed)
    assert evidence["source"]["status"] == "unavailable"
    assert evidence["source"]["head"] is None


@pytest.mark.skipif(os.name == "nt", reason="Windows launches directly without inherited POSIX FDs")
def test_packaged_file_shim_keeps_fd_contract_without_importing_sdk_package(tmp_path):
    package = tmp_path / "installed package" / "computer_use"
    native = package / "native"
    native.mkdir(parents=True)
    for initializer in (package / "__init__.py", native / "__init__.py"):
        initializer.write_text("raise AssertionError('The FD shim must not initialize the SDK package')\n")
    shim = native / "driver_exec.py"
    shim.write_bytes(Path(driver_exec.__file__).read_bytes())
    preview_read, preview_write = os.pipe()
    lifetime_read, lifetime_write = os.pipe()
    try:
        subprocess.run(
            [
                sys.executable,
                "-I",
                str(shim),
                str(preview_write),
                str(lifetime_write),
                sys.executable,
                "-I",
                "-c",
                "import os; os.write(3, b'preview'); os.write(4, b'lifetime')",
            ],
            check=True,
            capture_output=True,
            pass_fds=(preview_write, lifetime_write),
        )
        assert os.read(preview_read, 7) == b"preview"
        assert os.read(lifetime_read, 8) == b"lifetime"
    finally:
        for descriptor in (preview_read, preview_write, lifetime_read, lifetime_write):
            os.close(descriptor)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["appChanges", "waitAppChange", "readChange"])
async def test_change_replies_are_admitted_before_command_success(tmp_path, kind):
    query = {"kind": kind}
    raw = {"kind": kind, "session": SESSION}
    if kind == "readChange":
        query.update(after="old", timeoutMs=50)
        raw.update(surface="window", after="old", reason="event", events=["focus"], elapsedMs=10)
        target = {
            "session": SESSION,
            "instance": "app",
            "rootWindow": "root",
            "surface": "window",
            "node": None,
            "capture": None,
        }
        invalid = {**raw, "events": ["focus", "focus"]}
        detail = "protocol: wait event hints contradict their wake reason"
    else:
        query["application"] = "app"
        raw.update(application="app", incarnation="observer", token="new")
        target = {"session": SESSION, "application": "app"}
        if kind == "waitAppChange":
            query.update(after="old", timeoutMs=50)
            raw.update(reason="event", events=["window-opened"])
            invalid = {**raw, "token": None, "events": ["window-opened", "window-opened"]}
            detail = "protocol: application event hints contradict their wake reason"
        else:
            raw.update(available=True, notifications=["window-opened"])
            invalid = {**raw, "incarnation": None}
            detail = "protocol: available application changes require an observer incarnation and token"
    params = {"operation": "fixture", "target": target, "query": query}
    steps = [
        exchange(1, "inspect", params, envelope(1, raw)),
        exchange(2, "inspect", params, envelope(2, invalid), cancel=True),
    ]
    events = []
    async with wire_connection(tmp_path, CAPS, steps, diagnostic=events.append) as (connection, invalidated):
        assert await connection.request("inspect", params) == raw
        assert not invalidated
        with pytest.raises(ComputerUseError) as failure:
            await connection.request("inspect", params)
        assert (failure.value.code, failure.value.effect) == ("execution_unknown", "possible")
        assert type(failure.value.__cause__) is ValueError
        assert str(failure.value.__cause__) == detail
        assert invalidated == [None] and connection.session_identity is None
    (end,) = terminal_events(events, request_id=2)
    assert end["stage"] == "connection" and end["status"] == "error"


@pytest.mark.anyio
async def test_launch_reply_admits_primitive_before_checking_launch_unit(tmp_path):
    target = {"session": SESSION, "application": "app"}
    params = {"operation": "fixture", "target": target, "query": {"kind": "launch"}}
    primitive = {
        "id": "launch",
        "unit": "launch_application",
        "channel": "object",
        "supported": "yes",
        "readiness": "yes",
        "pending": [],
        "backgroundPreparation": "yes",
    }
    raw = {"kind": "launch", "binding": target, "observed": "seen", "targetLabel": "app", "primitives": [primitive]}
    invalid = {**raw, "primitives": [{**primitive, "unit": "native_scroll", "channel": "pointer"}]}
    steps = [
        exchange(1, "inspect", params, envelope(1, raw)),
        exchange(2, "inspect", params, envelope(2, invalid), cancel=True),
    ]
    events = []
    async with wire_connection(
        tmp_path, (*CAPS, "nativeUnitReadinessV2", "nativeLaunch"), steps, diagnostic=events.append
    ) as (connection, invalidated):
        assert await connection.request("inspect", params) == raw
        assert not invalidated
        with pytest.raises(ComputerUseError) as failure:
            await connection.request("inspect", params)
        assert (failure.value.code, failure.value.effect) == ("execution_unknown", "possible")
        assert type(failure.value.__cause__) is ValueError
        assert str(failure.value.__cause__) == "protocol: native unit cannot implement the declared channel"
        assert invalidated == [None] and connection.session_identity is None
    (end,) = terminal_events(events, request_id=2)
    assert end["stage"] == "connection" and end["status"] == "error"


def test_captured_text_reply_keeps_missing_field_precedence():
    from computer_use.protocol.execution import decode_execution_result
    from computer_use.protocol.schema import MISSING

    reply = {"kind": "capturedText", "session": MISSING, "textRef": "page", "page": MISSING}
    with pytest.raises(SchemaError) as failure:
        decode_execution_result("inspect", reply)
    (issue,) = failure.value.issues
    assert issue["path"] == ["session"]
    assert issue["message"] == "Invalid input: expected string, received undefined"
    with pytest.raises(KeyError, match="page"):
        decode_execution_result("inspect", {**reply, "session": SESSION})


@pytest.mark.anyio
async def test_captured_text_reply_uses_native_page_contract(tmp_path):
    target = {
        "session": SESSION,
        "instance": "app",
        "rootWindow": "root",
        "surface": "window",
        "node": "text",
        "capture": None,
    }
    params = {
        "operation": "fixture",
        "target": target,
        "query": {"kind": "capturedText", "textRef": "page", "offset": 8, "length": 4},
    }
    page = {"next": 12.0, "offset": 8.0, "total": 14.0, "text": "a\ud800🧪"}
    raw = {"kind": "capturedText", "session": SESSION, "textRef": "page", "page": page}

    def reply(request_id, value):
        response = {"version": 6, "session": SESSION, "id": request_id, "result": value}
        return json.dumps(response, ensure_ascii=True).encode("ascii") + b"\n"

    steps = [
        exchange(1, "inspect", params, reply(1, raw)),
        exchange(2, "inspect", params, reply(2, {**raw, "page": {**page, "next": 13}}), cancel=True),
    ]
    events = []
    async with wire_connection(tmp_path, (*CAPS, "nativeCapturedText"), steps, diagnostic=events.append) as (
        connection,
        invalidated,
    ):
        observed = await connection.request("inspect", params)
        assert observed == raw and list(observed["page"]) == list(page)
        assert type(observed["page"]["offset"]) is float and not invalidated
        with pytest.raises(ComputerUseError) as failure:
            await connection.request("inspect", params)
        assert (failure.value.code, failure.value.effect) == ("execution_failed", "none")
        assert type(failure.value.__cause__) is ValueError
        assert str(failure.value.__cause__) == "protocol: invalid text page"
        assert invalidated == [None] and connection.session_identity is None
    (end,) = terminal_events(events, request_id=2)
    assert end["stage"] == "connection" and end["status"] == "error"


@pytest.mark.anyio
async def test_native_capture_reply_keeps_png_identity_and_metadata(tmp_path):
    from computer_use.protocol.captures import PNG, capture_metadata

    target = {
        "session": SESSION,
        "instance": "app",
        "rootWindow": "root",
        "surface": "window",
        "node": None,
        "capture": None,
    }
    params = {"operation": "fixture", "target": target, "kind": "window", "maxDimension": 1024}
    raw = {
        "kind": "capture",
        "binding": target,
        "observed": {"id": "stamp", "startedAt": 1.25, "completedAt": 5},
        "image": {
            "id": "image",
            "kind": "window",
            "scope": "window",
            "startedAt": 2,
            "completedAt": 3,
            "width": 2,
            "height": 3,
            "png": "iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAEElEQVR4nGMQsUkBIgYUCgAqgQQ5TBDeKwAAAABJRU5ErkJggg==",
        },
        "coordinates": {
            "space": "window-pixels",
            "origin": "top-left",
            "nativeSpace": "screen-points",
            "toNative": [2**53 + 1, 2**53, 1, 1, 0, 0],
        },
    }
    events = []
    step = exchange(1, "capture", params, envelope(1, raw))
    async with wire_connection(
        tmp_path, (*CAPS, "nativeCapture", "nativeCaptureKinds"), [step], diagnostic=events.append
    ) as (connection, invalidated):
        value = await connection.request("capture", params)
        image = value["image"]
        assert isinstance(image, PNG) and value["coordinates"] == raw["coordinates"]
        metadata = capture_metadata(image)
        assert metadata["id"] == "image" and capture_metadata(PNG(image)) is None
        assert not invalidated
    end, _metrics = terminal_events(events)
    assert end["result"]["image"] == {"source": "window", **metadata}


@pytest.mark.anyio
async def test_directory_reply_retains_native_evidence(tmp_path):
    params = {"operation": "fixture", "query": {"kind": "applications"}}
    raw = {
        "kind": "applications",
        "applications": [],
        "session": SESSION,
        "platform": "macos",
        "observed": {"id": "seen", "startedAt": 1, "completedAt": 2},
        "stopReasons": [],
    }
    step = exchange(1, "inspect", params, envelope(1, raw))
    async with wire_connection(tmp_path, CAPS, [step]) as (connection, invalidated):
        assert await connection.request("inspect", params) == raw
        assert not invalidated


@pytest.mark.anyio
async def test_directory_reply_keeps_lone_units_and_exact_absolute_path(tmp_path):
    path = "/Applications/Meeting\ud800.app"
    params = {"operation": "fixture", "query": {"kind": "applications", "path": path}}
    raw = {
        "kind": "applications",
        "session": SESSION,
        "platform": "macos",
        "path": path,
        "observed": {"id": "seen", "startedAt": 1, "completedAt": 2},
        "stopReasons": ["state\udfff"],
        "applications": [
            {
                "id": "instance:\ud800",
                "displayName": "Meet🧪\ud800",
                "isRunning": True,
                "path": path,
                "identifier": None,
            }
        ],
    }

    def reply(request_id, value):
        response = {"version": 6, "session": SESSION, "id": request_id, "result": value}
        return json.dumps(response, ensure_ascii=True).encode("ascii") + b"\n"

    steps = [
        exchange(1, "inspect", params, reply(1, raw)),
        exchange(2, "inspect", params, reply(2, {**raw, "unknown\ud800": None}), cancel=True),
    ]
    async with wire_connection(tmp_path, CAPS, steps) as (connection, invalidated):
        assert await connection.request("inspect", params) == raw
        assert not invalidated
        with pytest.raises(ComputerUseError) as failure:
            await connection.request("inspect", params)
        assert isinstance(failure.value.__cause__, SchemaError)
        (issue,) = failure.value.__cause__.issues
        assert issue["keys"] == ["unknown\ud800"]
        assert issue["message"] == 'Unrecognized key: "unknown\ud800"'
        assert invalidated == [None] and connection.session_identity is None


@pytest.fixture
def open_frame(native_read):
    def open_raw(raw):
        body = json.dumps(raw).encode()
        return native_read.open_read_frame("json", body, len(body))

    return open_raw


def capture_evidence(open_frame, raw):
    capture = open_frame(raw).capture("window", [])
    numbered = capture.with_aliases({"root": "0"})
    scope = ("session", None, None, "surface", "root")
    return (json.dumps(capture.node_view("root")), capture.collection_issues(), numbered.element_details("0", scope))


def scalar(value):
    return {"nativeType": "provider", "data": value}


def payload(*, platform="macos"):
    return {
        "kind": "observation",
        "session": "session",
        "platform": platform,
        "roots": ["root"],
        "nodes": [
            {
                "id": "root",
                "source": "macos.ax",
                "kind": "element",
                "nativeType": "AXUIElement",
                "properties": [{"key": ["macos.ax", "AXRole"], "value": scalar("AXWindow")}],
            }
        ],
        "read": {
            "intervalNs": [1, 2],
            "details": [["surface", scalar("surface")], ["observation_id", scalar("capture")]],
        },
    }


def test_pooled_status_continuation_and_detail_keep_complete_normalized_evidence(open_frame):
    original = payload()
    failure = {
        "nativeType": "record",
        "data": {
            "fields": [
                ["state", scalar("timeout")],
                ["detail", scalar("provider timeout")],
                ["wrappedNativeValue", {"nativeType": "record", "data": {"fields": [["unusual", scalar(42)]]}}],
            ]
        },
    }
    continuation = {"nativeType": "record", "data": {"fields": [["textRef", scalar("token")]]}}
    original["nodes"][0]["properties"].append(
        {
            "key": ["macos.ax", "AXHelp"],
            "state": "timeout",
            "value": None,
            "read": {
                "complete": False,
                "details": [["extra", scalar("kept")]],
                "issues": [
                    {
                        "reason": "timeout",
                        "detail": "provider timeout",
                        "nativeStatus": failure,
                        "continuation": continuation,
                    }
                ],
            },
        }
    )
    compact = copy.deepcopy(original)
    compact["metadataValues"] = [failure, continuation, scalar("kept")]
    read = compact["nodes"][0]["properties"][-1]["read"]
    read["details"][0][1] = {"metadataValue": 2}
    read["issues"][0]["nativeStatus"] = {"metadataValue": 0}
    read["issues"][0]["continuation"] = {"metadataValue": 1}
    assert capture_evidence(open_frame, compact) == capture_evidence(open_frame, original)


@pytest.mark.parametrize(
    "failed_value",
    [
        {"nativeType": "CFDate", "data": 42},
        {"nativeType": "CFURL", "data": "doc.txt", "metadata": [["base", scalar("file:///tmp/parent/")]]},
        {
            "nativeType": "CFString",
            "data": "preview",
            "raw": base64.b64encode("a\ud800".encode("utf-16le", "surrogatepass")).decode(),
            "metadata": [["raw_encoding", scalar("utf-16le")]],
        },
        {"nativeType": "provider", "data": 1, "metadata": [["enum_name", scalar("one")]]},
        {"nativeType": "CFNumber", "data": {"float": "nan"}},
        {"nativeType": "CFArray", "data": {"items": [{"issue": {"reason": "unavailable", "nativeStatus": scalar(9)}}]}},
    ],
)
def test_pooled_special_native_values_keep_identical_semantic_facts(open_frame, failed_value):
    original = payload()
    status = {
        "nativeType": "record",
        "data": {
            "fields": [
                ["state", scalar("error")],
                ["wrappedNativeValue", failed_value],
            ]
        },
    }
    original["nodes"][0]["properties"].append(
        {
            "key": ["macos.ax", "AXHelp"],
            "state": "error",
            "value": None,
            "read": {"complete": False, "issues": [{"reason": "error", "nativeStatus": status}]},
        }
    )
    compact = copy.deepcopy(original)
    compact["metadataValues"] = [status]
    compact["nodes"][0]["properties"][-1]["read"]["issues"][0]["nativeStatus"] = {"metadataValue": 0}
    assert capture_evidence(open_frame, compact) == capture_evidence(open_frame, original)


@pytest.mark.parametrize("index", [-1, 1, True, False, 0.0, "0", None])
def test_metadata_reference_requires_a_declared_exact_integer_index(open_frame, index):
    raw = payload()
    raw["metadataValues"] = [scalar("entry")]
    raw["read"]["details"].append(["invalid", {"metadataValue": index}])
    with pytest.raises(ValueError, match=r"metadata.*(index|reference|value)"):
        open_frame(raw)


@pytest.mark.parametrize("where", ["property", "identity", "pool", "native_metadata", "native_collection"])
def test_reference_marker_is_confined_to_read_metadata_slots(open_frame, where):
    raw = payload()
    marker = {"metadataValue": 0}
    raw["metadataValues"] = [scalar("entry")]
    if where == "property":
        raw["nodes"][0]["properties"][0]["value"] = marker
    elif where == "identity":
        raw["nodes"][0]["identity"] = marker
    elif where == "pool":
        raw["metadataValues"][0] = marker
    elif where == "native_metadata":
        raw["metadataValues"][0] = {"nativeType": "provider", "data": "value", "metadata": [["invalid", marker]]}
    else:
        raw["metadataValues"][0] = {"nativeType": "CFArray", "data": {"items": [marker]}}
    with pytest.raises(ValueError):
        open_frame(raw)


@pytest.mark.parametrize(
    "table",
    [
        None,
        {},
        [scalar(float("inf"))],
        [{"nativeType": "AXUIElement", "data": {"ref": "missing"}}],
    ],
)
def test_even_unused_pool_entries_are_fully_validated(open_frame, table):
    raw = payload()
    raw["metadataValues"] = table
    with pytest.raises(ValueError):
        open_frame(raw)


def test_pool_reference_does_not_bypass_the_inline_value_nesting_budget(open_frame):
    nested = scalar("leaf")
    for _ in range(48):
        nested = {"nativeType": "provider", "data": "value", "metadata": [["nested", nested]]}
    raw = payload()
    raw["metadataValues"] = [nested]
    open_frame(raw)  # The unused entry is itself within the value-depth budget.
    raw["read"]["details"].append(["deep", {"metadataValue": 0}])
    with pytest.raises(ValueError, match=r"(depth|budget)"):
        open_frame(raw)
    raw.pop("metadataValues")
    raw["read"]["details"][-1][1] = nested
    with pytest.raises(ValueError, match=r"(depth|budget)"):
        open_frame(raw)


@pytest.mark.anyio
async def test_connection_collects_startup_evidence_once_per_spawn_not_per_request(tmp_path):
    connection = native_connection.Connection(lambda: None, allow_global_input=False)
    events = []
    set_diagnostic_handler(events.append)
    try:
        with patch("computer_use.runtime_provenance._source", side_effect=[{"startup": 1}, {"startup": 2}]) as collect:
            for startup in (1, 2):
                directory = tmp_path / str(startup)
                directory.mkdir()
                exchanges = [exchange(1, "finish", FINISH, envelope(1, {"cleanup": "complete"}))]
                connection.driver = prepare_driver(directory, CAPS, exchanges)
                try:
                    await connection.connect(3000)
                    await connection.connect(3000)
                    assert await connection.request("finish", FINISH) == {"cleanup": "complete"}
                    assert collect.call_count == startup
                finally:
                    connection.close()
                    await connection.wait_closed()
                assert request_ledger(directory) == [HELLO, exchanges[0]["request"]]
    finally:
        set_diagnostic_handler(None)

    launches = [event for event in events if event["kind"] == "connection"]
    assert [event["runtime"]["source"] for event in launches] == [{"startup": 1}, {"startup": 2}]
    executable = Path(sys.executable).resolve()
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    assert all(event["runtime"]["driver"]["path"] == str(executable) for event in launches)
    assert all(event["runtime"]["driver"]["sha256"] == digest for event in launches)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("units", "visible_help"),
    [("😀", "😀"), ("a\ud800", "a\ufffd")],
)
@pytest.mark.usefixtures("native_read")
async def test_ax_read_keeps_valid_window_evidence_with_original_cfstring_units(tmp_path, units, visible_help):
    raw = payload()
    raw["session"] = SESSION
    raw["nodes"][0]["properties"] += [
        {"key": ["macos.ax", "AXTitle"], "value": scalar("The surviving title")},
        {
            "key": ["macos.ax", "AXHelp"],
            "value": {
                "nativeType": "CFString",
                "data": "preview",
                "raw": base64.b64encode(units.encode("utf-16le", "surrogatepass")).decode(),
                "metadata": [["raw_encoding", scalar("utf-16-le")]],
            },
        },
    ]
    target = Binding(SESSION, None, None, "surface", node="root")
    query = {"kind": "nodes", "fields": ["AXTitle", "AXHelp"], "maxNodes": 1, "maxDepth": 0}
    params = {
        "operation": "fixture",
        "target": {
            "session": SESSION,
            "instance": None,
            "rootWindow": None,
            "surface": "surface",
            "node": "root",
            "capture": None,
        },
        "query": query,
    }
    steps = [{**exchange(1, "inspect", params, envelope(1, raw)), "allowCancel": True}]
    async with wire_connection(tmp_path, (*CAPS, "nativeObservationV1"), steps) as (connection, invalidations):
        await connection.connect()
        port = ConnectionPort(connection, HostPolicy())
        port._operations["fixture"] = (connection.session_identity, None)
        result = await port.nodes("fixture", target, ["AXTitle", "AXHelp"], 1, 0, 3000, scope_kind="window")
        assert result.capture.node_view("root", ["role", "name", "help"]) == {
            "role": "window",
            "name": "The surviving title",
            "help": visible_help,
        }
        assert not invalidations


def test_equal_observations_keep_distinct_metadata_and_acknowledge_only_once_after_host_error():
    import gc
    import weakref

    from computer_use.session.events import observation_delivered, register_observation_delivery
    from computer_use.session.observation import CapturedTextDocument, lazy_observation, peek_observation_text
    from computer_use.session.observation_data import component_delivered, observation_data, retain_observation_data

    class Capture:
        pass

    older = lazy_observation(CapturedTextDocument("same body"))
    newer = lazy_observation(CapturedTextDocument("same body"))
    capture = Capture()
    witness = weakref.ref(capture)
    retain_observation_data(
        older,
        (),
        ("2", "1", "2"),
        ("b", "a", "b"),
        captured_trees=(capture,),
        window_coverage=({"included": ("tree", "image"), "delivered": ()},),
    )
    retain_observation_data(
        newer,
        (),
        ("3",),
        ("c",),
        window_coverage=({"included": ("image",), "delivered": ()},),
    )

    component_delivered(older, "image")
    component_delivered(older, "tree")
    component_delivered(newer, "tree")
    assert tuple(observation_data(older)["refs"]) == ("2", "1")
    assert tuple(observation_data(older)["windows"]) == ("b", "a")
    assert tuple(observation_data(newer)["refs"]) == ("3",)
    assert observation_data(older)["window_coverage"][0]["delivered"] == ("image", "tree")
    assert observation_data(newer)["window_coverage"][0]["delivered"] == ()
    assert peek_observation_text(older) is None
    assert peek_observation_text(newer) is None

    deliveries = []
    older_ref = weakref.ref(older)

    def failing_ack():
        deliveries.append("older")
        observation_delivered(older_ref())
        raise RuntimeError("host stopped")

    register_observation_delivery(older, failing_ack)
    register_observation_delivery(newer, lambda: deliveries.append("newer"))
    with pytest.raises(RuntimeError, match="host stopped"):
        observation_delivered(older)
    observation_delivered(older)
    observation_delivered(newer)
    assert deliveries == ["older", "newer"]

    del older, capture
    gc.collect()
    assert witness() is None
    assert tuple(observation_data(newer)["windows"]) == ("c",)


def test_detail_cursors_use_python_character_offsets_and_retain_exact_element_document(monkeypatch):
    from types import SimpleNamespace

    from computer_use.errors import ComputerUseError
    from computer_use.session import detail_pages
    from computer_use.session.detail_pages import CapturedDetailPages, CapturedDetailText
    from computer_use.session.observation import CapturedTextDocument

    calls, tokens = [], []
    text = "舞🎵\ud800e\u0301"
    prefix = "Captured details (same observation):\n"
    element = 10**80

    def element_details(number):
        calls.append(number)
        return CapturedTextDocument(text)

    def token():
        tokens.append(f"{len(tokens) + 1:032x}")
        return SimpleNamespace(hex=tokens[-1])

    monkeypatch.setattr(detail_pages, "uuid4", token)
    pages = CapturedDetailPages(CapturedTextDocument("source"), element_details=element_details)
    with pytest.raises(ComputerUseError, match="details element must be a nonnegative integer"):
        pages.read(element=True)
    with pytest.raises(ComputerUseError, match="details length must be an integer"):
        pages.read(length=True)
    head = pages.read(element=element, length=len(prefix))
    assert type(head.text) is CapturedDetailText
    assert (head.text, head.total, head.next) == (prefix, len(prefix + text), "details_" + tokens[0])
    with pytest.raises(ComputerUseError, match="belongs to another element scope"):
        pages.read(element=element + 1, cursor=head.next)
    rebound = pages.with_document(CapturedTextDocument("host view"))
    with pytest.raises(ComputerUseError, match="does not belong to this observation"):
        rebound.read(cursor=head.next)
    cursor = head.next
    for character in text:
        page = pages.read(cursor=cursor, length=1)
        assert page.text == character
        assert pages.read(cursor=cursor, length=1).next == page.next
        cursor = page.next
    assert cursor is None
    assert len(tokens) == len(text)
    assert calls == [element]
    assert rebound.read().text == prefix + "host view"
    assert rebound.read(element=element).text == prefix + text
    assert calls == [element, element]


def test_retained_observation_binds_one_host_pair_without_reinterpreting_source_or_explicit_details():
    from computer_use.errors import ComputerUseError
    from computer_use.session.observation import (
        bind_observation_presentation,
        lazy_observation,
        observation_document,
        observation_presentation,
        observation_screenshot,
        peek_observation_text,
    )

    class Document:
        def __init__(self, body):
            self.body = body
            self.renders = 0

        @property
        def text(self):
            self.renders += 1
            return self.body

    original, host, ignored, native = (Document(label) for label in ("source", "host", "ignored", "native"))
    source_image, host_image = object(), object()
    observed = lazy_observation(original, source_image)
    explicit = lazy_observation(original, source_image, details_document=native)
    old_cursor = observed.read_details(length=1).next
    explicit_cursor = explicit.read_details(length=1).next
    old_text = observed.text
    assert old_text == "source"
    assert observed.text is old_text
    assert observed.screenshot is source_image
    assert observation_document(observed) is original
    assert observation_presentation(observed) is None
    assert host.renders == 0

    pair = bind_observation_presentation(observed, host, host_image)
    assert bind_observation_presentation(observed, ignored, object()) is pair
    assert observation_presentation(observed) is pair
    assert observed.screenshot is host_image
    assert observation_screenshot(observed) is source_image
    assert observation_document(observed) is original
    assert peek_observation_text(observed) is None
    assert host.renders == 0
    assert observed.text == "host"
    assert observed.text[:].observation is observed
    assert old_text.observation is observed
    assert old_text == "source"
    with pytest.raises(ComputerUseError, match="does not belong to this observation"):
        observed.read_details(cursor=old_cursor)
    assert observed.read_details().text.endswith("host")

    bind_observation_presentation(explicit, host, host_image)
    assert explicit.read_details(cursor=explicit_cursor).text.endswith("native")
    assert (original.renders, host.renders, ignored.renders, native.renders) == (2, 2, 0, 1)


def test_rendered_tree_iterator_and_cached_fields_keep_separate_lifetimes():
    calls = []

    class Handle:
        def text(self):
            calls.append("text")
            return "captured"

        def details(self):
            calls.append("details")
            return "complete"

        def detail_supplement(self):
            calls.append("supplement")
            return None

        def content_directory(self):
            calls.append("directory")
            return "directory", "payload"

    tree = RustRenderedTree(Handle(), False)
    keys = iter(tree)
    assert next(keys) == "text" and calls == []
    tree.includes_details = True
    assert keys.send("ignored") == "details" and calls == []
    assert next(keys) == "capturedContentDirectory"
    assert calls == ["supplement", "directory"]
    assert tree["capturedContentPayload"] == "payload"
    assert tree["details"] == tree["details"] == "complete"
    assert next(keys) == "capturedContentPayload"
    assert list(keys) == []
    assert calls == ["supplement", "directory", "details"]

    tree._text = "override"
    assert tree["text"] == "override"
    del tree._text
    assert tree["text"] == tree["text"] == "captured"
    assert calls == ["supplement", "directory", "details", "text"]


def test_rendered_tree_iterator_preserves_failure_from_a_lazy_key_probe():
    failure = StopIteration("provider stopped")

    class Handle:
        def content_directory(self):
            raise failure

    keys = iter(RustRenderedTree(Handle(), False))
    assert next(keys) == "text"
    with pytest.raises(RuntimeError, match="generator raised StopIteration") as caught:
        next(keys)
    assert caught.value.__cause__ is failure
    assert caught.value.__context__ is failure
    assert list(keys) == []


def test_element_details_page_lazily_renders_exact_native_sources():
    scope = ("session", None, None, "surface", "root")
    first = Mock(return_value="First source 舞🎵")
    second = Mock(return_value="Second source")
    capture = SimpleNamespace(backend="macos", session="session", source_tokens=("root",))
    a = NativeSnapshot(capture, SimpleNamespace(element_details=first), {"root": "0"})
    b = NativeSnapshot(capture, SimpleNamespace(element_details=second), {"root": "0"})
    first_part = DisplayTree(SimpleNamespace(snapshot=a), scope)
    second_part = DisplayTree(SimpleNamespace(snapshot=b), scope)
    document = DisplayDocument((first_part, first_part, second_part))
    details = CapturedDetailPages(document, element_details=CapturedElementDetails.from_document(document))
    first.assert_not_called()
    second.assert_not_called()
    page = details.read(element=0, length=3)
    rendered = str(page["text"])
    while page["next"] is not None:
        page = details.read(cursor=page["next"], length=3)
        rendered += page["text"]
    assert rendered == "Captured details (same observation):\nCaptured element 0:\n\nFirst source 舞🎵\n\nSecond source"
    assert page["total"] == len(rendered)
    first.assert_called_once_with("0", scope)
    second.assert_called_once_with("0", scope)


def test_native_document_reuses_capture_and_upgrades_detail_coverage(open_frame):
    def document(title):
        raw = payload()
        raw["nodes"][0]["properties"].append({"key": ["macos.ax", "AXTitle"], "value": scalar(title)})
        capture = open_frame(raw).capture("window", [])
        return DisplayDocument.tree(prepare_snapshot(NativeSnapshot.number(capture, {"root": "0"})))

    compact = document("Capture")
    snapshot = compact.parts[0].prepared
    assert not prepare_document(compact).snapshots[id(snapshot)].includes_details

    details = replace(compact, parts=(replace(compact.parts[0], details=True),))
    prepared = prepare_document(details)
    assert prepared.snapshots[id(snapshot)].includes_details
    assert prepare_document(compact).snapshots[id(snapshot)] is prepared.snapshots[id(snapshot)]

    forged = replace(prepared, request=replace(prepared.request, document=document("Other capture")))
    with pytest.raises(ValueError, match="Prepared delivery does not belong to these exact captured sources"):
        render_document(details, prepared=forged)


def test_deferred_details_retry_and_keep_reentrant_publication_order():
    attempts = []

    class Document:
        @property
        def text(self):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise ValueError("first render failed")
            if len(attempts) == 2:
                assert details.read().text.endswith("inner")
                return "outer"
            return "inner"

    details = CapturedDetailPages(Document())
    assert not attempts
    with pytest.raises(ValueError, match="first render failed"):
        details.read()
    assert details.read().text.endswith("outer")
    assert details.read().text.endswith("outer")
    assert attempts == [1, 2, 3]


def native_value(data, kind="CFString"):
    return {"nativeType": kind, "data": data}


@pytest.mark.parametrize(
    "provider, error_type",
    [
        ({"issue": "plain provider content"}, AttributeError),
        ({"issue": {"reason": ["not a status"]}}, TypeError),
        ({"nativeType": "provider record", "node": "not-in-this-observation"}, KeyError),
        ({"nativeType": "provider record", "node": ["not a token"]}, TypeError),
        ({"issue": {"reason": "partial", "detail": "provider coverage"}}, None),
    ],
)
def test_native_provider_dictionary_preserves_normalized_errors(open_frame, provider, error_type):
    def dictionary_value(value):
        if isinstance(value, dict):
            return native_value(
                {"entries": [[native_value(key), dictionary_value(item)] for key, item in value.items()]},
                "CFDictionary",
            )
        if isinstance(value, list):
            return native_value({"items": [dictionary_value(item) for item in value]}, "CFArray")
        return native_value(value)

    raw = payload()
    raw["nodes"][0]["properties"].append({"key": ["macos.ax", "AXHelp"], "value": dictionary_value(provider)})
    if error_type is None:
        assert open_frame(raw).capture("window", []).node_view("root")["role"] == "window"
        return
    details = {
        AttributeError: "'str' object has no attribute 'get'",
        TypeError: "unhashable type: 'list'",
        KeyError: "not-in-this-observation",
    }
    with pytest.raises(error_type) as error:
        open_frame(raw).capture("window", [])
    assert error.value.args == (details[error_type],)


def test_native_captured_issue_keeps_tagged_continuation_total(open_frame):
    raw = payload()
    properties = raw["nodes"][0]["properties"]
    properties[0]["value"]["data"] = "AXTextArea"
    continuation_fields = [
        ["textRef", scalar("text")],
        ["totalUnits", scalar(2**64)],
    ]
    properties.append(
        {
            "key": ["macos.ax", "AXValue"],
            "value": scalar("ab"),
            "read": {
                "complete": False,
                "issues": [
                    {
                        "reason": "partial",
                        "continuation": {"nativeType": "record", "data": {"fields": continuation_fields}},
                    }
                ],
            },
        },
    )
    capture = open_frame(raw).capture("window", [])
    native = NativeSnapshot.number(capture, {"root": "0"})
    scope = ("session", None, None, "surface", "root")
    details = CapturedElementDetails(((native, scope),))(0).text
    assert '"total":{"$type":"integer","value":"18446744073709551616"}' in details


def attribute_catalogue(*names):
    return {
        "nativeType": "CFArray",
        "data": {"items": [{"nativeType": "CFString", "data": name} for name in names]},
    }


def test_pooled_attribute_catalogue_preserves_per_node_values_and_normalized_evidence(open_frame):
    original = payload()
    names = attribute_catalogue("AXTitle", "AXRole", "AXTitle")
    original["nodes"][0]["properties"].append({"key": ["toolkit", "attribute_names"], "value": names})
    original["nodes"].append({**original["nodes"][0], "id": "other"})
    original["roots"].append("other")
    compact = copy.deepcopy(original)
    compact["metadataValues"] = [names]
    for node in compact["nodes"]:
        node["properties"][-1]["value"] = {"metadataValue": 0}

    def captured_details(raw):
        capture = open_frame(raw).capture("window", [])
        numbered = capture.with_aliases({"root": "0", "other": "1"})
        return tuple(
            numbered.element_details(ref, ("session", None, None, "surface", node))
            for (ref, node) in (("0", "root"), ("1", "other"))
        )

    assert captured_details(compact) == captured_details(original)


@pytest.mark.parametrize(
    "value, extra",
    [
        (scalar("AXRole"), {}),
        (attribute_catalogue("é"), {}),
        ({"nativeType": "CFArray", "data": {"items": [scalar("AXRole")]}}, {}),
        ({"nativeType": "CFArray", "data": {"items": [{"nativeType": "AXUIElement", "data": {"ref": "root"}}]}}, {}),
        (attribute_catalogue("AXRole"), {"arguments": [scalar("argument")]}),
        (attribute_catalogue("AXRole"), {"read": {"complete": False}}),
    ],
)
def test_pooled_attribute_catalogue_rejects_other_types_and_property_shapes(open_frame, value, extra):
    raw = payload()
    raw["metadataValues"] = [value]
    raw["nodes"][0]["properties"].append(
        {"key": ["toolkit", "attribute_names"], "value": {"metadataValue": 0}, **extra},
    )
    with pytest.raises(ValueError):
        open_frame(raw)


def input_context(target, tick):
    return {
        "kind": "inputContext",
        "binding": target,
        "observed": f"input:{tick}",
        "targetLabel": "Run fixture action",
        "identity": "yes",
        "withinScope": "yes",
        "enabled": "yes",
        "blockedBy": [],
        "nativeRole": "AXButton",
        "actionsComplete": True,
        "primitives": [
            {
                "id": "primitive:press",
                "unit": "native_action",
                "channel": "object",
                "supported": "yes",
                "readiness": "yes",
                "pending": [],
                "backgroundPreparation": "no",
                "action": {"name": "AXPress"},
            }
        ],
        "input": {
            "foregroundSurface": None,
            "keyboardSurface": None,
            "focusedNode": None,
            "focusAncestors": [],
            "editor": None,
            "editorOwner": None,
            "selectionKnown": "unknown",
            "geometryCurrent": "unknown",
            "windowRoute": "unknown",
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize("missing_node", [False, True])
async def test_context_reply_is_admitted_before_command_success(tmp_path, missing_node):
    target = {
        "session": SESSION,
        "instance": "app",
        "rootWindow": "window",
        "surface": "surface",
        "node": "owner",
        "capture": None,
    }
    params = {"operation": "fixture", "target": target, "query": {"kind": "inputContext", "within": []}}
    payload = input_context(target, 1)
    receiver = {"state": "confirmed", "ancestors": ["owner", "owner"]}
    if not missing_node:
        receiver["node"] = "descendant"
    payload["input"]["currentReceiver"] = receiver
    events = []
    step = exchange(1, "inspect", params, envelope(1, payload), cancel=True)
    async with wire_connection(tmp_path, (*CAPS, "nativeUnitReadinessV2"), [step], diagnostic=events.append) as (
        connection,
        invalidated,
    ):
        with pytest.raises(ComputerUseError) as failure:
            await connection.request("inspect", params)
        assert (failure.value.code, failure.value.effect) == ("execution_failed", "none")
        cause = failure.value.__cause__
        if missing_node:
            assert isinstance(cause, SchemaError)
            (issue,) = cause.issues
            assert issue["path"] == ["input", "currentReceiver", "node"]
            assert issue["message"] == "Invalid input: expected string, received undefined"
        else:
            assert type(cause) is ValueError
            assert str(cause) == "protocol: current receiver ancestry must be a distinct path"
        assert invalidated == [None] and connection.session_identity is None
    (end,) = terminal_events(events)
    assert end["stage"] == "connection" and end["status"] == "error"


@pytest.mark.parametrize("capture_result", ["native", "capture_error", "scope_error", "ordinary_reply"])
def test_native_nodes_register_cursors_once_and_require_rust_capture(native_read, capture_result):
    from types import SimpleNamespace

    from computer_use.native.contracts import Binding, HostPolicy
    from computer_use.interaction.read.accessibility.frame import NativeReadCapture
    from computer_use.native.transport import ConnectionPort
    from computer_use.protocol.observation.adapters.wire import DecodedDriverObservation, DriverWire

    async def run():
        target = Binding("session", "instance", "window", "surface", "root")
        connection = SimpleNamespace(
            protocol_version=6,
            session_identity=("session", 1),
            platform="macos",
            capabilities=frozenset(),
        )
        port = ConnectionPort(connection, HostPolicy())
        port._operations["op"] = (connection.session_identity, None)
        seed = Mock()
        seed.admit_nodes.return_value = [("prior", "session", "surface", "root", "AXChildren", 4, 9)]
        connection.request = AsyncMock(return_value=NativeReadFrame(seed))
        await port.nodes("op", target, ["AXRole"], 10, 2, 500)
        frame = Mock()
        frame.admit_nodes.return_value = [("next", "session", "surface", "root", "AXChildren", 8, 9)]
        if capture_result == "scope_error":
            frame.admit_nodes.side_effect = ValueError("Native fact batch changed its requested scope or budget")
        native_capture = object()

        def capture(*_):
            assert port._continuations["next"] == ("session", "surface", "root", "AXChildren", 8, 9)
            if capture_result == "capture_error":
                raise ValueError("native read capture could not be prepared")
            return native_capture

        frame.capture.side_effect = capture
        response = DecodedDriverObservation(payload()) if capture_result == "ordinary_reply" else NativeReadFrame(frame)
        connection.request = AsyncMock(return_value=response)
        with patch.object(DriverWire, "__init__", side_effect=AssertionError("ordinary wire decoder")):
            call = port.nodes(
                "op",
                target,
                ["AXRole"],
                10,
                2,
                500,
                scope_kind="window",
                children_page={"cursor": "prior", "limit": 4},
            )
            if capture_result in ("scope_error", "ordinary_reply"):
                reason = (
                    "changed its requested scope"
                    if capture_result == "scope_error"
                    else "did not return an observation"
                )
                with pytest.raises(ValueError, match=reason):
                    await call
                frame.capture.assert_not_called()
                assert "next" not in port._continuations
            else:
                if capture_result == "native":
                    result = await call
                    assert isinstance(result, NativeReadCapture) and result.capture is native_capture
                else:
                    with pytest.raises(ValueError, match="could not be prepared"):
                        await call
                frame.capture.assert_called_once_with("window", ())
        if capture_result == "ordinary_reply":
            frame.admit_nodes.assert_not_called()
        else:
            frame.admit_nodes.assert_called_once_with(
                "session", "instance", "window", "surface", "root", "macos", 10, (), ("AXChildren", 4, ("prior", 4, 9))
            )
        connection.request.assert_awaited_once()
        assert connection.request.await_args.kwargs == {"decode_response": native_read.open_read_envelope}

        connection.request.reset_mock(return_value=True)
        connection.request.return_value = DecodedDriverObservation(payload())
        facts = await port.query_nodes("op", target, ["AXRole"], 10, 2, 500)
        assert facts["nodes"][0]["values"]["AXRole"] == "AXWindow"
        assert connection.request.await_args.kwargs == {}

    asyncio.run(run())


@pytest.mark.parametrize("retire_older", [False, True])
def test_delivery_of_newer_window_then_older_window_keeps_default_and_commits_only_retained_scope(retire_older):
    from computer_use.interaction.query.instances.scope import adopt
    from computer_use.session.events import observation_delivered, set_observation_handler
    from computer_use.session.interfaces import WindowIdentity
    from computer_use.session.lookup_continuation import current_lookup_continuation, lookup_continuation_scope
    from computer_use.session.observation import peek_observation_text
    from regression.test_compat_observed_name_lookup import LookupFixture, directory, surface

    a, b = surface("a"), surface("b")
    fixture = LookupFixture(a, b)
    state = fixture.app._instance
    identities = WindowIdentity.of(a), WindowIdentity.of(b)
    events = []
    set_observation_handler(events.append)
    try:
        with lookup_continuation_scope():
            older, _ = fixture.observe("a", label="Older", node="older", capture="older")
            newer, _ = fixture.observe("b", label="Newer", node="newer", capture="newer")
            continuation = current_lookup_continuation(state)
            assert continuation.context.window == identities[1]
            assert continuation.windows == set(identities)
            assert state.delivered_observation_generation == 0
            assert state.input_context is None
            assert not state.delivered_lookup_windows
            assert not state.window_contexts

            if retire_older:
                adopt(fixture.controller, fixture.app, directory(b, focus="b", ended=(a,)))

            observation_delivered(newer)
            newest_generation = state.delivered_observation_generation
            newest_context = state.window_contexts[identities[1]]
            observation_delivered(older)
            observation_delivered(older)

            assert state.input_context == identities[1]
            assert state.lookup_context.window == identities[1]
            assert state.delivered_observation_generation == newest_generation
            assert state.window_contexts[identities[1]] is newest_context
            assert (identities[0] in state.window_contexts) is not retire_older
            expected = {identities[1]} if retire_older else set(identities)
            assert state.delivered_lookup_windows == expected
            assert [event["observation"]["state"] is older for event in events] == [True, False]
            assert peek_observation_text(older) is None
            assert peek_observation_text(newer) is None
    finally:
        set_observation_handler(None)


def test_reentrant_host_accepts_newer_publication_before_outer_returned_scope_commits():
    from computer_use.interaction.read.views.publication import publish
    from computer_use.session.events import observation_delivered, set_observation_handler
    from computer_use.session.lookup_continuation import current_lookup_continuation, lookup_continuation_scope
    from regression.test_compat_observed_name_lookup import LookupFixture

    fixture = LookupFixture()
    state = fixture.app._instance
    nested = None

    def receive(event):
        nonlocal nested
        if nested is None:
            # The host may run another SDK call synchronously while receiving
            # a result. Guard the nested publication against another reentry.
            nested = event["observation"]["state"]
            nested, _ = fixture.observe(label="Newest", node="newest", capture="newest")

    set_observation_handler(receive)
    try:
        with lookup_continuation_scope():
            outer, _ = fixture.observe(label="Outer", node="outer", capture="outer")
            assert nested is not outer
            continuation = current_lookup_continuation(state)
            assert continuation.generation == state.observation_generation
            assert fixture.find("Newest")["binding"].node.endswith(":newest")
            assert fixture.calls == []
            assert state.delivered_observation_generation == 0

            # A later catalogue-only result can advance the delivered input
            # context, but cannot resurrect the older script-local facts.
            publish(
                fixture.controller,
                fixture.app,
                "Catalogue",
                on_observation=lambda event: (observation_delivered(event["state"])),
            )
            assert current_lookup_continuation(state) is None
    finally:
        set_observation_handler(None)


@pytest.mark.anyio
async def test_missing_native_engine_does_not_dispatch_requested_read(tmp_path, monkeypatch):
    def missing_engine():
        raise RuntimeError("missing Rust read")

    from computer_use.native import transport

    monkeypatch.setattr(transport, "require_read_engine", missing_engine)
    async with wire_connection(tmp_path, CAPS, []) as (connection, invalidations):
        await connection.connect()
        port = ConnectionPort(connection, HostPolicy())
        port._operations["fixture"] = (connection.session_identity, None)
        target = Binding(SESSION, None, None, "surface", node="root")
        with pytest.raises(RuntimeError, match="missing Rust read"):
            await port.nodes("fixture", target, ["AXRole"], 1, 0, 3000)
        assert not invalidations


class NumberingCapture:
    surface = None
    scope_kind = "window"

    def __init__(self, source_tokens, visible_tokens, session="session", check_deadline=False):
        self.source_tokens = source_tokens
        self.visible_tokens = visible_tokens
        self.check_deadline = check_deadline
        self.session = session

    def with_aliases(self, aliases):
        return self

    def preview_tokens(self, checkpoint):
        if self.check_deadline and checkpoint is not None:
            checkpoint()
        return self.visible_tokens


def numbered_sources(session="session"):
    left_capture = NumberingCapture(("left-hidden", "shared"), ("shared",), session)
    right_capture = NumberingCapture(
        ("right-hidden", "shared", "right-visible"), ("right-visible", "shared"), session, check_deadline=True
    )
    return tuple(
        NativeSnapshot.number(capture, {token: str(index) for index, token in enumerate(capture.source_tokens)})
        for capture in (left_capture, right_capture)
    )


def test_numbering_preserves_shared_aliases_and_visits_visible_sources_first():
    from computer_use.interaction.read.numbering.pipeline import number_snapshots
    from computer_use.interaction.read.numbering.references import DisplayReferences

    left, right = number_snapshots(numbered_sources(), DisplayReferences("session"))
    assert dict(left.refs) == {"2": "left-hidden", "0": "shared"}
    assert list(right.refs.items()) == [("3", "right-hidden"), ("0", "shared"), ("1", "right-visible")]


def test_native_preview_expiration_keeps_earlier_touches_without_numbering_hidden_sources():
    from computer_use.interaction.read.numbering.pipeline import number_snapshots
    from computer_use.interaction.read.numbering.references import DisplayReferences
    from computer_use.pipeline.core import Control

    references = DisplayReferences("session")

    def deadline():
        raise TimeoutError("original operation deadline")

    with pytest.raises(TimeoutError, match="original operation deadline"):
        number_snapshots(numbered_sources(), references, _control=Control(remaining=deadline))

    assert references.reference((None, "window", "shared")) == "0"
    assert references.reference((None, "window", "after")) == "1"
    assert references.reference((None, "window", "left-hidden")) == "2"


def test_native_reader_reuses_public_numbers_until_driver_session_changes():
    from computer_use.interaction.read.app import NativeAppReader

    reader = NativeAppReader(SimpleNamespace())
    _, first = numbered_sources()
    expected = [("2", "right-hidden"), ("1", "shared"), ("0", "right-visible")]
    assert list(reader.number_snapshots((first,))[0].refs.items()) == expected
    references = reader._element_references
    assert references.reference(("other", "window", "shared")) == "3"
    assert list(reader.number_snapshots((first,))[0].refs.items()) == expected
    assert reader._element_references is references

    _, replacement = numbered_sources("next session")
    assert list(reader.number_snapshots((replacement,))[0].refs.items()) == expected
    assert reader._element_references is not references
    assert reader._element_references.reference(("other", "window", "shared")) == "3"


@pytest.mark.anyio
async def test_response_admission_and_diagnostics_share_the_reader_turn(tmp_path, monkeypatch):
    from computer_use.protocol.observation.adapters.wire import DecodedDriverObservation

    decode = DecodedDriverObservation.__init__
    order = []

    def admit(self, value):
        order.append("admit")
        asyncio.get_running_loop().call_soon(order.append, "next turn")
        decode(self, value)

    monkeypatch.setattr(DecodedDriverObservation, "__init__", admit)
    params = {"operation": "fixture", "query": {"kind": "nodes", "fields": ["AXRole"], "maxNodes": 2, "maxDepth": 1}}
    result = dict(payload(), session=SESSION)

    def diagnostic(event):
        if event["kind"] in ("command_end", "command_diagnostic_end"):
            order.append(event["kind"])

    steps = [exchange(1, "inspect", params, envelope(1, result))]
    async with wire_connection(tmp_path, CAPS, steps, diagnostic=diagnostic) as (connection, invalidated):
        reply = await connection.request("inspect", params)
        assert reply.wire.raw == result
        assert not invalidated
    assert order == ["admit", "command_end", "command_diagnostic_end", "next turn"]


@pytest.mark.anyio
@pytest.mark.parametrize("callback_kind", ["ordinary", "replace_node", "switch_sink", "relative_url"])
async def test_native_diagnostic_uses_current_sink_and_retains_original_index(tmp_path, native_read, callback_kind):
    from copy import deepcopy

    from computer_use.diagnostics import _register_observation_diagnostic

    original = payload()
    original["session"] = SESSION
    title = {"key": ["macos.ax", "AXTitle"], "value": scalar("original title")}
    original["nodes"][0]["properties"].append(title)
    if callback_kind == "relative_url":
        original["nodes"][0]["properties"].append(
            {
                "key": ["macos.ax", "AXURL"],
                "value": {
                    "data": "before.txt",
                    "nativeType": "CFURL",
                    "metadata": [["base", {"data": "file:///tmp/parent/", "nativeType": "CFString"}]],
                },
            }
        )
    received = []
    encoded_events = []

    def receive(event):
        if event["kind"] == "command_end":
            raw = event["result"]
            received.append(raw)
            if callback_kind == "replace_node":
                raw["nodes"][0] = deepcopy(raw["nodes"][0])
            properties = raw["nodes"][0]["properties"]
            properties[1]["value"]["data"] = "callback title"
            if callback_kind == "relative_url":
                properties[2]["value"]["data"] = "after.txt"

    params = {"operation": "fixture", "query": {"kind": "nodes", "fields": ["AXRole"], "maxNodes": 2, "maxDepth": 1}}
    data = envelope(1, original)
    switched = callback_kind == "switch_sink"
    steps = [exchange(1, "inspect", params, data, split=len(data) - 1 if switched else None)]
    temporary_sink = [].append
    async with wire_connection(tmp_path, CAPS, steps, diagnostic=temporary_sink if switched else receive) as (
        connection,
        invalidated,
    ):
        if switched:
            _register_observation_diagnostic(temporary_sink, lambda event, body: encoded_events.append((event, body)))
        pending = asyncio.create_task(
            connection.request("inspect", params, decode_response=native_read.open_read_envelope)
        )
        try:
            if switched:
                await wait_for(tmp_path / "paused-1", pending)
                assert not pending.done()
                set_diagnostic_handler(receive)
                (tmp_path / "continue-1").touch()
            result = await pending
        finally:
            if switched:
                (tmp_path / "continue-1").touch()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        assert not invalidated and not encoded_events
        assert result.raw_result() is received[0]
        assert received[0]["nodes"][0]["properties"][1]["value"]["data"] == "callback title"
        assert result.native.admit_nodes(SESSION, None, None, "surface", "root", "macos", 2, [], None) == []
        captured = result.capture("window", ())
        node = captured.node_view("root")
        assert captured.lookup_evidence(["AXTitle"])["nodes"][0]["values"] == {"AXTitle": "original title"}
        # The legacy normalizer reused admitted values and re-decoded the raw result for relative URLs.
        assert node["name"] == ("callback title" if callback_kind == "relative_url" else "original title")
        if callback_kind == "relative_url":
            assert node["url"] == "file:///tmp/parent/after.txt"


def test_native_acquisition_rejects_an_ordinary_reply_without_allocating_again():
    legacy = payload()
    numbered = []
    capture = SimpleNamespace(source_tokens=("window", "child"), with_aliases=numbered.append)
    seen = []

    async def nodes(*args, **options):
        seen.append(options)
        if len(seen) == 1:
            return NativeReadCapture(capture)
        return legacy

    reader = NativeReader(SimpleNamespace(nodes=nodes, connection=SimpleNamespace(capabilities=())))
    assert reader.display_references("s").reference("child") == "0"
    target = Binding("s", "instance", "root-window", "surface", "window")
    request = FactReadRequest(SimpleNamespace(token="lease", remaining=lambda: 1000))
    profile = FactProfile(FactScope(request, target), [], 1, {})
    collect, normalize = collect_facts(reader=reader), normalize_facts(reader=reader)

    first = normalize(asyncio.run(collect(profile, Control())), Control())
    with pytest.raises(ValueError, match="did not return a Rust capture"):
        normalize(asyncio.run(collect(profile, Control())), Control())

    assert seen == [
        {"scope_kind": None},
        {"scope_kind": None},
    ]
    assert isinstance(first, NativeSnapshot)
    assert numbered == [{"window": "1", "child": "0"}]
    assert first.refs == {"1": "window", "0": "child"}


@pytest.mark.parametrize("observed", [True, False])
def test_native_element_read_requires_normalized_observation_evidence(monkeypatch, observed):
    capture = SimpleNamespace(
        source_tokens=("node",),
        contains_observed=lambda token: token == "node" and observed,
    )
    tree = NativeSnapshot(capture, None, {"node": "7"})
    requested = []

    async def read(_reader, target, **options):
        requested.append((target, options))
        return tree

    monkeypatch.setattr(NativeReader, "read", read)
    state = SimpleNamespace(refs={})

    def remember(state, target, tree):
        state.refs.update({ref: {"binding": target, "source": {"ref": ref}} for ref in tree.refs})

    controller = SimpleNamespace(
        interfaces=SimpleNamespace(validate_snapshot=lambda *_: None),
        reader=SimpleNamespace(
            reader=NativeReader(SimpleNamespace(platform="macos")),
            number_snapshots=lambda sources: sources,
        ),
        transport=SimpleNamespace(capabilities=()),
        state=lambda _: state,
        remember_observation=remember,
    )
    target = Binding("s", "instance", "window", "surface", "node")
    request = {"binding": target, "scope_kind": "window"}
    pipeline = AgentElementReadPipeline(controller)
    if observed:
        retained, fresh = asyncio.run(pipeline.collect(SimpleNamespace(), request, max_nodes=1))
        assert retained is tree
        assert fresh["source"]["ref"] == "7"
    else:
        with pytest.raises(ComputerUseError) as caught:
            asyncio.run(pipeline.collect(SimpleNamespace(), request, max_nodes=1))
        assert caught.value.code == "observation_incomplete"
        assert not state.refs
    assert requested == [(target, {"scope_kind": "window", "max_nodes": 1})]


def test_startup_adoption_requests_native_read_without_changing_observation_scope(monkeypatch):
    selected = object()
    directory = {"session": "s"}
    result = SimpleNamespace(directory=directory, selection=selected)
    state = SimpleNamespace()
    captured = []

    async def read(_reader, selector, **options):
        captured.append((selector, options))
        return result

    def adopt(_controller, _owner, directory, selection):
        assert directory is result.directory
        assert selection is selected

    monkeypatch.setattr(NativeAppReader, "read", read)
    monkeypatch.setattr(instance_scope, "adopt", adopt)
    monkeypatch.setattr(instance_scope, "display_selection", lambda *_: selected)
    controller = SimpleNamespace(state=lambda _: state, reader=NativeAppReader(SimpleNamespace(platform="macos")))
    owner = SimpleNamespace(_instance=SimpleNamespace())
    assert asyncio.run(instance_scope.observe_application(controller, owner, "Editor", "tree")) == (directory, result)
    selector, options = captured[0]
    assert selector == "Editor"
    assert options["view"] == "tree"
    assert "scope_managed" not in options
    assert owner._instance.anchor is selected


@pytest.mark.parametrize("platform", ["macos", "windows"])
def test_lookup_facts_keep_selected_binding_with_native_reads(monkeypatch, platform):
    target = Binding("s", "instance", "window", "surface")
    selected = replace(target, node="selected")
    lookup = FindElement(object(), ({"name": "Chosen"},), 3000, False, strict_window=True)
    menu_window = object()
    state = SimpleNamespace(query_scope_kind="applicationMenu", menu_window_identity=menu_window)
    plan = FindPlan(lookup, state, target)
    lease = FindLease(plan, OperationLease("lease", target, lambda: 1000), selected=selected)
    calls = []
    retained = []
    observed = {"id": "sample"}

    def with_aliases(aliases):
        def bindings(scope, include_public_pages):
            retained.append((scope, include_public_pages))
            return [
                {"reference": aliases["other"], "node": "other"},
                {
                    "reference": aliases["selected"],
                    "node": "selected",
                    "container": False,
                    "scope_kind": "applicationMenu",
                    "observed": observed,
                    "text": {"text_ref": "captured-text", "total_units": 17},
                    "children": {"cursor": "captured-children", "offset": 2, "total": 7},
                },
            ], []

        return SimpleNamespace(retained_bindings=bindings)

    capture = SimpleNamespace(session="s", source_tokens=("selected", "other"), with_aliases=with_aliases)

    async def nodes(token, scope, _fields, _max_nodes, _max_depth, _timeout_ms, **options):
        calls.append((token, scope, options))
        return NativeReadCapture(capture)

    reader = NativeReader(
        SimpleNamespace(
            platform=platform,
            nodes=nodes,
            connection=SimpleNamespace(platform=platform, capabilities=()),
        )
    )
    references = DisplayReferences("s")
    monkeypatch.setattr(reader, "display_references", lambda _session: references)
    controller = SimpleNamespace(reader=SimpleNamespace(reader=reader))
    facts = asyncio.run(FindFacts(controller).arun(lease, Control()))
    result = build_lookup_snapshot(
        native_snapshots=SessionController._native_snapshots,
    ).run(facts, Control())

    options = {"scope_kind": "applicationMenu", "strict_window": True}
    assert isinstance(facts.tree, NativeSnapshot)
    assert retained == [(("s", "instance", "window", "surface", "selected"), False)]
    assert result["source"]["observed"] is observed
    assert result["text"] == CapturedField("s", text_ref="captured-text", total_units=17)
    assert result["children"] == ChildContinuation("captured-children", 2, 7)
    assert calls == [("lease", selected, options)]
    assert result["binding"] == selected
    assert result["source"]["ref"] is None
    assert result["menu_window_identity"] is menu_window


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_native_capture_owns_render_facts_across_renumbering(native_read, platform):
    from computer_use.interaction.read.numbering.pipeline import number_snapshots
    from unit_tests.test_read_rust_transport import _observation

    raw = _observation(platform)
    native = raw["nodes"][0]
    provider, element_type, role, button, name, children_key = {
        "windows": ("windows.uia", "UIAElement", "ControlType", "ControlType.Button", "Name", "Children"),
        "linux": ("linux.atspi", "AtspiAccessible", "role", "push button", "name", "children"),
    }[platform]
    tokens = ("root", *(f"native:{i}" for i in range(30)))
    native["properties"].append(
        {
            "key": [provider, children_key],
            "value": native_value(
                {"items": [native_value({"ref": token}, element_type) for token in tokens[1:]]}, "Array"
            ),
            "read": {
                "complete": True,
                "details": [
                    [key, scalar(value)]
                    for key, value in (
                        ("relation", True),
                        ("certainty", "confirmed"),
                        ("state", "complete"),
                        ("total", 30),
                    )
                ],
            },
        }
    )
    raw["nodes"].extend(
        {
            "id": token,
            "kind": "element",
            "source": provider,
            "nativeType": element_type,
            "properties": [
                {"key": [provider, role], "value": scalar(button)},
                {"key": [provider, name], "value": scalar(f"Control {index}")},
            ],
        }
        for index, token in enumerate(tokens[1:])
    )
    body = json.dumps(raw).encode()
    capture = native_read.open_read_frame("json", body, len(body)).capture("window", [])
    snapshot = NativeSnapshot.number(capture, {token: str(index) for index, token in enumerate(tokens)})
    original = prepare_snapshot(snapshot)
    refs = DisplayReferences("session")
    refs.reference("previous")
    (renumbered,) = number_snapshots((snapshot,), refs)
    second = prepare_snapshot(renumbered)

    assert original.snapshot.capture is second.snapshot.capture is capture
    assert (original.snapshot.refs["0"], second.snapshot.refs["1"]) == ("root", "root")
    assert "Control 29" in DisplayDocument.tree(second).text
    assert original.snapshot.surface == second.snapshot.surface == "surface"


class ResolvedDirectoryTests(unittest.TestCase):
    def request(self, **directory):
        from computer_use.interaction.read.app_read.model import ReadRequest
        from computer_use.native.contracts import Binding
        from computer_use.native.scopes import WorkingWindow

        scope = WorkingWindow("application", "Fixture", Binding("session", "instance", "root", "surface", "node"))
        return ReadRequest(scope, source_directory={"session": "session", "application": "application", **directory})

    def test_current_directory_remains_the_source_of_the_fixed_selection(self):
        from computer_use.interaction.read.app_read.helpers import parse_read_request
        from computer_use.pipeline import Control

        request = self.request()
        parsed = parse_read_request(request, Control())
        self.assertIs(parsed.source_directory, request.source_directory)
        self.assertIs(parsed.selector, request.selector)

    def test_directory_from_another_application_or_session_cannot_be_reused(self):
        from computer_use.interaction.read.app_read.helpers import parse_read_request
        from computer_use.pipeline import Control

        for field in ("session", "application"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_read_request(self.request(**{field: "another"}), Control())


class ApplicationListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_listing_reads_fresh_apps_without_querying_windows(self):
        from unittest.mock import AsyncMock

        from computer_use.interaction.query.instances.pipeline import ApplicationDiscoveryPipeline
        from computer_use.session.interfaces import ApplicationReferences
        from computer_use.session.windows import WindowReferences

        catalogue = {
            "session": "session",
            "applications": [
                {"id": "one", "displayName": "One", "isRunning": True},
                {"id": "helper", "displayName": "Helper", "isRunning": True},
                {"id": "installed", "displayName": "Installed", "isRunning": False},
            ],
            "stopReasons": [],
        }
        directory = SimpleNamespace(
            applications=AsyncMock(return_value=catalogue),
            surface_batch=AsyncMock(side_effect=AssertionError("Unexpected window query")),
            surfaces=AsyncMock(side_effect=AssertionError("Unexpected window query")),
            _query=AsyncMock(side_effect=AssertionError("Unexpected window query")),
            port=SimpleNamespace(
                connection=SimpleNamespace(
                    capabilities={
                        "nativeApplicationListing",
                        "nativeSurfaceBatch",
                        "nativeVisualSurfaceQuery",
                        "nativeVisualSurfaceBatch",
                    }
                )
            ),
        )
        references = ApplicationReferences()
        pipeline = ApplicationDiscoveryPipeline(directory, references, WindowReferences())
        first = await pipeline.run()
        catalogue["applications"][0]["displayName"] = "Updated"
        second = await pipeline.run()
        self.assertEqual(second[0].id, first[0].id)
        self.assertEqual(second[0].display_name, "Updated")
        self.assertEqual(directory.applications.await_count, 2)
        self.assertEqual([item.display_name for item in first], ["One", "Helper", "Installed"])
        self.assertEqual([item.is_running for item in second], [True, True, False])
        self.assertEqual(
            [references.resolve(item.id, "session").selector for item in second], ["one", "helper", "installed"]
        )
        directory.surface_batch.assert_not_awaited()
        directory.surfaces.assert_not_awaited()
        directory._query.assert_not_awaited()


def test_reference_record_rejects_extra_fields_and_native_refs_remain_distinct():
    raw = payload()
    raw["metadataValues"] = [{"nativeType": "AXUIElement", "data": {"ref": "root"}}]
    raw["read"]["details"].append(["owner", {"metadataValue": 0}])
    wire = DriverWire(raw)
    assert wire.details["owner"] == {"nativeType": "AXUIElement", "node": "root"}
    raw["read"]["details"][-1][1]["unexpected"] = True
    with pytest.raises(ValueError):
        DriverWire(raw)


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_inline_values_and_optional_pool_use_one_platform_neutral_contract(platform):
    raw = payload(platform=platform)
    expected = DriverWire(raw).details
    raw["metadataValues"] = [scalar("surface")]
    raw["read"]["details"][0][1] = {"metadataValue": 0}
    assert DriverWire(raw).details == expected


def test_batch_slot_provenance_is_retained_and_validated():
    raw = payload()
    raw["metadataValues"] = [
        {"nativeType": "AXUIElement", "data": {"ref": "root"}},
        {"nativeType": "CFNumber.i", "data": {"integer": "0"}},
    ]
    prop = raw["nodes"][0]["properties"][0]
    prop["read"] = {"details": [["batch_owner", {"metadataValue": 0}], ["batch_index", {"metadataValue": 1}]]}

    decoded = DecodedDriverObservation(raw)
    assert decoded.facts["nodes"][0]["values"]["AXRole"] == "AXWindow"
    assert decoded.wire.raw["nodes"][0]["properties"][0] is prop
    assert decoded.wire.property(prop).read["details"] == {
        "batch_owner": {"nativeType": "AXUIElement", "node": "root"},
        "batch_index": 0,
    }

    prop["read"]["details"][1][1]["metadataValue"] = 2
    with pytest.raises(ValueError, match="invalid metadata value reference"):
        DecodedDriverObservation(raw)


@pytest.mark.parametrize(
    "depth, retained, geometry_overflow, expected_error",
    [(15, True, False, None), (16, True, False, ValueError), (16, False, False, None), (16, True, True, OverflowError)],
)
def test_native_fact_errors_preserve_original_type_and_order(
    open_frame, depth, retained, geometry_overflow, expected_error
):
    nested = native_value("leaf")
    for _ in range(depth):
        nested = native_value({"entries": [[nested, native_value(None, "CFNull")]]}, "CFDictionary")
    raw = payload()
    properties = raw["nodes"][0]["properties"]
    if retained:
        properties.append({"key": ["macos.ax", "AXHelp"], "value": nested})
    else:
        raw["metadataValues"] = [nested]
    if geometry_overflow:
        point = native_value(
            {"fields": [["x", native_value({"integer": str(10**400)}, "CFNumber")]]}, "AXValue.CGPoint"
        )
        properties.append({"key": ["macos.ax", "AXPosition"], "value": point})
    if expected_error is None:
        assert open_frame(raw).capture("window", []).node_view("root")["role"] == "window"
        return

    messages = {
        ValueError: "observation: nodes.root.supplements.0.value: value does not match its declared type",
        OverflowError: "int too large to convert to float",
    }
    with pytest.raises(expected_error) as failure:
        open_frame(raw).capture("window", [])
    assert str(failure.value) == messages[expected_error]


def publish(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def await_marker(path):
    deadline = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < deadline, path
        time.sleep(0.005)


def serve(directory):
    spec = json.loads((directory / "spec.json").read_bytes())
    # The supervised close sends TERM on POSIX. Read the queued cancellation
    # first, then let the stdin EOF finish the fixture's own cleanup.
    signal.signal(signal.SIGTERM, lambda *_: None)
    with (directory / "requests.jsonl").open("x", encoding="utf-8", buffering=1) as journal:

        def expect(expected):
            raw = sys.stdin.buffer.readline()
            assert raw, ("EOF before expected request", expected)
            message = json.loads(raw)
            timeout = message.pop("timeoutMs", None)
            if message.get("id") and "method" in message:
                assert type(timeout) is int and 0 < timeout <= 30000, message
            journal.write(json.dumps(message) + "\n")
            assert message == expected, (message, expected)

        expect({"version": 6, "id": 0, "method": "hello", "params": {"globalInputAllowed": False}})
        hello = {
            "version": 6,
            "id": 0,
            "session": spec["session"],
            "result": {
                "platform": "macos",
                "globalInputAllowed": False,
                "maxTimeoutMs": 30000,
                "capabilities": spec["capabilities"],
            },
        }
        publish(json.dumps(hello).encode("utf-8") + b"\n")
        for index, exchange in enumerate(spec["exchanges"], 1):
            expect(exchange["request"])
            response = bytes.fromhex(exchange["response"])
            split = exchange.get("split")
            if split is not None:
                if split:
                    publish(response[:split])
                (directory / f"paused-{index}").touch()
                await_marker(directory / f"continue-{index}")
                response = response[split:]
            if exchange.get("replyAfterCancel"):
                (directory / f"paused-{index}").touch()
            elif response:
                publish(response)
            cancellation = {"version": 6, "session": spec["session"], "cancel": index}
            if exchange.get("cancel"):
                expect(cancellation)
            if exchange.get("allowCancel"):
                optional = sys.stdin.buffer.readline()
                if optional:
                    assert json.loads(optional) == cancellation
                    journal.write(optional.decode())
                return
            if exchange.get("replyAfterCancel"):
                try:
                    publish(response)
                except BrokenPipeError:
                    pass
                (directory / f"late-{index}").touch()
        assert not sys.stdin.buffer.readline(), "Unexpected request after fixture tape"


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
