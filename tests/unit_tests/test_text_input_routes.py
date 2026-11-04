# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Receiver preparation, delivery availability and text dispatch are separate contracts."""

import unittest
from dataclasses import replace

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.routes.policy import Attempts, choose
from computer_use.native.contracts import (
    Binding,
    Channel,
    Cleanup,
    Confirmation,
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

TARGET = Binding("session", "app", "root", "window", "field")
EDITOR = Primitive(
    "edit", "native_text_edit", Channel.EDITOR, Knowledge.YES, Knowledge.YES, background_preparation=Knowledge.YES
)
KEYS = Primitive(
    "keys", "key_sequence", Channel.KEYBOARD, Knowledge.YES, Knowledge.YES, background_preparation=Knowledge.YES
)
FOCUS = Primitive(
    "focus",
    "prepare_receiver",
    Channel.EDITOR,
    Knowledge.YES,
    Knowledge.NO,
    pending=("receiver",),
    background_preparation=Knowledge.YES,
)


def observed(*routes, **changes):
    return replace(
        Facts(
            TARGET,
            "observation",
            "input",
            Knowledge.YES,
            Knowledge.YES,
            Knowledge.YES,
            (),
            tuple(routes),
            InputContext(),
            platform="macos",
        ),
        **changes,
    )


class TextRouteSelectionTests(unittest.TestCase):
    def test_ready_editor_precedes_keyboard_preparation_on_every_platform(self):
        for platform in ("macos", "windows", "linux"):
            for kind in ("insert", "replace"):
                with self.subTest(platform=platform, kind=kind):
                    keys = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))
                    command = choose(
                        observed(EDITOR, keys, platform=platform),
                        Intent(kind, {"text": "query"}),
                        HostPolicy(),
                        Attempts(),
                    )
                    self.assertEqual(command.primitive, EDITOR)
                    self.assertFalse(command.prepare or command.foreground)

    def test_unavailable_keyboard_endpoint_cannot_preempt_ready_editor(self):
        keys = replace(KEYS, supported=Knowledge.NO)
        command = choose(observed(EDITOR, keys), Intent("insert", {"text": "query"}), HostPolicy(), Attempts())
        self.assertEqual(command.primitive, EDITOR)

    def test_ready_keyboard_preserves_ordinary_edit_callbacks(self):
        command = choose(observed(EDITOR, KEYS), Intent("insert", {"text": "query"}), HostPolicy(), Attempts())
        self.assertEqual(command.primitive, KEYS)
        self.assertFalse(command.prepare or command.foreground)

    def test_explicit_keys_and_paste_keep_their_semantics(self):
        command = choose(observed(EDITOR, KEYS), Intent("key", {"key": "Return"}), HostPolicy(), Attempts())
        self.assertEqual(command.primitive, KEYS)
        with self.assertRaises(ComputerUseError):
            choose(observed(EDITOR), Intent("paste", {"text": "query"}), HostPolicy(), Attempts())

    def test_ready_editor_does_not_override_target_boundaries(self):
        for changed in ({"identity": Knowledge.NO}, {"within_scope": Knowledge.NO}, {"blocked_by": ("modal",)}):
            with self.subTest(changed=changed), self.assertRaises(ComputerUseError):
                choose(observed(EDITOR, **changed), Intent("insert", {"text": "query"}), HostPolicy(), Attempts())


class RecordingPort:
    def __init__(self, observations, results, policy=None):
        self.policy = policy or HostPolicy()
        self.observations = list(observations)
        self.results = list(results)
        self.commands = []
        self.inspections = 0
        self.finished = []
        self.calls = CallLifecycle(self)

    async def begin(self, intent, target, timeout_ms):
        return OpenedOperation("operation", target)

    async def inspect(self, operation, target, timeout_ms):
        self.inspections += 1
        return self.observations.pop(0) if len(self.observations) > 1 else self.observations[0]

    async def execute(self, operation, step, command, timeout_ms):
        self.commands.append(command)
        return self.results.pop(0)

    async def finish(self, operation):
        self.finished.append(operation)
        return Cleanup.COMPLETE


class TextInputExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_focus_refreshes_capabilities_before_one_native_edit(self):
        cold_keys = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))
        unavailable_keys = replace(KEYS, supported=Knowledge.NO)
        port = RecordingPort(
            [observed(FOCUS, cold_keys), observed(EDITOR, unavailable_keys)],
            [
                StepResult(
                    TARGET,
                    Dispatch.NOT_DISPATCHED,
                    "native focus",
                    preparation=("native element focus",),
                    confirmation=Confirmation.CONFIRMED,
                ),
                StepResult(TARGET, Dispatch.DISPATCHED, "text sent"),
            ],
        )
        result = await Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(
            [(c.primitive.unit, c.prepare) for c in port.commands],
            [("prepare_receiver", True), ("native_text_edit", False)],
        )
        self.assertEqual(port.inspections, 2)
        self.assertTrue(all(c.target == TARGET and not c.foreground for c in port.commands))
        self.assertEqual(port.finished, ["operation"])

    async def test_preparation_does_not_invent_editing_capability(self):
        port = RecordingPort(
            [observed(FOCUS), observed()], [StepResult(TARGET, Dispatch.NOT_DISPATCHED, "native focus")]
        )
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
        self.assertEqual(len(port.commands), 1)
        self.assertTrue(port.commands[0].prepare)

    async def test_uncertain_edit_is_never_replayed_as_keys(self):
        cold_keys = replace(KEYS, readiness=Knowledge.NO, pending=("receiver",))
        for dispatch in (Dispatch.DISPATCHED, Dispatch.UNKNOWN, Dispatch.PARTIAL):
            with self.subTest(dispatch=dispatch):
                port = RecordingPort(
                    [observed(EDITOR, cold_keys)], [StepResult(TARGET, dispatch, "application effect unconfirmed")]
                )
                operation = Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
                if dispatch == Dispatch.DISPATCHED:
                    self.assertEqual((await operation)["status"], "dispatched")
                else:
                    with self.assertRaises(ComputerUseError) as caught:
                        await operation
                    self.assertNotEqual(caught.exception.effect, "none")
                self.assertEqual([c.primitive.unit for c in port.commands], ["native_text_edit"])

    async def test_modal_appearing_during_preparation_prevents_text(self):
        port = RecordingPort(
            [observed(FOCUS), observed(EDITOR, blocked_by=("new modal",))],
            [StepResult(TARGET, Dispatch.NOT_DISPATCHED, "native focus")],
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
        self.assertEqual(caught.exception.code, "blocked_by_modal")
        self.assertEqual(len(port.commands), 1)

    async def test_known_foreground_requirement_prepares_before_text(self):
        focus = replace(FOCUS, pending=("foreground",), background_preparation=Knowledge.NO)
        port = RecordingPort(
            [observed(focus), observed(EDITOR)],
            [
                StepResult(TARGET, Dispatch.NOT_DISPATCHED, "foreground focus"),
                StepResult(TARGET, Dispatch.DISPATCHED, "text sent"),
            ],
        )
        result = await Engine(port).execute(Intent("insert", {"text": "query"}), TARGET)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual([(c.prepare, c.foreground) for c in port.commands], [(True, True), (False, False)])
