"""Opt-in local AXPress-effect acceptance in one disposable Finder window.

--fault-library alters only the test driver's returned AX status. It does not
load code into Finder. Every original action is forwarded exactly once.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler


def apple(source, *arguments):
    return subprocess.check_output(
        [resolve_executable("osascript"), "-", *map(str, arguments)], input=source, text=True, timeout=20
    ).strip()


async def run(options):
    folder = options.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    data = folder / ("Finder-ComputerUse-" + folder.name)
    data.mkdir()
    (data / "alpha.txt").write_text("alpha\n")
    (data / "beta.txt").write_text("beta\n")
    window = None
    computer = None
    report = {
        "passed": False,
        "sdk": computer_use.__file__,
        "driver": str(options.driver.resolve()),
        "driverSha256": hashlib.sha256(options.driver.read_bytes()).hexdigest(),
    }

    def diagnostic(value):
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(value, default=str, ensure_ascii=False) + "\n")

    try:
        window = apple(
            """on run arguments
tell application "Finder"
set testWindow to make new Finder window to (POSIX file (item 1 of arguments) as alias)
set current view of testWindow to icon view
activate
return id of testWindow
end tell
end run""",
            data,
        )
        report["nativeWindow"] = window
        if options.fault_library:
            os.environ["DYLD_INSERT_LIBRARIES"] = str(options.fault_library.resolve())
            os.environ["COMPUTER_USE_TEST_AX_ERROR_PID"] = subprocess.check_output(
                [resolve_executable("pgrep"), "-x", "Finder"], text=True
            ).strip()
            os.environ["COMPUTER_USE_TEST_AX_ERROR_WINDOW"] = window
            os.environ["COMPUTER_USE_TEST_AX_ERROR_LOG"] = str(folder / "action-error.jsonl")
        setDiagnosticHandler(diagnostic)
        computer = Computer()
        computer._controller.transport.driver = {"command": str(options.driver.resolve()), "args": []}
        entries = await computer.listApps()
        matches = [entry for entry in entries if entry.displayName.endswith(" — " + data.name)]
        assert len(matches) == 1, matches
        app = computer.app(matches[0].id)
        before = await app.read(view="both", full=True)
        (folder / "before.txt").write_text(before.text)
        (folder / "before.png").write_bytes(before.screenshot)
        match = re.search(r"(?m)^\s*(\d+) (?:单选按钮|radio button) \(off\) (?:列表视图|List view)", before.text)
        assert match, before.text
        try:
            result = await (await app.element(int(match[1]))).click()
            report["receipt"] = {"status": result.status, "detail": result.detail}
        except Exception as error:
            report["dispatchError"] = str(error)
        await asyncio.sleep(0.6)
        after = await app.read(view="both", full=True)
        (folder / "after.txt").write_text(after.text)
        (folder / "after.png").write_bytes(after.screenshot)
        report["currentView"] = apple(
            """on run arguments
tell application "Finder" to return current view of Finder window id (item 1 of arguments as integer)
end run""",
            window,
        )
        fault_file = folder / "action-error.jsonl"
        report["nativeActions"] = (
            [json.loads(line) for line in fault_file.read_text().splitlines()] if fault_file.exists() else []
        )
        report["passed"] = report["currentView"] == "list view" and (
            len(report["nativeActions"]) == 1 if options.fault_library else True
        )
        report["passed"] &= report.get("receipt", {}).get("status") == options.expect
    except Exception as error:
        report["error"] = repr(error)
    finally:
        if computer:
            computer._controller.transport.close()
            await computer._controller.transport.waitClosed()
        setDiagnosticHandler(None)
        if window:
            try:
                apple(
                    """on run arguments
tell application "Finder" to close Finder window id (item 1 of arguments as integer)
end run""",
                    window,
                )
                report["closed"] = True
            except Exception as error:
                report["cleanupError"] = str(error)
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps(report, ensure_ascii=False))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fault-library", type=Path)
    parser.add_argument("--expect", choices=["verified", "dispatched"], default="verified")
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
