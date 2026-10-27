"""App keyboard/query context resumes ancestors; exact targets never do."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from sdk.interaction.read.acquisition.menus.test_window_menus import MenuPort, node
from v5_handles import nativeComputer  # Frozen facade: private pipeline behavior baseline.

from computer_use.errors import ComputerUseError
from computer_use.interaction.query.scope import app_context
from computer_use.interaction.read.app import NativeAppReader
from computer_use.native.contracts import Binding
from computer_use.native.scopes import WorkingWindow

ROOT = Binding("s", "i", "root", "root-s")
MODAL = replace(ROOT, surface="modal-s")
NESTED = replace(ROOT, surface="nested-s")


def surface(name, parent=None, modal=False):
    return dict(
        window=name,
        instance="i",
        surface=name + "-s",
        node=name + "-node",
        title=name,
        parent=parent,
        parentState="known" if parent else "none",
        modal=modal,
        kind="window",
    )


def directory(*items):
    return dict(session="s", application="app", surfaces=list(items), stopReasons=[])


class ContextTests(IsolatedAsyncioTestCase):
    def setup(self, *, nested=False):
        self.selection = WorkingWindow("app", "Fixture", ROOT)
        old = directory(surface("root"), surface("modal", "root", True))
        if nested:
            old["surfaces"].append(surface("nested", "modal", True))
        self.observed = SimpleNamespace(target=NESTED if nested else MODAL, source_directory=old)
        self.current = deepcopy(old)

        async def discover(selection, remaining, *, _control=None):
            remaining()
            NativeAppReader._selection(
                selection.application, selection.name, self.current, selection.root.root_window, previous=selection
            )
            return selection, self.current

        self.reader = SimpleNamespace(_discover=AsyncMock(side_effect=discover))

    async def resolve(self):
        return await app_context(self.reader, self.selection, self.observed, self.observed.target, lambda: 1000)

    async def refused(self, code):
        with self.assertRaises(ComputerUseError) as caught:
            await self.resolve()
        self.assertEqual((caught.exception.code, caught.exception.effect), (code, "none"))

    async def test_live_modal_and_root_keep_the_original_target(self):
        self.setup()
        self.assertEqual(await self.resolve(), MODAL)
        self.observed.target = ROOT
        self.reader._discover.reset_mock()
        self.assertEqual(await self.resolve(), ROOT)
        self.reader._discover.assert_not_awaited()

    async def test_closed_modal_returns_to_same_root_without_changing_observation(self):
        self.setup()
        self.current["surfaces"].pop()
        self.assertEqual(await self.resolve(), ROOT)
        self.assertEqual(self.observed.target, MODAL)
        self.assertEqual(len(self.observed.source_directory["surfaces"]), 2)

    async def test_nested_modal_returns_only_to_a_previously_observed_ancestor(self):
        self.setup(nested=True)
        self.current["surfaces"].pop()
        self.assertEqual(await self.resolve(), MODAL)
        self.current["surfaces"].pop()
        self.assertEqual(await self.resolve(), ROOT)

    async def test_query_context_uses_current_ancestor_root_without_retargeting_live_nodes(self):
        self.setup(nested=True)
        target = replace(NESTED, node="nested-node")
        self.assertEqual(await app_context(self.reader, self.selection, self.observed, target, lambda: 1000), target)
        self.current["surfaces"].pop()
        self.assertEqual(
            await app_context(self.reader, self.selection, self.observed, target, lambda: 1000),
            replace(MODAL, node="modal-node"),
        )
        self.current["surfaces"].pop()
        self.assertEqual(
            await app_context(self.reader, self.selection, self.observed, target, lambda: 1000),
            replace(ROOT, node="root-node"),
        )
        self.current["surfaces"][0]["node"] = None
        with self.assertRaises(ComputerUseError) as caught:
            await app_context(self.reader, self.selection, self.observed, target, lambda: 1000)
        self.assertEqual((caught.exception.code, caught.exception.effect), ("observation_incomplete", "none"))
        self.assertEqual(await self.resolve(), ROOT, "Keyboard context does not require an AX query root")

    async def test_new_or_independent_dialog_cannot_receive_unobserved_input(self):
        self.setup()
        self.current["surfaces"][-1] = surface("new", "root", True)
        await self.refused("stale_target")
        self.current["surfaces"] = [surface("root"), surface("other")]
        self.observed.source_directory["surfaces"][-1]["modal"] = False
        await self.refused("observation_incomplete")

    async def test_missing_root_changed_identity_partial_directory_and_unknown_ownership(self):
        for kind, code in [
            ("root", "stale_target"),
            ("identity", "stale_target"),
            ("partial", "observation_incomplete"),
            ("unknown", "observation_incomplete"),
        ]:
            self.setup()
            self.current["surfaces"].pop()
            if kind == "root":
                self.current["surfaces"] = [surface("other")]
            if kind == "identity":
                self.current["surfaces"][0]["instance"] = "replacement"
            if kind == "partial":
                self.current["stopReasons"] = ["Native directory unread remainder"]
            if kind == "unknown":
                self.current["surfaces"].append(surface("unknown"))
                self.current["surfaces"][-1]["parentState"] = "unknown"
            with self.subTest(kind=kind):
                await self.refused(code)

    async def test_still_present_but_reparented_or_demodalized_view_requires_read(self):
        self.setup()
        self.current["surfaces"][-1]["modal"] = False
        await self.refused("stale_target")
        self.setup()
        self.current["surfaces"][-1]["parent"] = "other"
        self.current["surfaces"].append(surface("other"))
        await self.refused("stale_target")

    async def test_explicit_dialog_app_does_not_return_to_its_parent(self):
        self.setup()
        root = Binding("s", "i", "modal", "modal-s")
        selected = WorkingWindow("app", "Fixture", root)
        result = await app_context(self.reader, selected, self.observed, root, lambda: 1000)
        self.assertEqual(result, root)  # Native begin must validate this exact closed target.
        self.reader._discover.assert_not_awaited()

    async def test_public_app_keyboard_methods_share_one_budget_and_keep_image_and_query_state(self):
        self.setup()
        self.current["surfaces"].pop()
        computer = nativeComputer(MenuPort())
        app = computer.app("Fixture")
        controller = app._controller
        state = controller.state(app)
        state.selection, state.observation, state.query_target = self.selection, self.observed, MODAL
        controller.reader._discover = self.reader._discover
        sent = []

        async def execute(intent, target, remaining, _control=None, **options):
            controller.port.calls.validate_claim(remaining)
            sent.append((intent.kind, target))
            return dict(status="dispatched", detail="test receipt")

        controller.engine.execute_in_call = execute
        controller.engine.execute = AsyncMock(side_effect=AssertionError("Must share the existing deadline"))
        for action in (lambda: app.typeText("text"), lambda: app.paste("text"), lambda: app.pressKey("Return")):
            self.assertEqual((await action()).status, "dispatched")
        self.assertEqual(sent, [("insert", ROOT), ("paste", ROOT), ("key", ROOT)])
        self.assertIs(state.observation, self.observed)
        self.assertEqual(state.query_target, MODAL)
        self.assertEqual(state.selection, self.selection)

    async def test_post_dispatch_failure_is_never_replayed(self):
        self.setup()
        self.current["surfaces"].pop()
        app = nativeComputer(MenuPort()).app("Fixture")
        controller = app._controller
        state = controller.state(app)
        state.selection, state.observation = self.selection, self.observed
        controller.reader._discover = self.reader._discover
        error = ComputerUseError(
            dict(
                code="execution_unknown",
                effect="possible",
                target="root",
                detail="Delivery outcome is unknown",
                next="Observe",
            )
        )
        controller.engine.execute_in_call = AsyncMock(side_effect=error)
        with self.assertRaises(ComputerUseError) as caught:
            await app.pressKey("Return")
        self.assertIs(caught.exception, error)
        controller.engine.execute_in_call.assert_awaited_once()
        self.reader._discover.assert_awaited_once()

    def query_app(self, port=None):
        self.setup()
        self.current["surfaces"].pop()
        if port is None:
            port = MenuPort(
                {
                    "root-node": node("frame", "root", ["input"]),
                    "input": node("entry", "Input", **{"Text.text": "parent value"}),
                }
            )
        app = nativeComputer(port).app("Fixture")
        state = app._controller.state(app)
        state.selection, state.observation = self.selection, self.observed
        state.query_target = replace(MODAL, node="modal-node")
        self.observed.target = state.query_target
        state.query_scope_kind = "window"
        app._controller.reader._discover = self.reader._discover
        return app, state

    async def test_public_query_read_uses_parent_and_preserves_display_and_snapshot_state(self):
        app, state = self.query_app()
        query = app.find(["Input"])
        self.assertEqual((await query.readText()).text, "parent value")
        calls = app._controller.port.events
        self.assertEqual(next(e[1] for e in calls if e[0] == "begin_read"), ROOT)
        self.assertTrue(all(e[1].surface == ROOT.surface for e in calls if e[0] == "nodes"))
        self.assertIs(state.observation, self.observed)
        self.assertEqual(state.query_target, replace(MODAL, node="modal-node"))
        self.assertFalse(state.refs, "Private lookup must not change displayed reference numbers")

    async def test_public_query_write_reconciles_before_one_engine_call_with_same_deadline(self):
        app, state = self.query_app()
        controller = app._controller

        async def execute(intent, target, remaining, _control=None, **options):
            controller.port.calls.validate_claim(remaining)
            self.assertEqual(target, replace(ROOT, node="root-node"))
            self.assertEqual(options, dict(path=[{"name": "Input"}], query_timeout_ms=137))
            return dict(status="dispatched", detail="test receipt")

        controller.engine.execute_in_call = AsyncMock(side_effect=execute)
        controller.engine.execute = AsyncMock(side_effect=AssertionError("No second call budget"))
        await app.find(["Input"], {"timeoutMs": 137}).typeText("new value", {"replace": True})
        self.assertIs(state.observation, self.observed)
        controller.engine.execute_in_call.assert_awaited_once()
        # An uncertain action must not be repeated in the old modal or parent.
        controller.engine.execute_in_call.reset_mock()
        controller.engine.execute_in_call.side_effect = ComputerUseError(
            dict(code="execution_unknown", effect="possible", target="Input", detail="unknown outcome", next="Observe")
        )
        with self.assertRaises(ComputerUseError):
            await app.find(["Input"]).click()
        controller.engine.execute_in_call.assert_awaited_once()

    async def test_menu_queries_keep_their_own_root_without_modal_reconciliation(self):
        app, state = self.query_app(MenuPort())
        state.query_target = replace(MODAL, node="bar")
        for scope_kind in ("window", "applicationMenu"):
            state.query_scope_kind = scope_kind
            found = await app._controller.find(app, [{"name": "File"}, {"name": "Open A"}], 0, False)
            self.assertEqual(found["binding"], replace(MODAL, node="open"))
            app._controller.engine.execute = AsyncMock(return_value=dict(status="dispatched", detail="test receipt"))
            await app.find(["File", "Open A"]).click()
            self.assertEqual(app._controller.engine.execute.call_args.args[1], state.query_target)
        self.reader._discover.assert_not_awaited()
