# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify background title/date/menu input while an owned external app is used.

Rebuild the source driver first. This workflow uses global mouse input only to
simulate the user clicking its disposable AppKit fixture, behind an explicit
--external-click flag. The SDK itself is denied global input. No real meeting or
business document is touched. Run serially with other desktop automation.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture
from qt_cocoa_activation_workflow import DEFAULT_PYTHON, ROOT, save_observation, sha, state

FIXTURE = ROOT / "tests/fixtures/qt_foreground_continuity_probe.py"
LOG = logging.getLogger(__name__)


async def verify(folder, python):
    if sys.platform != "darwin":
        raise RuntimeError("macOS Cocoa acceptance only")
    if not python.is_file():
        raise FileNotFoundError(f"Optional PySide6 interpreter missing: {python}")
    qt_folder, external_folder = folder / "qt", folder / "external"
    qt_folder.mkdir()
    external_folder.mkdir()
    bundle = "local.computer-use.continuity." + uuid4().hex
    title = "CU Continuity " + uuid4().hex[:12]
    driver = Path(native_driver()["command"])
    head = await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
    report = {
        "head": head.strip(),
        "sdk": computer_use.__file__,
        "python": sys.executable,
        "fixture_python": str(python),
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "sdk_allow_global_input": False,
        "external_click_transport": "globalHID into verified owned AppKit fixture only",
        "actions": [],
        "passed": False,
    }
    source_paths = [
        path
        for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
        for path in sorted((ROOT / directory).rglob(pattern))
    ]
    source_paths += [
        Path(__file__),
        FIXTURE,
        ROOT / "tests/fixtures/qt_cocoa_date_probe.py",
        ROOT / "tests/fixtures/ForegroundContinuityProbe.swift",
    ]
    (folder / "source-manifest.json").write_text(json.dumps({str(p.relative_to(ROOT)): sha(p) for p in source_paths}))
    computer = Computer(allow_global_input=False)
    events, child, external_pid, closed = [], None, None, False
    with (folder / "diagnostics.jsonl").open("w") as journal, (qt_folder / "stderr.log").open("w") as errors:

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        set_diagnostic_handler(record)
        try:
            build_fixture(external_folder, bundle, "ForegroundContinuityProbe")
            external = await state(external_folder)
            external_pid = external["pid"]
            await asyncio.to_thread(
                subprocess.run, ["open", "-a", str(external_folder / "ForegroundContinuityProbe.app")], check=True
            )
            await state(external_folder, lambda value: value["foregroundPID"] == external_pid)
            previous = {app.id for app in await asyncio.to_thread(computer.list_apps) if app.is_running}
            child = await asyncio.create_subprocess_exec(
                str(python),
                str(FIXTURE),
                "--output",
                str(qt_folder),
                "--title",
                title,
                cwd=ROOT,
                env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
                stdout=errors,
                stderr=errors,
            )
            report["initial_qt"] = await state(qt_folder, lambda value: value["visible"], process=child, seconds=10)
            choices = [
                app for app in await asyncio.to_thread(computer.list_apps) if app.is_running and app.id not in previous
            ]
            report["new_applications"] = [dict(app) for app in choices]
            assert len(choices) == 1, report["new_applications"]
            app = computer.app(choices[0].id)
            save_observation(folder, "initial", await asyncio.to_thread(app.read, view="both"))

            async def invoke(name, operation):
                started, first = time.monotonic(), len(events)
                action = {"name": name}
                report["actions"].append(action)
                try:
                    action["before_external"] = await state(external_folder, lambda value: value["uptime"] >= started)
                    action["before_qt"] = await state(qt_folder, process=child)
                    assert action["before_external"]["foregroundPID"] == external_pid, action
                    result = await asyncio.to_thread(operation)
                    action["result"] = result
                    assert result.status in ("dispatched", "verified"), result
                    save_observation(folder, name, result.observation)
                    action["qt"] = await state(qt_folder, process=child)
                    action["external"] = await state(external_folder, lambda value: value["uptime"] >= started)
                    assert action["external"]["foregroundPID"] == external_pid, action
                    assert action["qt"]["guard"] == "unchanged", action
                    return result
                finally:
                    action["elapsed_seconds"] = time.monotonic() - started
                    action["native_effects"] = [
                        event["value"]
                        for event in events[first:]
                        if event.get("kind") == "backend_diagnostic"
                        and isinstance(event.get("value"), dict)
                        and event["value"].get("kind") == "input_activity"
                        and event["value"].get("phase") == "willCall"
                    ]

            async def external_click(name):
                before = await state(external_folder)
                binary = external_folder / "ForegroundContinuityProbe.app/Contents/MacOS/ForegroundContinuityProbe"
                output = await asyncio.to_thread(
                    subprocess.check_output, [str(binary), "--click", str(external_pid), bundle], text=True
                )
                after = await state(external_folder, lambda value: value["clicks"] == before["clicks"] + 1)
                assert after["foregroundPID"] == external_pid, after
                samples = []
                for _ in range(8):
                    await asyncio.sleep(0.1)
                    current = await state(qt_folder, process=child)
                    samples.append(current)
                    assert current["popup"] is not None, current
                report[name] = {"external": after, "helper": output, "qt_samples": samples}

            topic = await asyncio.to_thread(app.element, "Meeting title")
            date = await asyncio.to_thread(app.element, "Meeting date")
            # Qt launch and first provider access can activate the application.
            # Establish the measured initial condition only after those setup
            # steps, recording both sides instead of silently masking an action.
            seed = {
                "before_external": await state(external_folder),
                "before_qt": await state(qt_folder, process=child),
                "setup_event_count": len(events),
                "command": ["open", "-a", str(external_folder / "ForegroundContinuityProbe.app")],
            }
            report["foreground_seed"] = seed
            seed_started = time.monotonic()
            await asyncio.to_thread(subprocess.run, seed["command"], check=True)
            seed["after_external"] = await state(
                external_folder,
                lambda value: value["uptime"] >= seed_started and value["foregroundPID"] == external_pid,
            )
            seed["after_qt"] = await state(qt_folder, process=child)
            seed["elapsed_seconds"] = time.monotonic() - seed_started
            seed["measured_event_start"] = len(events)
            await invoke("title", lambda: topic.type_text("CUA menu continuity", replace=True))
            # Preserve the troublesome preceding date click in the measured chain.
            await invoke("date-click", date.click)
            await invoke("date-replace", lambda: date.type_text("2026/11/9", replace=True))
            await state(qt_folder, lambda value: value["date"] == value["date_model"] == "2026/11/9", process=child)
            # Cocoa exposes stock QComboBox by its displayed value rather than
            # the QWidget accessibleName; both values are unique in this form.
            hour = await asyncio.to_thread(app.element, "08:00")
            await invoke("hour-open", hour.click)
            await state(qt_folder, lambda value: value["popup"] is not None, process=child)
            save_observation(folder, "hour-read", await asyncio.to_thread(app.read, view="both"))
            await external_click("hour-external-click")
            choice = await asyncio.to_thread(app.element, "10:00")
            await invoke("hour-select", choice.click)
            await state(
                qt_folder,
                lambda value: value["hour"] == "10:00" and value["hour_activations"] == ["10:00"],
                process=child,
            )
            duration = await asyncio.to_thread(app.element, "30 minutes")
            await invoke("duration-open", duration.click)
            await external_click("duration-external-click")
            choice = await asyncio.to_thread(app.element, "60 minutes")
            await invoke("duration-select", choice.click)
            await state(qt_folder, lambda value: value["duration_activations"] == ["60 minutes"], process=child)
            # A session-ending cleanup must not undo an explicitly opened popup.
            await invoke("hour-reopen", hour.click)
            await asyncio.to_thread(computer.close)
            closed = True
            await external_click("after-session-close")
            report["final"] = await state(qt_folder, process=child)
            global_effects = [
                effect
                for action in report["actions"]
                for effect in action["native_effects"]
                if effect.get("transport") == "globalHID"
            ]
            assert not global_effects, global_effects
            assert report["final"]["topic"] == "CUA menu continuity", report["final"]
            assert not report["final"]["submissions"], "The measured chain must not submit a meeting"
            report["passed"] = True
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
            raise
        finally:
            if not closed:
                await asyncio.to_thread(computer.close)
            set_diagnostic_handler(None)
            if child is not None and child.returncode is None:
                child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 3)
                except TimeoutError:
                    child.kill()
                    await child.wait()
            if external_pid:
                os.kill(external_pid, signal.SIGTERM)
            (folder / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--external-click", action="store_true", help="Allow global clicks inside the owned external fixture"
    )
    args = parser.parse_args()
    if not args.external_click:
        parser.error("This physical outside-click acceptance requires --external-click")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = args.output or Path(tempfile.mkdtemp(prefix="foreground-continuity-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(verify(folder.resolve(), args.python.absolute()))
    finally:
        LOG.info("Foreground continuity evidence: %s", folder)


if __name__ == "__main__":
    main()
