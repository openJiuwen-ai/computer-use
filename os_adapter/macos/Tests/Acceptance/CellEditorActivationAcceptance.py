"""Opt-in real cell activation/replace/Return acceptance; never launches an app.

Run once against one freshly launched, already observed self-owned fixture.
Failure stops without recovery, alternate scope, repeated text or extra click.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from computer_use import Computer
from computer_use.client.host import computerHost


async def run(options):
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == "shared"
    assert os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1"
    options.output.mkdir(parents=True, exist_ok=True)
    report = {"completed": False, "app": options.app, "cleanupConfirmed": False}
    host = None

    def oracle(name):
        state = json.loads(options.state.read_text())
        (options.output / f"{name}.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return state

    async def wait_oracle(name, predicate):
        for _ in range(40):
            value = oracle(name)
            if predicate(value):
                return value
            await asyncio.sleep(0.05)
        raise AssertionError(f"fixture callback not confirmed: {name}")

    try:
        before = oracle("before")
        assert not before["editing"] and before["editorCreations"] == 0 and before["ownerMouseDowns"] == 0
        computer = Computer()
        host = computerHost()
        apps = await computer.listApps()
        matches = [app for app in apps if app.isRunning and app.displayName == options.app]
        assert len(matches) == 1, f"expected one already running {options.app}: {len(matches)}"
        app = computer.app(matches[0].id)
        (options.output / "before-tree.txt").write_text((await app.read()).text)
        original_cell = await app.element([{"identifier": "original-cell"}])
        started = time.monotonic()
        result = await original_cell.typeText(options.text, replace=True)
        report["replace"] = {"seconds": time.monotonic() - started, "status": result.status, "detail": result.detail}
        edited = await wait_oracle("after-replace", lambda value: value["text"] == options.text)
        assert edited["editing"] and edited["editorCreations"] == 1 and edited["ownerMouseDowns"] == 2
        assert edited["editorHeight"] > edited["editingRootHeight"], "the first editor must exercise line-height overflow"
        if before["mode"] == "selection-activation-nonempty":
            assert edited["editingRootWidth"] > edited["ownerWidth"], "nonempty editing must exercise initial width expansion"
        assert edited["owner"] == before["owner"] and edited["guard"] == before["guard"]
        assert edited["ownerValueWrites"] == 0 and edited["ownerSelectionWrites"] == 0
        # Re-observation must refine the same original target lifetime, without
        # replacing the caller's cell handle with the transient text editor.
        (options.output / "editing-tree.txt").write_text((await app.read()).text)
        if options.pause_after_replace:
            print(json.dumps({"phase": "awaiting-explicit-background-setup", "fixturePID": before["pid"],
                              "next": "Place an existing unrelated app in foreground, then send continue on stdin."}), flush=True)
            assert (await asyncio.to_thread(sys.stdin.readline)).strip() == "continue"
            background = await wait_oracle("before-background-return", lambda value: value["frontmostPID"] != before["pid"])
            assert background["editing"] and background["text"] == options.text
            report["explicitBackgroundSetup"] = background
        started = time.monotonic()
        result = await original_cell.pressKey("Return")
        report["return"] = {"seconds": time.monotonic() - started, "status": result.status, "detail": result.detail}
        after = await wait_oracle("after-return", lambda value: not value["editing"] and value["committed"] == options.text)
        assert after["owner"] == options.text and after["guard"] == before["guard"]
        assert after["ownerMouseDowns"] == 2 and after["editorCreations"] == 1
        assert after["ownerValueWrites"] == 0 and after["ownerSelectionWrites"] == 0
        report["completed"] = True
    except Exception as error:
        report["error"] = repr(error)
    finally:
        if host is not None:
            try:
                await asyncio.wait_for(host.close(), 10)
                report["cleanupConfirmed"] = True
            except Exception as error:
                report["cleanupError"] = repr(error)
        (options.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))
    return report["completed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="CellEditorActivationProbe — Cell editor continuity")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text", default="FIRST DIRECT 🙂")
    parser.add_argument("--pause-after-replace", action="store_true",
                        help="Wait for explicit unrelated-app foreground setup before original-cell Return")
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
