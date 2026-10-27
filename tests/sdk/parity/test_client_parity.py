import copy
import json
import unittest

from baseline import baseline
from legacy_client import App, Computer, Controller, Element, computerForController, selectWorkingWindow
from legacy_results import decodeResult
from sdk.parity.hints import pythonHints
from sdk.parity.test_protocol_parity import APP, normalize

from computer_use.client import images
from computer_use.errors import ComputerUseError
from computer_use.session import images as images_state
from computer_use.session.events import setActionHandler, setObservationHandler


def step(target, method, *args, assign=None):
    value_value = dict(target=target, method=method, args=list(args))
    if assign:
        value_value["assign"] = assign
    return value_value


def read(window="1", revision=1, refs=("10", "11"), instance="fixture"):
    wire = copy.deepcopy(APP)
    scope = "scope-" + window
    wire.update(
        instance=instance,
        appRef=f"capture-{window}-{revision}",
        scope=scope,
        window=window,
        windows={"1": "scope-1", "2": "scope-2"},
        catalogScope="catalog",
    )
    wire["observed"]["id"] = f"observation-{revision}"
    wire["screenshot"].update(scope=scope, id=f"image-{window}-{revision}")
    wire["observation"].update(
        root=dict(
            ref=window,
            role="window",
            name="文档",
            children=[dict(ref=ref, role="textField", name="输入", value="内容", children=[]) for ref in refs],
        ),
        windows=[dict(ref="1", title="文档"), dict(ref="2", title="文档")],
        current=window,
        context=dict(app="Fixture"),
        title="文档",
    )
    wire["refs"] = {ref: f"token-{window}-{revision}-{ref}" for ref in [window, *refs]}
    return dict(wire=wire)


def specs():
    base = [
        step("computer", "app", "fixture", assign="app"),
        step("app", "read", assign="s1"),
        step("app", "element", "10", assign="element"),
    ]
    operations = [
        step("element", "click"),
        step("element", "click", dict(button="right", count=2)),
        step("element", "focus"),
        step("element", "openMenu"),
        step("element", "performAction", "删除"),
        step("element", "performAction", "Name:删除\nTarget:0x0\nSelector:(null)"),
        step("element", "typeText", "文本"),
        step("element", "typeText", "文本", dict(replace=True)),
        step("element", "paste", "一\n二\t三"),
        step("element", "pressKey", "Return"),
        step("element", "scroll", "down", 0.5),
        step("app", "click", 0, 0),
        step("app", "typeText", "hello"),
        step("app", "pressKey", "super+a"),
        step("app", "paste", "text\n"),
        step("app", "scroll", 0, 0, "down"),
        step("app", "drag", dict(fromX=0, fromY=0, toX=0.5, toY=0.5)),
        step("app", "read", dict(menuPath=["文件", "打开…"])),
        step("element", "read", dict(offset=2)),
    ]
    yield dict(responses={"readApp": [read()]}, steps=base + [dict(op="deliver", target="s1")] + operations)
    for method, args in [
        ("click", [None]),
        ("click", [{}]),
        ("click", [dict(count=True)]),
        ("click", [dict(count=3)]),
        ("click", [dict(extra=1)]),
        ("performAction", []),
        ("performAction", [""]),
        ("performAction", [" \n"]),
        ("performAction", [None]),
        ("performAction", [42]),
        ("performAction", ["删除", {}]),
        ("focus", [{}]),
        ("openMenu", [{}]),
        ("paste", []),
        ("paste", ["text", {}]),
        ("typeText", ["x", dict(replace=1)]),
        ("typeText", ["x", None]),
        ("typeText", ["x\ny"]),
        ("pressKey", ["cmd+wrong"]),
        ("scroll", ["north"]),
        ("scroll", ["down", True]),
        ("scroll", ["down", 0]),
        ("read", [dict(offset=-1)]),
        ("read", [dict(offset=True)]),
        ("read", [None]),
        ("readText", [dict(length=1)]),
        ("readText", [dict(offset=-1)]),
    ]:
        yield dict(responses={"readApp": [read()]}, steps=base + [step("element", method, *args)])
    for options in [
        None,
        [],
        {"full": 1},
        {"window": "2"},
        {"menuPath": []},
        {"menuPath": [""]},
        {"menuPath": ["x"] * 9},
        {"menuPath": ["😀" * 129]},
    ]:
        yield dict(responses={"readApp": [read()]}, steps=base + [step("app", "read", options)])
    for selector in ["", None, {}, [], 12, " \n"]:
        yield dict(responses={}, steps=[step("computer", "app", selector)])
    for ref in [None, "", "AllClear", "01", "-1", 12, "888"]:
        yield dict(
            responses={"readApp": [read()]}, steps=base + [step("app", "element", ref), step("app", "window", ref)]
        )
    yield dict(
        responses={"readApp": [read("1"), read("2"), read("1", 2)]},
        steps=base
        + [
            step("app", "window", "2", assign="second"),
            step("second", "read"),
            step("second", "element", "10", assign="other"),
            step("app", "read", assign="s2"),
            step("element", "click"),
            step("other", "typeText", "other"),
            step("app", "element", "10", assign="new"),
            step("new", "click"),
            dict(op="invalidate"),
            step("other", "click"),
        ],
    )
    yield dict(
        responses={"readApp": [read(), read(revision=2, refs=("11",))]},
        steps=base
        + [
            step("app", "read"),
            step("element", "click"),
            step("app", "element", "10", assign="retained"),
            step("retained", "click"),
        ],
    )
    yield dict(
        responses={"readApp": [read(), read(revision=2)]},
        steps=base
        + [
            step("app", "click", 0, 0),
            dict(op="deliver", target="s1"),
            step("app", "click", 0, 0),
            step("app", "read", assign="s2"),
            step("app", "click", 0, 0),
            dict(op="deliver", target="s2"),
            step("app", "click", 0, 0),
            dict(op="resetDelivery"),
            step("app", "click", 0, 0),
            dict(op="label", target="s1"),
        ],
    )
    failure = dict(
        code="input_unavailable", effect="possible", target="Fixture", detail="Native result unknown", next="read again"
    )
    yield dict(
        responses={"readApp": [read()], "performElementAction": [dict(error=failure)]},
        steps=base + [step("element", "performAction", "删除"), step("app", "read")],
    )
    find = dict(appRef="capture-1-1", scope="scope-1", ref="10", token="found", observed=APP["observed"])
    text_page = dict(text="hello", offset=0, next=None, total=5, source="value")
    # The controller's test seam returns decoded pages, so this fixture only
    # exercises lookup/continuation routing; wire pages have separate tests.
    yield dict(
        responses={"readApp": [read()], "findElement": [find], "readElementText": [text_page]},
        steps=base
        + [
            step("app", "find", [dict(role="textField", name="输入")], assign="query"),
            step("query", "typeText", "new"),
            step("query", "readText", dict(offset=1)),
            step("query", "readText"),
            step("query", "readText", dict(offset=2)),
            step("query", "click"),
        ],
    )
    yield dict(
        responses={"readApp": [read(), read("2", 2)]},
        steps=base
        + [
            dict(op="workingWindow", target="app", ref="2", assign="working"),
            step("working", "read"),
            step("working", "pressKey", "Return"),
        ],
    )


def value(value):
    if isinstance(value, (Computer, App, Element)):
        return dict(handle=type(value).__name__)
    return normalize(value)


async def execute(spec):
    images.requireImageDelivery()
    images.resetImageDelivery()
    commands, events, answers, counts = [], [], [], {}

    class Transport:
        async def request(self, method, params, options=None):
            command = dict(method=method, params=params)
            if options is not None:
                command["options"] = options
            commands.append(command)
            queue = spec["responses"].get(
                method,
                [dict(target="Fixture", status="dispatched", detail="Native action dispatched; outcome unconfirmed.")],
            )
            reply = queue[min(counts.get(method, 0), len(queue) - 1)]
            counts[method] = counts.get(method, 0) + 1
            if "error" in reply:
                raise ComputerUseError(reply["error"])
            return decodeResult(method, copy.deepcopy(reply["wire"])) if "wire" in reply else reply

    controller = Controller(lambda _: Transport())
    variables = dict(computer=computerForController(controller))
    setActionHandler(lambda receipt: events.append(dict(kind="action", receipt=normalize(receipt))))

    def observation(event):
        events.append(
            dict(
                kind="observed",
                **{
                    k: normalize(v) for k, v in event["observation"].items() if k not in ("source", "owner", "document")
                },
            )
            if event["kind"] == "observed"
            else dict(kind=event["kind"])
        )

    setObservationHandler(observation)
    try:
        for step_value in spec["steps"]:
            try:
                result = None
                if step_value.get("op") == "invalidate":
                    controller.invalidate()
                elif step_value.get("op") == "deliver":
                    images.imageDelivered(variables[step_value["target"]].screenshot)
                elif step_value.get("op") == "resetDelivery":
                    images.resetImageDelivery()
                elif step_value.get("op") == "label":
                    result = images.imageLabel(variables[step_value["target"]].screenshot)
                elif step_value.get("op") == "workingWindow":
                    result = selectWorkingWindow(variables[step_value["target"]], step_value["ref"])
                else:
                    result = getattr(variables[step_value["target"]], step_value["method"])(*step_value.get("args", []))
                    if hasattr(result, "__await__"):
                        result = await result
                if "assign" in step_value:
                    variables[step_value["assign"]] = result
                answers.append(dict(ok=True, value=value(result)))
            except Exception as error:
                answers.append(dict(ok=False, error=normalize(error)))
    finally:
        setActionHandler(None)
        setObservationHandler(None)
    return dict(commands=normalize(commands), events=events, answers=answers)


class ClientParity(unittest.IsolatedAsyncioTestCase):
    async def test_commands_results_binding_lifetimes_and_failures(self):
        inputs = list(specs())
        process = baseline("client", inputs)
        outputs = process
        self.assertEqual(len(inputs), len(outputs))
        old = images_state._deliveryRequired
        try:
            for i, (spec, expected) in enumerate(zip(inputs, outputs)):
                with self.subTest(index=i):
                    expected = pythonHints(expected)
                    # Historical snapshots accepted unusable non-string handles.
                    # The current SDK rejects them before any query or dispatch;
                    # keep every other answer, command and event comparison exact.
                    for j, item in enumerate(spec["steps"]):
                        if item.get("method") == "pressKey" and not expected["answers"][j]["ok"]:
                            # Only the documented key list grew; preserve the
                            # failure code, effect, target and dispatch history.
                            error = expected["answers"][j]["error"]
                            for field in ("message", "detail"):
                                error[field] = error[field].replace(
                                    "Use a–z, 0–9, ", "Use a–z, 0–9, punctuation -=[]\\;',./`, "
                                )
                        if item.get("method") == "element" and not isinstance(item["args"][0], str):
                            self.assertEqual(expected["answers"][j], dict(ok=True, value=dict(handle="Element")))
                            detail = "App.element expects an observed decimal reference string"
                            next_step = (
                                "read the intended App and use its element number as a string; use App.find "
                                "for a semantic "
                                "query"
                            )
                            expected["answers"][j] = dict(
                                ok=False,
                                error=dict(
                                    message=f"{detail}\nEffect: no action dispatched by this command\nNext: {next_step}",
                                    code="invalid_request",
                                    effect="none",
                                    target="request",
                                    detail=detail,
                                    next=next_step,
                                ),
                            )
                    self.assertEqual(await execute(json.loads(json.dumps(spec))), expected)
        finally:
            images_state._deliveryRequired = old
        print((f"Compared {len(inputs)} SDK operation sequences against frozen historical baselines."))


if __name__ == "__main__":
    unittest.main()
