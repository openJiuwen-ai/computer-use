# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The host exposes one full, background-first desktop authorization."""

from unittest.mock import Mock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.native.contracts import HostPolicy
from computer_use.protocol.desktop import decode_desktop
from computer_use.protocol.execution import decode_begin
from computer_use.protocol.session import decode_hello, desktop_mode
from computer_use_mcp.server import cli as mcp_cli
from computer_use_native_tools import native

from computer_use_agent.config import AgentConfig


def test_sdk_and_agent_default_to_the_same_full_authorization(monkeypatch):
    monkeypatch.delenv("COMPUTER_USE_DESKTOP", raising=False)
    assert desktop_mode() == HostPolicy().mode == AgentConfig().desktop == "exclusive"
    assert HostPolicy().foreground_allowed is True
    assert AgentConfig().mcp_config().args == ("-m", "computer_use_mcp", "--desktop", "exclusive")


@pytest.mark.parametrize("value", ["shared", "", "foreground", "EXCLUSIVE"])
def test_obsolete_desktop_modes_are_rejected_at_configuration_boundaries(monkeypatch, value):
    monkeypatch.setenv("COMPUTER_USE_DESKTOP", value)
    with pytest.raises(ComputerUseError, match="must be exclusive"):
        desktop_mode()
    with pytest.raises(ValueError, match="exclusive"):
        HostPolicy(value)
    with pytest.raises(ValueError, match="exclusive"):
        AgentConfig(desktop=value)


def test_foreground_assist_is_not_a_host_policy_switch():
    with pytest.raises(TypeError):
        HostPolicy(foreground_assist=False)


def test_mcp_rejects_obsolete_mode_before_starting_host(monkeypatch):
    start = Mock()
    monkeypatch.setattr("computer_use_mcp.server.asyncio.run", start)
    with pytest.raises(SystemExit):
        mcp_cli(["--desktop", "shared"])
    monkeypatch.setenv("COMPUTER_USE_DESKTOP", "shared")
    with pytest.raises(ComputerUseError, match="must be exclusive"):
        mcp_cli([])
    start.assert_not_called()


def test_agent_cli_rejects_obsolete_mode():
    from computer_use_agent.cli.main import parser

    with pytest.raises(SystemExit):
        parser().parse_args(["--desktop", "shared"])
    assert parser().parse_args([]).desktop is None


def test_handshake_does_not_accept_an_obsolete_backend():
    reply = {
        "version": 6,
        "session": "test-session",
        "id": 0,
        "result": {
            "platform": "macos",
            "desktopMode": "shared",
            "maxTimeoutMs": 30000,
            "capabilities": ["cancel", "requestDedup", "nativeOperations"],
        },
    }
    with pytest.raises(ValueError, match="desktop mode"):
        decode_hello(reply, "exclusive")
    with pytest.raises(ValueError, match="desktop mode"):
        decode_hello(reply, "shared")
    reply["result"]["desktopMode"] = "exclusive"
    assert decode_hello(reply, "exclusive")["desktopMode"] == "exclusive"


def test_desktop_observation_rejects_obsolete_mode():
    value = {
        "mode": "shared",
        "canvas": {"width": 100, "height": 100},
        "frontmost": None,
        "displays": [{"name": "test-display", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}, "captured": True}],
        "windows": [],
        "omittedWindows": 0,
    }
    with pytest.raises(ValueError):
        decode_desktop(value)
    value["mode"] = "exclusive"
    assert decode_desktop(value)["mode"] == "exclusive"


def test_linux_launcher_exposes_only_the_full_authorization(monkeypatch, tmp_path):
    monkeypatch.setattr(native.sys, "platform", "linux")
    env = {"DISPLAY": ":1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/test-bus"}
    launcher = native.linux_session_launcher(directory=tmp_path, env=env)
    text = launcher.read_text()
    assert "COMPUTER_USE_DESKTOP=exclusive" in text
    assert "COMPUTER_USE_FOREGROUND_ASSIST" not in text
    with pytest.raises(ValueError, match="must be exclusive"):
        native.linux_session_launcher(directory=tmp_path, env={**env, "COMPUTER_USE_DESKTOP": "shared"})


def test_retired_physical_preference_metadata_is_not_accepted():
    request = {
        "mode": "write",
        "target": {
            "session": "test-session",
            "instance": "test-instance",
            "rootWindow": "0",
            "surface": "test-surface",
            "node": "test-node",
            "capture": None,
        },
        "intent": {"kind": "click", "parameters": {"button": "left", "count": 1}},
        "clickContract": "v2",
    }
    assert decode_begin(request)["clickContract"] == "v2"
    with pytest.raises(ValueError):
        decode_begin({**request, "clickPreferenceHints": "v1"})
