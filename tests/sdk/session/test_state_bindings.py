"""Pure session owners preserve identity, windows, delivery and event contracts."""

import ast
import gc
import unittest
import weakref
from dataclasses import fields

from repository import ROOT as REPO_ROOT

from computer_use._identity import Record
from computer_use.errors import ComputerUseError
from computer_use.native.contracts import Binding
from computer_use.native.scopes import RunningApplication, WorkingWindow
from computer_use.session.bindings import SessionBindings
from computer_use.session.desktop_store import DesktopImageAccess, DesktopStore
from computer_use.session.events import (
    actionCompleted,
    observationCompleted,
    observationsInvalidated,
    pendingObservationsDiscarded,
    setActionHandler,
    setObservationHandler,
)
from computer_use.session.observation_data import observationData, retainObservationData
from computer_use.session.state import AppState


class Owner:
    pass


class SessionBindingTests(unittest.TestCase):
    def setUp(self):
        self.store, self.owner = SessionBindings(), Owner()
        self.store.register(self.owner, {"selector": "Fixture"})
        self.target = Binding("s", "i", "root", "surface", "node")

    def test_original_field_order_and_independent_defaults(self):
        self.assertEqual(
            [field.name for field in fields(AppState)],
            [
                "selector",
                "running",
                "selection",
                "observation",
                "query_target",
                "query_scope_kind",
                "directory",
                "refs",
                "windows",
                "delivered",
                "delivered_pixels",
                "capture_expired",
            ],
        )
        a, b = AppState("a"), AppState("b")
        a.refs["0"] = object()
        a.windows["0"] = "w"
        self.assertFalse(b.refs)
        self.assertFalse(b.windows)
        self.assertEqual((b.application, b.name), (None, ""))

    def test_registered_state_and_fixed_scope_survive_invalidation(self):
        state = self.store.state(self.owner)
        state.running = RunningApplication("app", "Fixture", "s")
        state.selection = WorkingWindow("app", "Fixture", self.target)
        app = Owner()
        self.store.bindApp(self.target, app)
        self.store.catalogue(state, {"session": "s"}, {"0": "root"})
        self.assertIs(self.store.boundApp(self.target), app)
        self.assertEqual(self.store.scope(self.owner), {"selector": self.target})
        self.store.invalidate()
        self.assertIs(self.store.state(self.owner), state)
        self.assertEqual(self.store.scope(self.owner), {"selector": self.target})
        self.assertIsNone(self.store.boundApp(self.target))
        self.assertEqual(len(self.store._window_catalogues), 0)
        other = Owner()
        self.store.register(other, {"selector": "ignored", "_nativeState": state})
        self.assertIs(self.store.state(other), state)

    def test_window_catalogue_is_exact_identity_only_and_bounded(self):
        state = self.store.state(self.owner)
        state.running = RunningApplication("exact-app", "Human name", "s")
        directory, windows = {"session": "s"}, {"0": "root"}
        self.store.catalogue(state, directory, windows)
        fresh = AppState("exact-app")
        running, observed, refs = self.store.windowCatalogue(fresh, "0", "s")
        self.assertIs(running, state.running)
        self.assertIs(observed, directory)
        self.assertEqual(refs, windows)
        windows["1"] = "unobserved"
        self.assertNotIn("1", refs)
        for selector in ("Human name", "other-app"):
            with self.assertRaises(ComputerUseError) as error:
                self.store.windowCatalogue(AppState(selector), "0", "s")
            self.assertEqual(error.exception.code, "stale_target")
        with self.assertRaises(ComputerUseError) as error:
            self.store.windowCatalogue(state, "0", "different-session")
        self.assertEqual(error.exception.detail, "window catalogue belongs to an expired driver session")
        for index in range(34):
            current = AppState(str(index), running=RunningApplication(str(index), "x", "s"))
            self.store.catalogue(current, {"session": "s"}, {"0": str(index)})
        self.assertEqual(len(self.store._window_catalogues), 32)
        self.assertNotIn(("s", "0"), self.store._window_catalogues)
        self.assertIn(("s", "33"), self.store._window_catalogues)

    def test_reference_limit_session_validation_and_identity_are_unchanged(self):
        state = self.store.state(self.owner)
        snapshots = {str(index): {"binding": self.target} for index in range(8193)}
        self.store.remember(state, snapshots)
        self.assertEqual(len(state.refs), 8192)
        self.assertNotIn("0", state.refs)
        self.assertIs(self.store.elementSnapshot(self.owner, "1", "s"), snapshots["1"])
        for ref, session in [("0", "s"), ("missing", "s"), ("1", "old")]:
            with self.subTest(ref=ref, session=session), self.assertRaises(ComputerUseError):
                self.store.elementSnapshot(self.owner, ref, session)
        with self.assertRaises(ComputerUseError):
            self.store.validateSnapshot({}, "s")
        self.store.validateSnapshot(self.target, "s")

    def test_weak_owners_and_wrappers_are_not_kept_alive(self):
        app = Owner()
        self.store.bindApp(self.target, app)
        ref = weakref.ref(app)
        del app
        gc.collect()
        self.assertIsNone(ref())
        self.assertIsNone(self.store.boundApp(self.target))
        owner = Owner()
        self.store.register(owner, {"selector": "temporary"})
        ref = weakref.ref(owner)
        del owner
        gc.collect()
        self.assertIsNone(ref())


class SessionDeliveryTests(unittest.TestCase):
    def tearDown(self):
        setActionHandler(None)
        setObservationHandler(None)

    def test_action_and_observation_handlers_remain_independent(self):
        actions, observations = [], []
        setActionHandler(actions.append)
        setObservationHandler(observations.append)
        receipt, observed, source = {"target": "button", "detail": "sent"}, {"text": "body"}, object()
        actionCompleted(receipt)
        observationCompleted(observed)
        observationsInvalidated(source)
        pendingObservationsDiscarded(source)
        self.assertEqual(actions, [receipt])
        self.assertEqual([item["kind"] for item in observations], ["observed", "invalidate", "discard-pending"])
        self.assertIs(observations[0]["observation"], observed)

        def fail(value):
            raise ValueError("unavailable host")

        setActionHandler(fail)
        with self.assertRaisesRegex(RuntimeError, "Action completed, but its receipt could not be displayed") as error:
            actionCompleted(receipt)
        self.assertIn("Effect: button: sent", str(error.exception))

    def test_observation_data_keeps_order_without_changing_captured_roots(self):
        state, roots = Record(text="body"), [{"ref": "0"}]
        retainObservationData(state, roots, ["2", "1", "2"], ["4", "3", "4"], "instance")
        data = observationData(state)
        self.assertIs(data["roots"], roots)
        self.assertEqual(tuple(data["refs"]), ("2", "1"))
        self.assertEqual(tuple(data["windows"]), ("4", "3"))
        self.assertEqual(data["instance"], "instance")

    def test_desktop_delivery_borrows_image_owner_and_exact_epoch(self):
        registered, epoch = [], [0]
        access = DesktopImageAccess(
            lambda png, source: registered.append((png, source)), lambda: epoch[0], lambda: True
        )
        store, image = DesktopStore(access), Owner()
        display = dict(rect=dict(x=0, y=0, width=100, height=50), captured=True)
        state = dict(screenshot=image, desktopRef="desktop-1", observation=dict(mode="exclusive", displays=[display]))
        store.record(state)
        self.assertEqual(len(registered), 1)
        source = registered[0][1]
        self.assertTrue(source["current"]())
        with self.assertRaises(ComputerUseError) as error:
            store.target(True)
        self.assertEqual(error.exception.code, "input_unavailable")
        source["delivered"]()
        self.assertEqual(store.target(True), "desktop-1")
        store.checkCoordinates(10, 10)
        epoch[0] += 1
        with self.assertRaises(ComputerUseError):
            store.target(True)
        source["delivered"]()
        with self.assertRaises(ComputerUseError):
            store.checkCoordinates(101, 10)
        with self.assertRaises(ComputerUseError):
            store.checkCoordinates(-1, 10)
        store.reset()
        self.assertFalse(source["current"]())
        with self.assertRaises(ComputerUseError):
            store.observed("desktop-1")

    def test_state_modules_do_not_import_interaction_client_or_native_services(self):
        root = REPO_ROOT / "computer_use_engine/session"
        for name in ("state", "bindings", "events", "observation_data", "desktop_store"):
            path = root / (name + ".py")
            tree = ast.parse(path.read_text())
            for value in ast.walk(tree):
                if isinstance(value, ast.ImportFrom):
                    module = value.module or ""
                    self.assertNotRegex(
                        module, r"(?:^|\.)(?:interaction|client|transport|lifecycle|query)(?:\.|$)", str(path)
                    )


if __name__ == "__main__":
    unittest.main()
