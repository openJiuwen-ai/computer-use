# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify a fixed element click across real mouse-hover geometry changes.

Run after computer-use-build: uv run python tests/manual/hover_pointer_workflow.py
The disposable native fixture is the only target; all mouse input must be process
directed. --driver and --expect-expand-blocked record a matching native baseline.
Loaded Python policy and relevant native source hashes distinguish versions.
"""

import argparse
import asyncio
import ctypes
import hashlib
import json
import logging
import os
import signal
import tempfile
import uuid
from pathlib import Path

from artifact_json import encode_artifact

import computer_use
import computer_use.interaction.write.progress as write_progress
import computer_use.interaction.write.routes.policy as write_policy
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state
from write_stage_workflow import wait_state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def require_window_directed_transport():
    """Refuse before fixture launch if the driver's directed symbols are absent."""
    library = ctypes.CDLL(
        "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
        mode=os.RTLD_LAZY | os.RTLD_LOCAL,
    )
    for name in ["CGEventSetWindowLocation", "SLEventSetIntegerValueField"]:
        if not getattr(library, name, None):
            raise RuntimeError("The fixture requires window-directed mouse transport: " + name)


async def verify(folder, expect_expand_blocked, driver=None, modes=None):
    require_window_directed_transport()
    bundle = "local.computer-use.hover-pointer." + uuid.uuid4().hex
    build_fixture(folder, bundle, "HoverPointerProbe")
    initial = await state(folder)
    computer = Computer()
    driver = Path(driver if driver is not None else native_driver()["command"]).resolve()
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    events, actions, global_mouse = [], [], []
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()
        effect = value.get("value") if value.get("kind") == "backend_diagnostic" else None
        if isinstance(effect, dict) and effect.get("kind") == "input_activity" and effect.get("phase") == "willCall":
            if effect.get("device") == "mouse" and effect.get("transport") != "process":
                global_mouse.append(effect)
                computer._controller.transport.close()

    sources = [
        ROOT / "os_adapter/macos/Sources/Control/NativeElementPointerUnit.swift",
        ROOT / "tests/fixtures/HoverPointerProbe.swift",
        Path(write_progress.__file__),
        Path(write_policy.__file__),
        Path(__file__).resolve(),
    ]
    report = {
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "window_directed_transport_symbols_available": True,
        "expect_expand_blocked": expect_expand_blocked,
        "actions": actions,
        "global_mouse": global_mouse,
    }
    set_diagnostic_handler(record)
    try:
        app = computer.app(bundle)
        for mode in modes or ["stable", "expand", "away", "cover"]:
            command = uuid.uuid4().hex
            (folder / "command.json").write_text(json.dumps({"id": command, "mode": mode}))
            before = await wait_state(folder, lambda value: value["lastCommand"] == command)
            assert before["downs"] == before["ups"] == 0 and not before["changed"], before
            observation = await asyncio.to_thread(app.read)
            (folder / (mode + "-before.txt")).write_text(observation.text)
            target = await asyncio.to_thread(app.element, "Hover target")
            start = len(events)
            error = None
            try:
                receipt = dict(await asyncio.to_thread(target.click))
            except ComputerUseError as caught:
                error = caught
                receipt = {"code": caught.code, "effect": caught.effect, "detail": caught.detail}
            await asyncio.sleep(0.15)
            after = await state(folder)
            effects = [
                e["value"]
                for e in events[start:]
                if e.get("kind") == "backend_diagnostic"
                and isinstance(e.get("value"), dict)
                and e["value"].get("kind") == "input_activity"
                and e["value"].get("phase") == "willCall"
            ]
            mouse = [effect for effect in effects if effect["device"] == "mouse"]
            action = {"mode": mode, "before": before, "result": receipt, "after": after, "effects": effects}
            actions.append(action)
            assert not global_mouse and mouse and all(effect["transport"] == "process" for effect in mouse), action
            assert len({(effect["point"]["x"], effect["point"]["y"]) for effect in mouse}) == 1, action
            assert after["other"] == "unchanged" and after["textWrites"] == 0, action
            assert after["decoyDowns"] == after["decoyUps"] == 0, action
            assert before["targetIdentity"] == after["targetIdentity"], action
            if mode == "expand":
                assert after["changed"] and after["frame"] != before["frame"], action
                assert after["fixedPointInTarget"] and after["viewHitIsTarget"] and after["axHitIsTarget"], action
            if mode in ["away", "cover"] or mode == "expand" and expect_expand_blocked:
                assert error is not None and after["downs"] == after["ups"] == 0, action
                assert [effect["eventType"] for effect in mouse] == [8, 5], action
            else:
                assert error is None and after["downs"] == after["ups"] == 1, action
                assert [effect["eventType"] for effect in mouse] == [8, 5, 1, 2], action
            if mode == "away":
                assert after["changed"] and not after["fixedPointInTarget"], action
            if mode == "cover":
                assert after["changed"] and after["viewHitIsDecoy"] and after["axHitIsDecoy"], action
        report["passed"] = True
    finally:
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
        await asyncio.to_thread(computer.close)
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-expand-blocked", action="store_true")
    parser.add_argument("--driver", type=Path, help="Use this retained native driver binary")
    parser.add_argument("--only", nargs="+", choices=["stable", "expand", "away", "cover"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = Path(tempfile.mkdtemp(prefix="hover-pointer-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, args.expect_expand_blocked, args.driver, args.only))
    finally:
        LOG.info("Hover pointer evidence: %s", folder)


if __name__ == "__main__":
    main()
