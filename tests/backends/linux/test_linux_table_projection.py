import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fixtures.native_graph import observation
from sdk.interaction.read.facts.test_native_facts import batch, edge, node

from computer_use.interaction.read.acquisition.reader import NativeReader
from computer_use.interaction.read.facts.normalize import normalizeFacts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.accounting.fields import account_fields
from computer_use.native.contracts import Binding
from computer_use.native.semantics import READ_RELATIONS
from computer_use.protocol.observation.adapters import decode_driver_observation, decode_driver_reply

"Linux table facts stay independent from input and containment authority."


SPAN = dict(row=2, column=3, rowSpan=2, columnSpan=1)


def linux_batch(nodes, relations=()):
    value = batch(nodes, relations)
    value["platform"] = "linux"
    return value


def linux_graph(raw):
    return decode_driver_observation(observation(raw))


def cell(**changes):
    return node(
        "cell",
        **{
            "role": "table cell",
            "name": "unique value",
            "visible": True,
            "showing": True,
            "TableCell.rowColumnSpan": dict(SPAN),
            **changes,
        },
    )


class LinuxTableProjectionTests(unittest.TestCase):
    def observe(self, nodes, relations=()):
        raw = linux_batch(nodes, relations)
        before = copy.deepcopy(raw)
        graph = linux_graph(raw)
        snapshot = normalizeFacts(graph, DisplayReferences("session"))
        output = snapshot.render()
        self.assertEqual(raw, before)
        self.assertIs(snapshot.native, graph)
        self.assertTrue(all(p.get("presentation") != "uncompressed" for p in output["projections"]))
        return snapshot, output

    def test_complete_span_has_two_actual_destinations_and_keeps_exact_binding(self):
        snapshot, output = self.observe([cell()])
        self.assertIn("Rows: 3–4, Column: 4", output["text"])
        self.assertEqual(output["refs"], {"0": "cell"})
        for name, destination in (
            ("row_index", "rowRange"),
            ("row_span", "rowRange"),
            ("column_index", "columnRange"),
            ("column_span", "columnRange"),
        ):
            source = snapshot.fields[("0", "structure." + name)]
            self.assertIsNone(source["field"])
            self.assertEqual(source["representation"], "projection")
            claim = next(
                x for x in output["projections"][0]["factAccounting"]["values"] if x["property"] == "structure." + name
            )
            self.assertEqual(claim["kind"], "equivalent")
            self.assertEqual([p["field"] for p in claim["projections"]], [destination])
        self.assertNotIn("TableCell.rowColumnSpan:", output["text"])
        self.assertNotIn("selected", snapshot.roots[0])

    def test_uncertain_visibility_or_span_keeps_raw_without_inventing_position(self):
        cases = [cell(visible=False), cell(showing=False), cell(visible=1)]
        missing = cell()
        missing["values"].pop("visible")
        missing["requestedFields"].remove("visible")
        cases.append(missing)
        for key in ("visible", "showing", "TableCell.rowColumnSpan"):
            failed = cell()
            failed["values"].pop(key)
            failed["fieldStatus"][key] = dict(state="error")
            cases.append(failed)
        for value in [
            dict(SPAN, row=-1),
            dict(SPAN, row=True),
            dict(SPAN, rowSpan=0),
            dict(SPAN, columnSpan=1.5),
            dict(SPAN, row=9_007_199_254_740_991),
            dict(SPAN, unknown=4),
            [2, 3, 2, 1],
        ]:
            cases.append(cell(**{"TableCell.rowColumnSpan": value}))
        for source in cases:
            with self.subTest(source=source):
                snapshot, output = self.observe([source])
                self.assertNotIn("rowRange", snapshot.roots[0])
                self.assertNotIn("columnRange", snapshot.roots[0])
                native = snapshot.native.nodes["cell"]
                original = source["values"].get("TableCell.rowColumnSpan")
                if original == SPAN:
                    self.assertEqual(
                        (
                            native.structure.row_index,
                            native.structure.column_index,
                            native.structure.row_span,
                            native.structure.column_span,
                        ),
                        (2, 3, 2, 1),
                    )
                    self.assertIn("structure.row_index", output["text"])
                elif original is not None:
                    retained = next(
                        item for item in native.supplements if item.key == "provider.linux.TableCell.rowColumnSpan"
                    )
                    self.assertEqual(retained.value, tuple(original) if isinstance(original, list) else original)
                    self.assertIn("provider.linux.TableCell.rowColumnSpan", output["text"])
                else:
                    self.assertTrue(
                        any(
                            issue.state == "error" and "TableCell.rowColumnSpan" in issue.path
                            for issue in snapshot.native.issues
                        )
                    )
                    self.assertIn("error", output["text"])

    def test_projection_requires_every_exact_noncontinuation_claim(self):
        snapshot, output = self.observe([cell()])
        fields = copy.deepcopy(snapshot.fields)
        native_nodes = {"cell": snapshot.facts["nodes"][0]}
        full = output["projections"][0]
        original = [c for c in full["fieldCoverage"] if c["field"] in ("rowRange", "columnRange")]
        self.assertEqual(len(original), 2)
        altered = []
        for change in (
            {"ref": "wrong"},
            {"field": "other"},
            {"value": {"location": 99, "length": 1}},
            {"kind": "continuation"},
        ):
            claims = copy.deepcopy(original)
            claims[1].update(change)
            altered.append(claims)
        for claims in ([], original[:1], *altered):
            with self.subTest(claims=claims):
                _, coverage = account_fields(["0"], snapshot.refs, fields, native_nodes, {"fieldCoverage": claims})
                span = next(c for c in coverage["values"] if c["property"] == "structure.column_span")
                self.assertEqual(span["kind"], "supplement")
        fields[("0", "structure.column_span")]["projections"] = []
        _, coverage = account_fields(["0"], snapshot.refs, fields, native_nodes, full)
        self.assertEqual(
            next(c for c in coverage["values"] if c["property"] == "structure.column_span")["kind"], "supplement"
        )

    def test_selection_is_an_independent_container_claim_with_conflicts_and_total(self):
        snapshot, output = self.observe(
            [
                node(
                    "table", role="table", **{"Table.nRows": 80, "Table.nColumns": 3, "Selection.nSelectedChildren": 7}
                ),
                cell(selected=False),
                node("header", role="table column header", name="Amount"),
            ],
            [
                edge(
                    "table",
                    "cell",
                    property="Selection.selectedChildren",
                    state="partial",
                    total=9,
                    offset=0,
                    cursor="next",
                ),
                edge("cell", "table", property="TableCell.table"),
                edge("cell", "header", property="TableCell.columnHeaderCells"),
            ],
        )
        self.assertEqual(snapshot.roots[0]["children"], [])
        self.assertFalse(snapshot.roots[1]["selected"])
        self.assertIn("Table rows: 80, Table columns: 3, Selected children: 7", output["text"])
        self.assertIn(
            (
                "Selected children of 0: 1 (partial; 1 listed of 9; node reports "
                "selected=false: 1; separate count reports "
                "7)"
            ),
            output["text"],
        )
        self.assertIn("Related elements (selected_children of 0)", output["text"])
        self.assertIn("Related elements (column_headers of 1)", output["text"])
        self.assertIn('Relation "table"', output["text"])

    def test_zero_unknown_and_unread_selection_are_distinguished(self):
        _, output = self.observe(
            [node("table", role="table", **{"Selection.nSelectedChildren": 0})],
            [edge("table", property="Selection.selectedChildren", total=0)],
        )
        self.assertIn("Selected children of 0: none (0 listed of 0)", output["text"])
        _, output = self.observe(
            [node("table", role="table")],
            [edge("table", property="Selection.selectedChildren", state="error", certainty="unknown")],
        )
        self.assertIn("no confirmed items collected (membership unknown; error)", output["text"])
        raw = linux_batch(
            [node("table", role="table")],
            [edge("table", "missing", property="Selection.selectedChildren", state="partial", total=4)],
        )
        raw["unread"] = ["missing"]
        output = normalizeFacts(linux_graph(raw), DisplayReferences("session")).render()
        self.assertIn("1 listed of 4; not expanded: 1", output["text"])
        self.assertEqual(output["refs"]["1"], "missing")

    def test_shared_header_is_not_duplicated_or_made_a_child_of_the_cell(self):
        snapshot, output = self.observe(
            [node("table", role="table"), cell(), node("header", role="table column header", name="Amount")],
            [
                edge("table", "cell", "header", property="children"),
                edge("cell", "table", property="TableCell.table"),
                edge("cell", "header", property="TableCell.columnHeaderCells"),
            ],
        )
        self.assertEqual(len(snapshot.roots), 1)
        self.assertEqual(snapshot.roots[0]["children"][0]["children"], [])
        self.assertEqual(output["text"].count("2 column header Amount"), 1)

    def test_formatter_failure_keeps_raw_span_and_relations(self):
        raw = linux_batch([cell()])
        snapshot = normalizeFacts(linux_graph(raw), DisplayReferences("session"))
        with patch(
            ("computer_use.interaction.read.presentation.rendering.passes.render_semantic_lines.renderProjectionLines"),
            side_effect=ValueError("fixture"),
        ):
            output = snapshot.render()
        self.assertEqual(output["projections"][0]["presentation"], "uncompressed")
        self.assertIn("structure.row_index", output["text"])
        self.assertIn("structure.row_span", output["text"])

    def test_identical_metadata_is_grouped_across_related_roots_without_merging_objects(self):
        snapshot, output = self.observe(
            [
                node("cell", role="table cell", enabled=True),
                node("h1", role="table column header", name="Same", enabled=True),
                node("h2", role="table column header", name="Same", enabled=True),
            ],
            [edge("cell", "h1", "h2", property="TableCell.columnHeaderCells")],
        )
        self.assertEqual(output["text"].count("Additional native facts"), 1)
        self.assertIn("1 column header Same", output["text"])
        self.assertIn("2 column header Same", output["text"])
        self.assertIn("0–2 states.enabled: true", output["text"])
        self.assertEqual(output["refs"], {"0": "cell", "1": "h1", "2": "h2"})
        self.assertEqual(output, snapshot.render(), "Repeated presentation must not mutate coverage or facts")
        self.assertEqual(sum(len(p["factAccounting"]["actions"]) for p in output["projections"]), 3)

    def test_mixed_fallback_keeps_its_facts_without_leaking_partial_supplements(self):
        from computer_use.interaction.read.presentation.rendering.formatting import renderProjectionLines

        raw = linux_batch(
            [
                node("a", role="table cell", enabled=True),
                node("b", role="table cell", enabled=False),
                node("c", role="table cell", enabled=True),
            ]
        )
        graph = linux_graph(raw)
        snapshot = normalizeFacts(graph, DisplayReferences("session"))

        def partial_failure(tree, context, projection):
            if tree["ref"] == "1":
                raise ValueError("fixture")
            return renderProjectionLines(tree, context, projection)

        with patch(
            ("computer_use.interaction.read.presentation.rendering.passes.render_semantic_lines.renderProjectionLines"),
            side_effect=partial_failure,
        ):
            output = snapshot.render()
        self.assertEqual(output["projections"][1]["presentation"], "uncompressed")
        self.assertIn("enabled", output["projections"][1]["text"])
        self.assertIn("false", output["projections"][1]["text"])
        self.assertIn("0, 2 states.enabled: true", output["text"])
        self.assertEqual(output["text"].count("Additional native facts"), 1)
        self.assertIs(snapshot.native, graph)


class LinuxTableReadProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_fields_and_relation_capabilities_are_independent_and_explicit_profile_is_raw(self):
        class Port:
            async def nodes(self, *args, **options):
                self.fields, self.options = args[2], options
                return decode_driver_reply(observation(linux_batch([node("table", role="table")])))

        target = Binding("session", "instance", "root", "surface", "table")
        read = SimpleNamespace(target=target, token="op", remaining=lambda: 3000)
        for features in (
            set(),
            {"nativeTableFacts"},
            {"nativeTableFacts", "nativeAdvertisedFields"},
            {"nativeFollowRelations"},
            {"nativeTableFacts", "nativeAdvertisedFields", "nativeFollowRelations"},
            {"nativeTableFacts", "nativeAdvertisedFields", "nativeFollowRelations", "nativeTableContent"},
            {"nativeFollowRelations", "nativeTableViewport"},
            {
                "nativeTableFacts",
                "nativeAdvertisedFields",
                "nativeFollowRelations",
                "nativeTableContent",
                "nativeTableViewport",
            },
        ):
            with self.subTest(features=features):
                port = Port()
                port.connection = SimpleNamespace(platform="linux", capabilities=features)
                await NativeReader(port).collect(read)
                supports_fields = {"nativeTableFacts", "nativeAdvertisedFields"} <= features
                self.assertEqual("TableCell.rowColumnSpan" in port.fields, supports_fields)
                self.assertEqual("visible" in port.fields, "nativeTableFacts" in features)
                self.assertEqual("Selection.nSelectedChildren" in port.fields, supports_fields)
                self.assertEqual("follow_relations" in port.options, "nativeFollowRelations" in features)
                if "nativeFollowRelations" in features:
                    self.assertEqual(
                        port.options["follow_relations"],
                        [
                            name
                            for name in READ_RELATIONS["linux"]
                            if (name != "Table.cells" or "nativeTableContent" in features)
                            and (name != "Table.viewportCandidates" or "nativeTableViewport" in features)
                        ],
                    )
                if supports_fields:
                    self.assertIn("TableCell.rowColumnSpan", port.options["advertised_fields"])
                await NativeReader(port).collect(read, fields=["TableCell.rowColumnSpan"])
                self.assertEqual(port.fields, ["TableCell.rowColumnSpan"])
                self.assertEqual(port.options, {"scope_kind": None})


if __name__ == "__main__":
    unittest.main()
