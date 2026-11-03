# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify keyboard replacement against a provider with a separate editing selection.

Run after computer-use-build: uv run python tests/manual/replacement_workflow.py
"""

import asyncio
import hashlib
import json
import logging
import os
import signal
import tempfile
from pathlib import Path

from artifact_json import encode_artifact

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


async def verify(folder):
    bundle = "local.computer-use.replacement-probe." + folder.name
    build_fixture(folder, bundle, "ReplacementInputProbe")
    initial = await state(folder)
    computer = Computer(allow_global_input=False)
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    set_diagnostic_handler(lambda value: journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n"))
    results = []
    try:
        app = computer.app(bundle)
        await asyncio.to_thread(app.read)
        field = await asyncio.to_thread(app.element, "Replacement input")
        selected = await asyncio.to_thread(app.element, "Reported full selection")
        for target, key, value in [
            (field, "input", "replacement"),
            (selected, "selected", "already selected"),
            (field, "input", ""),
            (field, "input", ""),
            (field, "input", "新的文本🙂"),
        ]:
            before = await state(folder)
            receipt = dict(await asyncio.to_thread(target.type_text, value, replace=True))
            current = await state(folder)
            already_empty = before[key]["value"] == value == ""
            assert current[key]["value"] == value, current
            assert current[key]["selectCommands"] == before[key]["selectCommands"] + (not already_empty), current
            if already_empty:
                assert current[key]["characters"] == before[key]["characters"], current
                assert receipt["status"] == "verified", receipt
            assert current[key]["selectionWrites"] == 0, current
            assert current["other"] == "unchanged", current
            results.append({"value": value, "receipt": receipt, "state": current})
        (folder / "steal-focus").touch()
        await asyncio.sleep(0.1)
        before = await state(folder)
        try:
            await asyncio.to_thread(field.type_text, "must not reach another control", replace=True)
        except ComputerUseError as error:
            results.append({"changed_receiver": {"code": error.code, "effect": error.effect}})
        else:
            raise AssertionError("Replacement must stop if Select All changes the receiver")
        current = await state(folder)
        assert current["input"]["value"] == before["input"]["value"] and current["other"] == "unchanged", current
        assert current["input"]["selectCommands"] == before["input"]["selectCommands"] + 1, current
        assert current["input"]["characters"] == before["input"]["characters"], current
        driver = Path(native_driver()["command"])
        source = ROOT / "os_adapter/macos/Sources/Control/NativeReplacementUnit.swift"
        report = {
            "sdk": computer_use.__file__,
            "global_input_allowed": False,
            "driver": str(driver),
            "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "actions": results,
        }
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
    finally:
        await asyncio.to_thread(computer.close)
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="replacement-", dir=output))
    try:
        asyncio.run(verify(folder))
    finally:
        LOG.info("Replacement evidence: %s", folder)


if __name__ == "__main__":
    main()
