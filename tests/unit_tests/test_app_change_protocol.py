# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application change hints retain exact observer scope and optional support."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.transport import ConnectionPort, encode_target
from computer_use.protocol.directory import app_change, decode_directory
from computer_use.protocol.execution import decode_execution_result, decode_inspect_query


def change():
    return {"session": "session", "application": "application", "incarnation": "observer", "token": "generation:4"}


def session_binding():
    return {"session": "session", "instance": None, "rootWindow": None, "surface": None, "node": None, "capture": None}


def begin():
    return {"operation": "operation", "target": session_binding(), "remainingMs": 400}


def execution_reply():
    return {
        "target": {
            **session_binding(),
            "instance": "instance",
            "rootWindow": "window",
            "surface": "surface",
            "node": "node",
        },
        "dispatch": "dispatched",
        "detail": "Native input returned",
        "preparation": [],
        "confirmation": "notRequested",
        "cleanup": "complete",
        "errorCode": None,
        "retryReason": None,
        "context": None,
    }


def surfaces():
    return {
        "kind": "surfaces",
        "application": "application",
        "surfaces": [],
        "rootDiscoveryComplete": True,
        "provider": {"status": "unsupported", "attribute": None, "writeStatus": None},
        "session": "session",
        "platform": "macos",
        "observed": {"id": "directory", "startedAt": 1, "completedAt": 2},
        "stopReasons": [],
    }


def app_changes():
    return {"kind": "appChanges", **change(), "available": True, "notifications": ["AXWindowCreated", "AXValueChanged"]}


def waited():
    return {"kind": "waitAppChange", **change(), "reason": "event", "events": ["AXWindowCreated"], "elapsedMs": 12.5}


def roots():
    return {
        "kind": "roots",
        "application": "application",
        "roots": [],
        "session": "session",
        "platform": "macos",
        "observed": {"id": "directory", "startedAt": 1, "completedAt": 2},
        "stopReasons": [],
    }


@pytest.mark.parametrize("kind", ["appChanges", "waitAppChange"])
def test_app_inspection_requests_are_strict_and_keep_application_scope(kind):
    query = {"kind": kind, "application": "application"}
    if kind == "waitAppChange":
        query.update(after="generation:4", timeoutMs=400)
    assert decode_inspect_query(query) == query
    for key in ("session", "surface", "unknown"):
        with pytest.raises(ValueError):
            decode_inspect_query({**query, key: "other"})
    for invalid in (None, "", "   ", False, 4):
        with pytest.raises(ValueError):
            decode_inspect_query({**query, "application": invalid})


@pytest.mark.parametrize("timeout", [0, 401, -1, 1.5, True, "100", None])
def test_wait_request_timeout_is_an_integer_in_the_operation_budget(timeout):
    with pytest.raises(ValueError):
        decode_inspect_query(
            {"kind": "waitAppChange", "application": "application", "after": "token", "timeoutMs": timeout}
        )


def test_wait_request_requires_after_identity_and_accepts_smallest_bound():
    query = {"kind": "waitAppChange", "application": "application", "after": "token", "timeoutMs": 1}
    assert decode_inspect_query(query) == query
    for invalid in (None, "", {}, 1):
        with pytest.raises(ValueError):
            decode_inspect_query({**query, "after": invalid})
    del query["after"]
    with pytest.raises(ValueError):
        decode_inspect_query(query)


def test_begin_and_directory_share_exact_four_field_app_change_contract():
    baseline = change()
    assert app_change.parse(baseline) == baseline
    started = decode_execution_result("begin", {**begin(), "appChangeBaseline": baseline})
    directory = decode_directory({**surfaces(), "appChange": baseline})
    root_directory = decode_directory({**roots(), "appChange": baseline})
    assert started["appChangeBaseline"] == directory["appChange"] == root_directory["appChange"] == baseline
    directory["appChange"]["token"] = "other-generation"
    started["appChangeBaseline"]["incarnation"] = "other-observer"
    assert baseline == change()


@pytest.mark.parametrize("owner", ["begin", "surfaces", "roots"])
@pytest.mark.parametrize("key", ["session", "application", "incarnation", "token"])
def test_embedded_app_change_fields_are_required_identity_strings(owner, key):
    for invalid in (None, "", " ", 1):
        evidence = {**change(), key: invalid}
        directory = roots() if owner == "roots" else surfaces()
        payload = (
            {**begin(), "appChangeBaseline": evidence} if owner == "begin" else {**directory, "appChange": evidence}
        )
        with pytest.raises(ValueError):
            decode_execution_result(owner, payload) if owner == "begin" else decode_directory(payload)
    evidence = change()
    del evidence[key]
    with pytest.raises(ValueError):
        if owner == "begin":
            decode_execution_result("begin", {**begin(), "appChangeBaseline": evidence})
        else:
            decode_directory({**directory, "appChange": evidence})


@pytest.mark.parametrize("owner", ["begin", "surfaces", "roots"])
def test_embedded_evidence_rejects_unknown_fields(owner):
    evidence = {**change(), "available": True}
    with pytest.raises(ValueError):
        if owner == "begin":
            decode_execution_result("begin", {**begin(), "appChangeBaseline": evidence})
        else:
            decode_directory({**(roots() if owner == "roots" else surfaces()), "appChange": evidence})


@pytest.mark.parametrize("directory", [surfaces, roots])
def test_embedded_evidence_cannot_change_directory_or_operation_identity(directory):
    for key in ("session", "application"):
        with pytest.raises(ValueError, match="directory scope"):
            decode_directory({**directory(), "appChange": {**change(), key: "another"}})
    with pytest.raises(ValueError, match="operation scope"):
        decode_execution_result("begin", {**begin(), "appChangeBaseline": {**change(), "session": "another"}})
    installation = {
        **begin(),
        "target": {"session": "session", "application": "another"},
        "appChangeBaseline": change(),
    }
    with pytest.raises(ValueError, match="operation scope"):
        decode_execution_result("begin", installation)


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
@pytest.mark.parametrize("directory", [surfaces, roots])
def test_unsupported_embedded_evidence_stays_optional_and_nullable(platform, directory):
    legacy = {**directory(), "platform": platform}
    assert "appChange" not in decode_directory(legacy)
    assert decode_directory({**legacy, "appChange": None})["appChange"] is None
    assert "appChangeBaseline" not in decode_execution_result("begin", begin())
    assert decode_execution_result("begin", {**begin(), "appChangeBaseline": None})["appChangeBaseline"] is None


def test_app_change_support_reply_preserves_notifications_without_payload_mutation():
    payload = app_changes()
    before = deepcopy(payload)
    parsed = decode_execution_result("inspect", payload)
    assert parsed == before
    parsed["notifications"].append("AXFocusedUIElementChanged")
    assert payload == before
    for key in ("incarnation", "token"):
        with pytest.raises(ValueError, match="observer incarnation and token"):
            decode_execution_result("inspect", {**payload, key: None})


def test_unavailable_support_is_evidence_not_protocol_failure():
    payload = {**app_changes(), "available": False, "incarnation": None, "token": None, "notifications": []}
    assert decode_execution_result("inspect", payload) == payload
    # A provider can retain its observer identity while its change feed is unavailable.
    payload["incarnation"] = "previous-observer"
    assert decode_execution_result("inspect", payload) == payload


@pytest.mark.parametrize("factory", [app_changes, waited])
def test_change_replies_reject_extra_missing_and_invalid_identity_fields(factory):
    payload = factory()
    for key in tuple(payload):
        missing = dict(payload)
        del missing[key]
        if key != "elapsedMs":
            with pytest.raises(ValueError):
                decode_execution_result("inspect", missing)
    for key in ("session", "application", "incarnation", "token"):
        for invalid in ("", " ", True, 3):
            with pytest.raises(ValueError):
                decode_execution_result("inspect", {**payload, key: invalid})
    with pytest.raises(ValueError):
        decode_execution_result("inspect", {**payload, "surface": "another-scope"})


def test_wait_event_requires_known_observer_and_nonempty_distinct_events():
    payload = waited()
    assert decode_execution_result("inspect", payload) == payload
    for events in ([], ["AXWindowCreated", "AXWindowCreated"]):
        with pytest.raises(ValueError, match="wake reason"):
            decode_execution_result("inspect", {**payload, "events": events})
    for key in ("incarnation", "token"):
        with pytest.raises(ValueError, match="observer incarnation and token"):
            decode_execution_result("inspect", {**payload, key: None})


@pytest.mark.parametrize("reason", ["timeout", "unavailable"])
def test_non_event_wait_reasons_allow_null_identity_but_require_empty_events(reason):
    payload = {**waited(), "incarnation": None, "token": None, "reason": reason, "events": []}
    assert decode_execution_result("inspect", payload) == payload
    with pytest.raises(ValueError, match="wake reason"):
        decode_execution_result("inspect", {**payload, "events": ["AXWindowCreated"]})


@pytest.mark.parametrize("elapsed", [-0.1, True, "1", None, float("nan"), float("inf")])
def test_wait_elapsed_time_is_optional_finite_nonnegative_number(elapsed):
    with pytest.raises(ValueError):
        decode_execution_result("inspect", {**waited(), "elapsedMs": elapsed})
    payload = waited()
    del payload["elapsedMs"]
    assert decode_execution_result("inspect", payload) == payload


@pytest.mark.parametrize(("factory", "field"), [(app_changes, "notifications"), (waited, "events")])
def test_notification_lists_have_bounded_identity_entries(factory, field):
    payload = factory()
    for invalid in ([""], [None], [1], [f"notification:{index}" for index in range(33)], "AXWindowCreated"):
        with pytest.raises(ValueError):
            decode_execution_result("inspect", {**payload, field: invalid})
    full = {**payload, field: [f"notification:{index}" for index in range(32)]}
    assert decode_execution_result("inspect", full) == full


def test_existing_window_read_change_query_and_reply_remain_unchanged():
    query = {"kind": "readChange", "after": "window-generation", "timeoutMs": 400}
    payload = {
        "kind": "readChange",
        "session": "session",
        "surface": "window-surface",
        "after": "window-generation",
        "reason": "event",
        "events": ["structure", "text"],
        "elapsedMs": 23,
    }
    assert decode_inspect_query(query) == query
    assert decode_execution_result("inspect", payload) == payload
    with pytest.raises(ValueError):
        decode_inspect_query({**query, "application": "application"})
    with pytest.raises(ValueError):
        decode_execution_result("inspect", {**payload, "events": ["AXWindowCreated"]})


@pytest.mark.parametrize("mode", ["present", "null", "omitted"])
def test_execute_input_baseline_preserves_exact_optional_nullable_four_field_contract(mode):
    payload = execution_reply()
    if mode != "omitted":
        payload["appChangeInputBaseline"] = change() if mode == "present" else None
    before = deepcopy(payload)
    parsed = decode_execution_result("execute", payload)
    assert parsed == before
    if mode == "present":
        parsed["appChangeInputBaseline"]["token"] = "another-token"
        assert payload == before
    elif mode == "omitted":
        assert "appChangeInputBaseline" not in parsed


def test_execute_input_baseline_rejects_changed_session_and_application_binding_identity():
    payload = {**execution_reply(), "appChangeInputBaseline": change()}
    with pytest.raises(ValueError, match="operation scope"):
        decode_execution_result(
            "execute", {**payload, "appChangeInputBaseline": {**change(), "session": "another-session"}}
        )
    application_reply = {**payload, "target": {"session": "session", "application": "application"}}
    assert decode_execution_result("execute", application_reply) == application_reply
    with pytest.raises(ValueError, match="operation scope"):
        decode_execution_result(
            "execute", {**application_reply, "appChangeInputBaseline": {**change(), "application": "another-app"}}
        )


@pytest.mark.parametrize("field", ["session", "application", "incarnation", "token"])
def test_execute_input_baseline_requires_each_identity_and_rejects_extra_fields(field):
    payload = execution_reply()
    for invalid in (None, "", " ", True, 1):
        with pytest.raises(ValueError):
            decode_execution_result("execute", {**payload, "appChangeInputBaseline": {**change(), field: invalid}})
    missing = change()
    del missing[field]
    with pytest.raises(ValueError):
        decode_execution_result("execute", {**payload, "appChangeInputBaseline": missing})
    with pytest.raises(ValueError):
        decode_execution_result("execute", {**payload, "appChangeInputBaseline": {**change(), "nativeTimestamp": 1}})


@pytest.fixture
def anyio_backend():
    return "asyncio"


def app_port(reply, *, platform="macos", capabilities=("nativeAppChangeEvents",)):
    async def request(method, parameters, options):
        assert method == "inspect"
        decode_inspect_query(parameters["query"])
        return decode_execution_result(method, reply)

    connection = SimpleNamespace(
        protocol_version=6,
        platform=platform,
        capabilities=capabilities,
        session_identity=(1, "session"),
        request=AsyncMock(side_effect=request),
    )
    port = ConnectionPort(connection, HostPolicy())
    port._operations["read"] = (connection.session_identity, "read", None)
    return port


@pytest.mark.anyio
@pytest.mark.parametrize("wait", [False, True])
async def test_app_port_uses_session_read_scope_and_preserves_application_and_wait_parameters(wait):
    application = "app:中文/unchanged-identity"
    reply = {**(waited() if wait else app_changes()), "application": application}
    port = app_port(reply)
    target = Binding("session", None, None, None)
    options = {"after": "generation:4", "delay_ms": 400} if wait else {}
    assert await port.app_changes("read", target, application, 900, **options) == reply
    method, parameters, timeout = port.connection.request.await_args.args
    assert method == "inspect"
    assert parameters["operation"] == "read"
    assert parameters["target"] == encode_target(target)
    assert parameters["query"] == {
        "kind": "waitAppChange" if wait else "appChanges",
        "application": application,
        **({"after": "generation:4", "timeoutMs": 400} if wait else {}),
    }
    assert timeout == {"timeoutMs": 900}


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["instance", "root_window", "surface", "node", "capture"])
async def test_app_port_does_not_use_an_old_window_element_or_capture_binding(field):
    port = app_port(app_changes())
    target = replace(Binding("session", None, None, None), **{field: "old-target"})
    with pytest.raises(ValueError, match="session discovery scope"):
        await port.app_changes("read", target, "application", 500)
    port.connection.request.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("after", "delay"),
    [
        ("", 100),
        (True, 100),
        ("token", None),
        ("token", 0),
        ("token", 401),
        ("token", True),
        ("token", 1.5),
    ],
)
async def test_app_port_rejects_invalid_wait_contract_before_request(after, delay):
    port = app_port(waited())
    with pytest.raises(ValueError, match="token or event-wait limit"):
        await port.app_changes(
            "read", Binding("session", None, None, None), "application", 500, after=after, delay_ms=delay
        )
    port.connection.request.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("field", ["session", "application"])
async def test_app_port_refuses_reply_that_changes_requested_session_or_application(wait, field):
    reply = {**(waited() if wait else app_changes()), field: "another-identity"}
    port = app_port(reply)
    options = {"after": "generation:4", "delay_ms": 100} if wait else {}
    with pytest.raises(ValueError, match="changed its session or application"):
        await port.app_changes("read", Binding("session", None, None, None), "application", 500, **options)


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
async def test_app_port_unsupported_path_does_not_dispatch_or_fall_back_to_window_events(platform):
    port = app_port(app_changes(), platform=platform, capabilities=())
    with pytest.raises(ValueError, match="requires nativeAppChangeEvents"):
        await port.app_changes("read", Binding("session", None, None, None), "application", 500)
    port.connection.request.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
async def test_app_port_declared_capability_has_the_same_contract_on_each_platform(platform):
    reply = app_changes()
    port = app_port(reply, platform=platform)
    assert await port.app_changes("read", Binding("session", None, None, None), "application", 500) == reply


@pytest.mark.anyio
async def test_windows_window_event_wait_keeps_original_target_token_and_event_contract():
    reply = {
        "kind": "readChange",
        "session": "session",
        "surface": "surface",
        "after": "window-generation",
        "reason": "event",
        "events": ["structure"],
        "elapsedMs": 20,
    }
    port = app_port(reply, platform="windows", capabilities=("nativeReadEvents",))
    target = Binding("session", "instance", "window", "surface", "root")
    assert await port.wait_for_change("read", target, "window-generation", 100, 500) == reply
    _, parameters, options = port.connection.request.await_args.args
    assert parameters["target"] == encode_target(target)
    assert parameters["query"] == {"kind": "readChange", "after": "window-generation", "timeoutMs": 100}
    assert options == {"timeoutMs": 500}
