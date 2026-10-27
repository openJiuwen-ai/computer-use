"""Installation launch uses the shared execution barrier and exact target scope."""

import unittest
from copy import deepcopy

import sdk.native.test_native_transport as fixture

from computer_use.errors import ComputerUseError
from computer_use.native.contracts import ApplicationBinding, Intent
from computer_use.protocol.execution import decodeExecutionResult, decodeUnitCommand

TARGET = ApplicationBinding("native-v4-fixture", "installation-observed")


class LaunchWireTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeTransportTests.asyncSetUp
    asyncTearDown = fixture.NativeTransportTests.asyncTearDown
    engine = fixture.NativeTransportTests.engine
    requests = fixture.NativeTransportTests.requests

    async def test_launch_returns_exact_instance_without_window_or_input_context(self):
        result = await self.engine("launch").execute(Intent("launch"), TARGET)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["application"]["id"], "running:app")
        calls = self.requests()
        self.assertEqual([r["method"] for r in calls], ["hello", "begin", "inspect", "execute", "finish"])
        self.assertEqual(calls[2]["params"]["query"], {"kind": "launch"})
        command = calls[3]["params"]["command"]
        self.assertEqual(command["target"], dict(session=TARGET.session, application=TARGET.application))
        self.assertEqual(command["kind"], "launch_application")
        self.assertEqual(calls[1]["params"]["intent"], dict(kind="launch", parameters={}))
        self.assertFalse(command["foreground"])

    async def test_already_running_is_confirmed_without_launching_again(self):
        result = await self.engine("launch-already-running").execute(Intent("launch"), TARGET)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["application"]["id"], "running:app")

    async def test_changed_installation_cannot_retarget_or_launch(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.engine("launch-stale").execute(Intent("launch"), TARGET)
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertFalse(any(r["method"] == "execute" for r in self.requests()))

    async def test_uncertain_launch_is_not_replayed(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.engine("launch-uncertain").execute(Intent("launch"), TARGET)
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(sum(r["method"] == "execute" for r in self.requests()), 1)
        self.assertEqual(self.requests()[-1]["method"], "finish")

    async def test_old_driver_refuses_before_write_begin(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.engine("launch-missing-capability").execute(Intent("launch"), TARGET)
        self.assertEqual(caught.exception.effect, "none")
        self.assertEqual(caught.exception.code, "mode_unsupported")
        self.assertEqual([r["method"] for r in self.requests()], ["hello"])

    async def test_installation_binding_never_grants_keyboard_or_window_access(self):
        engine = self.engine("launch")
        for intent, target, kwargs in [
            (Intent("key", dict(key="Return")), TARGET, {}),
            (Intent("launch"), fixture.TARGET, {}),
            (Intent("launch", dict(path="/arbitrary")), TARGET, {}),
            (Intent("launch"), TARGET, dict(path=["File"])),
        ]:
            with self.assertRaises(ValueError):
                await engine.execute(intent, target, **kwargs)
        self.assertFalse(self.journal.exists())


class LaunchSchemaTests(unittest.TestCase):
    def test_launch_parameters_and_scope_are_separate_from_window_inputs(self):
        command = dict(
            kind="launch_application",
            primitiveId="p",
            target=dict(session="s", application="installation-a"),
            foreground=False,
        )
        self.assertEqual(decodeUnitCommand(command), command)
        cases = []
        for key, value in [("foreground", True), ("kind", "key_sequence")]:
            cases.append({**command, key: value})
        bad = deepcopy(command)
        bad["target"]["node"] = "foreign"
        cases.append(bad)
        bad = deepcopy(command)
        bad["intent"] = dict(kind="launch", parameters={"path": "/not-observed"})
        cases.append(bad)
        for value in cases:
            with self.assertRaises(ValueError):
                decodeUnitCommand(value)

    def test_running_receipt_is_only_legal_for_installation_scope(self):
        reply = dict(
            target=dict(session="s", instance="i", rootWindow="r", surface="w", node=None, capture=None),
            dispatch="dispatched",
            detail="done",
            preparation=[],
            confirmation="confirmed",
            cleanup="complete",
            errorCode=None,
            retryReason=None,
            context=None,
            application=dict(id="running:a", displayName="A", isRunning=True, path="/A.app", identifier="a"),
        )
        with self.assertRaises(ValueError):
            decodeExecutionResult("execute", reply)


if __name__ == "__main__":
    unittest.main()
