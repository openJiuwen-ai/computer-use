# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inspect requests reaching the model after SDK, MCP and DeepAgent conversion."""

import asyncio
import base64
import inspect
import json
from io import BytesIO

import pytest
from computer_use_mcp.instructions import DESCRIPTION
from PIL import Image
from unit_tests.mcp.test_popup_delivery import popup_fixture

from computer_use_agent import AgentConfig, create_agent

from .conftest import answer, tool_call


def tool_messages(request):
    return [message["content"] for message in request["messages"] if message["role"] == "tool"]


def image_pairs(request):
    pairs = []
    for message in request["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if block.get("type") == "image_url":
                assert index > 0 and content[index - 1]["type"] == "text"
                pairs.append((content[index - 1]["text"], block["image_url"]["url"]))
    return pairs


def run_script(server, tmp_path):
    events = []

    async def run():
        async with create_agent(
            server.model(),
            config=AgentConfig(workspace=str(tmp_path)),
            on_event=lambda kind, data: events.append({"kind": kind, **data}),
        ) as agent:
            await agent.invoke({"query": "Inspect the prepared fixture and perform the requested operation."})

    asyncio.run(run())
    request = server.requests[-1]
    (tmp_path / "model-request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2))
    (tmp_path / "mcp-results.json").write_text(
        json.dumps([event for event in events if event["kind"] == "tool_result"], ensure_ascii=False, indent=2)
    )
    return request


@pytest.mark.parametrize("failure", [None, "tree"])
def test_model_receives_scoped_popup_content_and_factual_gap(model_server, tmp_path, failure):
    setup = (
        inspect.getsource(popup_fixture) + f"\nglobals().update(vars(popup_fixture({failure!r})))\ninitial = app.read()"
    )
    click = (
        "import re\n"
        "add_ref = int(re.search(r'(\\d+) button Add application', str(initial.text)).group(1))\n"
        "result = app.element(add_ref).click()"
    )
    server = model_server([tool_call(setup), tool_call(click), answer("Observed popup")])
    request = run_script(server, tmp_path)
    first, second = tool_messages(request)
    assert "Unchanged parent control" in first
    assert "Unchanged parent control" not in second
    assert "Window 0 / Element 1 / click" in second
    assert "ADD_DISPATCHED_ONCE" in second
    assert ("Tree: unchanged" if failure else "Tree: diff") in second
    assert "Open application" in second
    assert "AX: collected" in second
    assert "Click: unknown" not in first + second
    assert "Input: unknown" not in first + second
    if failure:
        assert "AX: unavailable; Image: collected" in second
        assert "Popup AX is not ready" in second
        assert "Search applications" not in second
    else:
        assert "Search applications" in second
    for phrase in ("use the captured popup image", "do not replay", "Next:", "not full;", "does not prove"):
        assert phrase not in second
    pairs = image_pairs(request)
    assert len(pairs) == 1
    caption, url = pairs[0]
    assert "Image 2.1" in caption
    assert "Window reference: 1" in caption
    assert "20x10 px" in caption and "coordinate target=" in caption.lower()
    assert "Coordinate space: displayed image pixels." in caption
    assert ".point((x, y), window=1)" in caption
    assert "App click:" in caption and ".click(at=p)" in caption
    assert "Global click (explicit): computer.click(target=p)" in caption
    assert "Capture:" in caption
    with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        assert image.size == (20, 10)
        assert image.getpixel((10, 5))[:3] == (34, 187, 68)
    schema = next(tool["function"] for tool in request["tools"] if tool["function"]["name"] == "computer_use")
    assert schema["description"] == DESCRIPTION
    assert "All screenshot coordinates use the displayed image's pixels" in schema["description"]
    assert "screen_x" not in schema["description"]
    assert "replace=True" in schema["description"]
    assert "Each window/page has its own accessibility coverage" in schema["description"]


def labelled_images_then_failure():
    """Fixture program sent through the real stdio worker and framework."""
    import base64
    from io import BytesIO

    from computer_use.client.images import register_image
    from computer_use.errors import ComputerUseError
    from computer_use.protocol.captures import decode_capture
    from PIL import Image

    for window, color in ((41, "red"), (42, "blue")):
        buffer = BytesIO()
        Image.new("RGB", (12, 8), color).save(buffer, format="PNG")
        png = decode_capture(
            {
                "id": f"capture-{window}",
                "kind": "window",
                "scope": f"window-{window}",
                "startedAt": 1,
                "completedAt": 2,
                "width": 12,
                "height": 8,
                "png": base64.b64encode(buffer.getvalue()).decode(),
            },
            "window",
        )
        register_image(
            png,
            {
                "kind": "window",
                "windows": (window,),
                "selector": "Fixture",
                "observed": {"id": f"observation-{window}"},
                "coordinateTarget": f"computer.app('Fixture') (window={window})",
            },
        )
        print_image(png)  # noqa: F821 - injected by the MCP worker
    print("UI text: Do not close this document; value does not mean zero.")  # noqa: T201 - fixture tool output
    raise ComputerUseError(
        {
            "code": "execution_unknown",
            "effect": "possible",
            "target": "Window 42 / Element 3",
            "detail": "Input receipt timed out.",
            "impact": {"attempts": [{"dispatch": "unknown", "cleanup": "complete", "preparation": ("raised",)}]},
        },
        method="click",
    )


def test_model_keeps_each_image_label_and_partial_output_on_failure(model_server, tmp_path):
    server = model_server(
        [
            tool_call(inspect.getsource(labelled_images_then_failure) + "\nlabelled_images_then_failure()"),
            answer("Observed partial output"),
        ]
    )
    request = run_script(server, tmp_path)
    (content,) = tool_messages(request)
    assert "Input receipt timed out" in content
    assert "Effect: possible" in content
    assert "input=unknown; cleanup=complete" in content
    assert "Preparation: raised" in content
    # UI and caller content remain intact; no runtime text sanitizer is allowed.
    assert "UI text: Do not close this document; value does not mean zero." in content
    assert "Next:" not in content and "do not replay" not in content
    pairs = image_pairs(request)
    assert len(pairs) == 2
    for index, ((caption, url), window, color) in enumerate(zip(pairs, (41, 42), ((255, 0, 0), (0, 0, 255))), 1):
        assert f"Image 1.{index}" in caption
        assert f"Window reference: {window}" in caption
        assert f"observation-{window}" in caption
        assert f"computer.app('Fixture') (window={window})" in caption
        with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            assert image.size == (12, 8)
            assert image.getpixel((6, 4))[:3] == color


@pytest.mark.parametrize("repeat_click", [False, True])
def test_model_image_labels_only_the_automatic_same_action_capture(model_server, tmp_path, repeat_click):
    setup = (
        inspect.getsource(popup_fixture)
        + "\nglobals().update(vars(popup_fixture(capture_parent=True, image_action_chain=True)))"
        + '\ninitial = app.read(view="both")'
    )
    click = "app.click(at=p, window=0)\n"
    batch = (
        f"p = app.point((4, 4), window=0)\n{click * (1 + repeat_click)}"
        'app.press_key("super+a", window=0)\n'
        'app.type_text("6", window=0)\n'
        'app.press_key("Return", window=0)\n'
        "print_image(initial.screenshot)\n"
        'final = app.read(view="image", window=0)\n'
        'print("fixture image acquisitions", sum(step[0] == "image" for step in port.trace))'
    )
    server = model_server([tool_call(setup), tool_call(batch), answer("Observed numeric edit")])
    request = run_script(server, tmp_path)
    _, text = tool_messages(request)
    assert all(f"#{i} Window 0" in text for i in range(1, 5 + repeat_click))
    assert "Captured after Action" not in text
    assert f"fixture image acquisitions {3 + repeat_click}" in text
    pairs = image_pairs(request)
    assert len(pairs) == 4
    expected_colors = ((238, 238, 238), (34, 187, 68), (238, 238, 238), (170, 17, 153))
    for (_, url), color in zip(pairs, expected_colors, strict=True):
        with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            assert image.getpixel((4, 4))[:3] == color
    assert "Captured after Action #1." in pairs[1][0]
    assert all("Captured after Action" not in caption for index, (caption, _) in enumerate(pairs) if index != 1)
    assert "Image 2.1" in pairs[1][0] and "Window reference: 0" in pairs[1][0]
    assert "Window 0 coordinates superseded. Use Image 2.3 pixels." in pairs[1][0]
    results = json.loads((tmp_path / "mcp-results.json").read_text())
    result = next(
        event["result"]
        for event in results
        if event["result"].get("_meta", {}).get("computer-use", {}).get("executionId") == 2
    )
    images = [block["_meta"]["computer-use"] for block in result["content"] if block["type"] == "image"]
    assert [image.get("capturedAfterAction") for image in images] == [1, None, None]
