# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unconfirmed native replies preserve the effects possible at each write stage."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.global_input import GlobalInput
from computer_use.interaction.requests import ComputerInput
from computer_use.interaction.write.engine import Engine
from computer_use.native.connection import Connection
from computer_use.native.contracts import ApplicationBinding, Binding, HostPolicy, Intent
from computer_use.native.transport import ConnectionPort

DRIVER = Path(__file__).resolve().parents[1] / "fixtures/input_context_timeout_driver.py"


def timeout_connection(directory, scenario):
    journal = directory / "driver-journal.jsonl"
    connection = Connection(
        lambda: None,
        driver={"command": sys.executable, "args": [str(DRIVER), scenario, str(journal)]},
    )
    return connection, journal


async def run_unconfirmed(connection, action):
    try:
        await connection.connect(3000)
        clock = asyncio.get_running_loop().time
        started = clock()
        try:
            return await action()
        except ComputerUseError:
            assert clock() - started >= 0.750
            assert connection.session_identity is None
            raise
    finally:
        connection.close()
        await connection.wait_closed()


def assert_driver_journal(journal, methods, *, cancelled):
    *requests, retired = (json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines())
    assert set(retired) == {"retired"} and retired["retired"] > 0
    if cancelled:
        cancellation = requests.pop()
        assert cancellation == {"version": 6, "session": "session", "cancel": requests[-1]["id"]}
    assert [request["method"] for request in requests] == ["hello", *methods]
    assert [request["id"] for request in requests] == list(range(len(requests)))


def test_unavailable_current_receiver_preserves_handled_exception():
    async def inspect():
        connection = SimpleNamespace(protocol_version=6, session_identity="session", capabilities=())
        port = ConnectionPort(connection, HostPolicy())
        port._operations["operation"] = ("session", None, None)
        target = Binding("session", "app", "root", "surface", "editor")
        try:
            raise LookupError("original route evidence")
        except LookupError as original:
            with pytest.raises(ComputerUseError) as caught:
                await port.inspect("operation", target, 50, current_receiver=True)
            assert caught.value.code == "unsupported_operation"
            assert caught.value.__context__ is original
        assert not port.calls.retired

    asyncio.run(inspect())


@pytest.mark.parametrize(
    ("scenario", "expected_effect", "methods"),
    [
        ("before_input", "none", ["begin", "inspect"]),
        ("after_preparation", "possible", ["begin", "inspect", "execute", "inspect"]),
        ("lost_execute", "possible", ["begin", "inspect", "execute"]),
    ],
)
def test_lost_input_context_has_no_effect_of_its_own(tmp_path, scenario, expected_effect, methods):
    connection, journal = timeout_connection(tmp_path, scenario)
    port = ConnectionPort(connection, HostPolicy())
    target = Binding("session", "app", "root", "surface", "editor")
    intent = Intent("key", {"key": "F6"})
    if scenario == "before_input":
        target = replace(target, node=None, capture="observed-image")
        intent = Intent("click", {"x": 198, "y": 317, "button": "left", "count": 1})
    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(run_unconfirmed(connection, lambda: Engine(port).execute(intent, target)))
    error = caught.value
    assert_driver_journal(journal, methods, cancelled=True)
    assert port.calls.retired
    assert "no terminal response within 750 ms cleanup grace" in error.detail
    assert any("Native operation cleanup unconfirmed" in note for note in error.__notes__)
    assert error.effect == expected_effect and f"Effect: {expected_effect}" in str(error)
    if scenario == "before_input":
        assert error.code == "execution_failed" and error.action_receipt is None
        assert "read-only request failed" in error.detail and error.impact["attempts"] == ()
    if scenario == "after_preparation":
        (preparation,) = error.impact["attempts"]
        assert preparation["dispatch"] == "notDispatched"
        assert preparation["actual"]["foreground_changed"] == "yes"
        assert preparation["actual"]["foreground_restoration"] == "unknown"
    if scenario == "lost_execute":
        (attempt,) = error.impact["attempts"]
        assert error.code == "execution_unknown" and attempt["dispatch"] == attempt["cleanup"] == "unknown"


@pytest.mark.parametrize("route", ["scoped_key", "launch", "global_key"])
def test_lost_macos_write_begin_reports_unissued_input_and_unknown_scope_cleanup(tmp_path, route):
    connection, journal = timeout_connection(tmp_path, "lost_begin")
    port = ConnectionPort(connection, HostPolicy())

    async def action():
        if route == "global_key":
            await GlobalInput(SimpleNamespace(port=port)).arun(ComputerInput(Intent("key", {"key": "t"}, True)))
        else:
            target = Binding("session", "app", "root", "surface")
            intent = Intent("key", {"key": "t"})
            if route == "launch":
                target, intent = ApplicationBinding("session", "installation"), Intent("launch")
            await Engine(port).execute(intent, target)

    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(run_unconfirmed(connection, action))
    error = caught.value
    assert_driver_journal(journal, ["begin"], cancelled=True)
    assert port.calls.retired
    assert error.code == "execution_failed" and error.effect == "none"
    assert "requested action was not dispatched" in error.detail
    assert error.scope_cleanup == "unknown" and "Scope cleanup: unknown." in str(error)
    assert error.action_receipt is None and error.impact["attempts"] == ()


def test_macos_write_begin_terminal_failure_keeps_confirmed_scope(tmp_path):
    connection, journal = timeout_connection(tmp_path, "rejected_begin")
    port = ConnectionPort(connection, HostPolicy())
    target = Binding("session", "app", "root", "surface")

    async def run():
        try:
            await connection.connect(3000)
            with pytest.raises(ComputerUseError) as caught:
                await Engine(port).execute(Intent("key", {"key": "t"}), target)
            assert not port.calls.retired and connection.session_identity == (0, "session")
            assert caught.value.effect == "none" and caught.value.scope_cleanup is None
            assert caught.value.detail == "native begin deadline exceeded"
        finally:
            connection.close()
            await connection.wait_closed()

    asyncio.run(run())
    assert_driver_journal(journal, ["begin"], cancelled=False)
