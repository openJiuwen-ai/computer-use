"""Compare full projection, including native sources and every folding decision."""

import copy
import importlib
import json
import random
import unittest

from baseline import baseline
from sdk.parity.action_contract import action_contract
from sdk.parity.test_protocol_parity import ROOT, normalize


def fixtures():
    for item in json.loads((ROOT / "tests/fixtures/observation-cases.json").read_text(encoding="utf-8")):
        yield item["root"], item["context"]
        yield item["root"], {**item["context"], "subtreeRef": item["root"]["ref"]}
        yield item["root"], {**item["context"], "includeBackground": True}
    rng = random.Random(194203)
    roles = [
        "application",
        "window",
        "sheet",
        "group",
        "cell",
        "column",
        "row",
        "outline",
        "staticText",
        "button",
        "textField",
        "heading",
        "image",
        "unknown",
        "menuBar",
    ]
    texts = ["", "Wi-Fi", "Value", "中文 😀", "Name > 99 button", "long" * 320]
    for _ in range(150):
        counter = 0

        def build(depth=0):
            nonlocal counter
            counter += 1
            result = dict(ref=str(counter), role=rng.choice(roles), children=[])
            for field in ("name", "description", "value", "identifier", "help", "placeholder"):
                if rng.random() < 0.18:
                    result[field] = rng.choice(texts)
            for field in (
                "selected",
                "selectable",
                "focused",
                "focusable",
                "pressable",
                "enabled",
                "modal",
                "preserveGroup",
                "valueExpected",
                "valueIsNumber",
                "expandable",
                "expanded",
                "disclosing",
                "submenu",
            ):
                if rng.random() < 0.15:
                    result[field] = rng.choice([True, False])
            if rng.random() < 0.22:
                result["capabilities"] = {
                    k: rng.choice(["available", "unknown", "unavailable"])
                    for k in ("click", "typeText", "replaceText", "focus")
                }
            if rng.random() < 0.25:
                result["actions"] = [
                    dict(
                        name=rng.choice(["AXPress", "AXShowMenu", "AXPick", "AXExpand", "AXShowDefaultUI", "Delete"]),
                        description="动作",
                    )
                ]
            if depth < 3:
                result["children"] = [build(depth + 1) for _ in range(rng.randrange(4))]
            if rng.random() < 0.2:
                result.update(
                    childCount=len(result["children"]) + rng.randrange(2), childrenRead=len(result["children"])
                )
            return result

        root = build()
        yield (
            root,
            {
                "app": "fixture",
                "focusedRef": str(rng.randint(1, counter)),
                "includeBackground": rng.choice([True, False]),
            },
        )


class ProjectionParity(unittest.TestCase):
    def test_projection_and_all_provenance_match(self):
        inputs = list(fixtures())
        requests = [dict(module="projection", method="projectTree", args=[root, context]) for root, context in inputs]
        process = baseline("functions", requests)
        expected = process
        self.assertEqual(len(expected), len(requests))
        project_tree = importlib.import_module(
            "computer_use.interaction.read.presentation.compression.projection"
        ).projectTree
        for index, ((root, context), oracle) in enumerate(zip(inputs, expected)):
            with self.subTest(index=index):
                before = copy.deepcopy(root)
                try:
                    actual = dict(ok=True, value=normalize(project_tree(action_contract(root), context)))
                except Exception as error:
                    actual = dict(ok=False, error=normalize(error))
                self.assertEqual(actual, action_contract(oracle))
                self.assertEqual(root, before, "Projection must not mutate native facts or routing targets")
        print(f"Compared {len(requests)} complete projections against frozen historical baselines.")


if __name__ == "__main__":
    unittest.main()
