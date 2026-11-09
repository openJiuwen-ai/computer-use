# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise the Edge contenteditable chain via the SDK or a real stdio MCP host."""

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
import unicodedata
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from windows_browser_fixture import browser_fixture

from computer_use_agent.config import AgentConfig
from computer_use_agent.connection import McpConnection


def element_ref(text: str, expression: str) -> int:
    matches = set(re.findall(expression, text, re.MULTILINE))
    if len(matches) != 1:
        raise RuntimeError("The fixture control could not be uniquely located in delivered UIA facts")
    return int(next(iter(matches)))


def applied_line(lines, marker: str) -> str | None:
    """Match the displayed apply count and fixture nonce despite OCR spacing."""
    def compact(value):
        return "".join(character for character in unicodedata.normalize("NFKC", value).casefold() if character.isalnum())

    expected = compact("Applied 1: " + marker)
    return next((line for line in lines if compact(line).startswith(expected)), None)


def sdk_image_lines(observation, ref: int) -> list[str]:
    """Read OCR for the exact displayed window from public observation details."""
    prefix = "Captured image metadata: "
    content, cursor = "", None
    for _ in range(16):
        page = observation.read_details(cursor=cursor, length=16384)
        content += page.text
        if prefix in content:
            try:
                header, _ = json.JSONDecoder().raw_decode(content.split(prefix, 1)[1])
            except json.JSONDecodeError:
                pass  # A details page may end in the middle of the metadata.
            else:
                return [
                    line["text"]
                    for image in header["captures"]
                    if image["ref"] == ref and image["displayed"] and image["scopeConfirmed"]
                    for line in (image["capture"] or {}).get("visualText", {}).get("lines", ())
                ]
        cursor = page.next
        if cursor is None:
            break
    return []


def mcp_image_lines(blocks, ref: int) -> list[str]:
    """Consume only an OCR caption tied to an actual delivered PNG block."""
    lines = []
    for index, block in enumerate(blocks):
        if block.type != "image" or block.mimeType != "image/png" or not block.data:
            continue
        meta = block.model_dump(by_alias=True).get("_meta") or {}
        caption = meta.get("computer-use", {}).get("caption")
        if not caption and index and blocks[index - 1].type == "text":
            caption = blocks[index - 1].text
        if not caption or f"Window reference: {ref}" not in caption.splitlines():
            continue
        visual = caption.partition("Visual text (Windows OCR")[2]
        for match in re.finditer(r'(?m)^\s*("(?:[^"\\]|\\.)*")\s+\([\d.,\s-]+\)$', visual):
            lines.append(json.loads(match[1]))
    return lines


async def workflow(call, image_lines, title: str, draft: str, report: dict) -> None:
    desktop = await call('print(computer.read(view="image", full=True).text)')
    row = next((line for line in desktop.splitlines() if title in line and "application:" in line), None)
    if row is None:
        raise RuntimeError("The exact browser window was absent from the delivered desktop catalogue")
    report["desktop_row"] = row
    app = re.search(r'computer\.app\((\d+)\)|app=(".*")$', row)
    if app is None:
        raise RuntimeError("The browser row did not publish an actionable App")
    selector = int(app[1]) if app[1] is not None else json.loads(app[2])
    directory = await call(f'app = computer.app({selector!r})\nprint(app.read(view="tree", full=True).text)')
    window = re.search(rf'(?m)^Window (\d+): "{re.escape(title)}(?:"|[ \t])', directory)
    if window is None:
        raise RuntimeError("The observed App does not contain the requested browser window")
    ref = int(window[1])
    tree = await call(f'print(app.read(view="tree", window={ref}, full=True).text)')
    editor_ref = element_ref(tree, r"^[ \t]*(\d+)\s+[^\n]*\(text-input\) Draft editor\b")
    apply_ref = element_ref(tree, r"^[ \t]*(\d+)\s+[^\n]*\(read-only\) Apply draft$")
    report["edit_receipts"] = await call(
        f'editor = app.element({editor_ref}, window={ref})\n'
        'print(editor.click().status)\n'
        'print(editor.press_key("ctrl+a").status)\n'
        f'print(editor.paste({draft!r}).status)'
    )
    started = time.monotonic()
    report["apply_receipt"] = await call(
        f'button = app.element({apply_ref}, window={ref})\n'
        'action = button.click()\nprint(action.status, action.observation_status)\n'
        'print(action.observation_error)'
    )
    report["apply_seconds"] = round(time.monotonic() - started, 3)
    report["related_observation_retired"] = "retired" in report["apply_receipt"].lower()
    started = time.monotonic()
    observed = await call(
        f'observation = app.read(view="both", window={ref}, full=True)\n'
        'print(observation.text)\nprint("CUA_FRESH_IMAGE:", observation.screenshot is not None)'
    )
    report["fresh_read_seconds"] = round(time.monotonic() - started, 3)
    report["fresh_image"] = "CUA_FRESH_IMAGE: True" in observed
    marker = draft.splitlines()[0]
    tree = re.split(r"(?m)^Image \d+\.\d+\s*$", observed, maxsplit=1)[0]
    line = next((entry for entry in tree.splitlines() if f"Applied 1: {marker}" in entry), None)
    report["verified_in_public_tree"] = line is not None
    source = "tree"
    if line is None and report["fresh_image"]:
        line = applied_line(image_lines(ref), marker)
        source = "image_ocr"
    report["verified_in_public_read"] = line is not None
    if line is not None:
        report["verification_evidence"] = {"source": source, "status_line": line}


async def mcp_workflow(title, draft, report):
    connection = McpConnection(AgentConfig().mcp_config())
    delivered = []
    try:
        async def call(code):
            result = await connection.call("computer_use", {"title": "Browser draft", "code": code})
            delivered[:] = result.content
            text = "\n".join(block.text for block in result.content if block.type == "text")
            if result.isError:
                raise RuntimeError(text)
            return text

        await workflow(call, lambda ref: mcp_image_lines(delivered, ref), title, draft, report)
    finally:
        await connection.close()


def run(mcp: bool) -> dict:
    nonce = uuid.uuid4().hex[:10]
    draft = f"CUA_{nonce}_browser\n中文草稿🙂"
    report = {
        "pass": False, "transport": "mcp" if mcp else "sdk",
        "provenance": startup_provenance(native_driver()["command"]),
    }
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cua-edge-") as folder, browser_fixture(
        nonce, Path(folder)
    ) as (title, state):
        try:
            if mcp:
                asyncio.run(mcp_workflow(title, draft, report))
            else:
                with Computer() as computer:
                    namespace = {"computer": computer}

                    async def call(code):
                        with redirect_stdout(StringIO()) as stream:
                            exec(code, namespace)  # noqa: S102 - fixed fixture code and quoted test values
                        return stream.getvalue()

                    asyncio.run(workflow(
                        call, lambda ref: sdk_image_lines(namespace["observation"], ref), title, draft, report,
                    ))
        except (ComputerUseError, RuntimeError, ValueError, TimeoutError) as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
        report["actual"] = state()
    actual = report["actual"]
    report["seconds"] = round(time.monotonic() - started, 3)
    report["pass"] = (
        report.get("verified_in_public_read") is True and report.get("fresh_image") is True
        and report.get("related_observation_retired") is False and actual.get("text") == actual.get("saved") == draft
        and actual.get("applied") == 1 and actual.get("other") == "untouched"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.mcp)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
