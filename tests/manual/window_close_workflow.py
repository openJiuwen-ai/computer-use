# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CU-006: normal close under screen sharing, veto and confirmation in real AppKit.

Only disposable fixture windows are operated. Native delegate callbacks verify
which window receives close and that a veto/confirmation never triggers a replay.
"""

import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from artifact_json import encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from d34_window_workflow import build_fixture, command, effect, refs, save_observation, source_fingerprint, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


async def verify(folder):
    bundle = "local.computer-use.cu006." + uuid.uuid4().hex
    fixture = build_fixture(folder, bundle)
    driver = Path(native_driver()["command"])
    report = {
        "passed": False,
        "source_before": source_fingerprint(),
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "calls": [],
    }
    computer, pid = None, None
    journal = (folder / "diagnostics.jsonl").open("w")

    def diagnostic(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=encode_artifact) + "\n")

    async def call(label, operation):
        start = time.monotonic()
        result = await asyncio.to_thread(operation)
        entry = {"label": label, "seconds": time.monotonic() - start}
        observed = getattr(result, "observation", None)
        if observed is None and hasattr(result, "screenshot"):
            observed = result
        if observed is not None:
            save_observation(folder, label, observed)
        for key in ("intent", "status", "target", "impact", "observation_status", "window_lifecycle"):
            if hasattr(result, key):
                entry[key] = getattr(result, key)
        report["calls"].append(entry)
        return result

    try:
        await asyncio.to_thread(subprocess.run, ["open", str(fixture)], check=True)
        pid = (await state(folder))["pid"]
        computer = Computer()
        set_diagnostic_handler(diagnostic)
        app = computer.app(bundle)
        directory = await call("01-directory", app.read)
        windows = {}
        for number in refs(directory.text):
            observed = await call(
                f"02-sharing-window-{number}", lambda number=number: app.read(window=number, view="both")
            )
            name = next(name for name in ("A", "B") if "Document " + name in observed.text)
            windows[name] = number
        a, b = windows["A"], windows["B"]
        report["windows"] = windows
        await command(folder, "focus", "B")
        result = await call("03-close-A-while-B-focused", lambda: app.close_window(a))
        assert result.intent == "closeWindow"
        current = await effect(folder, lambda x: x["documents"]["A"]["closed"])
        assert current["documents"]["A"]["closeRequests"] == 1
        assert not current["documents"]["B"]["closed"] and current["documents"]["B"]["closeRequests"] == 0
        try:
            await call("04-repeat-ended-A", lambda: app.close_window(a))
        except ComputerUseError as error:
            assert error.effect == "none"
            report["ended_refusal"] = {"code": error.code, "effect": error.effect}
        else:
            raise AssertionError("Ended A must not close B")
        await command(folder, "deny-close", "B")
        denied = await call("05-application-veto", lambda: app.close_window(b))
        current = await effect(folder, lambda x: x["documents"]["B"]["closeRequests"] == 1)
        assert denied.status == "dispatched" and not current["documents"]["B"]["closed"]
        await command(folder, "confirm-close", "B")
        pending = await call("06-confirmation", lambda: app.close_window(b))
        current = await effect(folder, lambda x: x["documents"]["B"]["attachedSheet"] != -1)
        assert not current["documents"]["B"]["closed"] and current["documents"]["B"]["closeRequests"] == 2
        assert "Cancel close" in pending.observation.text and "Close document" in pending.observation.text
        await call("07-cancel", lambda: app.element("Cancel close", window=b).click())
        current = await effect(folder, lambda x: x["documents"]["B"]["attachedSheet"] == -1)
        assert not current["documents"]["B"]["closed"] and current["documents"]["B"]["closeRequests"] == 2
        await call("08-request-after-cancel", lambda: app.close_window(b))
        await call("09-caller-confirms", lambda: app.element("Close document", window=b).click())
        current = await effect(folder, lambda x: x["documents"]["B"]["closed"])
        assert current["documents"]["B"]["closeRequests"] == 3
        report["final"] = current
        report["passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if computer is not None:
            await asyncio.to_thread(computer.close)
        set_diagnostic_handler(None)
        journal.close()
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        report["source_after"] = source_fingerprint()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["passed"] = report["passed"] and report["source_unchanged"]
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="cu006-window-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(directory))
    finally:
        LOG.info("CU-006 evidence: %s", directory)
