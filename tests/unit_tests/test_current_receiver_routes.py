# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Current focus selection must not add text semantics to key/focus intents."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.current_receiver import CurrentReceiverQuery
from computer_use.native.contracts import (
    Actuator,
    Binding,
    Channel,
    Cleanup,
    Confirmation,
    CurrentReceiverEvidence,
    Dispatch,
    Facts,
    FocusMember,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge,
    OpenedOperation,
    Primitive,
    StepResult,
)
from computer_use.native.lifecycle import CallLifecycle


@pytest.fixture
def anyio_backend():
    return "asyncio"


SCOPE = Binding("session", "app", "window", "surface", "container")
RECEIVER = replace(SCOPE, node="receiver")


class ReceiverPort:
    platform = "macos"
    capabilities = ("nativeCurrentReceiver", "nativeActuators")
    policy = HostPolicy()

    def __init__(self, *, role="AXGroup", readonly=False):
        self.calls = CallLifecycle(self)
        self.current = CurrentReceiverEvidence("confirmed", RECEIVER.node, (SCOPE.node,))
        self.role = role
        self.readonly = readonly
        self.focused = RECEIVER.node
        self.identity = self.within_scope = Knowledge.YES
        self.route_supported = Knowledge.YES
        self.blocked_by = ()
        self.commands = []
        self.node_queries = []
        self.inspections = []

    async def begin(self, intent, target, timeout_ms):
        return OpenedOperation("operation", target)

    async def inspect(self, operation, target, timeout_ms, **options):
        self.inspections.append((target, options))
        context = InputContext(focused_node=self.focused, current_receiver=self.current)
        primitives = ()
        if not options.get("current_receiver"):
            primitives = (
                Primitive("keys", "key_sequence", Channel.KEYBOARD, self.route_supported, Knowledge.YES),
                Primitive(
                    "focus",
                    "prepare_receiver",
                    Channel.EDITOR,
                    self.route_supported,
                    Knowledge.NO,
                    ("receiver",),
                    Knowledge.YES,
                ),
            )
        return Facts(
            target,
            "observed",
            "current receiver",
            self.identity,
            self.within_scope,
            Knowledge.YES,
            self.blocked_by,
            primitives,
            context,
            platform=self.platform,
            native_role=self.role,
        )

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        self.node_queries.append(target.node)
        values = {"AXEnabled": True, "AXFocused": True, "settable.AXFocused": True}
        if self.role is not None:
            values["AXRole"] = self.role
        if self.role == "AXTextField":
            values.update({"AXEditable": not self.readonly, "settable.AXValue": not self.readonly})
        node = {
            "node": target.node,
            "requestedFields": fields,
            "values": values,
            "fieldStatus": {field: {"state": "unsupported"} for field in fields if field not in values},
            "actions": {"state": "complete", "items": []},
        }
        children = {
            "source": target.node,
            "property": "AXChildren",
            "targets": [],
            "state": "complete",
            "certainty": "confirmed",
        }
        return {
            "session": target.session,
            "surface": target.surface,
            "platform": self.platform,
            "roots": [target.node],
            "nodes": [node],
            "relations": [children],
            "observed": {"id": "raw"},
        }

    async def execute(self, operation, step, command, timeout_ms):
        self.commands.append(command)
        if command.intent.kind == "focus":
            return StepResult(
                command.target, Dispatch.NOT_DISPATCHED, "focus confirmed", confirmation=Confirmation.CONFIRMED
            )
        return StepResult(command.target, Dispatch.DISPATCHED, "key sent")

    async def finish(self, operation):
        return Cleanup.COMPLETE


def query(port):
    return CurrentReceiverQuery(port, "operation", SCOPE, lambda: 1000)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
@pytest.mark.parametrize("role", ["AXGroup", "AXUnknown", None])
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
async def test_confirmed_key_receiver_does_not_require_role_or_editing_declarations(kind, role, platform):
    port = ReceiverPort(role=role)
    port.platform = platform
    assert await query(port).resolve(Intent(kind)) == (RECEIVER, (SCOPE.node,))
    assert port.node_queries == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
async def test_confirmed_canvas_receiver_reaches_the_original_native_route_once(kind):
    port = ReceiverPort()
    parameters = {"key": "Return"} if kind == "key" else {}
    result = await Engine(port).execute(Intent(kind, parameters), SCOPE, forward_current=True)
    assert result["status"] == ("dispatched" if kind == "key" else "verified")
    assert len(port.commands) == 1
    command = port.commands[0]
    assert command.target == RECEIVER and command.intent == Intent(kind, parameters)
    assert not command.foreground
    assert port.inspections[-1][1]["within"] == (SCOPE.node,)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insert", "paste"])
async def test_text_forwarding_retains_confirmed_editing_semantics(kind):
    port = ReceiverPort(role="AXTextField")
    assert await query(port).resolve(Intent(kind, {"text": "text"})) == (RECEIVER, (SCOPE.node,))
    assert port.node_queries == [RECEIVER.node]


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
async def test_readonly_text_receiver_can_still_receive_keys_or_focus(kind):
    port = ReceiverPort(role="AXTextField", readonly=True)
    assert await query(port).resolve(Intent(kind)) == (RECEIVER, (SCOPE.node,))
    assert port.node_queries == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insert", "paste"])
@pytest.mark.parametrize("role,readonly", [("AXGroup", False), ("AXTextField", True), (None, False)])
async def test_text_forwarding_still_rejects_nonediting_readonly_or_unknown_receivers(kind, role, readonly):
    port = ReceiverPort(role=role, readonly=readonly)
    with pytest.raises(ComputerUseError):
        await query(port).resolve(Intent(kind, {"text": "text"}))
    assert port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
@pytest.mark.parametrize("field", ["identity", "within_scope"])
@pytest.mark.parametrize("knowledge", [Knowledge.NO, Knowledge.UNKNOWN])
async def test_key_receiver_still_requires_original_scope_identity(kind, field, knowledge):
    port = ReceiverPort()
    setattr(port, field, knowledge)
    with pytest.raises(ComputerUseError) as caught:
        await query(port).resolve(Intent(kind))
    assert caught.value.code == "observation_incomplete"
    assert port.commands == [] and port.node_queries == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
@pytest.mark.parametrize(
    "evidence",
    [
        None,
        CurrentReceiverEvidence("absent"),
        CurrentReceiverEvidence("unavailable"),
        CurrentReceiverEvidence("conflict"),
        CurrentReceiverEvidence("confirmed", RECEIVER.node, ("other-container",)),
    ],
)
async def test_key_receiver_does_not_guess_missing_conflicting_or_other_scope_focus(kind, evidence):
    port = ReceiverPort()
    port.current = evidence
    with pytest.raises(ComputerUseError):
        await query(port).resolve(Intent(kind))
    assert port.commands == [] and port.node_queries == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
async def test_receiver_focus_change_after_forwarding_stops_before_dispatch(kind):
    port = ReceiverPort()
    port.focused = "unrelated-control"
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent(kind), SCOPE, forward_current=True)
    assert caught.value.code == "input_unavailable"
    assert "receiver changed" in caught.value.detail
    assert port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
async def test_confirmed_receiver_does_not_override_native_route_support(kind):
    port = ReceiverPort()
    port.route_supported = Knowledge.NO
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent(kind), SCOPE, forward_current=True)
    assert port.commands == []


class OpaqueScalarPort(ReceiverPort):
    capabilities = (*ReceiverPort.capabilities, "nativeTextReplacement")

    def __init__(self):
        super().__init__(role="AXTextField")
        self.route_ready = True
        self.fail_first_key = False
        self.actuator = self.receiver = None

    async def inspect(self, operation, target, timeout_ms, **options):
        facts = await super().inspect(operation, target, timeout_ms, **options)
        return replace(
            facts,
            actuator=self.actuator,
            receiver=self.receiver,
            primitives=tuple(
                replace(
                    primitive,
                    readiness=Knowledge.YES if self.route_ready else Knowledge.NO,
                    pending=() if self.route_ready else ("receiver",),
                )
                for primitive in facts.primitives
            ),
        )

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        batch = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)
        if target.node == RECEIVER.node:
            batch["relations"][0]["targets"] = ["opaque-child"]
        else:
            batch["nodes"][0]["values"].pop("AXRole", None)
            batch["nodes"][0]["fieldStatus"]["AXRole"] = {"state": "failed"}
        return batch

    async def execute(self, operation, step, command, timeout_ms):
        if self.fail_first_key and not self.commands:
            self.commands.append(command)
            return StepResult(
                command.target,
                Dispatch.NOT_DISPATCHED,
                "original key route unavailable",
                error_code="input_unavailable",
                retry_reason="routeUnavailable",
            )
        return await super().execute(operation, step, command, timeout_ms)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
@pytest.mark.parametrize("forward", [False, True])
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
async def test_ready_original_key_contract_does_not_require_opaque_child_evidence(kind, forward, platform):
    port = OpaqueScalarPort()
    port.platform = platform
    target = SCOPE if forward else RECEIVER
    result = await Engine(port).execute(Intent(kind), target, forward_current=forward)
    assert result["status"] == ("dispatched" if kind == "key" else "verified")
    assert len(port.commands) == 1
    command = port.commands[0]
    assert command.target == RECEIVER
    assert command.actuator is None and command.receiver is None
    assert port.node_queries == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insert", "paste", "replace"])
async def test_ready_keyboard_contract_does_not_skip_text_editor_resolution(kind):
    port = OpaqueScalarPort()
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent(kind, {"text": "text"}), RECEIVER)
    assert caught.value.code == "observation_incomplete"
    assert "unknown node" in caught.value.detail
    assert port.node_queries == [RECEIVER.node, "opaque-child"]
    assert port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
@pytest.mark.parametrize("condition", ["not-ready", "unsupported"])
async def test_unready_or_unsupported_key_contract_still_resolves_internal_editor(kind, condition):
    port = OpaqueScalarPort()
    if condition == "not-ready":
        port.route_ready = False
    else:
        port.route_supported = Knowledge.NO
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent(kind), RECEIVER)
    assert caught.value.code == "observation_incomplete"
    assert port.node_queries == [RECEIVER.node, "opaque-child"]
    assert port.commands == []


@pytest.mark.anyio
async def test_exhausted_original_key_route_cannot_hide_internal_editor_resolution():
    port = OpaqueScalarPort()
    port.fail_first_key = True
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("key", {"key": "Return"}), RECEIVER)
    assert caught.value.code == "observation_incomplete"
    assert port.node_queries == [RECEIVER.node, "opaque-child"]
    assert len(port.commands) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
@pytest.mark.parametrize("selected", ["actuator", "receiver"])
async def test_related_ready_contract_does_not_bypass_original_editor_resolution(kind, selected):
    port = OpaqueScalarPort()
    if selected == "actuator":
        port.actuator = Actuator("related-editor")
    else:
        port.receiver = FocusMember("related-focus")
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent(kind), RECEIVER)
    assert caught.value.code == "observation_incomplete"
    assert port.node_queries == [RECEIVER.node, "opaque-child"]
    assert port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
async def test_ready_original_contract_still_checks_forwarded_focus(kind):
    port = OpaqueScalarPort()
    port.focused = "unrelated-focus"
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent(kind), SCOPE, forward_current=True)
    assert caught.value.code == "input_unavailable"
    assert "receiver changed" in caught.value.detail
    assert port.node_queries == [] and port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["key", "focus"])
@pytest.mark.parametrize("boundary", ["identity", "scope", "modal"])
async def test_ready_original_contract_does_not_override_target_boundaries(kind, boundary):
    port = OpaqueScalarPort()
    if boundary == "identity":
        port.identity = Knowledge.NO
    elif boundary == "scope":
        port.within_scope = Knowledge.NO
    else:
        port.blocked_by = ("modal",)
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent(kind), RECEIVER)
    assert caught.value.code == ("blocked_by_modal" if boundary == "modal" else "stale_target")
    assert port.node_queries == [] and port.commands == []
