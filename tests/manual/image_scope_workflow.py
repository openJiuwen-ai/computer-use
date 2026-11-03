# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify original screenshot coordinates after a member moves or closes."""

import argparse
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
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


async def verify(folder, change, driver=None):
    bundle = "local.computer-use.image-scope." + folder.name
    build_fixture(folder, bundle, "ImageScopeProbe")
    initial = await state(folder)
    computer = Computer()
    driver = Path(driver if driver is not None else native_driver()["command"]).resolve()
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    report = {
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "change": change,
        "initial": initial,
    }

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        app = computer.app(bundle)
        observation = await asyncio.to_thread(app.read, view="both")
        (folder / "before.txt").write_text(observation.text)
        (folder / "before.png").write_bytes(observation.screenshot)
        image = app._image
        root_surface = computer._controller.state(app).selection.root.surface
        root = next(piece for piece in image.pieces if piece.image.target.surface == root_surface)
        assert len(image.pieces) > 1, observation.text
        x, y, width, height = root.box
        point = (
            x + initial["buttonPoint"][0] * width / initial["width"],
            y + initial["buttonPoint"][1] * height / initial["height"],
        )
        report["pieces"] = len(image.pieces)
        report["point"] = point
        command, changed = {
            "move": ("move-panel", "moved"),
            "close": ("close-panel", "closed"),
            "translate": ("translate-window", "translated"),
        }[change]
        (folder / command).touch()
        for _ in range(100):
            if (await state(folder))[changed]:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("Auxiliary panel did not change")
        receipt = dict(await asyncio.to_thread(app.click, at=point))
        for _ in range(100):
            current = await state(folder)
            if current["clicks"] == 1:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(current)
        report.update(receipt=receipt, state=current, passed=True)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        report["final"] = await state(folder)
        (folder / "result.json").write_text(json.dumps(report, indent=2, default=encode_artifact) + "\n")
        await asyncio.to_thread(computer.close)
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--change", choices=("move", "close", "translate"), default="move")
    parser.add_argument("--driver", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="image-scope-", dir=output))
    try:
        asyncio.run(verify(folder, args.change, args.driver))
    finally:
        LOG.info("Image scope evidence: %s", folder)


if __name__ == "__main__":
    main()
