# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A confirmed receiver's editing declaration does not depend on a role label."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import (
    Binding,
    Channel,
    Cleanup,
    Confirmation,
    CurrentReceiverEvidence,
    Dispatch,
    Facts,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge,
    OpenedOperation,
    Primitive,
    StepResult,
)
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.semantics import PROPERTIES, RELATIONS


@pytest.fixture
def anyio_backend():
    return "asyncio"


SCOPE = Binding("session", "app", "window", "surface", "container")
TARGET = replace(SCOPE, node="receiver")


class TextEvidencePort:
    """A provider with a fixed recipient, separate editing facts and dispatch log."""

    policy = HostPolicy()
    capabilities = ("nativeCurrentReceiver", "nativeActuators", "nativeTextInput", "nativeTextReplacement")

    def __init__(self, platform="macos", role="unknown", *, editable=True, focus_writable=True):
        self.platform, self.role = platform, role
        self.editable, self.focus_writable = editable, focus_writable
        self.calls = CallLifecycle(self)
        self.current = CurrentReceiverEvidence("confirmed", TARGET.node, (SCOPE.node,))
        self.focused = TARGET.node
        self.identity = self.within_scope = self.supported = Knowledge.YES
        self.blocked_by = ()
        self.queries, self.inspections, self.commands, self.finished = [], [], [], []
        self.contents = "original"
        self.foreground = False
        self.key_state = "ready"
        self.value_route = False
        self.actuator = self.receiver = None
        self.opaque_child = False

    def raw_role(self):
        roles = {
            "macos": {
                "unknown": "AXUnknown",
                "group": "AXGroup",
                "scalar": "AXComboBox",
                "field": "AXTextField",
                "custom": "AXProjectEditor",
                "button": "AXButton",
                "slider": "AXSlider",
            },
            "windows": {
                "unknown": "ControlType.Unknown",
                "group": "ControlType.Group",
                "field": "ControlType.Edit",
                "custom": "ControlType.Custom",
                "button": "ControlType.Button",
                "slider": "ControlType.Slider",
            },
            "linux": {
                "unknown": "unknown",
                "group": "panel",
                "field": "entry",
                "custom": "project text editor",
                "button": "push button",
                "slider": "slider",
            },
        }
        return roles[self.platform].get(self.role, roles[self.platform]["unknown"])

    def editing_values(self):
        values = {"enabled": True, "sensitive": True, "focusWritable": self.focus_writable}
        if self.editable is None:
            return values
        if self.platform == "macos":
            values.update(editable=self.editable, valueWritable=self.editable, textWritable=self.editable)
        elif self.platform == "windows":
            values.update(readOnly=not self.editable, interfaces=["ValuePatternIdentifiers.Pattern"])
        else:
            values.update(
                editable=self.editable, readOnly=not self.editable, interfaces=["EditableText", "Text", "Component"]
            )
        return values

    async def begin(self, intent, target, timeout_ms):
        self.intent = intent
        return OpenedOperation("operation", target)

    async def inspect(self, operation, target, timeout_ms, **options):
        self.inspections.append((target, options))
        primitives = []
        if self.value_route:
            primitives.append(Primitive("value", "native_value_replace", Channel.OBJECT, self.supported, Knowledge.YES))
        if self.key_state is not None:
            unit = "clipboard_paste" if getattr(self, "intent", Intent("insert")).kind == "paste" else "key_sequence"
            primitives.append(
                Primitive(
                    "keys",
                    unit,
                    Channel.KEYBOARD,
                    self.supported,
                    Knowledge.YES if self.key_state == "ready" else Knowledge.NO,
                    () if self.key_state == "ready" else ("receiver",),
                    Knowledge.YES,
                )
            )
        return Facts(
            target,
            "observation",
            "text receiver",
            self.identity,
            self.within_scope,
            Knowledge.YES,
            self.blocked_by,
            () if options.get("current_receiver") else tuple(primitives),
            InputContext(
                focused_node=self.focused,
                current_receiver=self.current,
                foreground_surface=target.surface if self.foreground else "other-surface",
            ),
            platform=self.platform,
            native_role=self.raw_role(),
            actuator=self.actuator,
            receiver=self.receiver,
        )

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        self.queries.append(target.node)
        properties = PROPERTIES[self.platform]
        values = {properties[key]: value for key, value in self.editing_values().items() if key in properties}
        values[properties["role"]] = self.raw_role()
        if target.node == "unrelated-field":
            values[properties["role"]] = {"macos": "AXButton", "windows": "ControlType.Button", "linux": "push button"}[
                self.platform
            ]
        statuses = {}
        if self.role in ("unsupported", "failed") or target.node == "opaque-child":
            values.pop(properties["role"])
            statuses[properties["role"]] = {"state": "unsupported" if self.role == "unsupported" else "failed"}
        for field in fields:
            if field not in values and field not in statuses:
                statuses[field] = {"state": "unsupported"}
        children = next(native for native, semantic in RELATIONS[self.platform].items() if semantic == "children")
        return {
            "session": target.session,
            "surface": target.surface,
            "platform": self.platform,
            "roots": [target.node],
            "nodes": [
                {
                    "node": target.node,
                    "requestedFields": fields,
                    "values": values,
                    "fieldStatus": statuses,
                    "actions": {"state": "complete", "items": []},
                }
            ],
            "relations": [
                {
                    "source": target.node,
                    "property": children,
                    "targets": ["opaque-child"] if self.opaque_child and target.node == TARGET.node else [],
                    "state": "complete",
                    "certainty": "confirmed",
                }
            ],
            "observed": {"id": f"raw-{len(self.queries)}"},
        }

    async def execute(self, operation, step, command, timeout_ms):
        assert command.target == TARGET
        self.commands.append(command)
        if command.prepare:
            self.key_state = "ready"
            return StepResult(
                command.target,
                Dispatch.NOT_DISPATCHED,
                "receiver prepared",
                preparation=("provider focus",),
                confirmation=Confirmation.CONFIRMED,
            )
        self.contents = (
            command.intent.parameters["text"]
            if command.intent.kind == "replace"
            else self.contents + command.intent.parameters["text"]
        )
        return StepResult(command.target, Dispatch.DISPATCHED, "one original recipient input sent")

    async def finish(self, operation):
        self.finished.append(operation)
        return Cleanup.COMPLETE


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
@pytest.mark.parametrize("role", ["unknown", "unsupported", "failed"])
@pytest.mark.parametrize("kind", ["insert", "paste"])
@pytest.mark.parametrize("focus_writable", [False, True])
async def test_confirmed_editable_recipient_does_not_require_role_or_writable_focus(
    platform, role, kind, focus_writable
):
    port = TextEvidencePort(platform, role, focus_writable=focus_writable)
    port.opaque_child = True
    result = await Engine(port).execute(Intent(kind, {"text": "X"}), SCOPE, forward_current=True)
    assert result["status"] == "dispatched" and port.contents == "originalX"
    assert len(port.commands) == 1 and port.commands[0].target == TARGET
    assert not port.commands[0].prepare and not port.commands[0].foreground
    assert port.commands[0].actuator is None and port.commands[0].receiver is None
    assert all(target == TARGET.node for target in port.queries)
    assert port.inspections[-1][1]["within"] == (SCOPE.node,)
    assert port.finished == ["operation"]


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
@pytest.mark.parametrize("kind", ["insert", "paste"])
@pytest.mark.parametrize(
    "role,editable", [("unknown", None), ("unknown", False), ("group", True), ("button", None), ("slider", None)]
)
async def test_confirmed_focus_does_not_invent_editing_or_turn_a_container_into_an_editor(
    platform, kind, role, editable
):
    port = TextEvidencePort(platform, role, editable=editable)
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent(kind, {"text": "X"}), SCOPE, forward_current=True)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boundary", ["identity", "scope", "modal", "focus", "conflict", "unavailable", "other-scope", "route"]
)
@pytest.mark.parametrize("kind", ["insert", "paste"])
async def test_role_independent_editing_still_requires_the_original_current_recipient(boundary, kind):
    port = TextEvidencePort()
    if boundary == "identity":
        port.identity = Knowledge.NO
    elif boundary == "scope":
        port.within_scope = Knowledge.NO
    elif boundary == "modal":
        port.blocked_by = ("modal",)
    elif boundary == "focus":
        port.focused = "another-field"
    elif boundary in ("conflict", "unavailable"):
        port.current = CurrentReceiverEvidence(boundary)
    elif boundary == "other-scope":
        port.current = CurrentReceiverEvidence("confirmed", TARGET.node, ("other-container",))
    else:
        port.supported = Knowledge.NO
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent(kind, {"text": "X"}), SCOPE, forward_current=True)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
async def test_missing_role_on_an_unselected_descendant_does_not_grant_editor_containment():
    port = TextEvidencePort(role="scalar")
    port.opaque_child = True
    with pytest.raises(ComputerUseError) as caught:
        await Engine(port).execute(Intent("insert", {"text": "X"}), TARGET)
    assert caught.value.code == "observation_incomplete"
    assert port.queries == [TARGET.node, "opaque-child"]
    assert port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insert", "paste"])
@pytest.mark.parametrize("focus_writable", [False, True])
async def test_windows_custom_current_recipient_uses_its_actual_editing_declaration(kind, focus_writable):
    port = TextEvidencePort("windows", "custom", focus_writable=focus_writable)
    port.opaque_child = True
    result = await Engine(port).execute(Intent(kind, {"text": "X"}), SCOPE, forward_current=True)
    assert result["status"] == "dispatched" and port.contents == "originalX"
    assert len(port.commands) == 1 and port.commands[0].target == TARGET
    assert not port.commands[0].prepare and not port.commands[0].foreground
    assert all(target == TARGET.node for target in port.queries)
