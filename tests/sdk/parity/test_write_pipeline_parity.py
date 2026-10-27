"""Exact native-call/effect baselines recorded before the v6 write migration."""

import asyncio
import json
import unittest
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

from repository import FIXTURES
from sdk.interaction.write.test_execution_engine import INVOKE, KEY, OBJECT, READY, TARGET, RecordingPort, facts, result

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Confirmation, Dispatch, HostPolicy, Intent, Primitive, UnitCommand


def frozen(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        # These traces exercise the legacy contract. Assert that negotiation did
        # not enable click extensions, then compare the original exact shape.
        absent = (
            {"click"}
            if isinstance(value, Primitive)
            else {"click_selection"}
            if isinstance(value, UnitCommand)
            else set()
        )
        for name in absent:
            assert getattr(value, name) is None, "Legacy trace unexpectedly enabled the click contract"
        return {f.name: frozen(getattr(value, f.name)) for f in fields(value) if f.name not in absent}
    if hasattr(value, "items"):
        return {k: frozen(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [frozen(v) for v in value]
    return value


class TracePort(RecordingPort):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    async def begin(self, intent, target, timeout_ms):
        self.events.append(["begin", frozen(intent), frozen(target)])
        return await super().begin(intent, target, timeout_ms)

    async def inspect(self, operation, target, timeout_ms, **scope):
        self.events.append(["inspect", operation, frozen(target), frozen(scope)])
        return await super().inspect(operation, target, timeout_ms)

    async def execute(self, operation, step, command, timeout_ms):
        self.events.append(["execute", operation, step, frozen(command)])
        return await super().execute(operation, step, command, timeout_ms)

    async def finish(self, operation):
        self.events.append(["finish", operation])
        return await super().finish(operation)


async def record_cases():
    ready_keys = facts(KEY, context=READY)
    cold_keys = facts(KEY)
    both = facts(OBJECT, KEY, context=READY)
    cases = {
        "object_replace": (
            Intent("replace", {"text": "value"}),
            [facts(OBJECT)],
            [result(confirmation=Confirmation.CONFIRMED)],
        ),
        "background_preparation": (
            Intent("insert", {"text": "value"}),
            [cold_keys, ready_keys],
            [result(Dispatch.NOT_DISPATCHED, preparation=("focused",)), result()],
        ),
        "empty_input": (Intent("insert", {"text": ""}), [facts(KEY)], []),
        "confirmed_focus": (
            Intent("focus"),
            [cold_keys],
            [result(Dispatch.NOT_DISPATCHED, preparation=("focused",), confirmation=Confirmation.CONFIRMED)],
        ),
        "alternate_before_dispatch": (
            Intent("replace", {"text": "value"}),
            [both],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="routeUnavailable"),
                result(),
            ],
        ),
        "one_precondition_replan": (
            Intent("insert", {"text": "value"}),
            [ready_keys],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preconditionChanged"),
                result(),
            ],
        ),
        "partial_never_replays": (Intent("replace", {"text": "value"}), [both], [result(Dispatch.PARTIAL)]),
        "unknown_never_replays": (Intent("replace", {"text": "value"}), [both], [result(Dispatch.UNKNOWN)]),
        "transport_unknown": (Intent("replace", {"text": "value"}), [both], [ConnectionError("lost reply")]),
        "cancelled_dispatch": (Intent("replace", {"text": "value"}), [both], [asyncio.CancelledError()]),
        "modal_before_dispatch": (Intent("click"), [facts(INVOKE, blocked_by=("modal",))], []),
        "foreground_assist": (
            Intent("key", {"key": "Return"}),
            [cold_keys, cold_keys, ready_keys],
            [
                result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preparationUnavailable"),
                result(Dispatch.NOT_DISPATCHED, preparation=("foreground",)),
                result(),
            ],
        ),
    }
    output = {}
    for name, (intent, observations, results) in cases.items():
        port = TracePort(observations, results, HostPolicy(foreground_assist=True))
        try:
            receipt = await Engine(port).execute(intent, TARGET)
        except ComputerUseError as error:
            receipt = {"error": {key: getattr(error, key) for key in ("code", "target", "detail", "effect", "next")}}
        except asyncio.CancelledError:
            receipt = {"cancelled": True}
        output[name] = {
            "calls": port.events,
            "result": frozen(receipt),
            "retired": port.calls.retired,
            "busy": port.calls.busy,
            "finished": port.finished,
        }
    return output


class WriteBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_order_target_commands_effects_and_cleanup_match_pre_v6(self):
        expected = json.loads((FIXTURES / "v6-write-baseline.json").read_text())
        actual = await record_cases()
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(actual[name], value)


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--record":
        Path(sys.argv[2]).write_text(json.dumps(asyncio.run(record_cases()), ensure_ascii=False, indent=2) + "\n")
    else:
        unittest.main()
