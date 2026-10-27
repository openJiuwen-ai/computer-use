"""Opt-in real Windows UIA/SendInput acceptance on ClickHitProbe only.

Build the current driver and ClickHitProbe.cs first; run this in the interactive
test desktop, passing an empty output directory. Optional --baseline-driver
records the historical same-HWND misclick without replaying any operation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use.client.host import setDiagnosticHandler
from computer_use.paths import nativeDriver

ROOT = Path(__file__).resolve().parents[5]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest():
    return {
        str(p.relative_to(ROOT)): sha(p)
        for p in sorted((ROOT / "computer_use_engine").rglob("*"))
        if p.suffix in (".py", ".cs") and ".build" not in p.parts and "__pycache__" not in p.parts
    }


async def main(options):
    assert sys.platform == "win32"
    os.environ["COMPUTER_USE_DESKTOP"] = "shared"
    os.environ["COMPUTER_USE_FOREGROUND_ASSIST"] = "0"
    os.environ["COMPUTER_USE_TRACE_NATIVE_INPUT"] = "1"
    folder = options.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    fixture_dir = folder / "fixture"
    fixture_dir.mkdir()
    driver = Path(nativeDriver()["command"])
    report = dict(
        passed=False,
        cases=[],
        driver=str(driver),
        driverSha256=sha(driver),
        sdk=computer_use.__file__,
        python=sys.executable,
        desktop="shared",
        foregroundAssist=False,
    )
    source_before = manifest()
    (folder / "sources-before.json").write_text(json.dumps(source_before, indent=2), encoding="utf-8")
    events = []
    c = None
    process = None
    command_id = 0

    def save(name, value):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def diagnostic(event):
        events.append(event)
        with (folder / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def state():
        return json.loads((fixture_dir / "state.json").read_text(encoding="utf-8"))

    async def wait_state(predicate=lambda x: True):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                value = state()
                if predicate(value):
                    return value
            except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError("fixture did not reach expected state")

    async def command(kind, **data):
        nonlocal command_id
        command_id += 1
        (fixture_dir / "command.json").write_text(json.dumps(dict(id=command_id, kind=kind, **data)))
        return await wait_state(lambda x: x["command"] == command_id)

    async def close_sdk():
        nonlocal c
        if c is not None:
            c._controller.transport.close()
            await c._controller.transport.waitClosed()
            c = None

    async def case(
        name,
        mode,
        target="Pointer target A",
        baseline=False,
        refused=False,
        uncertainty=False,
        action=None,
        capture=False,
        expected="A",
        combo=None,
        combo_before=None,
    ):
        nonlocal c
        await command("setup", mode=mode)
        await command("activate")
        await wait_state(lambda x: x["foreground"] == x["hwnd"])
        c = Computer()
        if baseline:
            c._controller.transport.driver = dict(command=str(options.baseline_driver.resolve()), args=[])
        entries = await c.listApps()
        matches = [a for a in entries if a.displayName.endswith(" — Click hit boundary fixture")]
        assert len(matches) == 1, [(a.id, a.displayName) for a in entries]
        app = c.app(matches[0].id)
        observed = await app.read(full=True, view="both")
        (folder / (name + "-before.txt")).write_text(observed.text, encoding="utf-8")
        if observed.screenshot:
            (folder / (name + "-before.png")).write_bytes(observed.screenshot)
        before = await wait_state()
        if combo_before is not None:
            assert before["comboExpanded"] == combo_before, (name, "unexpected combo precondition", before)
        offset = len(events)
        item = dict(name=name, mode=mode, baseline=baseline, before=before)
        try:
            if capture:
                captures = [
                    e["result"]
                    for e in events
                    if e.get("method") == "capture" and e.get("kind") == "command_end" and "result" in e
                ]
                a, b, skew, d, ox, oy = captures[-1]["coordinates"]["toNative"]
                assert b == skew == 0
                x, y, w, h = before["targetBounds"]
                item["coordinate"] = [(x + w / 2 - ox) / a, (y + h / 2 - oy) / d]
                result = await app.click(at=tuple(item["coordinate"]))
            else:
                element = await app.element(target)
                if combo is not None:
                    native = await element.read()
                    (folder / (name + "-element.txt")).write_text(native.text, encoding="utf-8")
                result = await element.performAction(action) if action else await element.click()
            item["result"] = dict(status=result.status, detail=result.detail)
        except ComputerUseError as error:
            item["error"] = dict(code=error.code, effect=error.effect, detail=str(error))
        await asyncio.sleep(0.25)
        after = await wait_state()
        item["after"] = after
        item["execute"] = [
            e["result"]
            for e in events[offset:]
            if e.get("kind") == "command_end" and e.get("method") == "execute" and "result" in e
        ]
        counts = after["counts"]
        checks = dict(foregroundUnchanged=after["foreground"] == before["foreground"], released=not after["leftDown"])
        if refused:
            checks.update(
                refused=item.get("error", {}).get("effect") == "none",
                noCallbacks=not counts,
                noExecute=not item["execute"],
                conflictExplained="another UIA control" in item.get("error", {}).get("detail", ""),
            )
        elif combo is not None:
            checks.update(
                noError="error" not in item, expanded=after["comboExpanded"] == combo, noPointerCallbacks=not counts
            )
        else:
            checks.update(
                noError="error" not in item,
                expectedCallback=counts.get(expected + (".invoke" if action else ".click")) == 1,
                unrelatedUntouched=all(v == 0 or key.startswith(expected + ".") for key, v in counts.items()),
                oneExecute=len(item["execute"]) == 1,
            )
            if expected == "row":
                checks["rowIntent"] = counts.get("row.click", 0) == (0 if action else 1) and counts.get(
                    "row.invoke", 0
                ) == (1 if action else 0)
            if not action:
                checks["oneMousePair"] = counts.get(expected + ".down") == counts.get(expected + ".up") == 1
            if uncertainty:
                checks["uncertaintyPreserved"] = "UIA element hit unconfirmed" in item.get("result", {}).get(
                    "detail", ""
                )
        item["checks"] = checks
        item["passed"] = all(checks.values())
        report["cases"].append(item)
        save("report.json", report)
        after_image = await app.read(full=True, view="both")
        if after_image.screenshot:
            (folder / (name + "-after.png")).write_bytes(after_image.screenshot)
        (folder / (name + "-after.txt")).write_text(after_image.text, encoding="utf-8")
        await close_sdk()
        print(json.dumps(dict(name=name, passed=item["passed"], checks=checks)), flush=True)

    setDiagnosticHandler(diagnostic)
    try:
        process = subprocess.Popen([str(options.fixture.resolve()), str(fixture_dir)])
        await wait_state()
        if options.baseline_driver:
            report["baselineDriverSha256"] = sha(options.baseline_driver)
            await case("baseline-other-control-clicked", "covered-all", baseline=True, expected="B")
        await case("covered-all-refused", "covered-all", refused=True)
        await case("covered-center-alternate", "covered")
        await case("exact-target", "plain")
        await case("coarse-ancestor", "ancestor", uncertainty=True)
        await case("coarse-other-page", "coarse", uncertainty=True)
        await case("failed-hit-query", "query-failed", uncertainty=True)
        await case("capture-with-failed-uia-hit", "query-failed", capture=True)
        await case("row-click-selects", "plain", target="Invoke-only document row", expected="row")
        await case(
            "row-explicit-invoke", "plain", target="Invoke-only document row", action="Invoke.Invoke", expected="row"
        )
        combo_target = [{"role": "comboBox", "name": "Disclosure combo"}]
        await case("combo-opens", "plain", target=combo_target, combo=True, combo_before=False)
        await case("combo-open-main-action", "plain", target=combo_target, combo=True, combo_before=True)
        await case(
            "combo-explicit-collapse",
            "plain",
            target=combo_target,
            action="ExpandCollapse.Collapse",
            combo=False,
            combo_before=True,
        )
        report["passed"] = all(x["passed"] for x in report["cases"])
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        await close_sdk()
        setDiagnosticHandler(None)
        if process is not None:
            try:
                await command("stop")
            except Exception:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
            report["fixtureExitCode"] = process.returncode
        source_after = manifest()
        report["sourceDrift"] = [
            k for k in source_before.keys() | source_after.keys() if source_before.get(k) != source_after.get(k)
        ]
        report["driverUnchanged"] = sha(driver) == report["driverSha256"]
        save("report.json", report)
        print(
            json.dumps(
                dict(
                    finished=str(folder),
                    passed=report["passed"],
                    sourceDrift=report["sourceDrift"],
                    driverUnchanged=report["driverUnchanged"],
                )
            ),
            flush=True,
        )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--baseline-driver", type=Path)
    asyncio.run(main(parser.parse_args()))
