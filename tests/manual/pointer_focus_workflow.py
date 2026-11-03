# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check real pointer-focus callbacks with a read-only AX selected range.

Run after computer-use-build: uv run python tests/manual/pointer_focus_workflow.py
Only the disposable fixture is changed; all mouse events must use process transport.
Focus establishes the receiver without preserving the old caret; insert and paste
must stop when that caret cannot be preserved, while replace owns its full selection.
Use --expect-replace-blocked only with the matching baseline Python route history
and driver. A retained driver alone may continue through another ready route
after the Python preparation-history fix, so it does not reproduce that baseline.
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
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state
from write_stage_workflow import wait_state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def require_window_directed_transport():
    """Use the native adapter's exact symbol admission before any UI input."""
    library = ctypes.CDLL(
        "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight",
        mode=os.RTLD_LAZY | os.RTLD_LOCAL,
    )
    for name in ["CGEventSetWindowLocation", "SLEventSetIntegerValueField"]:
        if not getattr(library, name, None):
            raise RuntimeError("The fixture requires window-directed mouse transport: " + name)


async def verify(folder, expect_replace_blocked, driver=None):
    require_window_directed_transport()
    bundle = "local.computer-use.pointer-focus." + uuid.uuid4().hex
    build_fixture(folder, bundle, "NativePointerFocusProbe")
    initial = await state(folder)
    computer = Computer()
    if driver is not None:
        computer._controller.transport.driver = {"command": str(driver), "args": []}
    events, actions = [], []
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    driver = Path(driver if driver is not None else native_driver()["command"])
    sources = [
        Path(__file__).resolve(),
        ROOT / "os_adapter/macos/Sources/Control/NativePointerFocusUnit.swift",
        ROOT / "os_adapter/macos/Sources/Control/Routes/Passes/BuildNativePointerFocusRoute.swift",
        ROOT / "tests/fixtures/NativePointerFocusProbe.swift",
    ]
    report = {
        "sdk": computer_use.__file__, "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "python_progress": {
            "module": write_progress.__file__,
            "sha256": hashlib.sha256(Path(write_progress.__file__).read_bytes()).hexdigest(),
        },
        "window_directed_transport_check": {
            "symbols": ["CGEventSetWindowLocation", "SLEventSetIntegerValueField"],
            "available": True, "scope": "transport capability only; receiver and delivery remain live checks",
        },
        "expect_replace_blocked": expect_replace_blocked, "actions": actions,
    }
    set_diagnostic_handler(record)
    try:
        app = computer.app(bundle)
        for name in ["replace", "insert", "paste", "focus"]:
            command = uuid.uuid4().hex
            (folder / "command.json").write_text(json.dumps({"id": command}))
            before = await wait_state(folder, lambda value: value["lastCommand"] == command)
            await app.read()
            field = await app.element("Pointer focus input")
            start = len(events)
            error = None
            try:
                if name == "replace":
                    receipt = dict(await field.type_text("replacement", replace=True))
                elif name == "insert":
                    receipt = dict(await field.type_text("I"))
                elif name == "paste":
                    receipt = dict(await field.paste("P"))
                else:
                    receipt = dict(await field.focus())
            except ComputerUseError as caught:
                error = caught
                receipt = {"code": caught.code, "effect": caught.effect, "detail": caught.detail}
            await asyncio.sleep(0.1)
            after = await state(folder)
            effects = [
                e["value"] for e in events[start:]
                if e.get("kind") == "backend_diagnostic" and isinstance(e.get("value"), dict)
                and e["value"].get("kind") == "input_activity" and e["value"].get("phase") == "willCall"
            ]
            mouse = [effect for effect in effects if effect["device"] == "mouse"]
            actions.append({"name": name, "before": before, "result": receipt, "after": after, "effects": effects})
            assert mouse and all(effect["transport"] == "process" for effect in mouse), actions[-1]
            assert after["mouseDowns"] == 1 and after["focused"], actions[-1]
            assert after["other"] == "unchanged" and after["valueWrites"] == 0, actions[-1]
            assert after["selectedTextWrites"] == 0, actions[-1]
            if name == "replace" and not expect_replace_blocked:
                assert error is None, actions[-1]
                assert after["value"] == "replacement" and after["selectCommands"] == 1, actions[-1]
                assert after["textCallbacks"] > 0, actions[-1]
            elif name == "focus":
                keyboard = [effect for effect in effects if effect["device"] == "keyboard"]
                selection_setters = [
                    effect for effect in effects
                    if effect["transport"] == "accessibility"
                    and effect["mechanism"] in {
                        "AXSelectedTextRange", "AXSelectedTextRanges", "AXSelectedTextMarkerRange",
                    }
                ]
                assert error is None and receipt["status"] == "verified", actions[-1]
                assert after["value"] == before["value"] == "original", actions[-1]
                assert after["other"] == before["other"], actions[-1]
                assert [effect["eventType"] for effect in mouse if effect["eventType"] in (1, 2)] == [1, 2], actions[-1]
                assert not selection_setters, actions[-1]
                assert after["selectCommands"] == 0 and after["textCallbacks"] == 0 and not keyboard, actions[-1]
            else:
                # Pointer preparation moved the caret, so the overall receipt
                # may correctly retain a possible preparation effect. The
                # native callback counters prove requested content was not sent.
                assert error is not None and error.code == "input_unavailable", actions[-1]
                assert after["value"] == "original" and after["range"] == [0, 0], actions[-1]
                assert after["selectCommands"] == 0 and after["textCallbacks"] == 0, actions[-1]
        report["passed"] = True
    finally:
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-replace-blocked", action="store_true",
        help="Expect the original failure only with corresponding baseline Python history and native driver",
    )
    parser.add_argument("--driver", type=Path, help="Use this retained native driver binary")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = Path(tempfile.mkdtemp(prefix="pointer-focus-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, args.expect_replace_blocked, args.driver))
    finally:
        LOG.info("Pointer focus evidence: %s", folder)


if __name__ == "__main__":
    main()
