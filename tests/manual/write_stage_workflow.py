# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Opt-in macOS acceptance of preparation, final validation and single dispatch.

Run after computer-use-build: uv run python tests/manual/write_stage_workflow.py
Only disposable fixture windows are changed. Callback state is the effect oracle.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def observed_ref(observation, line):
    match = re.search(r"(?m)^\s*(\d+) " + line + r"$", observation.text)
    if not match:
        raise AssertionError(f"The expected control is absent: {line}\n{observation.text}")
    return int(match[1])


class Recorder:
    def __init__(self, directory):
        self.events, self.actions = [], []
        self.journal = (directory / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(self, value):
        self.events.append(value)
        self.journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        self.journal.flush()

    async def action(self, name, operation, *, refusal=False, mouse=False):
        start = len(self.events)
        try:
            result = dict(await operation)
        except ComputerUseError as error:
            result = {"code": error.code, "effect": error.effect, "detail": error.detail}
            if not refusal or error.effect != "none":
                raise
        else:
            if refusal:
                raise AssertionError(f"{name} should have refused before input")
        effects = [
            e["value"]
            for e in self.events[start:]
            if e.get("kind") == "backend_diagnostic"
            and isinstance(e.get("value"), dict)
            and e["value"].get("kind") == "input_activity"
            and e["value"].get("phase") == "willCall"
        ]
        mouse_events = [e for e in effects if e["device"] == "mouse"]
        if not mouse and mouse_events:
            raise AssertionError(f"{name} sent unexpected mouse input: {mouse_events}")
        if refusal and effects:
            raise AssertionError(f"{name} produced side effects despite terminal evidence: {effects}")
        self.actions.append({"name": name, "result": result, "effects": effects})
        return result


async def wait_state(directory, predicate):
    for _ in range(100):
        value = await state(directory)
        if predicate(value):
            return value
        await asyncio.sleep(0.05)
    raise AssertionError(f"Fixture state did not reach the expected effect: {value}")


async def coarse_pointer(directory, recorder):
    directory.mkdir()
    bundle_id = "local.computer-use.write-stages." + uuid.uuid4().hex
    build_fixture(directory, bundle_id, "CoarsePointerProbe")
    initial = await state(directory)
    if initial["active"]:
        raise AssertionError("Coarse-hit acceptance must start with a background window")
    try:
        with patch.dict(os.environ, COMPUTER_USE_DESKTOP="exclusive"):
            computer = Computer()
            try:
                app = computer.app(bundle_id)
                observation = await app.read()
                option = await app.element(observed_ref(observation, "text 45 minutes"))
                await recorder.action("coarse hit prepare then click", option.click(), mouse=True)
                mouse = [e for e in recorder.actions[-1]["effects"] if e["device"] == "mouse"]
                assert [e["eventType"] for e in mouse if e["eventType"] in (1, 2)] == [1, 2]
                assert all(e["stage"] == "input" for e in mouse)
                await wait_state(directory, lambda v: v["optionClicks"] == 1 and v["guardClicks"] == 0)
                (directory / "command.json").write_text(json.dumps({"id": uuid.uuid4().hex, "kind": "sibling"}))
                await wait_state(directory, lambda v: v["mode"] == "sibling")
                await recorder.action("known sibling hit", option.click(), refusal=True)
                current = await state(directory)
                assert current["optionClicks"] == 1 and current["guardClicks"] == 0
            finally:
                computer._controller.transport.close()
                await computer._controller.transport.wait_closed()
    finally:
        os.kill(initial["pid"], signal.SIGTERM)


async def operations(directory, recorder):
    directory.mkdir()
    bundle_id = "local.computer-use.write-operations." + uuid.uuid4().hex
    build_fixture(directory, bundle_id, "WriteOperationsProbe")
    initial = await state(directory)
    with patch.dict(os.environ, COMPUTER_USE_DESKTOP="exclusive"):
        computer = Computer()
        try:
            app = computer.app(bundle_id)
            observation = await app.read()
            (directory / "before.txt").write_text(observation.text)
            button = await app.element("Count action")
            popup = await app.element("Test choices")
            await recorder.action("independent native action", button.perform_action("AXPress"))
            current = await wait_state(directory, lambda v: v["buttonClicks"] == 1)
            assert current["active"] == initial["active"]
            field = await app.element("Primary input")
            await recorder.action("element insertion", field.type_text("one"))
            await wait_state(directory, lambda v: v["field"] == "one")
            await recorder.action("element caret key", field.press_key("Left"))
            await recorder.action("insert at current caret", field.type_text("X"))
            await wait_state(directory, lambda v: v["field"] == "onXe")
            await recorder.action("element replacement", field.type_text("精确 input", replace=True))
            await wait_state(directory, lambda v: v["field"] == "精确 input")
            editor = await app.element("Paste editor")
            await recorder.action("explicit element focus", editor.focus())
            current = await wait_state(directory, lambda v: v["editorFocused"])
            clipboard_before = current["clipboardDigest"]
            await recorder.action("element paste", editor.paste("paste 精确"))
            await wait_state(
                directory,
                lambda v: v["editor"] == "paste 精确" and v["pastes"] == 1 and v["clipboardDigest"] == clipboard_before,
            )
            await recorder.action("window current receiver insertion", app.type_text(" suffix"))
            await wait_state(directory, lambda v: v["editor"] == "paste 精确 suffix")
            await recorder.action("semantic element click", button.click())
            await wait_state(directory, lambda v: v["buttonClicks"] == 2)
            area = await app.element("Test scroll area")
            await recorder.action("element scroll", area.scroll("down", pages=0.5), mouse=True)
            current = await wait_state(directory, lambda v: v["scrollY"] > 10)
            old_offset = current["scrollY"]
            window = app.window(0)
            image = await window.read(view="image")
            (directory / "scroll-before.png").write_bytes(image.screenshot)
            await recorder.action(
                "coordinate wheel", window.scroll("down", at=tuple(current["scrollPoint"]), pages=0.5), mouse=True
            )
            await wait_state(directory, lambda v: v["scrollY"] > old_offset + 10)
            image = await window.read(view="image")
            (directory / "drag-before.png").write_bytes(image.screenshot)
            current = await state(directory)
            await recorder.action(
                "coordinate drag", window.drag(tuple(current["dragStart"]), tuple(current["dragEnd"])), mouse=True
            )
            await wait_state(directory, lambda v: v["dragDowns"] == 1 and v["dragMoves"] > 0 and v["dragUps"] == 1)
            await recorder.action("open menu", popup.open_menu())
            await wait_state(directory, lambda v: v["menuOpens"] == 1)
            observation = await app.read()
            (directory / "menu.txt").write_text(observation.text)
            choice = await app.element(observed_ref(observation, "menu item Second choice.*"))
            await recorder.action("choose menu item", choice.click(), mouse=True)
            await wait_state(directory, lambda v: v["menuChoice"] == "Second choice")
            current = await state(directory)
            assert current["other"] == "unchanged"
            (directory / "final.json").write_text(json.dumps(current, ensure_ascii=False, indent=2))
        finally:
            computer._controller.transport.close()
            await computer._controller.transport.wait_closed()
            os.kill(initial["pid"], signal.SIGTERM)


async def main():
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="write-stages-", dir=output))
    recorder = Recorder(directory)
    set_diagnostic_handler(recorder.record)
    try:
        await coarse_pointer(directory / "coarse-pointer", recorder)
        await operations(directory / "operations", recorder)
        driver = Path(native_driver()["command"])
        result = {
            "sdk": computer_use.__file__,
            "driver": str(driver),
            "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "actions": recorder.actions,
        }
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        LOG.info("Verified %d write-stage actions: %s", len(recorder.actions), directory / "result.json")
    finally:
        set_diagnostic_handler(None)
        recorder.journal.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
