"""A container delegates only to evidenced current focus inside its own tree."""

import unittest
from dataclasses import replace
from types import SimpleNamespace as R

from sdk.native.query.test_target_query import ROOT, GraphPort, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.current_receiver import CurrentReceiverQuery
from computer_use.interaction.write.resolution.passes.validate_current_receiver import validate_current_receiver
from computer_use.native.contracts import Channel, CurrentReceiverEvidence, InputContext, Intent, Knowledge, Primitive


def field(**values):
    return node("AXTextField", **{"settable.AXValue": True, **values})


class CurrentReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, graph, intent="insert"):
        self.port = GraphPort(graph)
        return await CurrentReceiverQuery(self.port, "operation", ROOT, lambda: 3000).resolve(Intent(intent))

    async def test_focused_second_editor_wins_over_position_and_container_focus(self):
        target, within = await self.resolve(
            dict(
                root=node(children=["first", "layout"], AXFocused=True),
                first=field(AXFocused=False),
                layout=node(children=["second"]),
                second=field(AXFocused=True),
            )
        )
        self.assertEqual(target, replace(ROOT, node="second"))
        self.assertEqual(within, ("root", "layout"))
        self.assertFalse(any(event[0] in ("execute", "begin") for event in self.port.events))

    async def test_only_child_selection_and_focusability_are_not_current_focus(self):
        for changes in ({}, {"AXSelected": True}, {"settable.AXFocused": True}, {"AXFocused": False}):
            with self.subTest(changes=changes), self.assertRaises(ComputerUseError) as caught:
                await self.resolve(dict(root=node(children=["only"]), only=field(**changes)))
            self.assertEqual(caught.exception.code, "input_unavailable")

    async def test_multiple_independent_focused_branches_are_ambiguous(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve(dict(root=node(children=["a", "b"]), a=field(AXFocused=True), b=field(AXFocused=True)))
        self.assertEqual(caught.exception.code, "ambiguous_target")

    async def test_nested_window_is_not_a_contained_keyboard_recipient(self):
        with self.assertRaises(ComputerUseError):
            await self.resolve(
                dict(
                    root=node(children=["dialog"]),
                    dialog=node("AXWindow", children=["field"]),
                    field=field(AXFocused=True),
                )
            )

    async def test_current_button_accepts_keys_but_not_text_insertion(self):
        graph = dict(root=node(children=["button"]), button=node("AXButton", AXFocused=True))
        target, within = await self.resolve(graph, "key")
        self.assertEqual(target.node, "button")
        with self.assertRaises(ComputerUseError):
            await self.resolve(graph)

    def test_new_receiver_cannot_be_accepted_just_because_an_ancestor_has_focus(self):
        target = replace(ROOT, node="field")
        frame = R(
            request=R(bound=R(spec=R(forward_scope=ROOT, target=target, within=("root",)))),
            facts=R(input=InputContext(focused_node="root"), actuator=None, receiver=object(), target_label="scope"),
        )
        with self.assertRaises(ComputerUseError):
            validate_current_receiver(frame, None)
        frame.facts.input = InputContext(focused_node="editor", focus_ancestors=("field", "root"))
        self.assertIs(validate_current_receiver(frame, None), frame)


class EvidencePort(GraphPort):
    capabilities = frozenset({"nativeCurrentReceiver"})

    def __init__(self, graph, current):
        super().__init__(graph)
        self.current = current
        self.depths = []

    async def inspect(self, operation, target, timeout_ms, *, current_receiver=False, within=()):
        assert current_receiver, "Discovery must explicitly request query-only focus evidence"
        facts = await super().inspect(operation, target, timeout_ms, within=within)
        return replace(facts, primitives=(), input=InputContext(current_receiver=self.current))

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        self.depths.append(max_depth)
        return await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)


class CurrentReceiverEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, graph, current, intent="insert"):
        self.port = EvidencePort(graph, current)
        return await CurrentReceiverQuery(self.port, "operation", ROOT, lambda: 3000).resolve(Intent(intent))

    async def test_confirmed_receiver_ignores_large_unread_and_shared_subtrees(self):
        # These objects were the failure triggers of the previous census. No
        # unrelated subtree (including a second local focus flag) is consulted
        # after the native current-recipient contract has confirmed its path.
        graph = dict(
            root=node(children=["editor", "wrapper", "other"] + [f"label-{i}" for i in range(500)]),
            editor=field(AXFocused=True),
            wrapper=node(children=["editor"]),
            other=field(AXFocused=True),
        )
        current = CurrentReceiverEvidence("confirmed", "editor", ("wrapper", "root"))
        target, within = await self.resolve(graph, current)
        self.assertEqual((target.node, within), ("editor", ("root",)))
        self.assertEqual([event[1].node for event in self.port.events if event[0] == "nodes"], ["editor"])
        self.assertEqual(self.port.depths, [0])
        self.assertFalse(any(event[0] in ("execute", "begin", "relation") for event in self.port.events))

    async def test_missing_or_conflicting_native_evidence_never_falls_back_to_local_focus(self):
        graph = dict(root=node(children=["editor"]), editor=field(AXFocused=True))
        for state in ("absent", "unavailable", "conflict"):
            with self.subTest(state=state), self.assertRaises(ComputerUseError) as caught:
                await self.resolve(graph, CurrentReceiverEvidence(state, detail="native observation: " + state))
            self.assertEqual(caught.exception.effect, "none")
            self.assertEqual(
                caught.exception.code, "observation_incomplete" if state == "unavailable" else "input_unavailable"
            )
            self.assertEqual([event[0] for event in self.port.events], ["inspect"])

    async def test_confirmed_path_must_reach_exact_requested_container(self):
        graph = dict(root=node(children=["editor"]), editor=field(AXFocused=True))
        for path in ((), ("other-container",), ("root", "other-window")):
            with self.subTest(path=path), self.assertRaises(ComputerUseError):
                await self.resolve(graph, CurrentReceiverEvidence("confirmed", "editor", path))
            self.assertEqual([event[0] for event in self.port.events], ["inspect"])

    async def test_unavailable_observation_does_not_retire_scope_and_next_lookup_recovers(self):
        graph = dict(root=node(children=["editor"]), editor=field())
        port = EvidencePort(graph, CurrentReceiverEvidence("unavailable"))

        def query():
            return CurrentReceiverQuery(port, "operation", ROOT, lambda: 3000)

        with self.assertRaises(ComputerUseError):
            await query().resolve(Intent("insert"))
        port.current = CurrentReceiverEvidence("confirmed", "editor", ("root",))
        target, within = await query().resolve(Intent("insert"))
        self.assertEqual((target.node, within), ("editor", ("root",)))
        self.assertFalse(port.calls.retired)

    async def test_confirmed_focus_does_not_override_requested_text_semantics(self):
        graph = dict(button=node("AXButton"))
        current = CurrentReceiverEvidence("confirmed", "button", ("root",))
        self.assertEqual((await self.resolve(graph, current, "key"))[0].node, "button")
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve(graph, current)
        self.assertEqual(caught.exception.code, "input_unavailable")

    async def test_layout_or_unknown_receiver_role_never_delegates_to_a_child(self):
        for role in ("AXGroup", "AXUnknown", "missing"):
            with self.subTest(role=role):
                candidate = node(role, children=["editor"], AXFocused=True)
                if role == "missing":
                    candidate["statuses"]["AXRole"] = "error"
                graph = dict(root=candidate, editor=field(AXFocused=True))
                with self.assertRaises(ComputerUseError) as caught:
                    await self.resolve(graph, CurrentReceiverEvidence("confirmed", "root"), "key")
                self.assertEqual(caught.exception.effect, "none")
                self.assertEqual([event[1].node for event in self.port.events if event[0] == "nodes"], ["root"])
                self.assertFalse(any(event[0] in ("execute", "relation") for event in self.port.events))

    def test_query_evidence_never_overrides_receiver_change_before_dispatch(self):
        target = replace(ROOT, node="editor")
        frame = R(
            request=R(bound=R(spec=R(forward_scope=ROOT, target=target, within=("root",)))),
            facts=R(
                input=InputContext(focused_node="different", focus_ancestors=("root",)),
                actuator=None,
                target_label="scope",
            ),
        )
        with self.assertRaises(ComputerUseError) as caught:
            validate_current_receiver(frame, None)
        self.assertEqual(caught.exception.effect, "none")


class ForwardingPort(EvidencePort):
    def __init__(self, graph, current, live_focus="editor"):
        super().__init__(graph, current)
        self.live_focus = live_focus

    async def inspect(self, operation, target, timeout_ms, *, current_receiver=False, within=()):
        if current_receiver:
            return await super().inspect(operation, target, timeout_ms, current_receiver=True, within=within)
        facts = await GraphPort.inspect(self, operation, target, timeout_ms, within=within)
        return replace(
            facts,
            native_role="AXTextField",
            primitives=(Primitive("keys", "key_sequence", Channel.KEYBOARD, Knowledge.YES, Knowledge.YES),),
            input=InputContext(focused_node=self.live_focus, focus_ancestors=("root",), window_route=Knowledge.YES),
        )


class CurrentReceiverPipelineTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def port(current=None, live_focus="editor"):
        return ForwardingPort(
            dict(root=node(children=["editor"] + [f"unread-{i}" for i in range(600)]), editor=field()),
            current or CurrentReceiverEvidence("confirmed", "editor", ("root",)),
            live_focus,
        )

    async def test_success_has_one_lease_one_input_and_only_requested_scope_constraint(self):
        port = self.port()
        result = await Engine(port).execute(Intent("key", {"key": "Return"}), ROOT, forward_current=True)
        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(sum(event[0] == "begin" for event in port.events), 1)
        self.assertEqual([event[1].node for event in port.events if event[0] == "execute"], ["editor"])
        self.assertIn(("inspect", replace(ROOT, node="editor"), ("root",)), port.events)
        self.assertEqual(port.events[-1], ("finish", "op"))

    async def test_missing_evidence_or_changed_receiver_never_prepares_or_sends_input(self):
        for port in (self.port(CurrentReceiverEvidence("unavailable")), self.port(live_focus="different")):
            with self.assertRaises(ComputerUseError) as caught:
                await Engine(port).execute(Intent("key", {"key": "Return"}), ROOT, forward_current=True)
            self.assertEqual(caught.exception.effect, "none")
            self.assertFalse(any(event[0] == "execute" for event in port.events))
            self.assertFalse(port.calls.retired)
            self.assertEqual(port.events[-1], ("finish", "op"))
