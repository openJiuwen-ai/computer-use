"""A native object edit owns its evidence; optional editor trees cannot veto it."""

import unittest
from dataclasses import replace

from sdk.interaction.write.test_execution_engine import OBJECT, TARGET, VALUE, RecordingPort, facts, result

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Dispatch, Intent, Knowledge as K


class IndependentPort(RecordingPort):
    capabilities = frozenset(("nativeActuators", "nativeTextReplacement", "nativeTextInput"))
    platform = "macos"

    def __init__(self, observation=None, results=None):
        super().__init__([observation or facts(OBJECT)], results if results is not None else [result()])
        self.graph_reads = 0

    async def query_nodes(self, *args, **kwargs):
        self.graph_reads += 1
        raise ComputerUseError(
            dict(
                code="observation_incomplete",
                effect="none",
                target="optional editor tree",
                detail="The provider cannot enumerate editor metadata.",
                next="Use an independently declared route.",
            )
        )


class IndependentTextRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_object_contract_does_not_require_editor_metadata(self):
        for kind in ("replace", "insert"):
            with self.subTest(kind=kind):
                port = IndependentPort()
                outcome = await Engine(port).execute(Intent(kind, {"text": "中😀"}), TARGET)
                self.assertEqual(outcome["status"], "dispatched")
                self.assertEqual(port.graph_reads, 0)
                self.assertEqual(len(port.commands), 1)
                command = port.commands[0][1]
                self.assertEqual((command.target, command.primitive), (TARGET, OBJECT))
                self.assertFalse(command.prepare or command.foreground)
                self.assertIsNone(command.actuator)
                self.assertEqual(port.finished, ["operation"])

    async def test_independent_contract_preserves_target_and_enabled_guards(self):
        for change in ({"identity": K.NO}, {"within_scope": K.NO}, {"blocked_by": ("dialog",)}, {"enabled": K.NO}):
            for kind in ("replace", "insert"):
                with self.subTest(change=change, kind=kind):
                    port = IndependentPort(facts(OBJECT, **change), [])
                    with self.assertRaises(ComputerUseError) as caught:
                        await Engine(port).execute(Intent(kind, {"text": "never"}), TARGET)
                    self.assertEqual(caught.exception.effect, "none")
                    self.assertEqual(port.commands, [])

    async def test_missing_receiver_or_selection_is_not_an_independent_contract(self):
        for unit in (
            VALUE,
            replace(OBJECT, readiness=K.NO, pending=("receiver",)),
            replace(OBJECT, readiness=K.NO, pending=("selection",)),
            replace(OBJECT, supported=K.NO),
        ):
            with self.subTest(unit=unit):
                port = IndependentPort(facts(unit), [])
                # facts() models enabled guards; preserve the unit's other pending evidence.
                port.observations = [replace(facts(), primitives=(unit,))]
                with self.assertRaises(ComputerUseError) as caught:
                    await Engine(port).execute(Intent("replace", {"text": "never"}), TARGET)
                self.assertEqual(caught.exception.code, "observation_incomplete")
                self.assertEqual(port.graph_reads, 1)
                self.assertEqual(port.commands, [])

    async def test_optional_query_is_not_a_fallback_after_uncertain_dispatch(self):
        for dispatch in (Dispatch.PARTIAL, Dispatch.UNKNOWN):
            with self.subTest(dispatch=dispatch):
                port = IndependentPort(results=[result(dispatch)])
                with self.assertRaises(ComputerUseError) as caught:
                    await Engine(port).execute(Intent("replace", {"text": "once"}), TARGET)
                self.assertNotEqual(caught.exception.effect, "none")
                self.assertEqual((len(port.commands), port.graph_reads), (1, 0))

    async def test_failed_object_route_does_not_skip_receiver_resolution_on_replan(self):
        port = IndependentPort(
            results=[result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="routeUnavailable")]
        )
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("replace", {"text": "once"}), TARGET)
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertEqual((len(port.commands), port.graph_reads), (1, 1))


if __name__ == "__main__":
    unittest.main()
