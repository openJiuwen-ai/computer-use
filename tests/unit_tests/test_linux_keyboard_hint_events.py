# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native focus-loss events invalidate only their captured keyboard recipient."""

import importlib
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


class States(set):
    def contains(self, flag):
        return flag in self


class Node:
    def __init__(self, key, parent=None, *, role="spin button"):
        self.key, self.parent, self.role = key, parent, role
        self.children = []
        self.states = States(("FOCUSED", "EDITABLE", "SHOWING", "VISIBLE"))
        if parent is not None:
            parent.children.append(self)


@pytest.fixture(scope="module")
def native_modules():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return SimpleNamespace(
            sequence=importlib.import_module("computer_use_linux.control.native_sequence_receiver"),
            accessibility=importlib.import_module("computer_use_linux.objects.native_accessibility"),
            runtime=importlib.import_module("computer_use_linux.runtime.native"),
            operation=importlib.import_module("computer_use_linux.runtime.native_operation"),
            protocol=importlib.import_module("computer_use_linux.contracts.protocol"),
        )


@pytest.fixture
def scene(native_modules):
    ctx = native_modules.protocol.Request(time.monotonic() + 5, threading.Event())
    root = Node("root", role="frame")
    field = Node("field", root)
    focus = SimpleNamespace(xid=77)
    accessible = SimpleNamespace(
        get_state_set=lambda obj: obj.states,
        get_parent=lambda obj: obj.parent,
        get_role_name=lambda obj: obj.role,
        get_interfaces=lambda obj: ["Text"] if obj.role == "entry" else [],
        get_child_count=lambda obj: len(obj.children),
        get_child_at_index=lambda obj, index: obj.children[index],
    )
    flags = ("FOCUSED", "EDITABLE", "READ_ONLY", "MANAGES_DESCENDANTS", "SHOWING", "VISIBLE")
    ax = SimpleNamespace(
        native_key=lambda obj: obj.key,
        call=Mock(side_effect=lambda method, *args: method(*args)),
        live=Mock(),
        begin=lambda _: None,
        pump=lambda: None,
        ctx=ctx,
        glib=SimpleNamespace(Error=RuntimeError),
        atspi=SimpleNamespace(Accessible=accessible, StateType=SimpleNamespace(**{flag: flag for flag in flags})),
        _state="ready",
        _clear_cache=Mock(),
        focus_events=OrderedDict(),
    )
    desktop = SimpleNamespace(
        active=lambda: "x-window",
        focus_belongs=lambda _: True,
        keyboard_focus=lambda: focus.xid,
        press_key=Mock(),
        type_text=Mock(),
        pointer_sequence=Mock(),
    )
    backend = SimpleNamespace(ax=ax, desktop=desktop, coordinate=Mock())
    execution = native_modules.runtime.NativeExecution(backend, "session")

    def capture(*, pointer_focused=False):
        return native_modules.sequence.NativeSequenceReceiver(
            backend,
            SimpleNamespace(key="x-window", accessible=root),
            ctx,
            recipients=execution.keyboard_recipients,
            obj=field,
            pointer_focused=pointer_focused,
        )

    def lose(source):
        ax.call.reset_mock()
        ax.live.reset_mock()
        native_modules.accessibility.NativeAccessibility.event(
            ax, SimpleNamespace(type="object:state-changed:focused", source=source, detail1=0)
        )
        # The event boundary may invalidate local state, never inspect ancestry
        # or query the provider while it is delivering the notification.
        ax.call.assert_not_called()
        ax.live.assert_not_called()

    yield SimpleNamespace(
        ctx=ctx,
        root=root,
        field=field,
        focus=focus,
        ax=ax,
        execution=execution,
        capture=capture,
        lose=lose,
        failure=native_modules.protocol.Failure,
    )
    execution.close()
    desktop.press_key.assert_not_called()
    desktop.type_text.assert_not_called()
    desktop.pointer_sequence.assert_not_called()


@pytest.mark.parametrize("reuse", (False, True))
def test_unrelated_focus_loss_preserves_current_or_reused_capture(scene, reuse):
    sequence = scene.capture(pointer_focused=True)
    if reuse:
        sequence = scene.capture()
        assert sequence.source == "hint"

    scene.lose(Node("unrelated-field"))

    sequence.validate()
    assert sequence.hint is scene.execution.keyboard_recipients.hint


def test_requested_focus_scope_loss_does_not_invalidate_captured_editor(scene):
    editor = Node("editor", scene.field, role="entry")
    sequence = scene.capture(pointer_focused=True)
    assert sequence.obj is editor
    scene.field.states.remove("FOCUSED")

    scene.lose(scene.field)

    sequence.validate()
    assert sequence.hint is scene.execution.keyboard_recipients.hint


@pytest.mark.parametrize("reuse", (False, True))
@pytest.mark.parametrize("different_proxy", (False, True))
def test_captured_recipient_loss_stops_even_before_state_publication(scene, reuse, different_proxy):
    sequence = scene.capture(pointer_focused=True)
    if reuse:
        sequence = scene.capture()
    source = Node(scene.field.key) if different_proxy else scene.field

    scene.lose(source)

    assert scene.field.states.contains("FOCUSED")  # Provider publication is still lagging.
    assert scene.execution.keyboard_recipients.hint is None
    with pytest.raises(scene.failure, match="Native focus was lost"):
        sequence.validate()


@pytest.mark.parametrize("change", ("focused", "xid"))
def test_live_recipient_evidence_still_stops_input_without_an_event(scene, change):
    sequence = scene.capture(pointer_focused=True)
    if change == "focused":
        scene.field.states.remove("FOCUSED")
    else:
        scene.focus.xid = 88

    with pytest.raises(scene.failure):
        sequence.validate()
    assert scene.execution.keyboard_recipients.hint is None


@pytest.mark.parametrize("new_owner", (False, True))
def test_runtime_close_clears_hint_and_only_unhooks_its_own_callback(scene, new_owner):
    scene.capture(pointer_focused=True)
    newer_hook = Mock()
    if new_owner:
        scene.ax.focus_lost = newer_hook

    scene.execution.close()

    assert scene.execution.keyboard_recipients.hint is None
    assert scene.ax.focus_lost is (newer_hook if new_owner else None)


def test_recipient_loss_after_dispatch_never_replays_an_operation_step(scene, native_modules):
    sequence = scene.capture(pointer_focused=True)
    operation = native_modules.operation.NativeOperation({}, scene.ctx.deadline, {"kind": "replace"}, writable=True)
    attempts = []

    def execute():
        attempts.append("requested input barrier")
        operation.dispatched = True
        scene.lose(scene.field)
        try:
            sequence.validate()
        except scene.failure as error:
            return {"dispatch": "unknown", "errorCode": error.code, "retryReason": None}
        pytest.fail("The captured recipient's loss must stop the operation.")

    receipt = operation.step(1, {"kind": "key_sequence"}, execute)
    assert operation.step(1, {"kind": "key_sequence"}, execute) == receipt
    assert receipt == {"dispatch": "unknown", "errorCode": "input_unavailable", "retryReason": None}
    with pytest.raises(scene.failure, match="already ended or dispatched") as caught:
        operation.step(2, {"kind": "key_sequence"}, execute)
    assert caught.value.effect == "possible"
    assert len(attempts) == 1
