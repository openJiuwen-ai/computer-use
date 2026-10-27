"""Opt-in UI acceptance for coarse AX window hits; never run by unit tests."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler


async def run(options):
    folder = options.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    driver = options.driver.resolve()
    report = {
        "passed": False,
        "expect": options.expect,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driverSha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "desktop": os.environ.get("COMPUTER_USE_DESKTOP", "shared"),
    }
    computer, pid = None, None

    def save(name, value):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")

    def diagnostic(event):
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def state():
        return json.loads((folder / "state.json").read_text())

    async def wait(predicate):
        for _ in range(100):
            try:
                current = state()
                if predicate(current):
                    return current
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError("fixture did not reach its required state")

    async def observe(app, name):
        observation = await app.read(view="both", full=True)
        (folder / (name + ".txt")).write_text(observation.text)
        (folder / (name + ".png")).write_bytes(observation.screenshot)
        return observation

    setDiagnosticHandler(diagnostic)
    try:
        subprocess.run(
            [resolve_executable("open"), "-n", str(options.fixture.resolve()), "--args", str(folder)], check=True
        )
        before = await wait(lambda s: s["active"] and s["key"])
        pid = before["pid"]
        computer = Computer()
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        entries = await computer.listApps()
        matches = [e for e in entries if e.displayName.endswith(" — Coarse pointer probe")]
        assert len(matches) == 1, [e.displayName for e in entries]
        app = computer.app(matches[0].id)
        observed = await observe(app, "before")
        # The fixture exposes its text as AXValue, not AXTitle. Bind exactly
        # the delivered reference instead of exercising a separate name query.
        refs = re.findall(r"^\s*(\d+)\s+(?:text|文本)\s+45 minutes\s*$", observed.text, re.MULTILINE)
        assert len(refs) == 1, observed.text
        report["requestedRef"] = int(refs[0])
        option = await app.element(report["requestedRef"])
        report["before"] = state()
        try:
            result = await option.click()
            report["result"] = {"status": result.status, "detail": result.detail}
        except Exception as error:
            report["error"] = str(error)
        await asyncio.sleep(0.2)
        report["after"] = state()
        await observe(app, "after")
        expected = 1 if options.expect == "success" else 0
        assert report["after"]["optionClicks"] == expected and report["after"]["guardClicks"] == 0
        assert ("result" in report) == (options.expect == "success")

        # A known independent control covering the same rectangle must remain
        # a rejected hit. Reuse the original addressed option, never the guard.
        save("command.json", {"id": str(time.time_ns()), "kind": "sibling"})
        await wait(lambda s: s["mode"] == "sibling")
        try:
            await option.click()
            raise AssertionError("a sibling hit was accepted")
        except AssertionError:
            raise
        except Exception as error:
            report["siblingError"] = str(error)
        await asyncio.sleep(0.2)
        report["siblingAfter"] = state()
        await observe(app, "sibling")
        assert report["siblingAfter"]["optionClicks"] == expected and report["siblingAfter"]["guardClicks"] == 0
        report["passed"] = True
    except Exception as error:
        report["failure"] = repr(error)
    finally:
        if computer:
            computer._controller.transport.close()
            await computer._controller.transport.waitClosed()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        setDiagnosticHandler(None)
        save("report.json", report)
        print(json.dumps({"report": str(folder / "report.json"), **report}, ensure_ascii=False, default=str))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect", choices=("reject", "success"), required=True)
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
