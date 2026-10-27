"""Opt-in disposable document-context acceptance through the editable SDK.

Each invocation launches a fresh fixture and sends one requested gesture.
Use an explicit matching desktop mode and a frozen --driver for comparisons.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import struct
import subprocess
import time
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler

ROOT = Path(__file__).resolve().parents[4]


async def run(options):
    """Observe, dispatch once, and compare actual per-document counters."""
    folder = options.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    assert Path(computer_use.__file__).resolve() == ROOT / "computer_use_engine/__init__.py"
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == options.desktop
    diagnostics = []

    def diagnostic(event):
        diagnostics.append(event)
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")

    def state():
        return json.loads((folder / "state.json").read_text())

    async def wait(predicate):
        for _ in range(120):
            try:
                current = state()
                if predicate(current):
                    return current
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError({"state": state(), "waiting": "fixture precondition"})

    def save(name, value):
        (folder / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))

    driver = options.driver.resolve()
    sources = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "computer_use_engine").rglob("*")
        if p.suffix in (".py", ".swift") and ".build" not in p.parts and "__pycache__" not in p.parts
    }
    save(
        "version.json",
        {
            "driver": str(driver),
            "driverSha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "sdk": computer_use.__file__,
            "sources": sources,
            "desktop": options.desktop,
            "foregroundAssist": os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST", "0"),
        },
    )
    computer = None
    pid = None
    report = {"passed": False, "scenario": options.scenario}
    setDiagnosticHandler(diagnostic)
    try:
        subprocess.run(
            [resolve_executable("open"), "-n", str(options.fixture.resolve()), "--args", str(folder)], check=True
        )
        launched = await wait(lambda s: len(s["documents"]) == 2)
        pid = launched["pid"]
        selected = 0 if options.scenario == "already-ready" else 1
        save("command.json", {"id": str(time.time_ns()), "kind": "front-target" if selected == 0 else "front-other"})
        before = await wait(lambda s: s["active"] and s["keyWindow"] == s["documents"][selected]["window"])
        computer = Computer()
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        entries = await computer.listApps()
        matches = [e for e in entries if e.displayName.endswith(" — Document context 0")]
        assert len(matches) == 1, [e.displayName for e in entries]
        app = computer.app(matches[0].id)
        observation = await app.read(view="both", full=True)
        (folder / "before.txt").write_text(observation.text)
        (folder / "before.png").write_bytes(observation.screenshot)
        before = state()
        report["before"] = before
        report["driverPid"] = computer._controller.transport._child.pid
        event_path = folder / "events.jsonl"
        event_start = len(event_path.read_text().splitlines()) if event_path.exists() else 0
        if options.scenario == "pointer":
            width, height = struct.unpack(">II", observation.screenshot[16:24])
            doc = before["documents"][0]
            x, y, w, h = doc["frame"]
            point = ((doc["buttonScreen"][0] - x) * width / w, (doc["buttonScreen"][1] - y) * height / h)
            result = await app.click(at=point)
        else:
            button = await app.element("Document action")
            result = await button.click()
        report["result"] = {"status": result.status, "detail": result.detail}
        await asyncio.sleep(0.3)
        report["after"] = state()
        after = await app.read(view="both", full=True)
        (folder / "after.txt").write_text(after.text)
        (folder / "after.png").write_bytes(after.screenshot)
        delta = [a["actions"] - b["actions"] for a, b in zip(report["after"]["documents"], before["documents"])]
        report["actionDelta"] = delta
        events = (
            [json.loads(line) for line in event_path.read_text().splitlines()[event_start:]]
            if event_path.exists()
            else []
        )
        buttons = [e for e in events if e["type"] in (1, 2) and e.get("sourcePid") == report["driverPid"]]
        report["driverButtonEvents"] = buttons
        expected_buttons = 2 if options.scenario == "pointer" else 0
        report["passed"] = delta == [1, 0] and len(buttons) == expected_buttons
    except Exception as error:
        report["error"] = repr(error)
        if (folder / "state.json").exists():
            report["after"] = state()
    finally:
        if computer:
            computer._controller.transport.close()
            await computer._controller.transport.waitClosed()
        if pid:
            os.kill(pid, signal.SIGTERM)
        setDiagnosticHandler(None)
        save("report.json", report)
        print(json.dumps({"report": str(folder / "report.json"), **report}, ensure_ascii=False, default=str))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--desktop", choices=("shared", "exclusive"), required=True)
    parser.add_argument("--scenario", choices=("pointer", "action", "already-ready"), required=True)
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
