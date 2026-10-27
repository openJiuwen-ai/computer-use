"""Real SDK/AX acceptance against a disposable row-selection application."""

import asyncio
import hashlib
import json
import os
import plistlib
import subprocess
import time
from pathlib import Path

import computer_use
from computer_use import ClickOption, Computer
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler
from computer_use.paths import nativeDriver
from computer_use_native_tools.process import launch_logged_process

ROOT = Path(__file__).resolve().parents[4]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def main():
    mode = os.environ.get("COMPUTER_USE_DESKTOP")
    assert mode in ("exclusive", "shared")
    assert Path(computer_use.__file__).resolve() == ROOT / "computer_use_engine/__init__.py"
    folder = ROOT / ".artifacts/ax-selection-20261022" / str(time.time_ns())
    executable = folder / "RowSelectionProbe.app/Contents/MacOS/probe"
    executable.parent.mkdir(parents=True)
    subprocess.run(
        [
            resolve_executable("swiftc"),
            "-swift-version",
            "6",
            str(Path(__file__).with_name("RowSelectionProbe.swift")),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    (executable.parents[1] / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "local.computer-use.row-selection-" + folder.name,
                "CFBundleName": "RowSelectionProbe",
                "CFBundleExecutable": "probe",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    subprocess.run(
        [resolve_executable("codesign"), "--force", "--sign", "-", str(executable.parents[2])],
        check=True,
        capture_output=True,
    )
    driver = Path(nativeDriver()["command"])
    files = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "computer_use_engine").rglob("*")
        if p.suffix in (".py", ".swift") and ".build" not in p.parts
    }
    report = {
        "module": computer_use.__file__,
        "mode": mode,
        "driver": str(driver),
        "driverSHA256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "sources": files,
        "cases": [],
        "passed": False,
    }
    write(folder / "report.json", report)
    process = launch_logged_process([str(executable), str(folder)], folder / "fixture.log")
    computer = Computer()

    def diagnostic(event):
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    async def state(sequence=None):
        for _ in range(60):
            try:
                value = json.loads((folder / "state.json").read_text())
                if sequence is None or value["sequence"] == sequence:
                    return value
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.05)
        raise RuntimeError("fixture did not reach setup state")

    cases = [
        ("single", "normal", [0], "row", 1, [1], ["AXSelectedRows"], 0, "verified"),
        ("no-geometry", "no-geometry", [0], "row", 1, [1], ["AXSelectedRows"], 0, "verified"),
        ("multi", "normal", [0, 2], "row", 1, [1], ["AXSelectedRows"], 0, "verified"),
        ("already-selected", "normal", [1], "row", 1, [1], [], 1, "dispatched"),
        ("direct-empty", "direct", [], "row", 1, [1], ["AXSelected:1"], 0, "verified"),
        ("direct-preserves-single-click", "direct", [0], "row", 1, [1], [], 1, "dispatched"),
        ("readback-noop", "noop", [0], "row", 1, [0], ["AXSelectedRows"], 0, "dispatched"),
        ("inner-button", "normal", [0], "button", 1, [0], [], 0, "dispatched"),
        ("double", "normal", [0], "row", 2, [1], [], 2, "dispatched"),
    ]
    if mode == "shared":
        cases = [
            case
            for case in cases
            if case[0] in ("single", "no-geometry", "multi", "direct-empty", "readback-noop", "inner-button")
        ]
    setDiagnosticHandler(diagnostic)
    try:
        await state()
        apps = await computer.listApps()
        candidates = [
            entry for entry in apps if entry.displayName == "RowSelectionProbe — Computer Use Row Selection Probe"
        ]
        assert len(candidates) == 1, [(entry.id, entry.displayName) for entry in apps if "Row" in entry.displayName]
        app = computer.app(candidates[0].id)
        for sequence, (name, mode, initial, target, count, selected, writes, mouse, status) in enumerate(cases, 1):
            write(folder / "command.json", {"sequence": sequence, "mode": mode, "selected": initial})
            before_state = await state(sequence)
            before = await app.read(view="both", full=True)
            (folder / f"{name}-before.txt").write_text(before.text)
            (folder / f"{name}-before.png").write_bytes(before.screenshot)
            identifier = "selection-row-1" if target == "row" else "selection-inner-button"
            element = await app.element([{"role": target, "identifier": identifier}])
            result = await element.click(ClickOption(count=count))
            await asyncio.sleep(0.15)
            after = await app.read(view="both", full=True)
            (folder / f"{name}-after.txt").write_text(after.text)
            (folder / f"{name}-after.png").write_bytes(after.screenshot)
            actual = await state(sequence)
            case = {
                "name": name,
                "before": before_state,
                "after": actual,
                "status": result.status,
                "detail": result.detail,
            }
            report["cases"].append(case)
            write(folder / "report.json", report)
            assert actual["selected"] == selected, case
            assert actual["writes"] == writes, case
            assert actual["downs"] == actual["ups"] == mouse, case
            assert actual["buttons"] == (1 if target == "button" else 0), case
            assert result.status == status, case
            print(json.dumps({"case": name, "passed": True, "result": case}, ensure_ascii=False), flush=True)
        report["passed"] = True
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        setDiagnosticHandler(None)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        report["fixtureExitCode"] = process.returncode
        report["cleaned"] = True
        write(folder / "report.json", report)
        print(json.dumps({"folder": str(folder), "passed": report["passed"], "cleaned": True}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
