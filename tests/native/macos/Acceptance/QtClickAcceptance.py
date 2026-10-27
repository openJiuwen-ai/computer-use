"""Interactive, opt-in real Qt click acceptance using the current public SDK.

Run with ``COMPUTER_USE_DIAGNOSE_INPUT=1 COMPUTER_USE_FOREGROUND_ASSIST=0
uv run --with PySide6-Essentials python
tests/native/macos/Acceptance/QtClickAcceptance.py``. Inspect each saved
image before sending a JSON command on stdin. Commands are ``click`` (with
``at``), ``activateTarget``, and ``finish``. The two click calls are independent
operations; this runner never automatically retries an unconfirmed click.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler
from computer_use.paths import nativeDriver

ROOT = Path(__file__).resolve().parents[4]


def source_manifest() -> dict:
    paths = sorted(
        path
        for path in (ROOT / "computer_use_engine").rglob("*")
        if path.suffix in (".py", ".swift") and ".build" not in path.parts and "__pycache__" not in path.parts
    )
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return {"sourceSetSha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), "files": hashes}


async def main() -> None:
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == "shared"
    assert os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "0", (
        "fixture requires background-first, no automatic foreground assistance"
    )
    folder = ROOT / ".artifacts/qt-click-acceptance" / str(time.time_ns())
    folder.mkdir(parents=True)
    processes: list[subprocess.Popen] = []
    streams = []
    computer = Computer()
    diagnostics = []
    initial_sources = source_manifest()
    driver = Path(nativeDriver()["command"])
    report = {
        "completed": False,
        "folder": str(folder),
        "head": subprocess.check_output(
            [
                resolve_executable(
                    "git",
                    cwd=ROOT,
                ),
                "rev-parse",
                "HEAD",
            ],
            cwd=ROOT,
            text=True,
        ).strip(),
        "sdkModule": computer_use.__file__,
        "python": sys.executable,
        "driver": str(driver),
        "driverSha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "sourceSetSha256": initial_sources["sourceSetSha256"],
        "phases": [],
    }
    (folder / "sources-before.json").write_text(json.dumps(initial_sources, indent=2))

    def diagnostic(event):
        diagnostics.append(event)
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def state(role):
        return json.loads((folder / role / "state.json").read_text())

    async def wait_for(predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError("fixture condition did not become true")

    async def activate(role):
        (folder / role / "command.txt").write_text("activate")
        await wait_for(lambda: state(role)["foregroundPid"] == state(role)["pid"])
        await asyncio.sleep(0.15)

    setDiagnosticHandler(diagnostic)
    try:
        for role in ("target", "decoy"):
            destination = folder / role
            destination.mkdir()
            stream = (destination / "process.log").open("w")
            streams.append(stream)
            processes.append(
                subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("QtClickProbe.py")), str(destination), role],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            )
            await wait_for(lambda *, destination=destination: (destination / "state.json").exists())
        await activate("decoy")
        entries = await computer.listApps()
        names = [{"id": entry.id, "displayName": entry.displayName} for entry in entries]
        (folder / "apps.json").write_text(json.dumps(names, indent=2, ensure_ascii=False))
        matches = [entry for entry in entries if state("target")["title"] in entry.displayName]
        assert len(matches) == 1, {"expectedTitle": state("target")["title"], "entries": names}
        target = computer.app(matches[0].id)
        phase = "background"

        async def observe():
            observed = await target.read(full=True, view="both")
            assert observed.screenshot is not None
            (folder / f"{phase}.png").write_bytes(observed.screenshot)
            (folder / f"{phase}.txt").write_text(observed.text)
            print(
                json.dumps(
                    {
                        "ready": phase,
                        "image": str(folder / f"{phase}.png"),
                        "target": state("target"),
                        "decoy": state("decoy"),
                    }
                ),
                flush=True,
            )

        await observe()
        completed_phases = set()
        while line := await asyncio.to_thread(sys.stdin.readline):
            command = json.loads(line)
            if command["command"] == "finish":
                break
            if command["command"] == "activateTarget":
                assert completed_phases == {"background"}
                await activate("target")
                phase = "hid"
                await observe()
                continue
            assert command["command"] == "click" and phase not in completed_phases
            # Human review may move another application to the foreground. Background delivery
            # only requires the target to stay background; the HID experiment
            # explicitly sets up its own foreground immediately before the call.
            if phase == "hid" and state("target")["foregroundPid"] != state("target")["pid"]:
                await activate("target")
            before = {"target": state("target"), "decoy": state("decoy")}
            assert (before["target"]["foregroundPid"] == before["target"]["pid"]) == (phase == "hid")
            start = len(diagnostics)
            outcome = {}
            try:
                result = await target.click(at=tuple(command["at"]))
                outcome = {"status": result.status, "detail": result.detail}
            except Exception as error:
                outcome = {"error": repr(error)}
            await asyncio.sleep(1)
            after = {"target": state("target"), "decoy": state("decoy")}
            attempts = [
                event
                for event in diagnostics[start:]
                if event.get("kind") == "command_end" and event.get("method") == "execute"
            ]
            item = {
                "phase": phase,
                "coordinate": command["at"],
                "before": before,
                "after": after,
                "outcome": outcome,
                "executeResults": attempts,
            }
            item["clickedDelta"] = after["target"]["counts"]["target"] - before["target"]["counts"]["target"]
            item["foregroundUnchanged"] = after["target"]["foregroundPid"] == before["target"]["foregroundPid"]
            item["released"] = (
                not any(after["target"]["globalButtons"].values())
                and after["target"]["qtMouseButtons"] == 0
                and not after["target"]["buttons"]["target"]["down"]
            )
            item["unrelatedUntouched"] = (
                all(
                    after[role]["counts"]["sentinel"] == before[role]["counts"]["sentinel"]
                    and after[role]["unrelatedText"] == before[role]["unrelatedText"]
                    for role in ("target", "decoy")
                )
                and after["decoy"]["counts"] == before["decoy"]["counts"]
            )
            report["phases"].append(item)
            completed_phases.add(phase)
            (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            print(json.dumps(item, ensure_ascii=False, default=str), flush=True)
        report["completed"] = completed_phases == {"background", "hid"}
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        setDiagnosticHandler(None)
        for role in ("target", "decoy"):
            destination = folder / role
            if destination.exists():
                (destination / "command.txt").write_text("close")
        await asyncio.sleep(0.2)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()
        report["fixturesExited"] = all(process.poll() is not None for process in processes)
        final_sources = source_manifest()
        report["sourceUnchanged"] = final_sources["sourceSetSha256"] == initial_sources["sourceSetSha256"]
        report["driverUnchanged"] = hashlib.sha256(driver.read_bytes()).hexdigest() == report["driverSha256"]
        (folder / "sources-after.json").write_text(json.dumps(final_sources, indent=2))
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print(
            json.dumps(
                {
                    "finished": str(folder),
                    "completed": report["completed"],
                    "fixturesExited": report["fixturesExited"],
                    "sourceUnchanged": report["sourceUnchanged"],
                    "driverUnchanged": report["driverUnchanged"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
