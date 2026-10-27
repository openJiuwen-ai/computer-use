"""Semantic lookup uses raw facts and preserves scope until one input is sent."""

import asyncio
import unittest
from copy import deepcopy
from dataclasses import replace

from observation_fixture import facts_fixture, relation_fixture, reply

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.native.contracts import (
    Binding,
    Channel,
    Cleanup,
    Dispatch,
    Facts,
    HostPolicy,
    InputContext,
    Intent,
    Knowledge as K,
    OpenedOperation,
    Primitive,
    StepResult,
)
from computer_use.native.lifecycle import CallLifecycle
from computer_use.native.query.path import TargetQuery

ROOT = Binding("s", "i", "window", "surface", "root")


def node(role="AXGroup", name="", children=(), **values):
    return dict(
        values=dict(AXRole=role, AXTitle=name, **values),
        children=list(children),
        labels=[],
        statuses={},
        actions=dict(state="complete", items=[]),
    )


class GraphPort:
    platform = "macos"
    policy = HostPolicy()

    def __init__(self, graph):
        self.graph = deepcopy(graph)
        self.events = []
        self.calls = CallLifecycle(self)
        self.edges = {}
        self.pages = {}
        self.dispatch = Dispatch.DISPATCHED

    async def begin(self, intent, target, timeout_ms):
        self.events.append(("begin", target))
        return OpenedOperation("op", replace(target, root_window="window"))

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        self.events.append(("nodes", target))
        original = self.graph[target.node]
        values = {key: value for key, value in original["values"].items() if key in fields}
        statuses = {key: dict(state="absent") for key in fields if key not in values}
        for key, state in original["statuses"].items():
            if key in fields:
                values.pop(key, None)
                statuses[key] = dict(state=state)
        relations = []
        for property_name, destinations_value in (
            ("AXChildren", original["children"]),
            ("AXTitleUIElement", original["labels"]),
        ):
            edge = self.edges.get(
                (target.node, property_name), dict(targets=destinations_value, state="complete", certainty="confirmed")
            )
            relations.append(dict(source=target.node, property=property_name, **edge))
        unread = set(item for edge in relations for item in edge["targets"]) - {target.node}
        return facts_fixture(
            dict(
                kind="nodes",
                session=target.session,
                surface=target.surface,
                platform=self.platform,
                observed=dict(id="batch-" + target.node, startedAt=1, completedAt=2),
                roots=[target.node],
                nodes=[
                    dict(
                        node=target.node,
                        requestedFields=fields,
                        values=values,
                        fieldStatus=statuses,
                        actions=original["actions"],
                    )
                ],
                requestedRelations=["AXChildren", "AXTitleUIElement"],
                relations=relations,
                unread=sorted(unread),
                stopReasons=[],
            )
        )

    async def query_relation(self, operation, target, property, cursor, limit, timeout_ms):
        self.events.append(("relation", cursor))
        return relation_fixture(
            dict(
                kind="relation",
                session=target.session,
                surface=target.surface,
                platform=self.platform,
                observed=dict(id="page", startedAt=2, completedAt=3),
                relation=deepcopy(self.pages[cursor]),
            )
        )

    async def nodes(self, *args, **options):
        scope_kind = options.pop("scope_kind", None)
        return reply(
            await self.query_nodes(*args, **options),
            driver_features=getattr(self, "capabilities", ()),
            scope_kind=scope_kind,
        )

    async def relation(self, *args, **options):
        return reply(await self.query_relation(*args, **options))

    async def inspect(self, operation, target, timeout_ms, *, within=()):
        self.events.append(("inspect", target, within))
        primitive = Primitive("press", "native_action", Channel.OBJECT, K.YES, K.YES, action="AXPress")
        return Facts(
            target,
            "now",
            "button",
            K.YES,
            K.YES,
            K.YES,
            (),
            (primitive,),
            InputContext(),
            platform=self.platform,
            native_role="AXButton",
            actions_complete=True,
        )

    async def execute(self, operation, step, command, timeout_ms):
        self.events.append(("execute", command.target))
        return StepResult(
            command.target,
            self.dispatch,
            "sent once",
            error_code="execution_unknown" if self.dispatch == Dispatch.UNKNOWN else None,
        )

    async def finish(self, operation):
        self.events.append(("finish", operation))
        return Cleanup.COMPLETE


class QueryTests(unittest.IsolatedAsyncioTestCase):
    def query(self, graph, **options):
        self.port = GraphPort(graph)
        return TargetQuery(self.port, "op", ROOT, lambda: 1000, **options)

    async def error(self, query, path, code):
        with self.assertRaises(ComputerUseError) as caught:
            await query.resolve(path)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.effect, "none")

    async def test_wait_for_absent_target_stays_inside_one_write_lease(self):
        port = GraphPort(dict(root=node("AXWindow", children=["button"]), button=node("AXButton", "Not yet")))

        async def appear():
            await asyncio.sleep(0.01)
            port.graph["button"]["values"]["AXTitle"] = "Run"

        update = asyncio.create_task(appear())
        try:
            await Engine(port).execute(Intent("click"), ROOT, path=["Run"], query_timeout_ms=500)
        finally:
            await update
        self.assertEqual(sum(event[0] == "begin" for event in port.events), 1)
        self.assertEqual(sum(event[0] == "execute" for event in port.events), 1)
        self.assertEqual(sum(event == ("nodes", ROOT) for event in port.events), 2)
        self.assertEqual(port.events[-1], ("finish", "op"))

    async def test_zero_wait_is_one_lookup_and_ambiguity_is_never_retried(self):
        for graph, path, wait, code in [
            (dict(root=node("AXWindow")), ["Run"], 0, "element_not_found"),
            (
                dict(root=node("AXWindow", children=["a", "b"]), a=node("AXButton", "Run"), b=node("AXButton", "Run")),
                ["Run"],
                1000,
                "ambiguous_target",
            ),
        ]:
            port = GraphPort(graph)
            with self.assertRaises(ComputerUseError) as caught:
                await Engine(port).execute(Intent("click"), ROOT, path=path, query_timeout_ms=wait)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(sum(event == ("nodes", ROOT) for event in port.events), 1)
            self.assertFalse(any(event[0] == "execute" for event in port.events))

    async def test_explicit_anchors_survive_transparent_wrappers(self):
        query = self.query(
            dict(
                root=node("AXWindow", children=["group"]),
                group=node(name="Group A", children=["layout"]),
                layout=node(children=["field"]),
                field=node("AXTextField", "Name"),
            )
        )
        target = await query.resolve(["Group A", {"role": "textField", "name": "Name"}])
        self.assertEqual(target, replace(ROOT, node="field"))
        self.assertEqual(query.within, ("root", "group"))

    async def test_named_or_interactive_container_is_not_a_transparent_wrapper(self):
        graph = dict(
            root=node("AXWindow", children=["group"]),
            group=node(name="A", children=["inside"]),
            inside=node(name="B", children=["field"]),
            field=node("AXTextField", "Name"),
        )
        query = self.query(graph)
        await self.error(query, ["A", "Name"], "element_not_found")
        query = self.query(graph)
        await query.resolve(["A", "B", "Name"])
        graph["inside"]["values"]["AXTitle"] = ""
        graph["inside"]["actions"]["items"] = [dict(name="AXShowMenu", description="Show menu")]
        await self.error(self.query(graph), ["A", "Name"], "element_not_found")

    async def test_windows_scroll_item_only_layout_preserves_scoped_lookup(self):
        class WindowsPort(GraphPort):
            platform = "windows"

            async def query_nodes(self, *args, **kwargs):
                batch = await super().query_nodes(*args, **kwargs)
                properties = {"AXChildren": "RawViewChildren", "AXTitleUIElement": "LabeledBy"}
                batch["requestedRelations"] = [properties[p] for p in batch["requestedRelations"]]
                for relation in batch["relations"]:
                    relation["property"] = properties[relation["property"]]
                return facts_fixture(batch)

        def windows_node(role, name="", children=(), **values):
            item = node(children=children)
            item["values"] = dict(ControlType="ControlType." + role, Name=name, **values)
            return item

        graph = dict(
            root=windows_node("Window", "Host", ["scope"]),
            scope=windows_node("Group", "Scope", ["layout"]),
            layout=windows_node("Group", children=["button"]),
            button=windows_node("Button", "Run"),
        )
        graph["layout"]["actions"]["items"] = [dict(name="ScrollItem.ScrollIntoView", description="Scroll into view")]

        def query(data):
            return TargetQuery(WindowsPort(data), "op", ROOT, lambda: 1000)

        for path in (["Scope", "Run"], [dict(role="window", name="Host"), "Scope", "Run"]):
            scoped = query(graph)
            self.assertEqual((await scoped.resolve(path)).node, "button")
            self.assertEqual(scoped.within, ("root", "scope"))

        for field, value in [
            ("Name", "Named boundary"),
            ("AutomationId", "layout"),
            ("HasKeyboardFocus", True),
            ("Window.IsModal", True),
        ]:
            guarded = deepcopy(graph)
            guarded["layout"]["values"][field] = value
            await self.error(query(guarded), ["Scope", "Run"], "element_not_found")

        for action in ("Invoke.Invoke", "SelectionItem.Select", "UnknownAction"):
            guarded = deepcopy(graph)
            guarded["layout"]["actions"]["items"].append(dict(name=action, description=action))
            await self.error(query(guarded), ["Scope", "Run"], "element_not_found")

        incomplete = deepcopy(graph)
        incomplete["layout"]["actions"]["state"] = "unavailable"
        await self.error(query(incomplete), ["Scope", "Run"], "observation_incomplete")
        ambiguous = deepcopy(graph)
        ambiguous["scope"]["children"].append("sibling")
        ambiguous["sibling"] = windows_node("Button", "Run")
        await self.error(query(ambiguous), ["Scope", "Run"], "ambiguous_target")

    async def test_scroll_item_spelling_does_not_relax_other_platform_layouts(self):
        graph = dict(
            root=node("AXWindow", children=["scope"]),
            scope=node(name="Scope", children=["layout"]),
            layout=node(children=["button"]),
            button=node("AXButton", "Run"),
        )
        graph["layout"]["actions"]["items"] = [dict(name="ScrollItem.ScrollIntoView", description="Scroll into view")]
        await self.error(self.query(graph), ["Scope", "Run"], "element_not_found")

    async def test_unknown_wrapper_and_unread_branch_do_not_prove_uniqueness(self):
        graph = dict(root=node("AXWindow", children=["a", "b"]), a=node("AXButton", "Run"), b=node("AXButton", "Other"))
        query = self.query(graph)
        self.port.graph["b"]["statuses"]["AXTitle"] = "timeout"
        await self.error(query, [{"role": "button", "name": "Run"}], "observation_incomplete")
        graph = dict(
            root=node("AXWindow", children=["group"]),
            group=node(name="A", children=["layout"]),
            layout=node(children=["button"]),
            button=node("AXButton", "Run"),
        )
        query = self.query(graph)
        self.port.graph["layout"]["actions"]["state"] = "unavailable"
        await self.error(query, ["A", "Run"], "observation_incomplete")

    async def test_known_mismatch_can_exclude_a_candidate_with_unknown_name(self):
        query = self.query(
            dict(root=node("AXWindow", children=["a", "b"]), a=node("AXButton", "Run"), b=node("AXStaticText"))
        )
        self.port.graph["b"]["statuses"]["AXTitle"] = "error"
        self.assertEqual((await query.resolve([dict(role="button", name="Run")])).node, "a")

    async def test_field_query_does_not_enter_other_fields_private_editors(self):
        query = self.query(
            dict(
                root=node("AXWindow", children=["search", "target"]),
                search=node("AXTextField", "Search"),
                target=node("AXTextField", "Customer"),
            )
        )
        self.port.edges["search", "AXChildren"] = dict(targets=[], state="error", certainty="unknown")
        self.assertEqual((await query.resolve([dict(role="textField", name="Customer")])).node, "target")
        # A failed container relation can still hide another matching field.
        self.port.graph["search"]["values"]["AXRole"] = "AXGroup"
        query = self.query(self.port.graph)
        self.port.edges["search", "AXChildren"] = dict(targets=[], state="error", certainty="unknown")
        await self.error(query, [dict(role="textField", name="Customer")], "observation_incomplete")

    async def test_matching_uses_the_shared_windows_and_linux_role_maps(self):
        for platform, role_key, name_key, roles, relation_keys in (
            (
                "windows",
                "ControlType",
                "Name",
                ("ControlType.Window", "ControlType.Button"),
                ("RawViewChildren", "LabeledBy"),
            ),
            ("linux", "role", "name", ("frame", "push button"), ("children", "labelled-by")),
        ):

            class PlatformPort:
                async def query_nodes(
                    self,
                    operation,
                    target,
                    fields,
                    max_nodes,
                    max_depth,
                    timeout_ms,
                    *,
                    name_key=name_key,
                    platform=platform,
                    relation_keys=relation_keys,
                    role_key=role_key,
                    roles=roles,
                ):
                    records = []
                    relations = []
                    for token, role, name in (("root", roles[0], "Document"), ("button", roles[1], "Run")):
                        values = {role_key: role, name_key: name}
                        records.append(
                            dict(
                                node=token,
                                requestedFields=fields,
                                values=values,
                                fieldStatus={field: dict(state="absent") for field in fields if field not in values},
                                actions=dict(state="complete", items=[]),
                            )
                        )
                        for property_name_local in relation_keys:
                            relations.append(
                                dict(
                                    source=token,
                                    property=property_name_local,
                                    targets=["button"]
                                    if token == "root" and property_name_local == relation_keys[0]
                                    else [],
                                    certainty="confirmed",
                                    state="complete",
                                )
                            )
                    return facts_fixture(
                        dict(
                            kind="nodes",
                            session=target.session,
                            surface=target.surface,
                            platform=platform,
                            observed=dict(id="batch", startedAt=1, completedAt=2),
                            roots=["root"],
                            nodes=records,
                            requestedRelations=list(relation_keys),
                            relations=relations,
                            unread=[],
                            stopReasons=[],
                        )
                    )

            port = PlatformPort()
            port.platform = platform
            with self.subTest(platform=platform):
                query = TargetQuery(port, "operation", ROOT, lambda: 1000)
                self.assertEqual((await query.resolve([dict(role="button", name="Run")])).node, "button")
                self.assertEqual(query.within, ("root",))

    async def test_windows_empty_accessible_name_does_not_require_an_optional_label(self):
        class Port:
            platform = "windows"
            failed_name = False

            async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
                records, edges = [], []
                for token, role, name in [
                    ("root", "ControlType.Window", "Window"),
                    ("layout", "ControlType.Pane", ""),
                    ("list", "ControlType.List", "Items"),
                ]:
                    values = dict(ControlType=role, Name=name, HelpText="Items" if token == "layout" else "")
                    values = {key: value for key, value in values.items() if key in fields}
                    statuses = {field: dict(state="absent") for field in fields if field not in values}
                    if token == "layout" and self.failed_name:
                        values.pop("Name")
                        statuses["Name"] = dict(state="error", detail="provider unavailable")
                    records.append(
                        dict(
                            node=token,
                            requestedFields=fields,
                            values=values,
                            fieldStatus=statuses,
                            actions=dict(state="complete", items=[]),
                        )
                    )
                    edges.append(
                        dict(
                            source=token,
                            property="RawViewChildren",
                            certainty="confirmed",
                            state="complete",
                            targets=["layout"] if token == "root" else ["list"] if token == "layout" else [],
                        )
                    )
                    edges.append(
                        dict(
                            source=token,
                            property="LabeledBy",
                            certainty="unknown",
                            state="error",
                            targets=[],
                            detail="optional label unavailable",
                        )
                    )
                return facts_fixture(
                    dict(
                        kind="nodes",
                        session=target.session,
                        surface=target.surface,
                        platform="windows",
                        observed=dict(id="batch", startedAt=1, completedAt=2),
                        roots=["root"],
                        nodes=records,
                        requestedRelations=["RawViewChildren", "LabeledBy"],
                        relations=edges,
                        unread=[],
                        stopReasons=[],
                    )
                )

        port = Port()
        query = TargetQuery(port, "operation", ROOT, lambda: 1000)
        self.assertEqual((await query.resolve(["Items"])).node, "list")
        # An actual Name failure is still unknown, not permission to ignore a
        # possible duplicate. HelpText also cannot create an invented duplicate.
        port.failed_name = True
        with self.assertRaises(ComputerUseError) as caught:
            await TargetQuery(port, "operation", ROOT, lambda: 1000).resolve(["Items"])
        self.assertEqual(caught.exception.code, "observation_incomplete")

    async def test_duplicate_labels_are_ambiguous_but_native_shared_node_is_one_candidate(self):
        graph = dict(root=node("AXWindow", children=["a", "b"]), a=node("AXButton", "Run"), b=node("AXButton", "Run"))
        await self.error(self.query(graph), ["Run"], "ambiguous_target")
        graph["b"] = node(children=["a"])
        self.assertEqual((await self.query(graph).resolve(["Run"])).node, "a")

    async def test_native_label_relation_can_supply_a_name(self):
        query = self.query(
            dict(
                root=node("AXWindow", children=["field"]),
                field=node("AXTextField"),
                label=node("AXStaticText", AXValue="Account"),
            )
        )
        self.port.graph["field"]["labels"] = ["label"]
        self.assertEqual((await query.resolve([dict(role="textField", name="Account")])).node, "field")
        self.assertEqual(query.within, ("root",))

    async def test_independent_window_is_not_searched_as_content(self):
        query = self.query(
            dict(
                root=node("AXWindow", children=["sheet"]),
                sheet=node("AXSheet", children=["button"]),
                button=node("AXButton", "Run"),
            )
        )
        await self.error(query, ["Run"], "element_not_found")
        self.assertNotIn("button", [call[1].node for call in self.port.events if call[0] == "nodes"])

    async def test_only_confirmed_windowless_windows_modal_is_searchable_content(self):
        class WindowsPort:
            platform = "windows"

            async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
                records, edges = [], []
                data = [
                    ("root", "Window", "Host", ["modal"]),
                    ("modal", "Window", "Confirmation", ["confirm"]),
                    ("confirm", "Button", "Confirm", []),
                ]
                if getattr(self, "include_sibling", False):
                    data[0] = ("root", "Window", "Host", ["modal", "sibling"])
                    data.append(("sibling", "Button", "Confirm", []))
                for token, role, name, children in data:
                    values = dict(ControlType="ControlType." + role, Name=name)
                    if token == "modal":
                        values.update(self.modal_fields)
                    values = {key: value for key, value in values.items() if key in fields}
                    records.append(
                        dict(
                            node=token,
                            requestedFields=fields,
                            values=values,
                            fieldStatus={key: dict(state="absent") for key in fields if key not in values},
                            actions=dict(state="complete", items=[]),
                        )
                    )
                    edges.append(
                        dict(
                            source=token,
                            property="RawViewChildren",
                            targets=children,
                            certainty="confirmed",
                            state="complete",
                        )
                    )
                return facts_fixture(
                    dict(
                        kind="nodes",
                        session=target.session,
                        surface=target.surface,
                        platform="windows",
                        observed=dict(id="batch", startedAt=1, completedAt=2),
                        roots=[target.node],
                        nodes=records,
                        requestedRelations=["RawViewChildren"],
                        relations=edges,
                        unread=[],
                        stopReasons=[],
                    )
                )

        port = WindowsPort()
        for modal_fields, searchable in [
            ({"NativeWindowHandle": 0, "Window.IsModal": True}, True),
            ({"NativeWindowHandle": 123, "Window.IsModal": True}, False),
            ({"NativeWindowHandle": 0, "Window.IsModal": False}, False),
            ({"NativeWindowHandle": False, "Window.IsModal": True}, False),
            ({"NativeWindowHandle": "0", "Window.IsModal": True}, False),
            ({"Window.IsModal": True}, False),
            ({"NativeWindowHandle": 0}, False),
        ]:
            with self.subTest(fields=modal_fields):
                port.modal_fields = modal_fields
                query = TargetQuery(port, "operation", ROOT, lambda: 1000)
                if searchable:
                    self.assertEqual((await query.resolve([dict(role="button", name="Confirm")])).node, "confirm")
                    self.assertEqual(query.within, ("root",))
                    scoped = TargetQuery(port, "operation", ROOT, lambda: 1000)
                    self.assertEqual((await scoped.resolve(["Confirmation", "Confirm"])).node, "confirm")
                    self.assertEqual(scoped.within, ("root", "modal"))
                else:
                    await self.error(query, [dict(role="button", name="Confirm")], "element_not_found")

        # A role alone does not identify a separate working window. New
        # drivers provide native-host containment, including child HWNDs and
        # windowless nonmodal content. Old drivers keep the earlier boundary.
        port.capabilities = ("nativeWindowContent",)
        for scope, searchable in [("content", True), ("independent", False), ("unknown", False), (None, False)]:
            with self.subTest(native_scope=scope):
                port.modal_fields = {"NativeWindowHandle": 123, "Window.IsModal": False}
                if scope is not None:
                    port.modal_fields["Win32.WindowContentScope"] = scope
                for path in [
                    [dict(role="button", name="Confirm")],
                    [dict(role="window", name="Host"), dict(role="button", name="Confirm")],
                    ["Confirmation", "Confirm"],
                ]:
                    query = TargetQuery(port, "operation", ROOT, lambda: 1000)
                    if searchable:
                        self.assertEqual((await query.resolve(path)).node, "confirm")
                    else:
                        await self.error(
                            query, path, "element_not_found" if scope == "independent" else "observation_incomplete"
                        )

        port.include_sibling = True
        for scope, expected in [
            ("unknown", "observation_incomplete"),
            ("content", "ambiguous_target"),
            ("independent", None),
        ]:
            port.modal_fields = {"NativeWindowHandle": 123, "Win32.WindowContentScope": scope}
            query = TargetQuery(port, "operation", ROOT, lambda: 1000)
            if expected:
                await self.error(query, [dict(role="button", name="Confirm")], expected)
            else:
                self.assertEqual((await query.resolve([dict(role="button", name="Confirm")])).node, "sibling")

    async def test_explicit_root_only_query_does_not_scan_its_contents(self):
        query = self.query(dict(root=node("AXWindow", "Document", children=["unread"])))
        self.assertEqual(await query.resolve([dict(role="window", name="Document")]), ROOT)
        self.assertEqual(query.within, ())
        self.assertEqual(len(self.port.events), 1)

    async def test_live_continuation_must_cover_the_remaining_candidates(self):
        graph = dict(root=node("AXWindow", children=["a", "b"]), a=node("AXButton", "Other"), b=node("AXButton", "Run"))
        query = self.query(graph)
        self.port.edges[("root", "AXChildren")] = dict(
            targets=["a"], state="partial", certainty="confirmed", offset=0, total=2, cursor="second"
        )
        self.port.pages["second"] = dict(
            source="root",
            property="AXChildren",
            targets=["b"],
            state="partial",
            certainty="confirmed",
            offset=1,
            total=2,
        )
        self.assertEqual((await query.resolve(["Run"])).node, "b")
        query = self.query(graph)
        self.port.edges[("root", "AXChildren")] = dict(
            targets=["a"], state="partial", certainty="confirmed", offset=0, total=2
        )
        await self.error(query, ["Other"], "observation_incomplete")

    async def test_changed_relation_scope_or_total_is_not_spliced(self):
        query = self.query(dict(root=node("AXWindow", children=["a"]), a=node("AXButton", "Run")))
        self.port.edges[("root", "AXChildren")] = dict(
            targets=[], state="partial", certainty="confirmed", offset=0, total=1, cursor="page"
        )
        self.port.pages["page"] = dict(
            source="root",
            property="AXChildren",
            targets=["a"],
            state="partial",
            certainty="confirmed",
            offset=0,
            total=2,
        )
        await self.error(query, ["Run"], "observation_incomplete")

    async def test_cycles_and_budget_are_not_a_complete_negative_result(self):
        graph = dict(root=node("AXWindow", children=["a"]), a=node(children=["b"]), b=node(children=["a"]))
        await self.error(self.query(graph), ["Run"], "observation_incomplete")
        graph["b"] = node("AXButton", "Run")
        await self.error(self.query(graph, max_nodes=2), ["Run"], "observation_incomplete")
        await self.error(self.query(graph, max_depth=1), ["Run"], "observation_incomplete")
        for budget in (0, True, 9000):
            with self.assertRaises(ValueError):
                self.query(graph, max_nodes=budget)

    async def test_lookup_and_dispatch_share_one_operation_and_selected_identity(self):
        port = GraphPort(
            dict(
                root=node("AXWindow", children=["a"]),
                a=node("AXGroup", "Group", children=["b"]),
                b=node("AXButton", "Run"),
            )
        )
        receipt = await Engine(port).execute(Intent("click"), ROOT, path=["Group", "Run"])
        self.assertEqual(receipt["status"], "dispatched")
        self.assertEqual(port.events[0], ("begin", replace(ROOT, node=None)))
        inspect = next(call for call in port.events if call[0] == "inspect")
        self.assertEqual(inspect, ("inspect", replace(ROOT, node="b"), ("root", "a")))
        self.assertEqual(port.events[-2], ("execute", replace(ROOT, node="b")))
        self.assertEqual(port.events[-1], ("finish", "op"))

    async def test_query_failure_finishes_without_input_and_uncertain_input_does_not_requery(self):
        port = GraphPort(
            dict(root=node("AXWindow", children=["a", "b"]), a=node("AXButton", "Run"), b=node("AXButton", "Run"))
        )
        with self.assertRaises(ComputerUseError):
            await Engine(port).execute(Intent("click"), ROOT, path=["Run"])
        self.assertEqual(port.events[-1], ("finish", "op"))
        self.assertNotIn("execute", [call[0] for call in port.events])
        port.graph["b"]["values"]["AXTitle"] = "Other"
        port.events.clear()
        port.dispatch = Dispatch.UNKNOWN
        with self.assertRaises(ComputerUseError) as caught:
            await Engine(port).execute(Intent("click"), ROOT, path=["Run"])
        self.assertEqual(caught.exception.effect, "possible")
        self.assertEqual(sum(call[0] == "execute" for call in port.events), 1)
        self.assertEqual(sum(call[0] == "nodes" and call[1] == ROOT for call in port.events), 1)


if __name__ == "__main__":
    unittest.main()
