"""Immutable local fields keep native UTF-16 paging and coverage semantics."""

import unittest
from types import SimpleNamespace

from observation_fixture import normalize_fixture as normalizeFacts
from v5_handles import App  # Fixed captured-field pipeline baseline.

from computer_use.errors import ComputerUseError
from computer_use.interaction.controller import SessionController as NativeController
from computer_use.interaction.read.acquisition.reader import capturedField
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.lifecycle import CallLifecycle
from computer_use.protocol.text import decodeTextPage, textPage


class CapturedFieldTests(unittest.TestCase):
    @staticmethod
    def snapshot(value="A🙂e\u0301\r\nZ", status=None):
        status = {} if status is None else {"AXValue": status}
        return SimpleNamespace(
            facts=dict(session="s", nodes=[dict(node="n", values={"AXValue": value}, fieldStatus=status)]),
            refs={"1": "n"},
            text_refs={("1", "AXValue"): status["AXValue"]}
            if "AXValue" in status and "textRef" in status["AXValue"]
            else {},
        )

    def test_field_is_a_value_snapshot_without_retaining_the_tree(self):
        snapshot = self.snapshot()
        source = capturedField(snapshot, "1", "AXValue")
        snapshot.facts["nodes"][0]["values"]["AXValue"] = "later edit"
        self.assertEqual(source.value, "A🙂e\u0301\r\nZ")
        self.assertEqual(source.session, "s")
        self.assertIsNone(source.text_ref)

    def test_utf16_pages_preserve_surrogates_combining_marks_and_line_breaks(self):
        text = "A🙂e\u0301\r\nZ"
        first = decodeTextPage(textPage(text, length=2))
        second = decodeTextPage(textPage(text, offset=first["next"], length=2))
        rest = decodeTextPage(textPage(text, offset=second["next"]))
        self.assertEqual((first["text"], second["text"], rest["text"]), ("A", "🙂", "e\u0301\r\nZ"))
        self.assertEqual(rest["total"], 8)
        self.assertEqual(textPage(text, offset=8), dict(text="", offset=8, total=8, next=None))
        for offset in (2, 9):
            with self.assertRaises(ValueError):
                textPage(text, offset=offset)

    def test_unread_or_truncated_preview_is_never_a_complete_capture(self):
        for value, status in [
            (12, None),
            (True, None),
            (None, None),
            ("preview", {"state": "truncated", "totalUnits": 90000}),
            ("", {"state": "timeout"}),
        ]:
            self.assertIsNone(capturedField(self.snapshot(value, status), "1", "AXValue"))
        self.assertIsNone(capturedField(self.snapshot(), "2", "AXValue"))
        self.assertEqual(capturedField(self.snapshot(""), "1", "AXValue").value, "")

    def test_truncated_native_capture_keeps_reference_and_total_not_preview(self):
        source = capturedField(
            self.snapshot("preview", dict(state="truncated", textRef="text-capture", totalUnits=90000)), "1", "AXValue"
        )
        self.assertIsNone(source.value)
        self.assertEqual((source.text_ref, source.total_units), ("text-capture", 90000))


class CapturedValueSourceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def observed(values, statuses=None):
        statuses = statuses or {}
        wire = dict(
            kind="nodes",
            session="s",
            platform="windows",
            surface="surface",
            observed=dict(id="stamp", startedAt=1, completedAt=2),
            roots=["n"],
            nodes=[
                dict(
                    node="n",
                    requestedFields=list(dict.fromkeys([*values, *statuses])),
                    values=values,
                    fieldStatus=statuses,
                    actions=dict(state="complete", items=[], total=0),
                )
            ],
            requestedRelations=[],
            relations=[],
            unread=[],
            stopReasons=[],
        )
        tree = normalizeFacts(wire, DisplayReferences("s"))
        port = SimpleNamespace(connection=SimpleNamespace(session="s", capabilities=()), policy=HostPolicy())
        port.calls = CallLifecycle(port)
        from computer_use.client.factory import sessionController

        controller = sessionController(port)
        app = App(controller, dict(selector="fixture"), _internal=True)
        controller._remember(controller.state(app), Binding("s", "instance", "window", "surface", "n"), tree)
        return tree, app.element("0")

    async def test_document_pattern_without_value_pattern_is_readable(self):
        tree, element = self.observed(
            {"ControlType": "ControlType.Document", "Text.DocumentRange": "正文🙂"},
            {"Value.Value": dict(state="unsupported")},
        )
        self.assertEqual(tree.value_fields, {"0": "text.content"})
        self.assertEqual((await element.readText()).text, "正文🙂")

    async def test_document_body_and_empty_or_different_control_value_do_not_overwrite_each_other(self):
        for body, value in [("正文🙂\n第二段", ""), ("正文🙂", "different control value"), ("", "nonempty value")]:
            for reverse in (False, True):
                with self.subTest(body=body, value=value, reverse=reverse):
                    entries = [
                        ("ControlType", "ControlType.Document"),
                        ("Text.DocumentRange", body),
                        ("Value.Value", value),
                    ]
                    tree, element = self.observed(dict(reversed(entries) if reverse else entries))
                    self.assertEqual(tree.value_fields, {"0": "text.content"})
                    self.assertEqual((await element.readText()).text, body)
                    self.assertEqual(tree.roots[0]["controlValue"], value)
                    self.assertEqual(tree.roots[0]["value"], body)

    async def test_missing_document_body_cannot_borrow_a_control_values_complete_capture(self):
        for state in ("absent", "unsupported", "timeout", "error"):
            with self.subTest(state=state):
                tree, element = self.observed(
                    {"ControlType": "ControlType.Document", "Value.Value": "control only"},
                    {"Text.DocumentRange": dict(state=state)},
                )
                self.assertNotIn("0", tree.value_fields)
                self.assertNotIn("value", tree.roots[0])
                rendered = tree.render(compact=True)
                self.assertIn("Control value: control only", rendered["text"])
                self.assertIn("Document text of 0: " + state, rendered["text"])
                with self.assertRaises(ComputerUseError) as failure:
                    await element.readText()
                self.assertEqual(failure.exception.code, "observation_incomplete")

    async def test_document_continuation_binds_the_body_capture_even_when_value_has_another_capture(self):
        tree, element = self.observed(
            {
                "ControlType": "ControlType.Document",
                "Text.DocumentRange": "body preview",
                "Value.Value": "value preview",
            },
            {
                key: dict(state="truncated", textRef=ref, totalUnits=9000)
                for key, ref in [("Text.DocumentRange", "body-text"), ("Value.Value", "value-text")]
            },
        )
        # The public controller retains the same body source as the main view.
        source = NativeController._snapshots(Binding("s", "instance", "window", "surface", "n"), tree)["0"]["text"]
        self.assertEqual((source.text_ref, source.total_units), ("body-text", 9000))
        tree.facts["nodes"][0]["values"]["text.content"] = "later body"
        self.assertEqual(source.text_ref, "body-text")
        self.assertIn("Control value", tree.render(compact=True)["text"])

    async def test_continuation_uses_the_displayed_value_including_an_empty_value(self):
        for value in ("body", ""):
            for reverse in (False, True):
                with self.subTest(value=value, reverse=reverse):
                    entries = [("Value.Value", value), ("Text.DocumentRange", "other value")]
                    tree, element = self.observed(dict(reversed(entries) if reverse else entries))
                    self.assertEqual(tree.value_fields, {"0": "control_value.value"})
                    self.assertEqual((await element.readText()).text, value)
                    self.assertEqual(tree.facts["nodes"][0]["values"]["text.content"], "other value")

    async def test_incomplete_primary_value_does_not_borrow_another_fields_capture(self):
        tree, element = self.observed(
            {"Text.DocumentRange": "unrelated complete value", "Value.Value": "preview"},
            {"Value.Value": dict(state="truncated", detail="capture limit")},
        )
        self.assertNotIn("readText()", tree.render()["text"])
        with self.assertRaises(ComputerUseError) as result:
            await element.readText()
        self.assertEqual(result.exception.code, "observation_incomplete")

    async def test_derived_toggle_value_is_not_mistaken_for_document_text(self):
        tree, element = self.observed({"Name": "label", "Toggle.ToggleState": "On"})
        self.assertEqual(tree.roots[0]["toggleState"], "on")
        self.assertEqual(tree.value_fields, {})
        with self.assertRaises(ComputerUseError):
            await element.readText()

    async def test_only_primary_field_advertises_its_captured_continuation(self):
        tree, element = self.observed(
            {"Value.Value": "first", "Text.DocumentRange": "second"},
            {
                key: dict(state="truncated", textRef=ref, totalUnits=40000)
                for key, ref in [("Value.Value", "old-text"), ("Text.DocumentRange", "document-text")]
            },
        )
        self.assertIn("no public continuation for this field", "\n".join(tree.annotations))
        self.assertEqual("\n".join(tree.annotations).count("via this element's readText()"), 1)
        self.assertEqual(capturedField(tree, "0", tree.value_fields["0"]).text_ref, "old-text")
