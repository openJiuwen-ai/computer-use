# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify focused canvas forwarding and exact-field keys against real callbacks."""

import asyncio
import hashlib
import json
import logging
import os
import signal
import tempfile
import uuid
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


async def wait_state(folder, predicate):
    for _ in range(100):
        current = await state(folder)
        if predicate(current):
            assert current["other"] == "unchanged", current
            assert current["field"] == "original", current
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(current)


async def verify(folder):
    bundle = "local.computer-use.keyboard-scope." + uuid.uuid4().hex
    build_fixture(folder, bundle, "KeyboardScopeProbe")
    initial = await state(folder)
    computer = Computer()
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    actions = []

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        activation = await asyncio.create_subprocess_exec("open", "-a", str(folder / "KeyboardScopeProbe.app"))
        if await activation.wait() != 0:
            raise RuntimeError("The keyboard fixture could not be activated")
        await wait_state(folder, lambda value: value["canvasFocused"] and value["active"])
        app = computer.app(bundle)
        observation = await app.read()
        (folder / "canvas-before.txt").write_text(observation.text)
        scope = await app.element("Keyboard scope")
        receipt = dict(await scope.press_key("Left"))
        current = await wait_state(folder, lambda value: value["leftKeys"] == 1 and value["canvasFocused"])
        assert current["returnKeys"] == 0, current
        actions.append({"name": "container canvas Left", "receipt": receipt, "state": current})

        receipt = dict(await scope.focus())
        current = await wait_state(folder, lambda value: value["canvasFocused"])
        assert current["leftKeys"] == 1 and current["returnKeys"] == 0, current
        actions.append({"name": "container canvas focus", "receipt": receipt, "state": current})

        command_id = uuid.uuid4().hex
        (folder / "command.json").write_text(json.dumps({"id": command_id, "kind": "field"}))
        await wait_state(folder, lambda value: value["commandId"] == command_id and value["fieldFocused"])
        observation = await app.read()
        (folder / "field-before.txt").write_text(observation.text)
        field = await app.element("Opaque child input")
        receipt = dict(await field.press_key("Return"))
        current = await wait_state(folder, lambda value: value["returnKeys"] == 1 and value["fieldFocused"])
        assert current["leftKeys"] == 1, current
        actions.append({"name": "exact field Return with opaque child", "receipt": receipt, "state": current})

        driver = Path(native_driver()["command"])
        source_paths = [
            Path(computer_use.__file__),
            ROOT / "computer_use_engine/interaction/write/resolution/current_receiver.py",
            ROOT / "computer_use_engine/interaction/write/resolution/passes/resolve_text.py",
            ROOT / "tests/fixtures/KeyboardScopeProbe.swift",
        ]
        report = {
            "sdk": computer_use.__file__,
            "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths},
            "driver": str(driver),
            "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "actions": actions,
        }
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="keyboard-scope-", dir=output))
    try:
        asyncio.run(verify(folder))
    finally:
        LOG.info("Keyboard scope evidence: %s", folder)


if __name__ == "__main__":
    main()
