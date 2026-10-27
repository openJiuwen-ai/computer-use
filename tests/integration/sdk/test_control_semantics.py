"""Reading and execution consume the same facts without display-driven input."""

import unittest
from copy import deepcopy
from types import SimpleNamespace

from observation_fixture import from_records, normalize_fixture as normalizeFacts
from sdk.interaction.read.facts.test_native_facts import batch, edge, node as fact_node
from sdk.native.query.test_target_query import ROOT, GraphPort, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import Channel, Facts, InputContext, Intent, Knowledge as K, Primitive
from computer_use.native.semantics import UNKNOWN, field_value

FEATURES = frozenset(
    ("nativeActionIntents", "nativeActuators", "nativeKeyboardIntents", "nativeTextInput", "nativeTextReplacement")
)


class SemanticPort(GraphPort):
    capabilities = FEATURES

    def __init__(self, graph):
        super().__init__(graph)
        self.connection = SimpleNamespace(platform=self.platform, capabilities=self.capabilities)

    async def begin_read(self, target, timeout_ms):
        return await self.begin(None, target, timeout_ms)

    async def inspect(self, operation, target, timeout_ms, *, within=(), actuator=None, basis=()):
        if actuator:
            assert basis, "Composite selection must carry its structural dependencies"
        self.events.append(("inspect", target, actuator))
        # The fake native peer declares a route, as a real driver may. Python
        # must still establish the requested control's text semantics first.
        selected = actuator.node if actuator else target.node
        primitive = Primitive("unicode", "key_sequence", Channel.KEYBOARD, K.YES, K.YES)
        context = InputContext(
            keyboard_surface=target.surface,
            focused_node=selected,
            window_route=K.YES,
            editor=selected if actuator else None,
            editor_owner=target.node if actuator else None,
            focus_ancestors=(target.node,) if actuator else (),
        )
        return Facts(
            target,
            "now",
            "control",
            K.YES,
            K.YES,
            K.YES,
            (),
            (primitive,),
            context,
            platform=self.platform,
            native_role=self.graph[target.node]["values"]["AXRole"],
            actuator=actuator,
        )


class ControlSemanticsTests(unittest.IsolatedAsyncioTestCase):
    def test_menu_actions_require_the_observed_scope_not_just_a_menu_role(self):
        source = fact_node("command", AXRole="AXMenuItem", AXEnabled=True)
        source["actions"]["items"] = [dict(name="AXPress", description="Press")]
        for kind in (None, "window", "applicationMenu"):
            raw = batch([source])
            for supported in (False, True):
                features = FEATURES | ({"nativeMenuActions"} if supported else set())
                snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=features, scope_kind=kind)
                with self.subTest(kind=kind, supported=supported):
                    self.assertEqual(
                        snapshot.roots[0]["capabilities"]["click"],
                        "available" if kind == "applicationMenu" and supported else "unknown",
                    )
                    self.assertEqual(snapshot.native, from_records(raw))
                    self.assertEqual(snapshot.scope_kind, kind)

    async def test_direct_read_and_insert_share_editing_evidence_without_scanning_a_document(self):
        for role, values in (
            ("AXTextField", {"settable.AXValue": True, "settable.AXFocused": True}),
            ("AXTextArea", {"AXEditable": True}),
            ("AXWebArea", {"AXEditable": True}),
        ):
            with self.subTest(role=role):
                children = [] if role == "AXTextField" else ["unread-paragraphs"]
                port = SemanticPort(dict(root=node(role, children=children, **values)))
                snapshot = await NativeReader(port).read(ROOT)
                self.assertEqual(snapshot.roots[0]["capabilities"]["typeText"], "available")
                before = deepcopy(snapshot.facts)
                await Engine(port).execute(Intent("insert", {"text": "hello"}), ROOT)
                self.assertEqual(snapshot.facts, before)
                self.assertEqual([call[1].node for call in port.events if call[0] == "nodes"], ["root", "root"])
                self.assertEqual([call[1] for call in port.events if call[0] == "execute"], [ROOT])

    async def test_read_only_and_disabled_do_not_become_editors_even_with_a_writable_value(self):
        for field in ("AXEditable", "AXEnabled"):
            port = SemanticPort(
                dict(root=node("AXTextField", **{field: False, "settable.AXValue": True, "settable.AXFocused": True}))
            )
            snapshot = await NativeReader(port).read(ROOT)
            self.assertEqual(snapshot.roots[0]["capabilities"]["typeText"], "unavailable")
            with self.assertRaises(ComputerUseError):
                await Engine(port).execute(Intent("insert", {"text": "hello"}), ROOT)
            self.assertFalse(any(call[0] == "execute" for call in port.events))

    async def test_layout_and_selection_containers_are_not_text_receivers(self):
        for role in ("AXGroup", "AXRow", "AXTable", "AXStaticText"):
            port = SemanticPort(
                dict(
                    root=node(role, children=["field"]),
                    field=node("AXTextField", **{"settable.AXValue": True, "settable.AXFocused": True}),
                )
            )
            snapshot = await NativeReader(port).read(ROOT)
            self.assertEqual(snapshot.roots[0]["capabilities"]["typeText"], "unknown")
            with self.assertRaises(ComputerUseError):
                await Engine(port).execute(Intent("insert", {"text": "hello"}), ROOT)
            self.assertFalse(any(call[0] == "execute" for call in port.events))

    async def test_composite_hint_does_not_guess_an_unread_editor_but_execution_can_resolve_it(self):
        port = SemanticPort(
            dict(
                root=node("AXComboBox", children=["field"]),
                field=node("AXTextField", **{"settable.AXValue": True, "settable.AXFocused": True}),
            )
        )
        snapshot = await NativeReader(port).read(ROOT)
        self.assertEqual(snapshot.roots[0]["capabilities"]["typeText"], "unknown")
        await Engine(port).execute(Intent("insert", {"text": "hello"}), ROOT)
        self.assertEqual(
            [call[2].node if call[2] else None for call in port.events if call[0] == "inspect"], [None, "field"]
        )
        self.assertEqual([call[1] for call in port.events if call[0] == "execute"], [ROOT])

    def test_partial_action_catalogue_does_not_change_ordinary_click_semantics(self):
        for role, actions, state, expected in (
            ("AXButton", ["AXPress"], "partial", "available"),
            ("AXPopUpButton", ["AXShowMenu"], "complete", "available"),
            ("AXPopUpButton", ["AXShowMenu"], "partial", "unknown"),
            ("AXStaticText", ["AXShowMenu"], "complete", "unknown"),
            ("AXMenuItem", ["AXPress"], "complete", "unknown"),
            ("AXButton", [], "complete", "unknown"),
            ("AXTextField", ["AXPress"], "complete", "unknown"),
        ):
            source = fact_node("target", AXRole=role)
            source["actions"] = dict(state=state, items=[dict(name=name, description=name) for name in actions])
            raw = batch([source])
            before = deepcopy(raw)
            snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=FEATURES)
            with self.subTest(role=role, state=state):
                self.assertEqual(snapshot.roots[0]["capabilities"]["click"], expected)
                self.assertEqual(snapshot.native, from_records(before))
                self.assertEqual(
                    [{k: v for k, v in action.items() if k != "kind"} for action in snapshot.roots[0]["actions"]],
                    source["actions"]["items"],
                )

    def test_driver_features_are_required_and_focus_support_is_not_a_focus_report(self):
        source = fact_node(
            "target", AXRole="AXTextField", AXFocused=False, **{"settable.AXValue": True, "settable.AXFocused": True}
        )
        raw = batch([source])
        for features, available in (((), False), (FEATURES, True)):
            snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=features)
            caps = snapshot.roots[0]["capabilities"]
            self.assertEqual(caps["typeText"], "available" if available else "unknown")
            self.assertEqual(caps["focus"], "available" if available else "unknown")
            self.assertEqual(caps["replaceText"], "unknown", "Full replace needs a separate native editing contract")
            self.assertFalse(snapshot.roots[0]["focused"])

    def test_button_composition_preserves_an_independent_label_action_and_native_binding(self):
        button = fact_node("button", AXRole="AXButton")
        button["actions"]["items"] = [dict(name="AXPress", description="Press")]
        label = fact_node("label", AXRole="AXStaticText", AXValue="Open")
        label["actions"]["items"] = [dict(name="AXShowMenu", description="AXShowMenu")]
        raw = batch(
            [button, fact_node("layout", AXRole="AXGroup"), label], [edge("button", "layout"), edge("layout", "label")]
        )
        before = deepcopy(raw)
        snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=FEATURES)
        rendered = snapshot.render()
        self.assertIn("clickable", rendered["text"])
        self.assertIn("AXShowMenu", rendered["text"])
        self.assertEqual(rendered["refs"]["2"], "label")
        self.assertEqual(snapshot.native, from_records(before))

    def test_nonboolean_and_missing_fields_never_turn_into_positive_evidence(self):
        source = fact_node("target", AXRole="AXTextField", **{"settable.AXValue": 1, "settable.AXFocused": True})
        self.assertIs(field_value("macos", source, "valueWritable"), UNKNOWN)
        self.assertIs(field_value("macos", source, "editable"), UNKNOWN)
        snapshot = normalizeFacts(batch([source]), DisplayReferences("session"), driver_features=FEATURES)
        self.assertEqual(snapshot.roots[0]["capabilities"]["typeText"], "unknown")
        source["fieldStatus"]["settable.AXValue"] = dict(state="unsupported")
        source["values"].pop("settable.AXValue")
        self.assertIsNone(field_value("macos", source, "valueWritable"))

    def test_other_platform_facts_do_not_borrow_macos_editing_routes(self):
        for platform, values in (
            ("windows", {"ControlType": "ControlType.Edit", "IsKeyboardFocusable": True, "Value.IsReadOnly": False}),
            ("linux", {"role": "entry", "editable": True, "focusable": True}),
        ):
            raw = batch([fact_node("target", **values)])
            raw["platform"] = platform
            snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=FEATURES)
            caps = snapshot.roots[0]["capabilities"]
            self.assertEqual(
                {k: v for k, v in caps.items() if k != "focus"},
                dict(click="unknown", typeText="unknown", replaceText="unknown"),
            )
            # UIA SetFocus is now a declared Windows unit. Linux still requires
            # its Component interface, which this incomplete fixture omits.
            self.assertEqual(caps["focus"], "available" if platform == "windows" else "unknown")
            readonly = normalizeFacts(raw, DisplayReferences("session"), driver_features=())
            self.assertEqual(set(readonly.roots[0]["capabilities"].values()), {"unknown"})


if __name__ == "__main__":
    unittest.main()
