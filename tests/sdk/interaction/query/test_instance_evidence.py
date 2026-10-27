"""Directory uncertainty suspends authorization without destroying fixed handles."""

from copy import deepcopy
from types import SimpleNamespace as R
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.pipeline import AgentInterfacePipeline
from computer_use.interaction.query.instances.scope import adopt, allowed, refresh, resolve, validate_session
from computer_use.interaction.read.app import NativeAppReader
from computer_use.native.contracts import Binding, Knowledge
from computer_use.native.lifecycle import CallLifecycle
from computer_use.session.interfaces import InstanceState, WindowIdentity, WindowLifetime
from computer_use.session.state import AppState


def surface(name, parent=None, **changes):
    return dict(
        window=name,
        instance="i",
        surface=name + "-s",
        node=name + "-node",
        title=name,
        parent=parent,
        parentState="known" if parent else "none",
        modal=False,
        kind="window",
        nativeWindow=True,
        accessibility=True,
        **changes,
    )


class ScopeFixture:
    """Hold all native objects constant while varying only directory evidence."""

    def __init__(self):
        self.states = {}
        self.discovery_calls = []
        self.port = R()
        self.port.calls = CallLifecycle(self.port)
        self.transport = R(session="s")
        self.reader = R(
            _discover=self.discover,
            _selection=NativeAppReader._selection,
            directory=R(),
            window_aliases=lambda directory: {
                str(index): item["window"] for index, item in enumerate(directory["surfaces"])
            },
        )
        self.actElement = AsyncMock(return_value="dispatched")
        self._execute = AsyncMock(return_value="dispatched")
        self.interfaces = AgentInterfacePipeline(self)

    def register(self, owner, options):
        self.states[id(owner)] = options["_nativeState"]

    def state(self, owner):
        return self.states[id(owner)]

    def validateElement(self, target):
        assert target.session == "s"

    async def discover(self, previous, remaining, *, accessibility=True):
        remaining()
        self.discovery_calls.append(accessibility)
        selected = NativeAppReader._selection(
            previous.application, previous.name, self.current, previous.root.root_window, previous=previous
        )
        return selected, deepcopy(self.current)


class InstanceEvidenceTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller = ScopeFixture()
        self.full = dict(
            session="s", application="app", stopReasons=[], surfaces=[surface("root"), surface("child", "root")]
        )
        self.instance = InstanceState("Fixture")
        self.app = R(_instance=self.instance, _anchor=None)
        self.controller.states[id(self.app)] = AppState("Fixture")
        self.selection = NativeAppReader._selection("app", "Fixture", self.full, "root")
        adopt(self.controller, self.app, deepcopy(self.full), self.selection)
        self.window = R(
            _instance=self.instance,
            _anchor="child",
            _app=self.app,
            _window_identity=Binding("s", "i", "child", "child-s"),
        )
        self.identity = WindowIdentity("child", "child-s", "i")
        self.snapshot = dict(
            binding=Binding("s", "i", "root", "child-s", "editor"), container=False, window_identity=self.identity
        )
        self.controller.current = deepcopy(self.full)

    def omitted(self, *, partial=True, root=False):
        return dict(
            self.full,
            surfaces=[] if root else [surface("root")],
            stopReasons=["temporary collection timeout"] if partial else [],
        )

    async def refused(self, awaitable, code):
        with self.assertRaises(ComputerUseError) as caught:
            await awaitable
        self.assertEqual((caught.exception.code, caught.exception.effect), (code, "none"))

    async def test_partial_child_omission_retains_identity_without_authority_then_window_recovers(self):
        self.controller.current = self.omitted()
        await self.refused(refresh(self.controller, self.window), "observation_incomplete")
        state = self.instance.evidence[self.identity]
        self.assertEqual((state.membership, state.lifetime), (Knowledge.UNKNOWN, WindowLifetime.RETAINED))
        self.assertEqual(set(allowed(self.app)), {"root"})
        self.assertEqual(allowed(self.window), {})
        validate_session(self.controller, self.window)
        self.controller.current = self.full
        self.controller.discovery_calls.clear()
        await refresh(self.controller, self.window)
        self.assertEqual(self.controller.discovery_calls, [True])
        self.assertEqual(set(allowed(self.window)), {"child"})

    async def test_element_operation_can_recover_through_its_own_window_or_app(self):
        for ui in (self.window, self.app):
            with self.subTest(ui=ui._anchor):
                adopt(self.controller, self.app, self.omitted(), self.selection)
                self.controller.current = self.full
                self.controller.actElement.reset_mock()
                result = await self.controller.interfaces.act_element(ui, self.snapshot, "insert", {"text": "once"})
                self.assertEqual(result, "dispatched")
                self.controller.actElement.assert_awaited_once_with(ui, self.snapshot, None, "insert", {"text": "once"})

    async def test_writable_group_retains_its_concrete_receiver_after_normalization(self):
        from observation_fixture import normalize_fixture
        from sdk.interaction.read.facts.test_native_facts import batch, node

        from computer_use.interaction.controller import SessionController
        from computer_use.interaction.read.numbering.references import DisplayReferences

        raw = batch([node("editor", AXRole="AXGroup", **{"settable.AXSelectedText": True})])
        raw["session"] = "s"
        tree = normalize_fixture(raw, DisplayReferences("s"))
        retained = SessionController._snapshots(self.snapshot["binding"], tree)["0"]
        self.assertFalse(retained["container"])
        self.assertEqual(retained["binding"].node, "editor")
        snapshot = {**self.snapshot, **retained}
        for intent, parameters in (
            ("insert", {"text": "once"}),
            ("key", {"key": "Return"}),
            ("replace", {"text": "new value"}),
        ):
            with self.subTest(intent=intent):
                self.controller.actElement.reset_mock()
                result = await self.controller.interfaces.act_element(self.window, snapshot, intent, parameters)
                self.assertEqual(result, "dispatched")
                self.controller.actElement.assert_awaited_once_with(self.window, snapshot, None, intent, parameters)
                self.controller._execute.assert_not_awaited()

    async def test_role_normalization_preserves_native_input_scope(self):
        from observation_fixture import normalize_fixture
        from sdk.interaction.read.facts.test_native_facts import batch, node

        from computer_use.interaction.controller import SessionController
        from computer_use.interaction.read.numbering.references import DisplayReferences

        cases = [
            ("macos", "AXRole", "AXVendorEditor", False),
            ("macos", "AXRole", "AXUnknown", True),
            ("macos", "AXRole", "AXGroup", True),
            ("macos", "AXRole", "AXSheet", False),
            ("windows", "ControlType", "ControlType.Custom", False),
            ("windows", "ControlType", "ControlType.VendorEditor", False),
            ("windows", "ControlType", "ControlType.Group", True),
            ("linux", "role", "unknown provider role", True),
        ]
        for backend, field, role, container in cases:
            with self.subTest(backend=backend, role=role):
                raw = batch([node("editor", **{field: role})])
                raw.update(session="s", platform=backend)
                tree = normalize_fixture(raw, DisplayReferences("s"))
                retained = SessionController._snapshots(self.snapshot["binding"], tree)["0"]
                self.assertIs(retained["container"], container)
                self.assertIs(tree.collection.input_containers["editor"], container)
                snapshot = {**self.snapshot, **retained}
                self.controller.actElement.reset_mock()
                self.controller._execute.reset_mock()
                result = await self.controller.interfaces.act_element(self.window, snapshot, "insert", {"text": "once"})
                self.assertEqual(result, "dispatched")
                if container:
                    self.controller.actElement.assert_not_awaited()
                    self.controller._execute.assert_awaited_once()
                    self.assertEqual(self.controller._execute.await_args.kwargs, {"forward_current": True})
                else:
                    self.controller.actElement.assert_awaited_once_with(
                        self.window, snapshot, None, "insert", {"text": "once"}
                    )
                    self.controller._execute.assert_not_awaited()

    async def test_unknown_child_cannot_dispatch_but_root_action_is_unaffected(self):
        self.controller.current = self.omitted()
        for ui in (self.window, self.app):
            await self.refused(
                self.controller.interfaces.act_element(ui, self.snapshot, "insert", {"text": "never"}),
                "observation_incomplete",
            )
        self.controller.actElement.assert_not_awaited()
        root = dict(binding=Binding("s", "i", "root", "root-s", "root-editor"), container=False)
        self.assertEqual(
            await self.controller.interfaces.act_element(self.app, root, "insert", {"text": "root"}), "dispatched"
        )
        self.controller.actElement.assert_awaited_once()

    async def test_unknown_parent_does_not_destroy_window_even_in_complete_catalogue(self):
        self.controller.current["surfaces"][1]["parentState"] = "unknown"
        await self.refused(refresh(self.controller, self.window), "observation_incomplete")
        self.assertEqual(self.instance.evidence[self.identity].lifetime, WindowLifetime.RETAINED)
        self.controller.current = self.full
        await refresh(self.controller, self.window)
        self.assertEqual(self.instance.evidence[self.identity].membership, Knowledge.YES)

    async def test_unread_ancestor_path_is_unknown_even_when_child_is_present(self):
        self.controller.current["surfaces"][1]["parent"] = "unread-parent"
        await self.refused(refresh(self.controller, self.window), "observation_incomplete")
        self.assertEqual(self.instance.evidence[self.identity].membership, Knowledge.UNKNOWN)

    async def test_confirmed_reparenting_is_excluded_without_destroying_native_identity(self):
        self.controller.current["surfaces"].append(surface("other"))
        self.controller.current["surfaces"][1]["parent"] = "other"
        await self.refused(refresh(self.controller, self.window), "stale_target")
        state = self.instance.evidence[self.identity]
        self.assertEqual((state.membership, state.lifetime), (Knowledge.NO, WindowLifetime.RETAINED))
        await self.refused(
            self.controller.interfaces.act_element(self.app, self.snapshot, "insert", {"text": "never"}), "stale_target"
        )
        self.controller.actElement.assert_not_awaited()
        self.controller.current = self.full
        await refresh(self.controller, self.window)

    async def test_complete_child_disappearance_retires_and_cannot_resurrect_old_identity(self):
        self.controller.current = self.omitted(partial=False)
        await self.refused(refresh(self.controller, self.window), "stale_target")
        self.assertEqual(self.instance.evidence[self.identity].lifetime, WindowLifetime.RETIRED)
        self.controller.current = self.full
        await refresh(self.controller, self.app)
        self.assertNotIn("child", self.instance.members)
        self.controller.discovery_calls.clear()
        await self.refused(refresh(self.controller, self.window), "stale_target")
        self.assertFalse(self.controller.discovery_calls)
        await self.refused(
            self.controller.interfaces.act_element(self.app, self.snapshot, "insert", {}), "stale_target"
        )

    async def test_replacement_gets_a_new_private_owner_and_needs_a_new_observation(self):
        for field in ("surface", "instance"):
            with self.subTest(field=field):
                self.setUp()
                old_owner = self.instance.owners["child"]
                self.instance.observed.add("child")
                self.controller.current["surfaces"][1][field] = "new-generation"
                await refresh(self.controller, self.app)
                self.assertIsNot(self.instance.owners["child"], old_owner)
                selected = self.controller.state(self.instance.owners["child"]).selection.root
                self.assertEqual(getattr(selected, field), "new-generation")
                self.assertNotIn("child", self.instance.observed)
                await self.refused(refresh(self.controller, self.window), "stale_target")
                self.controller.current = self.full
                await refresh(self.controller, self.app)
                self.assertNotIn("child", self.instance.members)

    async def test_same_surface_new_window_instance_allows_new_snapshot_but_rejects_old(self):
        self.controller.current["surfaces"][1]["instance"] = "new-generation"
        await refresh(self.controller, self.app)
        fresh = dict(self.snapshot, window_identity=WindowIdentity("child", "child-s", "new-generation"))
        await self.refused(
            self.controller.interfaces.act_element(self.app, self.snapshot, "insert", {"text": "old"}), "stale_target"
        )
        self.controller.actElement.assert_not_awaited()
        self.assertEqual(
            await self.controller.interfaces.act_element(self.app, fresh, "insert", {"text": "new"}), "dispatched"
        )
        self.controller.actElement.assert_awaited_once_with(self.app, fresh, None, "insert", {"text": "new"})

    async def test_unknown_old_window_cannot_borrow_same_surface_from_a_new_window(self):
        self.controller.current = self.omitted()
        self.controller.current["surfaces"].append(dict(surface("new-child", "root"), surface="child-s"))
        await refresh(self.controller, self.app)
        await self.refused(
            self.controller.interfaces.act_element(self.app, self.snapshot, "insert", {"text": "old"}),
            "observation_incomplete",
        )
        self.controller.actElement.assert_not_awaited()

    async def test_exact_unknown_snapshot_reads_refresh_even_when_new_window_uses_its_surface(self):
        for reading in ("element", "text"):
            with self.subTest(reading=reading):
                self.setUp()
                self.controller.current = self.omitted()
                self.controller.current["surfaces"].append(dict(surface("new-child", "root"), surface="child-s"))
                await refresh(self.controller, self.app)
                self.controller.current = self.full
                self.controller.discovery_calls.clear()

                async def read_after_refresh(*args):
                    self.controller.interfaces.validate_snapshot(self.app, self.snapshot)
                    self.assertEqual(self.controller.discovery_calls, [True])
                    return "read original element"

                method = (
                    "computer_use.interaction.read.element.agent.AgentElementReadPipeline.run"
                    if reading == "element"
                    else "computer_use.interaction.read.text.agent.AgentTextReadPipeline.run"
                )
                with patch(method, new=AsyncMock(side_effect=read_after_refresh)) as native_read:
                    result = (
                        await self.controller.interfaces.read_element(self.app, self.snapshot, None)
                        if reading == "element"
                        else await self.controller.interfaces.read_text(self.app, self.snapshot, None, 100)
                    )
                    self.assertEqual(result, "read original element")
                    native_read.assert_awaited_once()
                self.assertEqual(self.snapshot["window_identity"], self.identity)

    async def test_still_unknown_snapshot_reads_requery_but_never_reach_native_element_read(self):
        self.controller.current = self.omitted()
        self.controller.current["surfaces"].append(dict(surface("new-child", "root"), surface="child-s"))
        await refresh(self.controller, self.app)
        self.controller.reader.reader = R(read=AsyncMock())
        for reading in ("element", "text"):
            with self.subTest(reading=reading):
                self.controller.discovery_calls.clear()
                operation = (
                    self.controller.interfaces.read_element(self.app, self.snapshot, None)
                    if reading == "element"
                    else self.controller.interfaces.read_text(self.app, self.snapshot, None, 100)
                )
                await self.refused(operation, "observation_incomplete")
                self.assertEqual(self.controller.discovery_calls, [True])
                self.controller.reader.reader.read.assert_not_awaited()

    async def test_retired_snapshot_reads_do_not_refresh_or_borrow_a_new_generation(self):
        self.controller.current["surfaces"][1]["instance"] = "replacement"
        await refresh(self.controller, self.app)
        self.controller.discovery_calls.clear()
        await self.refused(self.controller.interfaces.read_element(self.app, self.snapshot, None), "stale_target")
        await self.refused(self.controller.interfaces.read_text(self.app, self.snapshot, None, 100), "stale_target")
        self.assertFalse(self.controller.discovery_calls)

    async def test_partial_root_failure_suspends_members_then_recovers_without_new_app(self):
        self.controller.current = self.omitted(root=True)
        await self.refused(refresh(self.controller, self.app), "observation_incomplete")
        self.assertEqual(self.instance.members, {})
        self.assertTrue(all(item.lifetime is WindowLifetime.RETAINED for item in self.instance.evidence.values()))
        self.controller.current = self.full
        await refresh(self.controller, self.window)
        self.assertEqual(set(self.instance.members), {"root", "child"})

    async def test_complete_root_absence_or_replacement_is_terminal_for_old_app(self):
        for changed in (
            self.omitted(root=True, partial=False),
            dict(self.full, surfaces=[dict(surface("root"), surface="replacement")]),
        ):
            with self.subTest(changed=changed):
                self.setUp()
                self.controller.current = changed
                await self.refused(refresh(self.controller, self.app), "stale_target")
                self.controller.current = self.full
                self.controller.discovery_calls.clear()
                await self.refused(refresh(self.controller, self.app), "stale_target")
                await self.refused(refresh(self.controller, self.window), "stale_target")
                self.assertFalse(self.controller.discovery_calls)

    async def test_generic_query_failure_has_no_authority_to_retire_window(self):
        error = ComputerUseError(
            dict(
                code="stale_target",
                effect="none",
                target="optional query",
                detail="unrelated observation expired",
                next="retry",
            )
        )
        with patch.object(self.controller.reader, "_discover", AsyncMock(side_effect=error)):
            await self.refused(refresh(self.controller, self.window), "stale_target")
            self.assertEqual(self.instance.evidence[self.identity].lifetime, WindowLifetime.RETAINED)
        await refresh(self.controller, self.window)

    async def test_preselected_root_before_first_read_preserves_unknown_or_retirement(self):
        for partial in (True, False):
            with self.subTest(partial=partial):
                self.setUp()
                self.app._instance = InstanceState("Fixture")
                self.controller.state(self.app).selection = self.selection
                self.controller.current = self.omitted(root=True, partial=partial)
                await self.refused(
                    resolve(self.controller, self.app, "tree"), "observation_incomplete" if partial else "stale_target"
                )
                self.controller.current = self.full
                if partial:
                    await resolve(self.controller, self.app, "tree")
                    self.assertEqual(set(self.app._instance.members), {"root", "child"})
                else:
                    await self.refused(resolve(self.controller, self.app, "tree"), "stale_target")
