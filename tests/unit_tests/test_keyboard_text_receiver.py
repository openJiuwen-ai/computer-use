# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keyboard streams retain their own admission and exact recipient contract."""

from dataclasses import replace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Channel, Dispatch, Intent, Knowledge, Primitive, StepResult
from computer_use.native.semantics import control_capabilities, keyboard_input_evidence
from unit_tests.test_confirmed_text_receiver_evidence import SCOPE, TARGET, TextEvidencePort


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StreamPort(TextEvidencePort):
    """The recorded Terminal AX facts, without any app name in qualification."""

    def __init__(self, *, raw_role="AXTextArea", **changes):
        super().__init__(editable=None)
        self.native_role = raw_role
        self.changes = changes

    def raw_role(self):
        return self.native_role

    def editing_values(self):
        # Terminal omitted AXEnabled/AXEditable, exposed writable AXFocused,
        # and explicitly reported both buffer setters as not writable.
        return {"focusWritable": True, "valueWritable": False, "textWritable": False, **self.changes}


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insert", "paste"])
@pytest.mark.parametrize("forward_current", [False, True])
@pytest.mark.parametrize("initial_state", ["ready", "preparable"])
async def test_text_stream_input_does_not_require_ax_buffer_setters(kind, forward_current, initial_state):
    port = StreamPort()
    port.key_state = initial_state
    result = await Engine(port).execute(
        Intent(kind, {"text": "printf hello"}),
        SCOPE if forward_current else TARGET,
        forward_current=forward_current,
    )
    assert result["status"] == "dispatched"
    assert port.contents == "originalprintf hello"
    assert all(command.target == TARGET for command in port.commands)
    assert sum(not command.prepare for command in port.commands) == 1
    assert sum(command.prepare for command in port.commands) == (initial_state == "preparable")
    assert all(not command.foreground for command in port.commands)
    assert port.finished == ["operation"]


@pytest.mark.anyio
@pytest.mark.parametrize("raw_role", ["AXGroup", "AXButton", "AXUnknown", "AXStaticText"])
async def test_focus_writable_arbitrary_objects_do_not_become_text_streams(raw_role):
    port = StreamPort(raw_role=raw_role)
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent("insert", {"text": "X"}), TARGET)
    assert port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [{"editable": False}, {"enabled": False}])
@pytest.mark.parametrize("forward_current", [False, True])
async def test_explicit_disabled_or_noneditable_text_stream_is_still_refused(changes, forward_current):
    port = StreamPort(**changes)
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(
            Intent("insert", {"text": "X"}),
            SCOPE if forward_current else TARGET,
            forward_current=forward_current,
        )
    assert port.commands == []


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["identity", "within_scope", "supported", "modal"])
async def test_text_stream_semantics_do_not_override_native_target_or_route_rejection(boundary):
    port = StreamPort()
    if boundary == "modal":
        port.blocked_by = ("modal",)
    else:
        setattr(port, boundary, Knowledge.NO)
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent("insert", {"text": "X"}), TARGET)
    assert port.commands == []


@pytest.mark.anyio
async def test_keyboard_stream_capability_does_not_authorize_whole_buffer_replacement():
    port = StreamPort()
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent("replace", {"text": "replacement"}), TARGET)
    assert port.commands == [] and port.contents == "original"


def test_observation_advertises_text_input_separately_from_replacement():
    node = {
        "values": {
            "AXRole": "AXTextArea", "settable.AXFocused": True,
            "settable.AXValue": False, "settable.AXSelectedText": False,
        },
        "fieldStatus": {"AXEditable": {"state": "unsupported"}, "AXEnabled": {"state": "unsupported"}},
        "actions": {"state": "complete", "items": []},
    }
    capabilities = control_capabilities("macos", node, ("nativeTextInput", "nativeActuators"))
    assert capabilities["typeText"] == "available"
    assert capabilities["replaceText"] == "unknown"


@pytest.mark.parametrize("declaration", ["readOnly", "textReadOnly"])
def test_explicit_read_only_declarations_do_not_qualify_a_text_stream(declaration):
    values = {"focusWritable": True, "valueWritable": False, "textWritable": False, declaration: True}
    assert keyboard_input_evidence("textArea", values, platform="macos") is not True


@pytest.mark.anyio
@pytest.mark.parametrize("offers_cell_selection", [False, True])
async def test_cell_selection_preparation_reinspects_the_original_before_text(offers_cell_selection):
    class CellPort(TextEvidencePort):
        capabilities = (*TextEvidencePort.capabilities, "nativeEditorPreparation")

        def __init__(self):
            super().__init__(editable=None)
            self.focused = None
            self.key_state = None
            self.selected = False

        def raw_role(self):
            return "AXCell"

        async def inspect(self, operation, target, timeout_ms, **options):
            facts = await super().inspect(operation, target, timeout_ms, **options)
            if self.selected:
                actuator = options.get("actuator")
                if actuator is None:
                    return replace(facts, primitives=())
                assert actuator.node == "original-editor"
                return replace(facts, actuator=actuator)
            if not offers_cell_selection:
                return facts
            preparation = Primitive(
                "cell-selection", "prepare_receiver", Channel.EDITOR, Knowledge.YES,
                Knowledge.NO, ("receiver",), Knowledge.YES,
            )
            return replace(facts, primitives=(preparation,))

        async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
            batch = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)
            if self.selected and target.node == TARGET.node:
                batch["relations"][0]["targets"] = ["original-editor"]
            elif target.node == "original-editor":
                node = batch["nodes"][0]
                node["values"].update({"AXRole": "AXTextField", "AXEnabled": True, "AXEditable": True})
                node["fieldStatus"].pop("AXEditable", None)
                batch["relations"].append(
                    {
                        "source": target.node, "property": "AXParent", "targets": [TARGET.node],
                        "state": "complete", "certainty": "confirmed",
                    }
                )
            return batch

        async def execute(self, operation, step, command, timeout_ms):
            if command.prepare:
                self.selected = True
            return await super().execute(operation, step, command, timeout_ms)

    port = CellPort()
    if not offers_cell_selection:
        with pytest.raises(ComputerUseError):
            await Engine(port).execute(Intent("insert", {"text": "X"}), TARGET)
        assert not port.commands
        return
    result = await Engine(port).execute(Intent("insert", {"text": "X"}), TARGET)
    assert result["status"] == "dispatched"
    preparation, insertion = port.commands
    assert preparation.prepare and not insertion.prepare
    assert preparation.target == insertion.target == TARGET
    assert preparation.actuator is None and insertion.actuator.node == "original-editor"
    assert not preparation.foreground and not insertion.foreground
    assert port.contents == "originalX"


class WindowsTextStreamPort(TextEvidencePort):
    """A Windows text peer with a native keyboard offer and separate text-range facts."""

    capabilities = ("nativeTextInput", "nativeTextReplacement")

    def __init__(self):
        super().__init__("windows")
        self.keyboard_surface = TARGET.surface
        self.declarations = {
            "enabled": True,
            "focusWritable": True,
            "interfaces": ["TextPatternIdentifiers.Pattern"],
            "textReadOnly": False,
        }

    def raw_role(self):
        return "ControlType.Text"

    def editing_values(self):
        return self.declarations

    async def inspect(self, operation, target, timeout_ms, **options):
        facts = await super().inspect(operation, target, timeout_ms, **options)
        return replace(facts, input=replace(facts.input, keyboard_surface=self.keyboard_surface))


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["insert", "paste"])
async def test_windows_focused_text_range_reuses_the_exact_ready_keyboard_offer(kind):
    port = WindowsTextStreamPort()
    result = await Engine(port).execute(Intent(kind, {"text": "X"}), TARGET)
    assert result["status"] == "dispatched" and port.contents == "originalX"
    assert len(port.commands) == 1 and port.commands[0].target == TARGET
    assert not port.commands[0].prepare and not port.commands[0].foreground


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled", False),
        ("readOnly", True),
        ("textReadOnly", True),
        ("textReadOnly", None),
        ("interfaces", ["ValuePatternIdentifiers.Pattern"]),
        ("focus", "other-node"),
        ("queue", "other-surface"),
        ("readiness", "preparable"),
    ],
)
async def test_windows_text_stream_retains_semantic_and_native_receiver_boundaries(field, value):
    port = WindowsTextStreamPort()
    if field == "focus":
        port.focused = value
    elif field == "queue":
        port.keyboard_surface = value
    elif field == "readiness":
        port.key_state = value
    else:
        port.declarations[field] = value
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent("insert", {"text": "X"}), TARGET)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
async def test_windows_focused_text_stream_does_not_gain_a_replace_route():
    port = WindowsTextStreamPort()
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent("replace", {"text": "X"}), TARGET)
    assert port.commands == [] and port.contents == "original"


@pytest.mark.anyio
async def test_windows_text_stream_does_not_reprepare_after_the_exact_route_loses_focus():
    port = WindowsTextStreamPort()

    async def lose_focus(operation, step, command, timeout_ms):
        port.commands.append(command)
        port.key_state = "preparable"
        port.focused = "other-node"
        return StepResult(
            TARGET,
            Dispatch.NOT_DISPATCHED,
            "The original receiver lost focus.",
            error_code="input_unavailable",
            retry_reason="routeUnavailable",
        )

    port.execute = lose_focus
    with pytest.raises(ComputerUseError):
        await Engine(port).execute(Intent("insert", {"text": "X"}), TARGET)
    assert len(port.inspections) == 2
    assert len(port.commands) == 1 and not port.commands[0].prepare
    assert port.contents == "original"
