"""IR correctness is independent of tree rules and operating-system calls."""

import unittest

from computer_use.interaction.read.presentation.ir import (
    FactKey,
    PresentationIR,
    ProjectionEditor,
    ProjectionInput,
    ProjectionView,
    export_projection,
    validate_projection,
)


def node(ref, role, *children, **fields):
    return dict(ref=str(ref), role=role, children=list(children), **fields)


class PresentationIRTests(unittest.TestCase):
    @staticmethod
    def projection(root):
        ir = PresentationIR.build(ProjectionInput.build(root))
        return ir, ProjectionView(ir), ProjectionEditor(ir)

    def test_deep_input_is_read_only_and_fields_keep_typed_sources(self):
        ir, view, edit = self.projection(
            node(
                0,
                "button",
                node(1, "staticText", value="Save"),
                value=False,
                capabilities=dict(click="available"),
                actions=[dict(name="AXPress")],
            )
        )
        with self.assertRaises(TypeError):
            view.nativeNodes["0"]["capabilities"]["click"] = "unavailable"
        with self.assertRaises(TypeError):
            view.nodes["0"]["actions"][0]["name"] = "Delete"
        edit.adopt(view.nodes["0"], [dict(sourceRef="1", attribute="value", text="Save")], "name")
        presented = ir.nodes["0"].fields["name"]
        self.assertEqual(presented.parts[0].facts, (FactKey("1", "value"),))
        self.assertIs(view.nodes["0"]["value"], False)
        self.assertEqual(ir.nodes["0"].fields["value"].parts[0].facts, (FactKey("0", "value"),))
        for name in ("ref", "role", "actions", "capabilities", "control"):
            with self.assertRaises(ValueError):
                edit.set_field(view.nodes["0"], name, "invented")

    def test_suppression_has_no_fallback_to_original_description(self):
        ir, view, edit = self.projection(node(0, "button", description="Save"))
        edit.adopt(view.view, [dict(sourceRef="0", attribute="description", text="Save")], "name")
        edit.suppress(view.view, "description")
        result = export_projection(ir)
        self.assertEqual(result["roots"][0]["description"], "")
        self.assertEqual(result["nativeNodes"]["0"]["description"], "Save")
        self.assertEqual(view.view["name"], "Save")

    def test_splice_destinations_remain_one_to_many_and_empty_is_distinct(self):
        ir, view, edit = self.projection(
            node(0, "window", node(1, "group", node(2, "button"), node(3, "button")), node(4, "row"))
        )
        edit.elide(view.nodes["1"], "splice", view.nodes["1"]["children"])
        edit.elide(view.nodes["4"], "empty")
        edit.children(view.view, [view.nodes["2"], view.nodes["3"]])
        edit.finalize_folds()
        validate_projection(ir)
        self.assertEqual(ir.resolved_elisions["1"], ("2", "3"))
        self.assertEqual(ir.resolved_elisions["4"], ())
        self.assertEqual(view.foldedInto, {})
        self.assertEqual(set(view.nativeNodes), {"0", "1", "2", "3", "4"})

    def test_absent_fact_is_not_a_null_fact(self):
        ir, view, edit = self.projection(node(0, "button"))
        with self.assertRaisesRegex(ValueError, "source field"):
            edit.text_source(view.view, dict(sourceRef="0", attribute="missing", text=None, field="name"))

    def test_diagnostic_event_cannot_authorize_silent_node_loss(self):
        ir, view, edit = self.projection(node(0, "window", node(1, "button")))
        edit.children(view.view, [])
        edit.event(sourceRef="1", kind="layout")
        with self.assertRaisesRegex(ValueError, "without recording"):
            validate_projection(ir)

    def test_chained_caption_sources_merge_provenance_without_retargeting(self):
        ir, view, edit = self.projection(node(0, "button", node(1, "group", node(2, "staticText", value="Save"))))
        edit.adopt(view.nodes["1"], [dict(sourceRef="2", attribute="value", text="Save")], "name")
        edit.fold(view.nodes["2"], view.nodes["1"], "label")
        edit.children(view.nodes["1"], [])
        edit.adopt(view.view, view.nodes["1"]["textSources"], "name")
        edit.fold(view.nodes["1"], view.view, "label")
        edit.children(view.view, [])
        edit.finalize_folds()
        validate_projection(ir)
        self.assertEqual(ir.resolved_elisions["2"], ("0",))
        self.assertEqual(view.foldedInto, {"2": "0", "1": "0"})
        self.assertEqual(view.nativeNodes["2"]["role"], "staticText")
        self.assertNotIn("name", view.nativeNodes["0"])


if __name__ == "__main__":
    unittest.main()


class RenderedProvenanceTests(unittest.TestCase):
    def test_selection_transfer_keeps_editor_fact_through_render_and_accounting(self):
        from computer_use.interaction.read.presentation.accounting.fields import validate_source_emissions
        from computer_use.interaction.read.presentation.rendering.formatting import renderTree

        root = node(
            0,
            "comboBox",
            node(1, "textField", value="abc", selectionRange=dict(location=1, length=1), selectedText="b"),
            value="abc",
            control=dict(kind="textInput", members=[dict(ref="1", part="editor")]),
        )
        rendered = renderTree(root, dict(recordFields=True))
        self.assertEqual(rendered["projection"]["foldedInto"], {"1": "0"})
        emitted = [entry for entry in rendered.source_emissions if entry.field in ("selectionRange", "selectedText")]
        self.assertEqual(
            {(entry.fact.source, entry.fact.attribute, entry.target) for entry in emitted},
            {("1", "selectionRange", "0"), ("1", "selectedText", "0")},
        )
        validate_source_emissions(rendered, {"0", "1"})
        # Source tracking augments the internal contract without changing the
        # existing rendered mapping or dropping legacy detail supplements.
        self.assertNotIn("source_emissions", rendered)

    def test_same_text_retains_distinct_fact_origins_without_false_value_claim(self):
        from computer_use.interaction.read.presentation.accounting.fields import validate_source_emissions
        from computer_use.interaction.read.presentation.rendering.formatting import renderProjection

        ir = PresentationIR.build(
            ProjectionInput.build(
                node(0, "button", node(1, "staticText", value="Save"), node(2, "staticText", value="Save"))
            )
        )
        view, edit = ProjectionView(ir), ProjectionEditor(ir)
        edit.adopt(view.view, [dict(sourceRef=str(ref), attribute="value", text="Save") for ref in (1, 2)], "name")
        rendered = renderProjection(view.nativeNodes["0"], dict(recordFields=True), export_projection(ir))
        name_sources = [
            emission for emission in rendered.source_emissions if emission.target == "0" and emission.field == "name"
        ]
        self.assertEqual({entry.fact for entry in name_sources}, {FactKey("1", "value"), FactKey("2", "value")})
        validate_source_emissions(rendered, {"0", "1", "2"})
        with self.assertRaisesRegex(ValueError, "source fact"):
            validate_source_emissions(rendered, {"0", "1"})
