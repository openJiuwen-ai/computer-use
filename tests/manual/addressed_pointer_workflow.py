# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Confirm coordinate input reaches an AppKit button without global mouse events."""

import asyncio
import hashlib
import json
import logging
import os
import signal
import tempfile
import uuid
from pathlib import Path

from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state
from write_stage_workflow import ROOT, Recorder, wait_state


async def main():
    directory = Path(tempfile.mkdtemp(prefix="addressed-pointer-", dir=ROOT / ".artifacts"))
    bundle = "local.computer-use.addressed-pointer." + uuid.uuid4().hex
    build_fixture(directory, bundle, "WriteOperationsProbe")
    initial = await state(directory)
    recorder = Recorder(directory)
    computer = Computer()
    set_diagnostic_handler(recorder.record)
    try:
        current = await wait_state(directory, lambda value: "buttonPoint" in value)
        app = computer.app(bundle)
        observation = await app.read(view="both")
        (directory / "before.png").write_bytes(observation.screenshot)
        await recorder.action("addressed coordinate button", app.click(at=tuple(current["buttonPoint"])), mouse=True)
        current = await wait_state(directory, lambda value: value["buttonClicks"] == 1)
        assert current["other"] == initial["other"]
        effects = recorder.actions[-1]["effects"]
        mouse = [effect for effect in effects if effect["device"] == "mouse"]
        assert mouse and all(effect["transport"] == "process" for effect in mouse)
        image = await app.read(view="image")
        (directory / "after.png").write_bytes(image.screenshot)
        driver = Path(native_driver()["command"])
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
                    "state": current,
                    "actions": recorder.actions,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        logging.info("Verified addressed pointer callback: %s", directory / "result.json")
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        recorder.journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
