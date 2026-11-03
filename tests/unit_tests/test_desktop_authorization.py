# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Full input is available by default without changing low-impact route priority."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from computer_use import Computer
from computer_use.errors import ComputerUseError, public_error_code
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.ir import freeze
from computer_use.interaction.write.routes.policy import Attempts, choose
from computer_use.native.connection import Connection
from computer_use.native.contracts import Channel, HostPolicy, Intent, Knowledge, Primitive
from computer_use.protocol.desktop import decode_desktop
from computer_use.protocol.session import decode_hello
from computer_use.session.observation import lazy_observation
from computer_use_mcp.server import cli as mcp_cli
from unit_tests.test_unified_write_routing import CLICK, click_route, facts

from computer_use_agent.config import AgentConfig


def test_sdk_default_allows_global_input_and_preserves_explicit_opt_out():
    with Computer() as computer, Computer(allow_global_input=False) as restricted:
        assert computer._controller.port.policy.allow_global_input is True
        assert computer._controller.transport.allow_global_input is True
        assert restricted._controller.port.policy.allow_global_input is False
        assert restricted._controller.transport.allow_global_input is False
    assert public_error_code("permission_required") == "permission_required"


@pytest.mark.parametrize("value", [1, 0, "true", "false", None, [], {}])
def test_permission_requires_an_explicit_boolean(value):
    with pytest.raises(TypeError, match="bool"):
        Computer(allow_global_input=value)
    with pytest.raises(TypeError, match="bool"):
        HostPolicy(allow_global_input=value)
    with pytest.raises(TypeError, match="bool"):
        Connection(lambda: None, allow_global_input=value)


@pytest.mark.parametrize("preparation", [(), ("background",), ("foreground",)])
def test_denied_global_permission_preserves_addressed_route_and_activation(preparation):
    addressed = click_route("surface_pointer", "preparationRequired" if preparation else "ready", preparation)
    physical = click_route("desktop_pointer")
    selected = choose(facts(physical, addressed), CLICK, HostPolicy(False), Attempts())
    assert selected.primitive == addressed
    assert selected.foreground == (preparation == ("foreground",))
    assert any(e.reason == "globalInputPermissionRequired" for e in selected.click_selection.exclusions)


def test_required_global_pointer_reports_permission_without_selecting_a_wrong_semantic_route():
    native = click_route("object_action")
    physical = click_route("desktop_pointer", profile="physicalRequired")
    with pytest.raises(ComputerUseError) as caught:
        choose(facts(native, physical), CLICK, HostPolicy(False), Attempts())
    assert caught.value.code == "permission_required" and caught.value.effect == "none"
    assert "allow_global_input" in caught.value.next
    assert choose(facts(native, physical), CLICK, HostPolicy(True), Attempts()).primitive == physical


def test_known_global_keyboard_does_not_hide_independent_object_edit():
    value = Primitive("value", "native_value_replace", Channel.OBJECT, Knowledge.YES, Knowledge.YES)
    keyboard = Primitive(
        "key", "key_sequence", Channel.KEYBOARD, Knowledge.YES, Knowledge.YES, global_input=Knowledge.YES
    )
    request = Intent("replace", {"text": "replacement"})
    current = facts(keyboard, value)
    assert choose(current, request, HostPolicy(), Attempts()).primitive == value
    # Full permission permits a mechanism; it does not promote it above a qualified
    # independently ready route that avoids global input.
    assert choose(current, request, HostPolicy(True), Attempts()).primitive == value
    with pytest.raises(ComputerUseError) as caught:
        choose(facts(keyboard), request, HostPolicy(False), Attempts())
    assert caught.value.code == "permission_required"
    directed = replace(keyboard, global_input=Knowledge.NO)
    assert choose(facts(directed), request, HostPolicy(), Attempts()).primitive == directed


@pytest.mark.parametrize("allowed", [False, True])
def test_unknown_keyboard_impact_does_not_claim_addressed_priority(allowed):
    value = Primitive(
        "value", "native_value_replace", Channel.OBJECT, Knowledge.YES, Knowledge.YES, global_input=Knowledge.NO
    )
    unknown_keys = Primitive("key", "key_sequence", Channel.KEYBOARD, Knowledge.YES, Knowledge.YES)
    selected = choose(facts(unknown_keys, value), Intent("replace", {"text": "new"}), HostPolicy(allowed), Attempts())
    assert selected.primitive == value


@pytest.mark.parametrize("allowed", [False, True])
def test_handshake_echoes_exact_permission(allowed):
    reply = {
        "version": 6,
        "session": "test-session",
        "id": 0,
        "result": {
            "platform": "macos",
            "globalInputAllowed": allowed,
            "maxTimeoutMs": 30000,
            "capabilities": ["cancel", "requestDedup", "nativeOperations"],
        },
    }
    assert decode_hello(reply, allowed)["globalInputAllowed"] is allowed
    with pytest.raises(ValueError, match="permission"):
        decode_hello(reply, not allowed)
    for invalid in (0, 1, None, "true"):
        reply["result"]["globalInputAllowed"] = invalid
        with pytest.raises(ValueError, match="permission"):
            decode_hello(reply, allowed)
    del reply["result"]["globalInputAllowed"]
    with pytest.raises(ValueError):
        decode_hello(reply, allowed)


def test_desktop_observation_has_no_input_authority():
    value = {
        "canvas": {"width": 100, "height": 100},
        "frontmost": None,
        "displays": [{"name": "test-display", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}, "captured": True}],
        "windows": [],
        "omittedWindows": 0,
    }
    assert decode_desktop(value) == value


@pytest.mark.parametrize("captured", [True, False])
def test_desktop_capture_without_mode_delivers_lazy_text_and_details(captured):
    value = decode_desktop({
        "canvas": {"width": 100, "height": 100},
        "frontmost": {"name": "Document app", "selector": "original-app"},
        "displays": [{
            "name": "test-display", "rect": {"x": 0, "y": 0, "width": 100, "height": 100},
            "captured": captured, **({} if captured else {"error": "capture unavailable"}),
        }],
        "windows": [{
            "app": "Document app", "title": "Owned document", "selector": "original-app",
            "rect": {"x": 10, "y": 20, "width": 30, "height": 40},
        }],
        "omittedWindows": 0,
    })
    document = DisplayDocument.join([DisplayText("desktop", (freeze(value), {"original-app": 7}, {"0": 11}))])
    observation = lazy_observation(document)
    # Both fields render the actual retained desktop document. An eagerly
    # decoded protocol object alone did not catch the removed-mode lookup.
    details = observation.read_details(length=16384)
    assert details.next is None and details.text.endswith(observation.text)
    assert observation.text.startswith("Desktop: full view\n")
    assert '"Owned document" (10, 20, 30, 40) computer.window(11); application: computer.app(7)' in observation.text
    assert 'Foreground: "Document app"; computer.app(7)' in observation.text
    assert ("Screenshot: desktop 100x100 px" if captured else "Screenshot: unavailable") in observation.text


def test_agent_defaults_and_explicit_opt_out_reach_mcp():
    assert AgentConfig().allow_global_input is True
    assert "--allow-global-input" in AgentConfig().mcp_config().args
    assert "--allow-global-input" in AgentConfig(allow_global_input=True).mcp_config().args
    assert "--no-allow-global-input" in AgentConfig(allow_global_input=False).mcp_config().args


@pytest.mark.parametrize(
    "arguments, expected", [([], True), (["--allow-global-input"], True), (["--no-allow-global-input"], False)]
)
def test_mcp_cli_forwards_default_and_explicit_restriction(monkeypatch, arguments, expected):
    received = []

    async def start(*, allow_global_input):
        received.append(allow_global_input)

    monkeypatch.setattr("computer_use_mcp.server.main", start)
    mcp_cli(arguments)
    assert received == [expected]


def test_removed_mode_is_not_an_mcp_option(monkeypatch):
    start = Mock()
    monkeypatch.setattr("computer_use_mcp.server.asyncio.run", start)
    with pytest.raises(SystemExit):
        mcp_cli(["--desktop", "exclusive"])
    start.assert_not_called()
