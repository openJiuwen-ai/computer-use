"""Observation scope, captions and sparse updates compared with the TS oracle."""

import copy
import importlib
import json
import re
import unittest

from baseline import baseline
from display_documents import fixture_renderer, with_document
from sdk.parity.action_contract import action_contract
from sdk.parity.hints import pythonHints
from sdk.parity.test_protocol_parity import ROOT, STAMP, case, normalize, numeric_fixture

from computer_use.interaction.read.presentation.rendering.formatting import renderTree


def observations():
    for fixture in json.loads((ROOT / "tests/fixtures/observation-cases.json").read_text(encoding="utf-8")):
        f = numeric_fixture(fixture)
        base = dict(root=f["root"], context=f["context"], windows=[dict(ref="9999", title="文档")], current="9999")
        yield base
        yield dict(
            base,
            title="文档",
            document="file:///tmp/a%20b.txt",
            screenshot=dict(width=800, height=600),
            applicationMenu=dict(mainWindow="9999", focusedWindow="9999"),
            notes=["原生数据", " x\ny "],
            sharedFocus=dict(refs=[f["root"]["ref"], "1234567"], outside=2),
        )
        yield dict(
            base,
            windows=[
                dict(ref="9999", title="文档", document=None),
                dict(ref="9998", title="文档", parent="9999", kind="sheet", modal=True),
                dict(ref="9997", title="其他", kind="dialog", modal=False, onScreen=False),
            ],
            current="9998",
            document=None,
            applicationMenu=dict(mainWindow="9999", focusedWindow="9997"),
            menuPath=["文件", "另存为…"],
            commandRef=f["root"]["ref"],
            screenshotError="未授权",
        )
    for current in (None, "1", "2", "3"):
        yield dict(
            root=None,
            context={},
            title="",
            windows=[dict(ref="1", title="文档"), dict(ref="2", title="文档", document="file:///tmp/2")],
            current=current,
            unavailable="a11y unavailable",
            applicationMenu=dict(mainWindow="1", focusedWindow="2"),
        )


def state(lines, prefix="App: Test.\nTree: full (current UI only)"):
    text = prefix + "\n0 window Test\n" + "\n".join(lines)
    text += "\n\t99 text " + "unchanged content " * 150
    refs = {match[1]: "token" + match[1] for match in re.finditer(r"^\t*([0-9]+) ", text, re.MULTILINE)}
    return dict(text=text, refs=refs)


def requests():
    for observation in observations():
        yield case("observation_output", "renderObservation", observation, STAMP)
        yield case("coverage", "observationCoverage", observation)
    for fixture in json.loads((ROOT / "tests/fixtures/observation-cases.json").read_text(encoding="utf-8")):
        f = numeric_fixture(fixture)
        rendered = renderTree(action_contract(f["root"]), f["context"])
        before = dict(
            text=rendered["text"],
            refs={ref: "token" + ref for ref in rendered["refs"]},
            contextRefs=rendered["contextRefs"],
        )
        yield case("snapshot", "snapshotDiff", before, before)
        yield case(
            "snapshot",
            "snapshotDiff",
            before,
            dict(before, text=before["text"].replace("Value:", "Value: modified", 1)),
        )
    structure = [("1", 1), ("2", 2), ("3", 3), ("4", 2), ("5", 1), ("6", 2), ("7", 3), ("8", 2)]
    before = state(["\t" * depth + ref + " group Node" + ref for ref, depth in structure])
    for mask in range(256):
        after = state(
            [
                "\t" * depth + ref + " group Node" + ref + (" changed" if mask & (1 << i) else "")
                for i, (ref, depth) in enumerate(structure)
            ]
        )
        yield case("snapshot", "snapshotDiff", before, after)
    for lines in [
        [],
        ["\t1 group Changed"],
        ["\t2 button Changed", "\t1 group Node1"],
        ["\t1 group Node1", "\t\t2 text area", "\t\t\tValue:", "\t\t\t| old", "\t\t\tOmitted: 10 characters (Value)  "],
    ]:
        after = state(lines)
        for not_observed in ([], ["3", "4"]):
            yield case("snapshot", "snapshotDiff", before, after, not_observed)
        yield case("snapshot", "snapshotDiff", after, after)
    for prefix in [
        'Window: "Other", App: Test.\nTree: full',
        "App: Other.\nTree: full",
        'Windows: 0 "Same"; 7 "Same"; current=7\nApp: Test.\nTree: full',
        'App: Test.\nDocument: "file:///other"\nTree: full',
        "App: Test.\nWindow relations: 0 [modal]\nTree: full",
    ]:
        yield case("snapshot", "snapshotDiff", before, state(["\t1 group Node1"], prefix))
    for old, new in [
        (None, None),
        ({"parents": {"1": None, "2": "1", "3": "2"}, "complete": []}, {"parents": {"1": None}, "complete": ["1"]}),
        ({"parents": {"1": None, "2": "1", "3": "2"}, "complete": []}, {"parents": {"1": None}, "complete": []}),
    ]:
        for ref in ("1", "2", "3", "4"):
            yield case("coverage", "confirmsRemoval", old, new, ref)
    for facts in [
        dict(kind="unknown"),
        dict(kind="desktop", stale=True),
        dict(kind="desktop", stale=False),
        dict(kind="overview"),
        dict(kind="overview", context=None),
        dict(kind="overview", context="title"),
        dict(kind="window", identity="image", context="title", stale=False, omitContext=False),
        dict(kind="window", identity="image", context="title", stale=True, omitContext=True),
    ]:
        yield case("image_caption", "renderImageCaption", 100, 200, facts)
        yield case(
            "image_caption",
            "renderImageCaption",
            100,
            200,
            {**facts, "stopReasons": ["Initial repaint is unconfirmed.", 'Budget: "100ms"\npartial pixels retained.']},
        )
    for observed in (None, STAMP):
        yield case("text_source", "textSourceLabel", dict(ref="1", observed=observed))
    for captured in (True, False):
        for rect in [dict(x=-0.005, y=-12.55, width=500, height=300), dict(x=1.005, y=13.456, width=0.1, height=1)]:
            for windows in ([], [dict(app="测试", selector="test", title="", rect=rect)]):
                yield case(
                    "desktop_output",
                    "renderDesktop",
                    dict(
                        mode="shared",
                        canvas=dict(width=1000, height=900),
                        frontmost=dict(name="测试", selector="test"),
                        displays=[dict(name="屏幕", rect=rect, captured=captured, error="未授权")],
                        windows=windows,
                        omittedWindows=2,
                        catalogError="部分可用",
                    ),
                )


class ObservationParity(unittest.TestCase):
    @fixture_renderer()
    def test_scope_coverage_diff_and_captions(self):
        inputs = list(requests())
        process = baseline("functions", inputs)
        expected = process
        self.assertEqual(len(inputs), len(expected))
        for i, (request, oracle) in enumerate(zip(inputs, expected)):
            with self.subTest(index=i, method=request["method"]):
                request = json.loads(json.dumps(request))
                before = copy.deepcopy(request["args"])
                module_name = {
                    "observation_output": "computer_use.interaction.read.delivery.formatting",
                    "coverage": "computer_use.interaction.read.delivery.coverage",
                    "snapshot": "computer_use.interaction.read.delivery.diff.formatting",
                    "image_caption": "computer_use.interaction.read.delivery.image_caption",
                    "text_source": "computer_use.interaction.read.facts.text_source",
                    "desktop_output": "computer_use.interaction.read.presentation.rendering.desktop",
                }[request["module"]]
                module = importlib.import_module(module_name)
                try:
                    args = action_contract(request["args"])
                    if request["module"] == "snapshot":
                        args = [with_document(value) if index < 2 else value for index, value in enumerate(args)]
                    result = dict(ok=True, value=normalize(getattr(module, request["method"])(*args)))
                except Exception as error:
                    result = dict(ok=False, error=normalize(error))
                expected_result = action_contract(pythonHints(oracle))
                if request["method"] == "renderDesktop" and expected_result.get("ok"):
                    # Windows now merges independent Win32/UIA discoveries.
                    # The old universal z-order claim is intentionally removed;
                    # compare every other byte with the frozen baseline.
                    expected_result = {
                        **expected_result,
                        "value": expected_result["value"].replace(
                            "On-screen windows (front to back; rectangles may overlap; not element references):",
                            "On-screen windows (rectangles may overlap; not element references):",
                        ),
                    }
                self.assertEqual(result, expected_result)
                self.assertEqual(request["args"], before)
        print(f"Compared {len(inputs)} observation/diff/coverage cases against frozen historical baselines.")


if __name__ == "__main__":
    unittest.main()
