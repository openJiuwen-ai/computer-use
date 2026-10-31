# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify focused text editing and hosted file-panel input with the source driver.

Run after computer-use-build: uv run python tests/manual/input_receiver_workflow.py --panel
The desktop uses the unified background-first pipeline with exclusive authorization.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import plistlib
import re
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def build_fixture(directory, bundle_id, name="SearchInputProbe"):
    app = directory / (name + ".app")
    binary = app / ("Contents/MacOS/" + name)
    binary.parent.mkdir(parents=True)
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleExecutable": name,
        "CFBundleName": name,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
    }
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "6",
            "-parse-as-library",
            str(ROOT / "tests/fixtures" / (name + ".swift")),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(["open", "-g", str(app)], check=True)


async def state(directory):
    for _ in range(100):
        try:
            return json.loads((directory / "state.json").read_text())
        except FileNotFoundError:
            await asyncio.sleep(0.05)
    raise TimeoutError("The fixture did not publish its initial state")


def search_ref(text):
    matches = re.findall(r"(?m)^\s*(\d+) (?:search text field|搜索文本栏|搜索文本框)\b", text)
    if len(matches) != 1:
        raise AssertionError(f"Expected one search field in the selected window, found {matches}")
    return int(matches[0])


async def verify(directory, include_panel):
    bundle_id = "local.computer-use.input-receiver." + uuid.uuid4().hex
    build_fixture(directory, bundle_id)
    initial = await state(directory)
    events, actions = [], []
    computer = Computer()
    journal = (directory / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    async def input_action(name, operation):
        start = len(events)
        receipt = await operation
        effects = [
            e["value"]
            for e in events[start:]
            if e.get("kind") == "backend_diagnostic"
            and isinstance(e.get("value"), dict)
            and e["value"].get("kind") == "input_activity"
            and e["value"].get("phase") == "willCall"
        ]
        if any(e["device"] == "mouse" for e in effects):
            raise AssertionError(f"{name} dispatched mouse input during receiver preparation")
        actions.append(
            {"name": name, "receipt": dict(receipt), "mechanisms": sorted({e["mechanism"] for e in effects})}
        )

    async def assert_search(expected):
        for _ in range(40):
            current = await state(directory)
            if current["value"] == expected and current["result"] == "Matched: " + expected:
                if current["other"] != "unchanged":
                    raise AssertionError("The unrelated input was changed")
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"Search callbacks did not receive {expected!r}: {current}")

    set_diagnostic_handler(record)
    try:
        app = computer.app(bundle_id)
        observation = await app.read()
        field = await app.element(search_ref(observation.text))
        await input_action("cold element insertion", field.type_text("one"))
        await assert_search("one")
        await input_action("move caret", field.press_key("Left"))
        await input_action("insert at caret", field.type_text("X"))
        await assert_search("onXe")
        await input_action("replace text", field.type_text("精确 query", replace=True))
        await assert_search("精确 query")
        if include_panel:
            await (await app.element("Open test panel")).click()
            observation = await app.read()
            match = re.search(r"(?m)^Window (\d+):.*\(current", observation.text)
            if not match:
                raise AssertionError("The modal file panel was not discovered")
            panel = app.window(int(match[1]))
            observation = await panel.read()
            field = await panel.element(search_ref(observation.text))
            await input_action("hosted panel insertion", field.type_text("WorkSwarm"))
            if "Value: WorkSwarm" not in (await field.read()).text:
                raise AssertionError("The hosted search field did not receive the query")
            await input_action("hosted panel replacement", field.type_text("Terminal", replace=True))
            await input_action("window receiver insertion", panel.type_text(" app"))
            if "Value: Terminal app" not in (await field.read()).text:
                raise AssertionError("The hosted search field did not receive the replacement and suffix")
        driver = Path(native_driver()["command"])
        report = {
            "sdk": computer_use.__file__,
            "driver": str(driver),
            "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "mode": os.environ.get("COMPUTER_USE_DESKTOP", "exclusive"),
            "actions": actions,
        }
        (directory / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        LOG.info("Verified %d input actions: %s", len(actions), directory / "result.json")
    finally:
        computer._controller.transport.close()  # Standalone source-driver test owns this session.
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="input-receiver-", dir=output))
    asyncio.run(verify(directory, args.panel))


if __name__ == "__main__":
    main()
