import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from observation_fixture import normalize_fixture as normalizeFacts
from sdk.interaction.read.acquisition.facts.test_element_content_profile import Provider
from sdk.interaction.read.facts.test_native_facts import batch, edge, node
from sdk.interaction.read.presentation.test_compact_observations import present

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.pages import ContentPages
from computer_use.native.contracts import Binding
from computer_use.protocol.execution import decodeInspectQuery

"Encoded metadata, page coverage and deep graphs retain their source routes."


class PresentationLimitsTests(unittest.TestCase):
    def test_encoded_metadata_is_short_with_exact_values_actions_and_refs_in_details(self):
        for header in ("/svg+xml;base64,", "data:image/png;base64,", "data:text/plain;charset=utf-8;base64,"):
            for field in ("AXTitle", "AXDescription", "AXHelp", "AXValue"):
                with self.subTest(header=header, field=field):
                    value = header + "YWJj" * 1024
                    source = node("image", AXRole="AXImage", **{field: value})
                    source["actions"]["items"] = [dict(name="AXPress", description="Press")]
                    snapshot, output = present([source])
                    self.assertLess(len(output["text"]), 400)
                    self.assertIn("see details", output["text"])
                    self.assertIn(value, output["details"])
                    self.assertEqual(output["refs"], {"0": "image"})
                    self.assertEqual(
                        snapshot.native.nodes["image"].control_value.value
                        if field == "AXValue"
                        else getattr(
                            snapshot.native.nodes["image"],
                            {"AXTitle": "name", "AXDescription": "description", "AXHelp": "help"}[field],
                        ),
                        value,
                    )
                    self.assertEqual(output["projections"][0]["factAccounting"]["values"][-1]["kind"], "supplement")

    def test_unmarked_base64_normal_text_and_payload_with_other_content_are_not_hidden(self):
        for value in (
            "YWJj" * 100,
            "证书说明" * 100,
            "/svg+xml;base64,YWJj",
            "Read data:image/png;base64," + "YWJj" * 100,
            "/svg+xml;base64," + "YWJj" * 100 + " (click to open)",
            "data:text/plain,not encoded " * 50,
        ):
            _, output = present([node("button", AXRole="AXButton", AXDescription=value)])
            self.assertIn(value, output["text"])

    def test_shared_encoded_help_does_not_claim_a_lossless_display(self):
        value = "data:image/png;base64," + "YWJj" * 128
        _, output = present(
            [
                node("w", AXRole="AXWindow"),
                *[node(str(i), AXRole="AXButton", AXTitle=str(i), AXHelp=value) for i in range(3)],
            ],
            [edge("w", "0", "1", "2")],
        )
        self.assertNotIn("YWJj", output["text"])
        self.assertIn(value, output["details"])
        help_records = [r for r in output["projections"][0]["factAccounting"]["values"] if r["property"] == "help"]
        self.assertEqual([r["kind"] for r in help_records], ["supplement"] * 3)

    def test_children_have_one_continuation_nonchildren_keep_separate_cursor(self):
        raw = batch(
            [node("parent", AXRole="AXGroup", AXTitle="Region"), node("child", AXRole="AXButton")],
            [
                edge(
                    "parent",
                    "child",
                    state="partial",
                    total=8,
                    offset=2,
                    cursor="native-children",
                    detail="native relation has an unread continuation",
                ),
                edge("parent", property="AXRows", state="partial", total=4, offset=0, cursor="native-rows"),
            ],
        )
        raw["stopReasons"] = ["partial native relation: AXChildren"]
        before = copy.deepcopy(raw)
        snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeNodePages",))
        target = Binding("session", "app", "window", "surface", "parent")
        pages = ContentPages()
        pages.remember(target, snapshot)
        output = snapshot.render(compact=True)
        text = output["text"] + pages.hints(target, snapshot, compact=True, inline_children=output["inlineChildRefs"])
        self.assertEqual(text.count('"offset": 3'), 1)
        self.assertEqual(text.count('"cursor":'), 1)
        self.assertIn("1/8 read, offset 2", text)
        self.assertNotIn("inspected through", text)
        self.assertNotIn("unread continuation", text)
        self.assertEqual(raw, before)
        self.assertIn("native-children", output["details"])
        self.assertIn("native-rows", output["details"])
        self.assertEqual(pages.resolve("p2", target)["cursor"], "native-rows")

    def test_partial_provider_failure_remains_explicit_beside_continuation(self):
        raw = batch(
            [node("a", AXRole="AXGroup")],
            [edge("a", state="partial", total=3, cursor="cursor", detail="native provider changed while collecting")],
        )
        snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeNodePages",))
        text = snapshot.render(compact=True)["text"]
        self.assertIn("native provider changed while collecting", text)
        self.assertIn('Inspect subtree: element("0").read()', text)
        self.assertNotIn('"offset":', text)  # No provider page origin was acquired.
        self.assertIn("cursor", snapshot.render()["details"])

    def test_final_children_page_states_its_range_and_end_without_another_entry(self):
        raw = batch(
            [node("a", AXRole="AXGroup"), node("b", AXRole="AXButton")],
            [edge("a", "b", state="partial", total=3, offset=2)],
        )
        snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeNodePages",))
        text = snapshot.render(compact=True)["text"]
        self.assertIn("1/3 read, offset 2; end of direct children", text)
        self.assertEqual(text.count("Children:"), 1)
        self.assertNotIn(".read(", text)

    def test_projection_fallback_keeps_its_public_continuation_in_the_footer(self):
        for role in ("AXGroup", "AXMenuBarItem"):
            with self.subTest(role=role):
                raw = batch([node("a", AXRole=role)], [edge("a", state="partial", total=3, offset=0, cursor="native")])
                snapshot = normalizeFacts(raw, DisplayReferences("session"), driver_features=("nativeNodePages",))
                target = Binding("session", "app", "window", "surface", "a")
                pages = ContentPages()
                pages.remember(target, snapshot)
                with patch(
                    (
                        "computer_use.interaction.read.presentation.rendering.passes.render_semantic_"
                        "lines.renderProjectionLines"
                    ),
                    side_effect=ValueError("test fallback"),
                ):
                    output = snapshot.render(compact=True)
                self.assertEqual(output["inlineChildRefs"], [])
                hints = pages.hints(target, snapshot, compact=True, inline_children=output["inlineChildRefs"])
                self.assertIn('"cursor": "p1"', hints)
                self.assertEqual(pages.resolve("p1", target)["cursor"], "native")

    def test_deep_graph_keeps_containment_and_every_action_reference(self):
        for count in (100, 1100):
            with self.subTest(count=count):
                sources = [node(str(i), AXRole="AXGroup", AXTitle=f"Layer {i}") for i in range(count)]
                sources[-1] = node(str(count - 1), AXRole="AXButton", AXTitle="Deep leaf")
                sources[-1]["actions"]["items"] = [dict(name="AXPress", description="Press")]
                raw = batch(sources, [edge(str(i), str(i + 1)) for i in range(count - 1)])
                snapshot = normalizeFacts(raw, DisplayReferences("session"))
                self.assertEqual(len(snapshot.roots), 1)
                current = snapshot.roots[0]
                for i in range(count - 1):
                    self.assertEqual(current["ref"], str(i))
                    current = current["children"][0]
                output = snapshot.render(compact=True)
                self.assertIn("Deep leaf", output["text"])
                self.assertEqual(len(output["refs"]), count)
                self.assertEqual(output["refs"][str(count - 1)], str(count - 1))
                self.assertNotIn("depth limit", output["text"])
                self.assertEqual(snapshot.native.nodes[str(count - 1)].name, "Deep leaf")
                self.assertEqual(snapshot.native.nodes["0"].relations["children"].targets, ("1",))


class NativeDepthPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_depth_uses_node_budget_only_on_advertised_drivers(self):
        target = Binding("session", "app", "window", "surface", "table")
        read = SimpleNamespace(target=target, token="read", remaining=lambda: 5000)
        for supported in (False, True):
            with self.subTest(supported=supported):
                provider = Provider()
                if supported:
                    provider.connection.capabilities.add("nativeNodeDepthBudget")
                reader = NativeReader(provider)
                await reader.collect(read, max_nodes=1000)
                self.assertEqual(provider.calls[-1]["max_depth"], 999 if supported else 40)
                await reader.collect(read, max_nodes=10, max_depth=2)
                self.assertEqual(provider.calls[-1]["max_depth"], 2)
                if supported:
                    await reader.collect(read, max_nodes=1)
                    self.assertEqual(provider.calls[-1]["max_depth"], 0)

    def test_protocol_keeps_finite_explicit_limits(self):
        request = dict(kind="nodes", fields=["AXRole"], maxNodes=2000, maxDepth=1999)
        self.assertEqual(decodeInspectQuery(request), request)
        for limit in (-1, True, 0.5, 2001):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                decodeInspectQuery({**request, "maxDepth": limit})
