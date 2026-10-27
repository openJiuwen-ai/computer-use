"""Explicit Window input keeps its observed scope when an editor appears."""

from copy import deepcopy
from types import SimpleNamespace as R
from unittest import IsolatedAsyncioTestCase

from sdk.interaction.query.test_instance_evidence import ScopeFixture, surface

from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.app import NativeAppReader
from computer_use.native.contracts import Binding
from computer_use.session.interfaces import InstanceState
from computer_use.session.state import AppState


class WindowKeyboardContextTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller = ScopeFixture()
        self.directory = dict(
            session="s", application="app", stopReasons=[], surfaces=[surface("root"), surface("child", "root")]
        )
        self.instance = InstanceState("Fixture")
        self.app = R(_instance=self.instance, _anchor=None)
        self.controller.states[id(self.app)] = AppState("Fixture")
        self.selection = NativeAppReader._selection("app", "Fixture", self.directory, "root")
        adopt(self.controller, self.app, deepcopy(self.directory), self.selection)
        self.window = R(
            _instance=self.instance, _anchor="root", _app=self.app, _window_identity=Binding("s", "i", "root", "root-s")
        )
        self.controller.states[id(self.window)] = AppState("Fixture", selection=self.selection)
        self.instance.observed.add("root")
        self.controller.current = deepcopy(self.directory)

    async def refused(self, ui, code):
        with self.assertRaises(ComputerUseError) as caught:
            await self.controller.interfaces.keyboard(ui, "typeText", {"text": "once"})
        self.assertEqual((caught.exception.code, caught.exception.effect), (code, "none"))
        self.controller._execute.assert_not_awaited()

    async def target(self, ui):
        self.assertEqual(await self.controller.interfaces.keyboard(ui, "typeText", {"text": "once"}), "dispatched")
        self.controller._execute.assert_awaited_once()
        owner, intent, target = self.controller._execute.await_args.args
        self.assertIs(owner, ui)
        self.assertEqual((intent.kind, dict(intent.parameters)), ("insert", {"text": "once"}))
        return target

    async def test_explicit_observed_window_keeps_original_scope_once(self):
        self.assertEqual(await self.target(self.window), Binding("s", "i", "root", "root-s"))
        self.assertFalse(hasattr(self.window, "_context"))

    async def test_unqualified_app_still_requires_member_context(self):
        await self.refused(self.app, "ambiguous_target")

    async def test_unobserved_original_window_still_refuses(self):
        self.instance.observed.clear()
        await self.refused(self.window, "stale_target")

    async def test_unobserved_modal_is_not_replaced_by_anchor(self):
        self.controller.current["surfaces"][1]["modal"] = True
        await self.refused(self.window, "stale_target")

    async def test_confirmed_member_focus_still_wins(self):
        self.controller.current["focusedWindow"] = "child"
        self.instance.observed.add("child")
        self.assertEqual(await self.target(self.window), Binding("s", "i", "root", "child-s"))

    async def test_existing_observed_context_still_wins(self):
        self.window._context = "child"
        self.instance.observed.add("child")
        self.assertEqual(await self.target(self.window), Binding("s", "i", "root", "child-s"))

    async def test_unobserved_previous_context_does_not_borrow_anchor_authority(self):
        self.window._context = "child"
        await self.refused(self.window, "stale_target")

    async def test_other_independent_focus_does_not_change_requested_window(self):
        self.controller.current["surfaces"].append(surface("other"))
        self.controller.current["focusedWindow"] = "other"
        self.assertEqual(await self.target(self.window), Binding("s", "i", "root", "root-s"))

    async def test_out_of_scope_context_does_not_change_requested_window(self):
        self.window._context = "other"
        self.controller.current["surfaces"].append(surface("other"))
        self.assertEqual(await self.target(self.window), Binding("s", "i", "root", "root-s"))

    async def test_unconfirmed_original_root_still_refuses(self):
        self.controller.current["surfaces"] = [surface("child")]
        self.controller.current["stopReasons"] = ["root unavailable"]
        await self.refused(self.window, "observation_incomplete")
