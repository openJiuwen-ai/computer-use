# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise the SDK image/action/observation chain against an independent UI counter.

No global input is authorized. The optional observation preparation may activate
only this disposable fixture. Run after rebuilding the current native driver.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


async def verify(folder, case, prepare):
    arguments = {"weak": ("--weak-ax",), "close": ("--close-on-click",)}.get(case, ())
    bundle = "local.computer-use.ga-visual." + folder.name
    build_fixture(folder, bundle, "ImageScopeProbe", arguments=arguments)
    initial = await state(folder)
    computer = Computer()
    driver = Path(native_driver()["command"])
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    events = []
    source_hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for directory, suffix in (("computer_use_engine", ".py"), ("os_adapter/macos/Sources", ".swift"))
        for p in sorted((ROOT / directory).rglob("*" + suffix))
    }
    (folder / "source-manifest.json").write_text(json.dumps(source_hashes, indent=2) + "\n")
    report = {
        "case": case,
        "explicit_observation_preparation": prepare,
        "head": (
            await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
        ).strip(),
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "allow_global_input": False,
        "initial": initial,
    }

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        app = computer.app(bundle)
        before = await state(folder)
        observation = await app.read(view="both")
        (folder / "default.txt").write_text(observation.text)
        (folder / "default.png").write_bytes(observation.screenshot)
        after_default = await state(folder)
        assert before["frontmostPID"] == after_default["frontmostPID"], (before, after_default)
        assert after_default["clicks"] == 0
        if case == "weak":
            assert "Count action" not in observation.text, observation.text

        if prepare:
            prepared = await app.read(view="both", allow_foreground=True)
            (folder / "prepared.txt").write_text(prepared.text)
            (folder / "prepared.png").write_bytes(prepared.screenshot)
            assert prepared.preparation, "Explicit preparation effects must be available"
            for _ in range(40):
                ready = await state(folder)
                if ready["frontmostPID"] == initial["pid"]:
                    break
                await asyncio.sleep(0.05)
            assert ready["frontmostPID"] == initial["pid"], ready
            assert ready["clicks"] == 0, "Observation preparation must not activate a business control"

        # Ground-truth fixture geometry is independent of SDK AX element lookup.
        # Convert through the actual delivered App composite, just as a caller
        # selecting a point in the delivered screenshot would do.
        image = app._image
        root_surface = computer._controller.state(app).selection.root.surface
        piece = next(p for p in image.pieces if p.image.target.surface == root_surface)
        x, y, width, height = piece.box
        point = (
            x + initial["buttonPoint"][0] * width / initial["width"],
            y + initial["buttonPoint"][1] * height / initial["height"],
        )
        report["point"] = point
        if case == "translate":
            (folder / "translate-window").touch()
            for _ in range(40):
                if (await state(folder))["translated"]:
                    break
                await asyncio.sleep(0.05)
            assert (await state(folder))["translated"]

        start = time.monotonic()
        result = await app.click(at=point)
        report["command_elapsed_seconds"] = time.monotonic() - start
        report["receipt"] = {k: v for k, v in dict(result).items() if k != "observation"}
        for _ in range(40):
            current = await state(folder)
            if current["clicks"] == 1:
                break
            await asyncio.sleep(0.05)
        assert current["clicks"] == 1, current
        assert result.status in ("dispatched", "verified")
        if result.observation is not None:
            (folder / "after.txt").write_text(result.observation.text)
            if result.observation.screenshot:
                (folder / "after.png").write_bytes(result.observation.screenshot)
        if case == "close":
            assert current["rootClosed"]
            assert result.observation_status == "unavailable"
            assert result.window_lifecycle in ("closed", "unknown")
            assert result.observation_error, "Missing pixels/lifecycle evidence must be explicit"
        else:
            assert result.observation is not None, dict(result)
            (folder / "after.txt").write_text(result.observation.text)
            assert result.observation.screenshot, "Image action must return usable related pixels"
            (folder / "after.png").write_bytes(result.observation.screenshot)
        activities = [
            e["value"]
            for e in events
            if e.get("kind") == "backend_diagnostic"
            and isinstance(e.get("value"), dict)
            and e["value"].get("kind") == "input_activity"
        ]
        report["input_activity"] = activities
        report.update(final=current, passed=True)
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        report["final"] = await state(folder)
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        journal.close()
        try:
            os.kill(initial["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("good", "weak", "translate", "close"), default="good")
    parser.add_argument(
        "--prepare", action="store_true", help="Explicitly prepare the original window before observing"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = Path(tempfile.mkdtemp(prefix="ga-visual-" + args.case + "-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, args.case, args.prepare))
    finally:
        LOG.info("GA visual evidence: %s", folder)


if __name__ == "__main__":
    main()
