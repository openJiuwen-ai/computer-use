"""Element wheel selection preserves the requested region and exact OS hit."""

import unittest
from dataclasses import replace
from types import SimpleNamespace

from sdk.interaction.write.resolution.test_scroll_query import ScrollPort, graph as page_graph
from sdk.native.query.test_target_query import ROOT, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.scroll import ScrollPointerQuery
from computer_use.native.contracts import (
    Channel,
    Confirmation,
    Dispatch,
    Facts,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge as K,
    Primitive,
    ScrollPointerActuator,
    SelectionDependency,
    StepResult,
    UnitCommand,
)
from computer_use.native.transport import ConnectionPort, intent_key
from computer_use.protocol.execution import decodeInspectQuery


def graph():
    result = dict(
        root=node("AXScrollArea", children=["unrelated-body"]),
        layout=node("AXGroup", children=["label"]),
        label=node("AXStaticText", name="Row 30"),
        requested=node("AXStaticText", name="Row 1"),
    )
    result["layout"]["parent"] = ["root"]
    result["label"]["parent"] = ["layout"]
    result["requested"]["parent"] = ["root"]
    return result


class PointerPort(ScrollPort):
    capabilities = frozenset(("nativeScroll", "nativeElementScroll", "nativeSelectionBasis"))
    hit = "label"
    prepared = True
    after_preparation = None
    page_unavailable = False

    async def inspect(self, operation, target, timeout_ms, *, within=(), actuator=None, basis=()):
        if actuator is not None:
            assert basis
        self.events.append(("inspect", target, actuator, basis))
        primitives = ()
        if actuator is not None and actuator.relation == "scrollPage":
            primitives = (Primitive("page", "native_scroll", Channel.OBJECT, K.YES, K.YES),)
        elif actuator is not None:
            geometry = actuator.node == self.hit
            ready = geometry and self.prepared
            primitives = (
                Primitive(
                    "wheel",
                    "pointer_sequence",
                    Channel.POINTER,
                    K.YES,
                    K.YES if ready else K.UNKNOWN,
                    pending=() if ready else ("receiver",) if geometry else ("geometry",),
                    background_preparation=K.YES,
                ),
            )
        return Facts(
            target,
            "current",
            "requested element",
            K.YES,
            K.YES,
            K.YES,
            (),
            primitives,
            InputContext(pointer_hit=self.hit),
            platform="macos",
            actuator=actuator,
        )

    async def execute(self, operation, step, command, timeout_ms):
        self.events.append(("execute", command))
        if command.prepare:
            self.prepared = True
            if self.after_preparation is not None:
                self.after_preparation(self)
            return StepResult(command.target, Dispatch.NOT_DISPATCHED, "prepared", confirmation=Confirmation.CONFIRMED)
        if command.primitive.unit == "native_scroll" and self.page_unavailable:
            return StepResult(
                command.target,
                Dispatch.NOT_DISPATCHED,
                "page unavailable",
                error_code="input_unavailable",
                retry_reason="routeUnavailable",
            )
        return StepResult(
            command.target,
            self.dispatch,
            "wheel sent",
            error_code="execution_unknown" if self.dispatch in (Dispatch.PARTIAL, Dispatch.UNKNOWN) else None,
        )


class ScrollPointerTests(unittest.IsolatedAsyncioTestCase):
    async def test_region_probe_then_exact_passive_hit_without_loading_document(self):
        port = PointerPort(graph())
        for requested in ("root", "requested"):
            query = ScrollPointerQuery(port, "op", replace(ROOT, node=requested), lambda: 1000)
            self.assertEqual(await query.resolve(), ScrollPointerActuator("root", "root"))
            self.assertEqual(await query.resolve("label"), ScrollPointerActuator("label", "root"))
            self.assertTrue({"root", "layout", "label"} <= {d.node for d in query.basis})
            for dep in query.basis:
                self.assertFalse({"AXTitle", "AXValue", "AXFocused"} & set(dep.fields))
            self.assertTrue(all(e[2] == 0 for e in port.events if e[0] == "nodes"))
            self.assertNotIn("unrelated-body", [e[1].node for e in port.events if e[0] == "nodes"])

    async def test_editable_document_and_collection_hits_share_their_observed_region(self):
        editing = ("AXEditable", "settable.AXFocused", "settable.AXValue", "settable.AXSelectedText")
        for role in (
            "AXTextArea",
            "AXTextField",
            "AXTable",
            "AXOutline",
            "AXList",
            "AXBrowser",
            "AXRow",
            "AXColumn",
            "AXCell",
            "AXWebArea",
        ):
            for unknown in (False, True):
                with self.subTest(role=role, editing_unknown=unknown):
                    data = graph()
                    data["label"]["values"]["AXRole"] = role
                    for field in editing:
                        if unknown:
                            data["label"]["statuses"][field] = "timeout"
                        else:
                            data["label"]["values"][field] = True
                    data["label"]["actions"] = dict(
                        state="complete",
                        items=[
                            dict(name="AXShowMenu", description="Show menu"),
                            dict(name="AXPress", description="Press"),
                        ],
                    )
                    query = ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000)
                    self.assertEqual(await query.resolve("label"), ScrollPointerActuator("label", "root"))
                    deps = [d for d in query.basis if d.node == "label"]
                    self.assertTrue(any(d.actions and "AXParent" in d.relations for d in deps))
                    self.assertFalse(set(editing) & {field for d in deps for field in d.fields})

    async def test_unknown_and_independent_hit_boundaries_do_not_authorize_a_wheel(self):
        for role in (
            "AXButton",
            "AXLink",
            "AXScrollArea",
            "AXScrollBar",
            "AXSlider",
            "AXComboBox",
            "AXIncrementor",
            "AXPopUpButton",
            "AXWindow",
            "AXUnknown",
            None,
        ):
            data = graph()
            data["label"]["values"]["AXRole"] = role
            self.assertIsNone(
                await ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000).resolve("label"), role
            )
        data = graph()
        data["label"]["statuses"]["AXRole"] = "timeout"
        self.assertIsNone(await ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000).resolve("label"))
        for token in ("layout", "label"):
            for role in ("AXScrollArea", "AXButton", "AXSlider", "AXComboBox"):
                data = graph()
                data[token]["values"]["AXRole"] = role
                self.assertIsNone(
                    await ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000).resolve("label")
                )

    async def test_ambiguous_wrappers_still_require_known_nonediting_capabilities(self):
        for token in ("layout", "label"):
            for field in ("AXEditable", "settable.AXFocused", "settable.AXValue", "settable.AXSelectedText"):
                for unknown in (False, True):
                    data = graph()
                    if unknown:
                        data[token]["statuses"][field] = "timeout"
                    else:
                        data[token]["values"][field] = True
                    query = ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000)
                    self.assertIsNone(await query.resolve("label"))

    async def test_click_and_context_actions_do_not_declare_wheel_consumption(self):
        data = graph()
        for token, action in (("layout", "AXPress"), ("label", "AXShowMenu")):
            data[token]["actions"] = dict(state="complete", items=[dict(name=action, description=action)])
        self.assertEqual(
            await ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000).resolve("label"),
            ScrollPointerActuator("label", "root"),
        )

    async def test_scroll_into_view_action_on_web_content_preserves_the_original_wheel(self):
        # Native failure shape: the requested ScrollArea's center hits its
        # focus-writable WebArea, which declares ShowMenu + ScrollToVisible.
        # ScrollToVisible reveals an object; it is not an independent wheel
        # consumer and must not hide this already proved viewport/member path.
        data = dict(
            root=node("AXScrollArea", children=["document"]),
            document=node(
                "AXWebArea",
                **{
                    "AXEnabled": True,
                    "settable.AXFocused": True,
                    "settable.AXValue": False,
                    "settable.AXSelectedText": False,
                },
            ),
        )
        data["document"]["parent"] = ["root"]
        data["document"]["actions"] = dict(
            state="complete", items=[dict(name=name, description=name) for name in ("AXShowMenu", "AXScrollToVisible")]
        )
        port = PointerPort(data)
        port.hit = "document"
        intent = Intent("scroll", dict(direction="down", pages=1))
        result = await Engine(port).execute(intent, ROOT)
        self.assertEqual(result["status"], "dispatched")
        commands = [event[1] for event in port.events if event[0] == "execute"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(
            (commands[0].target, commands[0].intent, commands[0].primitive.unit), (ROOT, intent, "pointer_sequence")
        )
        self.assertEqual(commands[0].actuator, ScrollPointerActuator("document", "root"))
        basis = [event for event in port.events if event[0] == "inspect"][-1][3]
        self.assertTrue({"root", "document"} <= {dependency.node for dependency in basis})
        self.assertTrue(
            any(
                dependency.node == "document" and dependency.actions and "AXParent" in dependency.relations
                for dependency in basis
            )
        )

    async def test_scroll_into_view_does_not_bypass_independent_wheel_boundaries(self):
        for role, action in (
            ("AXScrollArea", "AXShowMenu"),
            ("AXSlider", "AXShowMenu"),
            ("AXWebArea", "custom"),
            ("AXWebArea", "AXIncrement"),
            ("AXWebArea", "AXScrollDownByPage"),
        ):
            with self.subTest(role=role, action=action):
                data = graph()
                data["label"]["values"]["AXRole"] = role
                data["label"]["actions"] = dict(
                    state="complete",
                    items=[dict(name=name, description=name) for name in ("AXScrollToVisible", action)],
                )
                port = PointerPort(data)
                with self.assertRaises(ComputerUseError) as error:
                    await Engine(port).execute(Intent("scroll", dict(direction="down", pages=1)), ROOT)
                self.assertEqual(error.exception.effect, "none")
                self.assertFalse(any(event[0] == "execute" for event in port.events))

    async def test_incomplete_unknown_or_wheel_actions_preserve_the_boundary(self):
        for role in ("AXGroup", "AXStaticText", "AXTextArea", "AXTextField", "AXTable"):
            for token in ("layout", "label"):
                for action in (None, "AXIncrement", "AXDecrement", "AXScrollDownByPage", "custom"):
                    with self.subTest(role=role, token=token, action=action):
                        data = graph()
                        data[token]["values"]["AXRole"] = role
                        data[token]["actions"] = (
                            dict(state="partial", items=[])
                            if action is None
                            else dict(state="complete", items=[dict(name=action, description="Native action")])
                        )
                        self.assertIsNone(
                            await ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000).resolve("label")
                        )

    async def test_parent_must_be_single_complete_noncyclic_and_reach_the_chosen_region(self):
        for parents in ([], ["outside"]):
            data = graph()
            data["layout"]["parent"] = parents
            data["outside"] = node("AXScrollArea")
            self.assertIsNone(await ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000).resolve("label"))
        for parents in (["layout"], ["root", "other"]):
            data = graph()
            data["layout"]["parent"] = parents
            with self.assertRaises(ComputerUseError):
                await ScrollPointerQuery(PointerPort(data), "op", ROOT, lambda: 1000).resolve("label")
        port = PointerPort(graph())
        port.edges["label", "AXParent"] = dict(targets=["layout"], state="partial", certainty="confirmed")
        with self.assertRaises(ComputerUseError):
            await ScrollPointerQuery(port, "op", ROOT, lambda: 1000).resolve("label")

    async def test_nearest_region_is_not_bypassed_and_bar_does_not_supply_a_viewport(self):
        data = graph()
        data["nested"] = node("AXScrollArea")
        data["nested"]["parent"] = ["root"]
        data["requested"]["parent"] = ["nested"]
        target = replace(ROOT, node="requested")
        query = ScrollPointerQuery(PointerPort(data), "op", target, lambda: 1000)
        self.assertEqual(await query.resolve(), ScrollPointerActuator("nested", "nested"))
        self.assertIsNone(await query.resolve("label"))
        data["nested"]["values"]["AXRole"] = "AXScrollBar"
        self.assertIsNone(await ScrollPointerQuery(PointerPort(data), "op", target, lambda: 1000).resolve())

    async def test_half_page_engine_keeps_original_target_intent_and_single_write(self):
        for direction in ("up", "down", "left", "right"):
            port = PointerPort(graph())
            target = replace(ROOT, node="requested")
            intent = Intent("scroll", dict(direction=direction, pages=0.5))
            result = await Engine(port).execute(intent, target)
            self.assertEqual(result["status"], "dispatched")
            commands = [e[1] for e in port.events if e[0] == "execute"]
            self.assertEqual(len(commands), 1)
            self.assertEqual((commands[0].target, commands[0].intent), (target, intent))
            self.assertEqual(commands[0].actuator, ScrollPointerActuator("label", "root"))
            self.assertEqual(sum(e[0] == "begin" for e in port.events), 1)

    async def test_half_page_over_editable_text_dispatches_the_original_scroll_once(self):
        # Recorded failure: a TextArea in a ScrollArea exposes writable focus,
        # value and selected text plus AXShowMenu. These are normal text facts,
        # not a reason to reject a wheel or to enter a text/click route.
        data = graph()
        data["label"]["values"].update(
            AXRole="AXTextArea",
            **{"settable.AXFocused": True, "settable.AXValue": True, "settable.AXSelectedText": True},
        )
        data["label"]["actions"] = dict(state="complete", items=[dict(name="AXShowMenu", description="显示菜单")])
        port = PointerPort(data)
        intent = Intent("scroll", dict(direction="down", pages=0.5))
        result = await Engine(port).execute(intent, ROOT)
        self.assertEqual(result["status"], "dispatched")
        commands = [e[1] for e in port.events if e[0] == "execute"]
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(
            (command.target, command.intent, command.actuator), (ROOT, intent, ScrollPointerActuator("label", "root"))
        )
        self.assertEqual(command.primitive.unit, "pointer_sequence")
        self.assertEqual(sum(e[0] == "begin" for e in port.events), 1)

    async def test_table_text_cell_confirmation_is_not_a_wheel_boundary(self):
        # AppKit cell-based tables expose editable text cells as TextField;
        # AXConfirm/AXCancel describe editing, not wheel-based value changes.
        for row_role in ("AXRow", "AXCell"):
            data = graph()
            data["label"]["values"].update(
                AXRole="AXTextField", **{"settable.AXFocused": True, "settable.AXValue": True}
            )
            data["label"]["actions"] = dict(
                state="complete",
                items=[dict(name=name, description=name) for name in ("AXShowMenu", "AXConfirm", "AXCancel")],
            )
            data["layout"]["values"]["AXRole"] = row_role
            data["layout"]["parent"] = ["table"]
            data["table"] = node("AXTable")
            data["table"]["parent"] = ["root"]
            port = PointerPort(data)
            await Engine(port).execute(Intent("scroll", dict(direction="down", pages=0.5)), ROOT)
            commands = [e[1] for e in port.events if e[0] == "execute"]
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0].actuator, ScrollPointerActuator("label", "root"))
            basis = [e for e in port.events if e[0] == "inspect"][-1][3]
            self.assertTrue({"label", "layout", "table", "root"} <= {d.node for d in basis})

    async def test_text_field_does_not_bypass_an_independent_parent_control(self):
        for parent_role in ("AXComboBox", "AXIncrementor", "AXSlider", "AXScrollArea", "AXUnknown"):
            data = graph()
            data["label"]["values"].update(AXRole="AXTextField", **{"settable.AXValue": True})
            data["label"]["actions"] = dict(state="complete", items=[dict(name="AXConfirm", description="Confirm")])
            data["layout"]["values"]["AXRole"] = parent_role
            port = PointerPort(data)
            with self.assertRaises(ComputerUseError) as error:
                await Engine(port).execute(Intent("scroll", dict(direction="down", pages=0.5)), ROOT)
            self.assertEqual(error.exception.effect, "none")
            self.assertFalse(any(e[0] == "execute" for e in port.events))

    async def test_whole_page_remains_native_and_known_no_dispatch_can_advance_to_wheel(self):
        for refused in (False, True):
            data = page_graph()
            data.update(layout=graph()["layout"], label=graph()["label"])
            port = PointerPort(data)
            port.page_unavailable = refused
            await Engine(port).execute(Intent("scroll", dict(direction="down")), ROOT)
            units = [e[1].primitive.unit for e in port.events if e[0] == "execute"]
            self.assertEqual(units, ["native_scroll", "pointer_sequence"] if refused else ["native_scroll"])

    async def test_optional_page_facts_do_not_block_an_independently_confirmed_wheel(self):
        for field in ("AXOrientation", "AXRole"):
            data = page_graph()
            data.update(layout=graph()["layout"], label=graph()["label"])
            data["bar"]["statuses"][field] = "timeout"
            port = PointerPort(data)
            await Engine(port).execute(Intent("scroll", dict(direction="down", pages=1)), ROOT)
            commands = [e[1] for e in port.events if e[0] == "execute"]
            self.assertEqual([c.primitive.unit for c in commands], ["pointer_sequence"])
            # Failed page-only facts are not carried into the independent route.
            final = [e for e in port.events if e[0] == "inspect"][-1]
            self.assertNotIn("bar", {d.node for d in final[3]})

    async def test_original_region_failures_are_never_treated_as_optional_page_facts(self):
        for field in ("AXRole", "AXEnabled"):
            data = graph()
            if field == "AXRole":
                data["root"]["statuses"][field] = "timeout"
            else:
                data["root"]["values"][field] = False
            port = PointerPort(data)
            with self.assertRaises(ComputerUseError):
                await Engine(port).execute(Intent("scroll", dict(direction="down", pages=1)), ROOT)
            self.assertFalse(any(e[0] == "execute" for e in port.events))

    async def test_uncertain_page_and_wheel_never_replay(self):
        for pages in (0.5, 1):
            for state in (Dispatch.PARTIAL, Dispatch.UNKNOWN):
                data = page_graph()
                data.update(layout=graph()["layout"], label=graph()["label"])
                port = PointerPort(data)
                port.dispatch = state
                with self.assertRaises(ComputerUseError) as error:
                    await Engine(port).execute(Intent("scroll", dict(direction="down", pages=pages)), ROOT)
                self.assertEqual(error.exception.effect, "partial" if state == Dispatch.PARTIAL else "possible")
                self.assertEqual(sum(e[0] == "execute" for e in port.events), 1)

    async def test_preparation_rechecks_wheel_content_path_before_input(self):
        for change in (None, "nested_region", "value_control", "wheel_action", "different_parent"):
            port = PointerPort(graph())
            port.prepared = False
            port.graph["label"]["values"].update(AXRole="AXTextArea", **{"settable.AXValue": True})
            if change:

                def mutate(p, *, change=change):
                    if change == "nested_region":
                        p.graph["layout"]["values"]["AXRole"] = "AXScrollArea"
                    elif change == "value_control":
                        p.graph["label"]["values"]["AXRole"] = "AXSlider"
                    elif change == "wheel_action":
                        p.graph["label"]["actions"]["items"] = [dict(name="AXDecrement", description="Decrement")]
                    else:
                        p.graph["label"]["parent"] = ["outside"]
                        p.graph["outside"] = node("AXScrollArea")

                port.after_preparation = mutate
                with self.assertRaises(ComputerUseError):
                    await Engine(port).execute(Intent("scroll", dict(direction="down", pages=0.5)), ROOT)
            else:
                await Engine(port).execute(Intent("scroll", dict(direction="down", pages=0.5)), ROOT)
            commands = [e[1] for e in port.events if e[0] == "execute"]
            self.assertEqual([c.prepare for c in commands], [True] if change else [True, False])

    async def test_rejected_hit_does_not_prepare_or_send_input(self):
        data = graph()
        data["layout"]["values"]["AXRole"] = "AXScrollArea"
        port = PointerPort(data)
        with self.assertRaises(ComputerUseError) as error:
            await Engine(port).execute(Intent("scroll", dict(direction="down", pages=0.5)), ROOT)
        self.assertEqual(error.exception.effect, "none")
        self.assertFalse(any(e[0] == "execute" for e in port.events))

    async def test_new_contract_requires_capability_and_basis_before_io(self):
        async def unexpected(*args):
            raise AssertionError("Unsupported contract reached driver")

        connection = SimpleNamespace(
            protocol_version=6,
            sessionIdentity=("s", 1),
            platform="macos",
            capabilities=frozenset(("nativeElementPointer", "nativeSelectionBasis")),
            request=unexpected,
        )
        port = ConnectionPort(connection, HostPolicy())
        intent = Intent("scroll", dict(direction="down", pages=0.5))
        port._operations["op"] = (
            connection.sessionIdentity,
            intent_key(dict(kind="scroll", parameters=dict(intent.parameters))),
            None,
        )
        selected = ScrollPointerActuator("label", "root")
        basis = (SelectionDependency("observation", "root", ("AXRole",)),)
        with self.assertRaisesRegex(ComputerUseError, "nativeElementScroll"):
            await port.inspect("op", ROOT, 1000, actuator=selected, basis=basis)
        primitive = Primitive("p", "pointer_sequence", Channel.POINTER, K.YES, K.YES)
        for prepare in (False, True):
            with self.assertRaisesRegex(ComputerUseError, "nativeElementScroll"):
                await port.execute("op", 1, UnitCommand(primitive, ROOT, intent, selected, prepare=prepare), 1000)
        connection.capabilities |= {"nativeElementScroll"}
        with self.assertRaisesRegex(ValueError, "selection dependencies"):
            await port.inspect("op", ROOT, 1000, actuator=selected)
        with self.assertRaisesRegex(ValueError, "selection dependencies"):
            decodeInspectQuery(dict(kind="inputContext", within=[], actuator=selected.wire()))
        valid = dict(kind="inputContext", within=[], actuator=selected.wire(), basis=[d.wire() for d in basis])
        self.assertEqual(decodeInspectQuery(valid), valid)
        moving = ScrollPointerActuator("label", "root", ("label", "table"))
        with self.assertRaisesRegex(ComputerUseError, "nativeScrollContentPath"):
            await port.inspect("op", ROOT, 1000, actuator=moving, basis=basis)
        for prepare in (False, True):
            with self.assertRaisesRegex(ComputerUseError, "nativeScrollContentPath"):
                await port.execute("op", 1, UnitCommand(primitive, ROOT, intent, moving, prepare=prepare), 1000)
        for path in ([], ["label"], ["label", "label"], ["table", "label"], ["label"] * 41):
            with self.subTest(path=path), self.assertRaises(ValueError):
                decodeInspectQuery(dict(valid, actuator=dict(selected.wire(), contentPath=path)))
        self.assertEqual(decodeInspectQuery(dict(valid, actuator=moving.wire()))["actuator"], moving.wire())


if __name__ == "__main__":
    unittest.main()
