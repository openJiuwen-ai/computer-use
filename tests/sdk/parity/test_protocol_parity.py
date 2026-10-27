"""Same-input comparison with the original implementation, including failures.

Expected results were frozen before retiring TypeScript; see baselines/README.md.
This is an offline regression, not a desktop test.
"""

from __future__ import annotations

import base64
import copy
import importlib
import json
import random
import unittest
from collections.abc import Mapping

from baseline import baseline
from repository import ROOT as REPO_ROOT

from computer_use.protocol.captures import captureMetadata, decodeCapture
from computer_use.protocol.png import PNG
from computer_use.protocol.validation import validateKey, validatePasteText, validateText

ROOT = REPO_ROOT
PNG64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII="
STAMP = dict(id="observation", startedAt=1000, completedAt=2000)
CAPTURE = dict(id="image", kind="window", scope="scope", startedAt=1200, completedAt=1500, width=1, height=1, png=PNG64)
OBSERVATION = dict(
    root=None, context={}, windows=[dict(ref="1", title="文档")], current="1", screenshot=dict(width=1, height=1)
)
APP = dict(
    instance="instance",
    appRef="app",
    observed=STAMP,
    scope="scope",
    windows={"1": "scope"},
    window="1",
    observation=OBSERVATION,
    screenshot=CAPTURE,
    refs={},
)
CONTEXT = dict(after="action", scope="scope", window="1")
RECEIPT = dict(
    target="target", status="dispatched", detail="Native action dispatched; outcome unconfirmed.", context=CONTEXT
)
FAILURE = dict(code="input_unavailable", target="fixture", effect="none", detail="Not available", next="read again")
HELLO = dict(
    version=3,
    session="session",
    id=0,
    result=dict(
        platform="macos",
        desktopMode="shared",
        maxTimeoutMs=30000,
        capabilities=[
            "structuredObservations",
            "cancel",
            "requestDedup",
            "captureMetadata",
            "retainedCaptures",
            "desktopCoordination",
        ],
    ),
)


def normalize(value):
    if isinstance(value, PNG):
        return dict(png=base64.b64encode(value).decode(), metadata=normalize(captureMetadata(value)))
    if isinstance(value, BaseException):
        return dict(
            message=str(value),
            **{k: getattr(value, k) for k in ("code", "effect", "target", "detail", "next") if hasattr(value, k)},
        )
    if isinstance(value, Mapping):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def invoke(case):
    module = "computer_use.errors" if case["module"] == "errors" else "computer_use.protocol." + case["module"]
    if case["module"] == "observation":
        module = "computer_use.protocol.presentation"
    if (case["module"], case["method"]) in (("session", "decodeHello"), ("validation", "decodeResult")):
        module = "legacy_protocol"
    try:
        value = getattr(importlib.import_module(module), case["method"])(*case["args"])
        return dict(ok=True, value=normalize(value))
    except Exception as error:
        return dict(ok=False, error=normalize(error))


def case(module, method, *args):
    return dict(module=module, method=method, args=list(args))


def mutations(value):
    yield value
    yield None
    yield []
    yield {**value, "extra": True}
    for key in value:
        yield {k: v for k, v in value.items() if k != key}
        for replacement in (None, "", True, False, 0, -1, 0.5, [], {}):
            yield {**value, key: replacement}


def numeric_fixture(fixture):
    result = copy.deepcopy(fixture)
    ids = {}

    def ref(value):
        if value not in ids:
            ids[value] = str(len(ids))
        return ids[value]

    def visit(value, key=None):
        if isinstance(value, dict):
            return {k: visit(v, k) for k, v in value.items()}
        if isinstance(value, list):
            if key in ("labelTargets", "ownedModals", "blockedBy", "focusAncestorRefs", "inheritedBlocks"):
                return [ref(v) for v in value]
            return [visit(v) for v in value]
        if isinstance(value, str) and value and key in ("ref", "titleRef", "focusedRef", "subtreeRef", "disclosedBy"):
            return ref(value)
        return value

    return visit(result)


def cases():
    for method in ("validateText", "validatePasteText", "validateKey"):
        for value in (
            None,
            True,
            0,
            [],
            {},
            "",
            " ",
            "中文 😀",
            "a\nb",
            "a\tb",
            "\u2028",
            "\x85",
            "\ufeff",
            "\ud800",
            "\udfff",
            "\ud83d\ude00",
            "super+p",
            "ctrl+super+shift+p",
            "ctrl+ctrl+p",
            "Return",
            "return",
            "F12",
            "F13",
            "a+",
            "super+é",
        ):
            yield case("validation", method, value)
    for value in (
        [],
        ["Open"],
        [{"role": "button", "name": "Open"}],
        [{"identifier": "name"}],
        ["x"] * 17,
        [{"role": "button\n"}],
        ["😀" * 129],
        ["\ufeff"],
        ["\x85"],
        [True],
        [None],
        [{}],
        [{"name": "x", "pid": "1"}],
    ):
        yield case("element_path", "elementPath", value)
    for value in mutations(STAMP):
        yield case("captures", "decodeStamp", value)
    for value in mutations(CAPTURE):
        yield case("captures", "decodeCapture", value, "window", "scope", STAMP)
    for notes in (
        [],
        ["Initial client repaint is unconfirmed."],
        ["reason"] * 16,
        ["reason"] * 17,
        ["😀" * 2048],
        ["😀" * 2049],
        [""],
        [" "],
        [False],
        None,
        "reason",
    ):
        yield case("captures", "decodeCapture", {**CAPTURE, "stopReasons": notes}, "window", "scope", STAMP)
    for value in mutations(CONTEXT):
        yield case("action_context", "decodeActionContext", value)
    for value in mutations(FAILURE):
        yield case("session", "decodeFailure", value)
    for value in mutations(HELLO):
        yield case("session", "decodeHello", value, "shared")
    for value in mutations(HELLO["result"]):
        yield case("session", "decodeHello", {**HELLO, "result": value}, "shared")
    for value in (
        "busy",
        "mode_mismatch",
        "session_mismatch",
        "request_expired",
        "unrecognized",
        "stale_target",
        "execution_unknown",
    ):
        yield case("errors", "publicErrorCode", value)
        yield case("session", "failureError", "typeText", {**FAILURE, "code": value, "context": CONTEXT})
    for value in mutations(APP):
        yield case("validation", "decodeResult", "readApp", value)
    for value in mutations(RECEIPT):
        yield case("validation", "decodeResult", "performElementAction", value)
    for value in mutations(dict(appRef="app", scope="scope", ref="12", token="token", observed=STAMP)):
        yield case("validation", "decodeResult", "findElement", value)
    for value in mutations(dict(text="😀a", offset=1, total=4, next=None)):
        yield case("validation", "decodeResult", "readElementText", value)
    for text in ("", "a", "😀", "a" * 16384, "😀" * 8193):
        length = len(text.encode("utf-16-le")) // 2
        for next_offset in (None, length, length + 1, 0, False):
            yield case(
                "validation",
                "decodeResult",
                "readElementText",
                dict(text=text, offset=0, total=length, next=next_offset),
            )
    app = dict(id="id", displayName="应用", isRunning=True)
    for value in mutations(app):
        yield case("validation", "decodeResult", "listApps", [value])
    for value in (None, {}, [], [app, app]):
        yield case("validation", "decodeResult", "listApps", value)
    for value in mutations(OBSERVATION):
        yield case("observation", "decodeObservation", value)
    node = dict(
        ref="0",
        role="button",
        children=[],
        name="hello",
        enabled=True,
        childCount=0,
        actions=[dict(name="native", description="描述")],
        capabilities=dict(click="available", typeText="unavailable", replaceText="unknown", focus="available"),
    )
    for value in mutations(node):
        yield case("observation", "decodeObservation", {**OBSERVATION, "root": value})
    for value in json.loads((ROOT / "tests/fixtures/observation-cases.json").read_text(encoding="utf-8")):
        adapted = numeric_fixture(value)
        yield case(
            "observation",
            "decodeObservation",
            dict(root=adapted["root"], context=adapted["context"], windows=[], current=None),
        )
    rectangle = dict(x=0, y=0, width=1, height=1)
    desktop = dict(
        mode="shared",
        canvas=dict(width=1, height=1),
        frontmost=None,
        displays=[dict(name="display", rect=rectangle, captured=True)],
        windows=[],
        omittedWindows=0,
    )
    for value in mutations(desktop):
        yield case("desktop", "decodeDesktop", value)
    for value in mutations(rectangle):
        yield case(
            "desktop", "decodeDesktop", {**desktop, "displays": [dict(name="display", rect=value, captured=True)]}
        )
    yield case(
        "validation",
        "decodeResult",
        "readDesktop",
        dict(
            desktopRef="desktop",
            observed=STAMP,
            scope="scope",
            observation=desktop,
            screenshot={**CAPTURE, "kind": "desktop"},
        ),
    )
    data = base64.b64decode(PNG64)
    for value in (None, "", PNG64, PNG64 + "\n", "data:image/png;base64," + PNG64):
        yield case("png", "decodePNG", value)
    for i in range(len(data)):
        mutated = bytearray(data)
        mutated[i] ^= 1
        yield case("png", "decodePNG", base64.b64encode(mutated).decode())
    for n in range(len(data)):
        yield case("png", "decodePNG", base64.b64encode(data[:n]).decode())
    # Deterministic non-BMP/ref/empty/type mutations catch coercion differences.
    rng = random.Random(20261008)
    for _ in range(100):
        ref = rng.choice(["0", "1", "01", "-1", "1\n", "a0", "１２", "18446744073709551615"])
        yield case(
            "validation",
            "decodeResult",
            "findElement",
            dict(appRef="app", scope="scope", ref=ref, token="token", observed=STAMP),
        )


class ProtocolParity(unittest.TestCase):
    def test_standalone_super_is_a_key_but_cannot_be_modified(self):
        validateKey("super")
        for value in ("super+super", "ctrl+super", "Super", "shift", "alt", "ctrl"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validateKey(value)

    def test_same_inputs_match_historical_baseline(self):
        requests = list(cases())
        process = baseline("functions", requests)
        # JSONL is LF-framed. str.splitlines() also splits valid JSON string
        # characters such as U+0085/U+2028, corrupting Unicode observations.
        expected = process
        self.assertEqual(len(expected), len(requests))
        for index, (request, oracle) in enumerate(zip(requests, expected)):
            # Feed Python the same JSON representation consumed by the JS oracle.
            request = json.loads(json.dumps(request, ensure_ascii=True))
            # The accepted historical inputs/statuses stay fixed. The one
            # intentional message extension advertises newly supported keys.
            if request["method"] == "validateKey" and not oracle["ok"]:
                oracle = copy.deepcopy(oracle)
                oracle["error"]["message"] = oracle["error"]["message"].replace(
                    "Use a–z, 0–9, ", "Use a–z, 0–9, punctuation -=[]\\;',./`, "
                )
            with self.subTest(index=index, module=request["module"], method=request["method"]):
                self.assertEqual(invoke(request), oracle, json.dumps(request, ensure_ascii=True)[:1200])
        print(f"Compared {len(requests)} protocol inputs against frozen historical baselines.")

    def test_capture_copy_and_immutability(self):
        png = decodeCapture(CAPTURE, "window", "scope", STAMP)
        self.assertEqual(captureMetadata(png)["id"], "image")
        self.assertIsNone(captureMetadata(PNG(png)))
        self.assertIsNone(captureMetadata(bytes(png)))
        with self.assertRaises(TypeError):
            captureMetadata(png)["id"] = "other"
        validateText("\ud83d\ude00")
        validatePasteText("\ud83d\ude00\n")


if __name__ == "__main__":
    unittest.main()
