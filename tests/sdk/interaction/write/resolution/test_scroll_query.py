"""The Python scroll route preserves nearest-region and discrete-page intent."""

import unittest
from dataclasses import replace

from observation_fixture import facts_fixture, relation_fixture
from sdk.native.query.test_target_query import ROOT, GraphPort, node

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.engine import Engine
from computer_use.interaction.write.resolution.scroll import ScrollQuery
from computer_use.native.contracts import (
    Channel,
    Dispatch,
    Facts,
    Intent,
    Knowledge as K,
    Primitive,
    ScrollActuator,
    StepResult,
)


def graph():
    result = dict(
        root=node("AXScrollArea", children=["large-document"]),
        content=node("AXStaticText"),
        bar=node("AXScrollBar", children=["up", "down"], AXOrientation="AXVerticalOrientation"),
        up=node("AXButton", AXSubrole="AXDecrementPage"),
        down=node("AXButton", AXSubrole="AXIncrementPage"),
    )
    result["root"]["verticalScrollBar"] = ["bar"]
    result["content"]["parent"] = ["root"]
    result["bar"]["parent"] = ["root"]
    for key in ("up", "down"):
        result[key]["parent"] = ["bar"]
        result[key]["actions"]["items"] = [dict(name="AXPress", description="Press")]
    return result


class ScrollPort(GraphPort):
    capabilities = {"nativeScroll"}
    inline_bars = True

    async def query_nodes(self, operation, target, fields, max_nodes, max_depth, timeout_ms):
        result = await super().query_nodes(operation, target, fields, max_nodes, max_depth, timeout_ms)
        self.events[-1] += (max_depth,)
        original = self.graph[target.node]
        for property_name, key_value in (
            ("AXParent", "parent"),
            ("AXVerticalScrollBar", "verticalScrollBar"),
            ("AXHorizontalScrollBar", "horizontalScrollBar"),
        ):
            if not self.inline_bars and property_name != "AXParent":
                continue
            result["requestedRelations"].append(property_name)
            edge = self.edges.get(
                (target.node, property_name),
                dict(targets=original.get(key_value, []), state="complete", certainty="confirmed"),
            )
            result["relations"].append(dict(source=target.node, property=property_name, **edge))
        result["unread"] = sorted({n for r in result["relations"] for n in r["targets"]} - {target.node})
        return facts_fixture(result)

    async def query_relation(self, operation, target, property, cursor, limit, timeout_ms):
        if property not in ("AXVerticalScrollBar", "AXHorizontalScrollBar") or cursor is not None:
            return await super().query_relation(operation, target, property, cursor, limit, timeout_ms)
        self.events.append(("relation", target, property))
        key = "verticalScrollBar" if property == "AXVerticalScrollBar" else "horizontalScrollBar"
        return relation_fixture(
            dict(
                kind="relation",
                session=target.session,
                surface=target.surface,
                platform=self.platform,
                observed=dict(id="scroll-relation", startedAt=2, completedAt=3),
                relation=dict(
                    source=target.node,
                    property=property,
                    targets=self.graph[target.node].get(key, []),
                    state="complete",
                    certainty="confirmed",
                ),
            )
        )

    async def inspect(self, operation, target, timeout_ms, *, within=(), actuator=None, basis=()):
        assert actuator is None or basis
        self.events.append(("inspect", target, actuator))
        primitive = Primitive("pages", "native_scroll", Channel.OBJECT, K.YES, K.YES)
        return Facts(
            target,
            "now",
            "scroll area",
            K.YES,
            K.YES,
            K.YES,
            (),
            (primitive,) if actuator else (),
            platform="macos",
            actuator=actuator,
        )

    async def execute(self, operation, step, command, timeout_ms):
        self.events.append(("execute", command))
        return StepResult(
            command.target,
            self.dispatch,
            "page action",
            error_code="execution_unknown" if self.dispatch in (Dispatch.UNKNOWN, Dispatch.PARTIAL) else None,
        )


class ScrollQueryTests(unittest.IsolatedAsyncioTestCase):
    def query(self, data=None, target=ROOT, **options):
        self.port = ScrollPort(data or graph())
        return ScrollQuery(self.port, "op", target, lambda: 1000, **options)

    async def test_region_and_content_resolve_the_same_exact_page_button_without_reading_document(self):
        for token in ("root", "content", "bar"):
            query = self.query(target=replace(ROOT, node=token))
            actual = await query.resolve(dict(direction="down", pages=2))
            self.assertEqual(actual, ScrollActuator("down", "bar" if token == "bar" else "root", "bar"))
            self.assertTrue(all(event[2] == 0 for event in self.port.events if event[0] == "nodes"))
            self.assertNotIn("large-document", [event[1].node for event in self.port.events])

    async def test_nearest_nested_region_is_not_replaced_with_an_outer_working_scroller(self):
        data = graph()
        data["nested"] = node("AXScrollArea")
        data["nested"]["parent"] = ["root"]
        data["content"]["parent"] = ["nested"]
        query = self.query(data, replace(ROOT, node="content"))
        self.assertIsNone(await query.resolve(dict(direction="down")))
        self.assertNotIn("root", [event[1].node for event in self.port.events])

    async def test_scroll_bar_relationships_are_read_on_demand_without_expanding_the_document(self):
        query = self.query()
        self.port.inline_bars = False
        self.assertEqual(await query.resolve(dict(direction="down")), ScrollActuator("down", "root", "bar"))
        self.assertEqual(
            [e for e in self.port.events if e[0] == "relation"], [("relation", ROOT, "AXVerticalScrollBar")]
        )

    async def test_fractional_pages_and_absent_axis_do_not_get_rounded_or_substituted(self):
        query = self.query()
        self.assertIsNone(await query.resolve(dict(direction="down", pages=0.5)))
        self.assertEqual(self.port.events, [])
        self.assertIsNone(await query.resolve(dict(direction="right")))

    async def test_bare_bar_needs_an_axis_but_a_standard_area_link_supplies_that_evidence(self):
        data = graph()
        data["bar"]["values"].pop("AXOrientation")
        self.assertEqual(await self.query(data).resolve(dict(direction="down")), ScrollActuator("down", "root", "bar"))
        with self.assertRaises(ComputerUseError):
            await self.query(data, replace(ROOT, node="bar")).resolve(dict(direction="down"))

    async def test_horizontal_direction_selects_matching_standard_relationship(self):
        data = graph()
        data["root"]["horizontalScrollBar"] = data["root"].pop("verticalScrollBar")
        data["bar"]["values"]["AXOrientation"] = "AXHorizontalOrientation"
        query = self.query(data)
        self.assertEqual(await query.resolve(dict(direction="left")), ScrollActuator("up", "root", "bar"))

    async def test_disabled_and_ambiguous_controls_fail_before_any_input(self):
        for disabled in ("root", "bar", "down"):
            data = graph()
            data[disabled]["values"]["AXEnabled"] = False
            query = self.query(data)
            with self.assertRaises(ComputerUseError) as caught:
                await query.resolve(dict(direction="down"))
            self.assertEqual((caught.exception.code, caught.exception.effect), ("input_unavailable", "none"))
        data = graph()
        data["other"] = node("AXButton", AXSubrole="AXIncrementPage")
        data["bar"]["children"].append("other")
        with self.assertRaises(ComputerUseError) as caught:
            await self.query(data).resolve(dict(direction="down"))
        self.assertEqual(caught.exception.code, "ambiguous_target")

    async def test_unknown_role_orientation_or_page_metadata_cannot_prove_a_unique_route(self):
        for token, field in (("content", "AXRole"), ("bar", "AXOrientation"), ("down", "AXSubrole")):
            data = graph()
            data[token]["statuses"][field] = "timeout"
            with self.assertRaises(ComputerUseError) as caught:
                await self.query(data, replace(ROOT, node="content")).resolve(dict(direction="down"))
            self.assertEqual(caught.exception.code, "observation_incomplete")

    async def test_conflicting_backlinks_and_cycles_do_not_escape_the_requested_region(self):
        for token in ("bar", "down"):
            data = graph()
            data[token]["parent"] = ["another-region"]
            data["another-region"] = node("AXScrollArea")
            with self.assertRaises(ComputerUseError):
                await self.query(data).resolve(dict(direction="down"))
        data = graph()
        data["content"]["parent"] = ["content"]
        with self.assertRaises(ComputerUseError):
            await self.query(data, replace(ROOT, node="content")).resolve(dict(direction="down"))

    async def test_standard_links_allow_missing_backlinks_and_transparent_parent_wrappers(self):
        for token, parent in (("bar", "root"), ("down", "bar")):
            data = graph()
            data[token]["parent"] = []
            self.assertEqual(
                await self.query(data).resolve(dict(direction="down")), ScrollActuator("down", "root", "bar")
            )
            data[token]["parent"] = ["layout"]
            data["layout"] = node("AXUnknown")
            data["layout"]["parent"] = [parent]
            self.assertEqual(
                await self.query(data).resolve(dict(direction="down")), ScrollActuator("down", "root", "bar")
            )

    async def test_incomplete_children_and_native_actions_do_not_mean_empty(self):
        query = self.query()
        self.port.edges["bar", "AXChildren"] = dict(targets=["down"], state="partial", certainty="confirmed", total=5)
        with self.assertRaises(ComputerUseError):
            await query.resolve(dict(direction="down"))
        data = graph()
        data["down"]["actions"]["state"] = "unavailable"
        with self.assertRaises(ComputerUseError):
            await self.query(data).resolve(dict(direction="down"))

    async def test_increment_is_not_a_page_scroll_and_unknown_buttons_remain_unknown(self):
        data = graph()
        data["down"]["actions"]["items"] = [dict(name="AXIncrement", description="Increment")]
        self.assertIsNone(await self.query(data).resolve(dict(direction="down")))
        data = graph()
        data["down"]["values"]["AXSubrole"] = "AXIncrementArrow"
        self.assertIsNone(await self.query(data).resolve(dict(direction="down")))

    async def test_bad_requests_do_not_read_the_desktop(self):
        for p in (
            {},
            {"direction": "back"},
            {"direction": "down", "pages": False},
            {"direction": "down", "pages": 0},
            {"direction": "down", "pages": float("nan")},
            {"direction": "down", "x": 10},
        ):
            query = self.query()
            with self.assertRaises(ValueError):
                await query.resolve(p)
            self.assertEqual(self.port.events, [])

    async def test_engine_retains_original_target_and_one_discrete_unit(self):
        port = ScrollPort(graph())
        original = replace(ROOT, node="content")
        result = await Engine(port).execute(Intent("scroll", dict(direction="down", pages=2)), original)
        self.assertEqual(result["status"], "dispatched")
        writes = [e[1] for e in port.events if e[0] == "execute"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].target, original)
        self.assertEqual(writes[0].actuator, ScrollActuator("down", "root", "bar"))
        self.assertFalse(writes[0].prepare)

    async def test_partial_and_unknown_scroll_never_start_an_alternate_route(self):
        for dispatch in (Dispatch.PARTIAL, Dispatch.UNKNOWN):
            port = ScrollPort(graph())
            port.dispatch = dispatch
            with self.assertRaises(ComputerUseError) as caught:
                await Engine(port).execute(Intent("scroll", dict(direction="down", pages=2)), ROOT)
            self.assertEqual(caught.exception.effect, "partial" if dispatch == Dispatch.PARTIAL else "possible")
            self.assertEqual(len([e for e in port.events if e[0] == "execute"]), 1)
            self.assertEqual(port.events[-1], ("finish", "op"))


if __name__ == "__main__":
    unittest.main()
