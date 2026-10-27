"""Semantic provenance crosses the driver boundary without mutable UI content."""

import unittest
from copy import deepcopy

import sdk.native.test_native_transport as transport
from sdk.interaction.write.resolution.test_receiver_query import ReceiverPort, editor
from sdk.interaction.write.test_execution_engine import KEY, READY, TARGET, RecordingPort, facts, result
from sdk.native.query.test_target_query import ROOT, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.receiver import ReceiverQuery
from computer_use.native.contracts import Confirmation, Dispatch, Intent
from computer_use.protocol.execution import decodeInspectQuery


class SelectionBasisTests(unittest.IsolatedAsyncioTestCase):
    async def test_editor_selection_records_wrapper_capabilities_and_ordered_relationship_sources(self):
        port = ReceiverPort(
            dict(
                root=node("AXComboBox", children=["layout"]),
                layout=node("AXGroup", children=["editor"]),
                editor=editor(),
            )
        )
        query = ReceiverQuery(port, "operation", ROOT, lambda: 1000)
        selected = (await query.resolve(require_editable=True)).actuator
        value = dict(kind="inputContext", within=[], actuator=selected.wire(), basis=[d.wire() for d in query.basis])
        self.assertEqual(decodeInspectQuery(value), value)
        entries = {d.node: d for d in query.basis}
        self.assertTrue(entries["layout"].actions)
        self.assertIn("AXRole", entries["layout"].fields)
        self.assertEqual(entries["layout"].relations, ("AXChildren",))
        self.assertEqual(entries["layout"].observed, "batch-layout")
        self.assertIn("AXChildren", entries["root"].relations)
        self.assertFalse(entries["editor"].actions)
        self.assertFalse(
            entries["editor"].relations, "An editor owns its content; do not guard paragraphs during typing"
        )
        self.assertTrue(
            all(not set(d.fields) & {"AXValue", "AXTitle", "AXFocused", "AXSelectedTextRange"} for d in query.basis)
        )

    async def test_continuation_keeps_each_native_observation_in_the_basis(self):
        port = ReceiverPort(dict(root=node("AXComboBox", children=["editor"]), editor=editor()))
        port.edges[("root", "AXChildren")] = dict(
            targets=[], state="partial", certainty="confirmed", offset=0, total=1, cursor="next"
        )
        port.pages["next"] = dict(
            source="root",
            property="AXChildren",
            targets=["editor"],
            state="partial",
            certainty="confirmed",
            offset=0,
            total=1,
        )
        query = ReceiverQuery(port, "operation", ROOT, lambda: 1000)
        await query.resolve()
        self.assertEqual({d.observed for d in query.basis if "AXChildren" in d.relations}, {"batch-root", "page"})

    async def test_preparation_and_delivery_share_one_replan_allowance(self):
        changed = result(Dispatch.NOT_DISPATCHED, error_code="input_unavailable", retry_reason="preconditionChanged")
        prepared = result(Dispatch.NOT_DISPATCHED, confirmation=Confirmation.CONFIRMED, preparation=("focus prepared",))
        for second_change in (False, True):
            port = RecordingPort(
                [facts(KEY), facts(KEY), facts(KEY, context=READY)],
                [changed, prepared, changed if second_change else result()],
            )
            if second_change:
                with self.assertRaises(ComputerUseError):
                    await Engine(port).execute(Intent("insert", dict(text="hello")), TARGET)
            else:
                self.assertEqual(
                    (await Engine(port).execute(Intent("insert", dict(text="hello")), TARGET))["status"], "dispatched"
                )
            self.assertEqual([c.prepare for _, c in port.commands], [True, True, False])
            self.assertTrue(all(c.target == TARGET and not c.foreground for _, c in port.commands))

    def test_dependency_values_cannot_be_supplied_by_the_client(self):
        value = dict(
            kind="inputContext",
            within=[],
            actuator=dict(node="editor", relation="containedEditor"),
            basis=[dict(observed="facts", node="layout", fields=["AXRole"], actions=True, relations=[])],
        )
        for changed in ([], [value["basis"][0]] * 2, [dict(value["basis"][0], expected="AXGroup")]):
            bad = deepcopy(value)
            bad["basis"] = changed
            with self.assertRaises(ValueError):
                decodeInspectQuery(bad)


class SelectionTransportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = transport.NativeTransportTests.asyncSetUp
    asyncTearDown = transport.NativeTransportTests.asyncTearDown
    engine = transport.NativeTransportTests.engine
    requests = transport.NativeTransportTests.requests

    async def test_missing_capability_or_changed_echo_cannot_reach_preparation(self):
        for mode in ("keyboard-actuator-missing-basis", "keyboard-actuator-wrong-basis"):
            start = len(self.requests()) if self.journal.exists() else 0
            with self.assertRaises(ComputerUseError):
                await self.engine(mode).execute(Intent("insert", dict(text="hello")), transport.TARGET)
            calls = self.requests()[start:]
            self.assertFalse(any(call["method"] == "execute" for call in calls))
            self.assertEqual(calls[-1]["method"], "finish")
