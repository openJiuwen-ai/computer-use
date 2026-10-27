import copy
import importlib
import json
import unittest

from baseline import baseline
from sdk.parity.action_contract import action_contract
from sdk.parity.hints import pythonHints
from sdk.parity.test_projection_parity import fixtures
from sdk.parity.test_protocol_parity import ROOT, normalize, numeric_fixture


class RenderParity(unittest.TestCase):
    def test_full_text_refs_summaries_and_provenance(self):
        inputs = list(fixtures())
        for item in json.loads((ROOT / "tests/fixtures/observation-cases.json").read_text(encoding="utf-8")):
            adapted = numeric_fixture(item)
            inputs.append((adapted["root"], adapted["context"]))
        requests = [dict(module="render", method="renderTree", args=[root, context]) for root, context in inputs]
        special = [
            "",
            "中文",
            " spaced ",
            "\ufeff",
            "\x85",
            "x > 99 button",
            "\ud800",
            "\ud83d\ude00",
            "\u2028",
            "\x00",
            'a,"b',
            "(partial: abc)",
        ]
        for value in special:
            for method in ("quoted", "field"):
                requests.append(dict(module="render", method=method, args=[value]))
        for value in ["x" * 1300, "😀" * 300, "👩🏽‍💻" * 300, "e\u0301" * 300, "🇨🇳" * 300, "क्‍ष" * 300, "a\r\nb" * 300]:
            for limit in (80, 120, 200, 1200):
                requests.append(dict(module="render", method="bounded", args=[value, limit]))
        process = baseline("functions", requests)
        expected = process
        self.assertEqual(len(expected), len(requests))
        module = importlib.import_module("computer_use.interaction.read.presentation.rendering.formatting")
        for index, (request, oracle) in enumerate(zip(requests, expected)):
            with self.subTest(index=index, method=request["method"]):
                request = json.loads(json.dumps(request))
                before = copy.deepcopy(request["args"])
                try:
                    actual = dict(
                        ok=True, value=normalize(getattr(module, request["method"])(*action_contract(request["args"])))
                    )
                except Exception as error:
                    actual = dict(ok=False, error=normalize(error))
                self.assertEqual(actual, action_contract(pythonHints(oracle)))
                self.assertEqual(request["args"], before)
        print(f"Compared {len(requests)} full-render/Unicode cases against frozen historical baselines.")


if __name__ == "__main__":
    unittest.main()
