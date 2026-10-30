# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior contracts at the public adaptation / execution boundary."""

import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from computer_use.client.factory import session_controller
from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import ActOnElement, ResolveElement, UIKeyboard, UIPointer
from computer_use.interaction.query.pipeline import FindElementPipeline
from computer_use.interaction.requests import ExecuteAction
from computer_use.native.contracts import Binding, Intent
from computer_use.pipeline import Control, Pipeline, Repeat, Scope, Select, Sequence
from computer_use.session.events import set_action_handler, set_observation_handler
from computer_use.session.interface_images import signature


class UIStub(SimpleNamespace):
    """Weak-referenceable handle for session registration."""


class InteractionBehaviorTests(IsolatedAsyncioTestCase):
    def setUp(self):
        port = SimpleNamespace(
            connection=SimpleNamespace(session="test", capabilities=()), calls=SimpleNamespace(retired=False)
        )
        self.controller = session_controller(port)
        self.target = Binding("test", "app", "window", "surface", "node")
        self.snapshot = {"binding": self.target}
        instance = SimpleNamespace(
            anchor=None,
            refs={"1": self.snapshot},
            members={"window": {"surface": "surface", "window": "window"}},
            surface_evidence=lambda *_: None,
            menu_contexts={},
        )
        self.ui = UIStub(_instance=instance, _anchor=None, _menu_only=True, _lookup_scopes=(object(),))
        self.controller.register(self.ui, {"selector": "test-app"})
        self.receipt = {"status": "dispatched", "target": "test element", "detail": "sent once"}
        self.engine = AsyncMock(return_value=self.receipt)
        self.controller.engine.execute = self.engine
        self.events = []
        set_action_handler(lambda value: self.events.append(("action", value)))
        set_observation_handler(lambda value: self.events.append((value["kind"], value)))

    def tearDown(self):
        set_action_handler(None)
        set_observation_handler(None)

    async def test_cached_reference_and_expired_session(self):
        result = await self.controller.run(ResolveElement(self.ui, 1, 0))
        self.assertIs(result, self.snapshot)
        self.controller.transport.session = "new-session"
        with self.assertRaises(ComputerUseError) as caught:
            await self.controller.run(ResolveElement(self.ui, 1, 0))
        self.assertEqual(caught.exception.code, "stale_target")
        self.engine.assert_not_awaited()

    async def test_unknown_reference_fails_without_input(self):
        with self.assertRaises(ComputerUseError) as caught:
            await self.controller.run(ResolveElement(self.ui, 999, 0))
        self.assertEqual(caught.exception.code, "stale_target")
        self.engine.assert_not_awaited()

    async def test_element_intents_preserve_binding_and_parameters(self):
        for intent, parameters in (
            ("click", {}),
            ("insert", {"text": "abc"}),
            ("replace", {"text": "xyz"}),
            ("key", {"key": "Return"}),
        ):
            with self.subTest(intent=intent):
                self.engine.reset_mock()
                await self.controller.run(ActOnElement(self.ui, self.snapshot, intent, parameters))
                self.engine.assert_awaited_once()
                args, options = self.engine.call_args
                self.assertEqual(args, (Intent(intent, parameters), self.target))
                self.assertEqual(options["path"], None)
                self.assertEqual(options["query_timeout_ms"], 0)
        self.assertEqual(len([kind for kind, _ in self.events if kind == "action"]), 4)

    async def test_container_forwarding_and_replace_rejection(self):
        snapshot = dict(self.snapshot, container=True)
        await self.controller.run(ActOnElement(self.ui, snapshot, "insert", {"text": "abc"}))
        self.assertTrue(self.engine.call_args.kwargs["forward_current"])
        self.engine.reset_mock()
        with self.assertRaises(ComputerUseError):
            await self.controller.run(ActOnElement(self.ui, snapshot, "replace", {"text": "abc"}))
        self.engine.assert_not_awaited()

    async def test_keyboard_refresh_precedes_single_dispatch(self):
        order = []

        async def refresh(*_):
            order.append("refresh")

        async def execute(*_, **__):
            order.append("dispatch")
            return self.receipt

        self.engine.side_effect = execute
        with (
            patch("computer_use.interaction.agent.pipeline.refresh", refresh),
            patch.object(self.controller.interfaces, "keyboard_context", return_value=self.target),
        ):
            await self.controller.run(UIKeyboard(self.ui, "type_text", {"text": "abc"}))
        self.assertEqual(order, ["refresh", "dispatch"])
        self.assertEqual(self.engine.call_args.args[0], Intent("insert", {"text": "abc"}))

    async def test_failed_or_cancelled_write_is_not_replayed(self):
        for failure in (
            local_failure("uncertain dispatch", "read first", "execution_unknown"),
            asyncio.CancelledError(),
        ):
            self.engine.reset_mock()
            self.engine.side_effect = failure
            self.events.clear()
            with self.assertRaises(type(failure)) as caught:
                await self.controller.run(ActOnElement(self.ui, self.snapshot, "click", {}))
            self.assertIs(caught.exception, failure)
            self.engine.assert_awaited_once()
            self.assertEqual([kind for kind, _ in self.events], ["discard-pending"])

    async def test_receipt_delivery_failure_does_not_replay_action(self):
        def fail(_):
            raise ValueError("delivery failed")

        set_action_handler(fail)
        with self.assertRaisesRegex(RuntimeError, "do not replay"):
            await self.controller.run(ActOnElement(self.ui, self.snapshot, "click", {}))
        self.engine.assert_awaited_once()

    async def test_pointer_maps_coordinates_and_dispatches_once(self):
        target = replace(self.target, node=None, capture="capture")
        piece = SimpleNamespace(window="window", image=SimpleNamespace(target=target))
        members = {
            "window": {"window": "window", "surface": "surface", "parent": None, "parentState": "none", "modal": False}
        }
        self.ui._image = SimpleNamespace(
            png=b"image",
            digest=hashlib.sha256(b"image").digest(),
            expired=False,
            membership=signature(members),
            pieces=(piece,),
            hit=lambda point: (piece, (point[0] - 10, point[1] - 20)),
        )
        self.controller.adapters = replace(self.controller.adapters, image_delivery_required=lambda: False)
        with (
            patch("computer_use.interaction.write.ui.pipeline.refresh", new_callable=AsyncMock),
            patch("computer_use.interaction.write.ui.pipeline.allowed", return_value=members),
        ):
            await self.controller.run(UIPointer(self.ui, "click", ((30, 50),), {"button": "left"}))
        self.engine.assert_awaited_once()
        self.assertEqual(self.engine.call_args.args, (Intent("click", {"x": 20, "y": 30, "button": "left"}), target))

    async def test_internal_desktop_write_entry_stays_available(self):
        target = Binding("test", None, None, None, capture="image")
        await self.controller.run(ExecuteAction(None, Intent("click", {"x": 1, "y": 2}), target))
        self.assertEqual(self.engine.call_args.args[1], target)

    async def test_lookup_unique_missing_and_ambiguous(self):
        missing = local_failure("not found", "read again", "element_not_found")
        self.ui._lookup_scopes = (object(), object())
        second = {"binding": replace(self.target, node="other")}
        for results, error in (
            ([missing, self.snapshot], None),
            ([missing, missing], "element_not_found"),
            ([self.snapshot, second], "ambiguous_target"),
        ):
            with (
                self.subTest(error=error),
                patch.object(FindElementPipeline, "arun", new_callable=AsyncMock) as lookup,
            ):
                lookup.side_effect = results
                if error:
                    with self.assertRaises(ComputerUseError) as caught:
                        await self.controller.run(ResolveElement(self.ui, "button", 1000))
                    self.assertEqual(caught.exception.code, error)
                else:
                    self.assertIs(await self.controller.run(ResolveElement(self.ui, "button", 1000)), self.snapshot)
                requests = [call.args[0] for call in lookup.call_args_list]
                self.assertEqual([request.owner for request in requests], list(self.ui._lookup_scopes))
                self.assertLessEqual(requests[1].timeout_ms, requests[0].timeout_ms)
                self.assertEqual(tuple(dict(segment) for segment in requests[0].path), ({"name": "button"},))
        self.engine.assert_not_awaited()

    async def test_incomplete_scope_cannot_claim_unique_match(self):
        self.ui._menu_only = False
        self.ui._instance.observed = set()
        with (
            patch("computer_use.interaction.agent.pipeline.allowed", return_value={"unknown": {}}),
            patch.object(FindElementPipeline, "arun", new_callable=AsyncMock, return_value=self.snapshot),
        ):
            with self.assertRaises(ComputerUseError) as caught:
                await self.controller.run(ResolveElement(self.ui, "button", 0))
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.engine.assert_not_awaited()

    async def test_public_write_and_lookup_enter_router_only_once(self):
        original = self.controller.run
        with patch.object(self.controller, "run", wraps=original) as entry:
            await self.controller.run(ActOnElement(self.ui, self.snapshot, "click", {}))
            self.assertEqual(entry.await_count, 1)
        with (
            patch.object(self.controller, "run", wraps=original) as entry,
            patch.object(FindElementPipeline, "arun", new_callable=AsyncMock, return_value=self.snapshot),
        ):
            await self.controller.run(ResolveElement(self.ui, "button", 0))
            self.assertEqual(entry.await_count, 1)

    async def test_write_and_lookup_inherit_parent_control(self):
        def remaining():
            return 1234

        def cancelled():
            return False

        events = []
        control = Control(
            remaining=remaining,
            cancelled=cancelled,
            diagnostic=events.append,
            call_id="public-call",
            path=("Caller",),
            attempt=2,
        )
        await self.controller.pipeline.arun(ActOnElement(self.ui, self.snapshot, "click", {}), control)
        received = self.engine.call_args.kwargs["_control"]
        self.assertIs(received.remaining, remaining)
        self.assertIs(received.cancelled, cancelled)
        self.assertEqual(received.call_id, "public-call")
        self.assertEqual(received.attempt, 2)
        self.assertEqual(received.path.count("SessionPipeline"), 1)
        self.assertEqual(received.path[0], "Caller")
        self.assertIn("AgentInterface", received.path)
        with patch.object(FindElementPipeline, "arun", new_callable=AsyncMock, return_value=self.snapshot) as lookup:
            await self.controller.pipeline.arun(ResolveElement(self.ui, "button", 0), control)
            query_control = lookup.call_args.args[1]
            self.assertIs(query_control.remaining, remaining)
            self.assertIs(query_control.cancelled, cancelled)
            self.assertEqual(query_control.call_id, "public-call")
            self.assertEqual(query_control.path.count("SessionPipeline"), 1)
            self.assertIn("AgentInterface", query_control.path)

    def test_composition_graph_is_acyclic_and_shares_execution_nodes(self):
        pipeline = self.controller.pipeline
        adapter = pipeline._root.branches["agent:write"]
        self.assertIs(pipeline._root.branches["write"], adapter.interfaces.write)
        self.assertIs(pipeline._root.branches["query"], adapter.interfaces.lookup)
        self.assertIn(adapter.interfaces.write, adapter.children)
        self.assertIn(adapter.interfaces.lookup, adapter.children)

        visited = set()

        def visit(node, ancestors):
            identity = id(node)
            self.assertNotIn(identity, ancestors)
            if identity in visited:
                return
            visited.add(identity)
            if isinstance(node, Pipeline):
                children = (node._root,)
            elif isinstance(node, Select):
                children = (node.selector, *node.branches.values())
            elif isinstance(node, Sequence):
                children = node.steps
            elif isinstance(node, Scope):
                children = (node.body,)
            elif isinstance(node, Repeat):
                children = (node.body, node.gate)
            else:
                children = getattr(node, "children", ())
            for child in children:
                visit(child, (*ancestors, identity))

        visit(pipeline, ())
        self.assertIn(id(adapter.interfaces.write), visited)
        self.assertIn(id(adapter.interfaces.lookup), visited)
