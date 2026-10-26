"""Opt-in AppKit click-boundary acceptance using the editable public SDK.

Default invocation only builds the fixture. To operate its disposable UI:
  COMPUTER_USE_DIAGNOSE_INPUT=1 COMPUTER_USE_FOREGROUND_ASSIST=0 uv run python \
    os_adapter/macos/Tests/Acceptance/ClickSafetyAcceptance.py --run

Use --driver /absolute/path to compare a frozen historical driver and --cases
plain-element,plain-capture for the extra-preparation-click counterexample.
--allow-failures records a failing historical run without changing its verdict.
--accepts-first-mouse=true selects a separate provider contract (default false).
--desktop exclusive requires matching COMPUTER_USE_DESKTOP=exclusive and lets
the SDK perform explicitly authorized foreground preparation. The fixture never
activates itself. Each case starts a fresh fixture and SDK process; no operation
is retried after failure or uncertain dispatch.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import plistlib
import signal
import struct
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
CASES = ("plain-element", "plain-capture", "activation", "covered", "clipped")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest() -> dict:
    files = {
        str(path.relative_to(ROOT)): sha(path)
        for path in sorted((ROOT / "computer_use_engine").rglob("*"))
        if path.suffix in (".py", ".swift") and ".build" not in path.parts and "__pycache__" not in path.parts
    }
    return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def run_suite(options) -> None:
    """Each child owns one fixture, so cold first-mouse behavior cannot leak."""
    folder = ROOT / ".artifacts/macos-click-safety-acceptance" / ("suite-" + str(time.time_ns()))
    folder.mkdir(parents=True)
    reports = []
    for case in options.cases.split(","):
        assert case in CASES, case
        args = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--run",
            "--cases",
            case,
            "--desktop",
            options.desktop,
            "--accepts-first-mouse",
            options.accepts_first_mouse,
            "--allow-failures",
        ]
        if options.driver is not None:
            args += ["--driver", str(options.driver)]
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await process.communicate()
        (folder / f"{case}.log").write_bytes(output)
        print(output.decode(), end="", flush=True)
        paths = []
        for line in output.decode().splitlines():
            try:
                value = json.loads(line)
                if "finished" in value:
                    paths.append(Path(value["finished"]))
            except ValueError:
                pass
        if len(paths) == 1:
            report = json.loads((paths[0] / "report.json").read_text())
            sources = json.loads((paths[0] / "sources-before.json").read_text())
            reports.append(
                {
                    "case": case,
                    "report": str(paths[0] / "report.json"),
                    "passed": report["passed"],
                    "driverSha256": report["driverSha256"],
                    "sourceSha256": sources["sha256"],
                    "exitCode": process.returncode,
                }
            )
        else:
            reports.append(
                {"case": case, "passed": False, "exitCode": process.returncode, "error": "child report missing"}
            )
    summary = {
        "cases": reports,
        "freshFixturePerCase": True,
        "desktopMode": options.desktop,
        "acceptsFirstMouse": options.accepts_first_mouse == "true",
    }
    summary["driverStableAcrossCases"] = len({item.get("driverSha256") for item in reports}) == 1
    summary["sourceStableAcrossCases"] = len({item.get("sourceSha256") for item in reports}) == 1
    summary["passed"] = (
        all(item["passed"] for item in reports)
        and summary["driverStableAcrossCases"]
        and summary["sourceStableAcrossCases"]
    )
    write_json(folder / "summary.json", summary)
    print(json.dumps({"suite": str(folder), "passed": summary["passed"]}), flush=True)
    if not summary["passed"] and not options.allow_failures:
        raise SystemExit(1)


async def main(options) -> None:
    assert sys.platform == "darwin"
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == options.desktop, (
        "host mode must explicitly match --desktop"
    )
    assert os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST", "0") == "0"
    assert Path(computer_use.__file__).resolve() == ROOT / "computer_use_engine/__init__.py"
    if options.run and "," in options.cases:
        await run_suite(options)
        return
    folder = ROOT / ".artifacts/macos-click-safety-acceptance" / str(time.time_ns())
    bundle = folder / "ClickSafetyProbe.app"
    executable = bundle / "Contents/MacOS/probe"
    executable.parent.mkdir(parents=True)
    subprocess.run(
        [
            resolve_executable("swiftc"),
            "-swift-version",
            "6",
            str(Path(__file__).with_name("ClickSafetyProbe.swift")),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "local.computer-use.click-safety-" + folder.name,
                "CFBundleName": "ClickSafetyProbe",
                "CFBundleExecutable": "probe",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    subprocess.run(
        [resolve_executable("codesign"), "--force", "--sign", "-", str(bundle)], check=True, capture_output=True
    )
    if not options.run:
        print(json.dumps({"built": str(executable), "sha256": sha(executable), "uiStarted": False}), flush=True)
        return

    driver = (options.driver or Path(nativeDriver()["command"])).resolve()
    assert driver.is_file()
    source_before = source_manifest()
    write_json(folder / "sources-before.json", source_before)
    report = {
        "passed": False,
        "cases": [],
        "folder": str(folder),
        "sdkModule": computer_use.__file__,
        "python": sys.executable,
        "driver": str(driver),
        "driverSha256": sha(driver),
        "fixture": str(executable),
        "fixtureSha256": sha(executable),
        "desktopMode": options.desktop,
        "foregroundAssist": False,
        "acceptsFirstMouse": options.accepts_first_mouse == "true",
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
    }
    diagnostics: list[dict] = []
    computer = None
    fixture_pid = None

    def diagnostic(event):
        diagnostics.append(event)
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def state():
        return json.loads((folder / "state.json").read_text())

    async def wait_state(predicate=lambda value: True):
        latest = None
        for _ in range(200):
            try:
                latest = state()
                if predicate(latest):
                    return latest
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError({"condition": "fixture state", "latest": latest})

    async def close_sdk():
        nonlocal computer
        if computer is not None:
            computer._controller.transport.close()
            await computer._controller.transport.waitClosed()
            computer = None

    async def observe(app, label):
        observed = await app.read(full=True, view="both")
        (folder / f"{label}.txt").write_text(observed.text)
        assert observed.screenshot is not None
        (folder / f"{label}.png").write_bytes(observed.screenshot)
        return observed

    setDiagnosticHandler(diagnostic)
    try:
        launch = ["open", "-g", str(bundle)]
        if options.accepts_first_mouse == "true":
            launch += ["--args", "--accepts-first-mouse"]
        subprocess.run(launch, check=True, capture_output=True)
        initial = await wait_state()
        fixture_pid = initial["pid"]
        assert initial["foregroundPid"] != fixture_pid, "fixture must remain background"
        for case in options.cases.split(","):
            assert case in CASES, case
            scenario = "plain" if case.startswith("plain-") else case
            generation = state()["generation"]
            command_tmp = folder / "command.tmp"
            write_json(command_tmp, {"command": "setup", "scenario": scenario})
            command_tmp.replace(folder / "command.json")
            await wait_state(
                lambda value, *, generation=generation, scenario=scenario: (
                    value["generation"] > generation and value["scenario"] == scenario
                )
            )
            await asyncio.sleep(0.1)
            computer = Computer()
            # Driver selection is test-host configuration before negotiation;
            # all observation and write operations remain the public SDK path.
            computer._controller.transport.driver = {"command": str(driver), "args": []}
            entries = await computer.listApps()
            matches = [entry for entry in entries if entry.displayName.endswith(" — Click safety target")]
            assert len(matches) == 1, [entry.displayName for entry in entries]
            app = computer.app(matches[0].id)
            image = await observe(app, case + "-before")
            oracle = json.loads(subprocess.check_output([str(executable), "--inspect", str(fixture_pid)], text=True))
            before = await wait_state()
            assert before["foregroundPid"] != fixture_pid, "fixture became foreground before operation"
            item = {"case": case, "before": before, "axOracle": oracle}
            item["activationPointMatchesQuartz"] = oracle.get("targetActivationAX") == before["targetActivationQuartz"]
            item["activationPointMatchesAppKit"] = oracle.get("targetActivationAX") == before["targetActivationAppKit"]
            start = len(diagnostics)
            try:
                if case == "plain-capture":
                    width, height = struct.unpack(">II", image.screenshot[16:24])
                    x, y = before["targetCenterImage"]
                    frame_width, frame_height = before["windowSize"]
                    at = (x * width / frame_width, y * height / frame_height)
                    item["coordinate"] = at
                    result = await app.click(at=at)
                else:
                    target = await app.element([{"identifier": "pointer-target"}])
                    result = await target.click()
                item["outcome"] = {"status": result.status, "detail": result.detail}
            except Exception as error:
                item["outcome"] = {"error": repr(error)}
            await asyncio.sleep(0.35)
            after = await wait_state()
            item["after"] = after
            received = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
            item["receivedEvents"] = [
                event
                for event in received
                if event.get("generation") == before["generation"] and event.get("kind") != "setup"
            ]
            item["checks"] = {
                "targetCallbackOnce": after["counts"]["target"] - before["counts"]["target"] == 1,
                "unrelatedUntouched": after["counts"]["decoy"] == before["counts"]["decoy"] == 0
                and after["counts"]["overlay"] == before["counts"]["overlay"] == 0,
                "exactlyOneMousePair": after["applicationDowns"] - before["applicationDowns"] == 1
                and after["applicationUps"] - before["applicationUps"] == 1,
                "targetReceivedPair": after["targetDowns"] - before["targetDowns"] == 1
                and after["targetUps"] - before["targetUps"] == 1,
                "globalReleased": not after["leftPressed"],
                "noSdkError": "error" not in item["outcome"],
            }
            if options.desktop == "shared":
                item["checks"]["foregroundUnchanged"] = after["foregroundPid"] == before["foregroundPid"]
            else:
                item["checks"]["foregroundReachedTarget"] = after["foregroundPid"] == fixture_pid
            item["executeResults"] = [
                event
                for event in diagnostics[start:]
                if event.get("kind") == "command_end" and event.get("method") == "execute"
            ]
            item["checks"]["singleExecute"] = len(item["executeResults"]) == 1
            item["passed"] = all(item["checks"].values())
            if case == "activation":
                item["checks"]["activationCoordinateBridged"] = (
                    item["activationPointMatchesQuartz"] and oracle.get("activationExactTarget") is True
                )
                downs = [
                    event
                    for event in item["receivedEvents"]
                    if event.get("kind") == "application" and event.get("type") == 1
                ]
                item["checks"]["activationPointDispatched"] = len(downs) == 1 and all(
                    abs(a - b) < 1 for a, b in zip(downs[0]["point"], before["targetActivationWindow"])
                )
                item["passed"] = all(item["checks"].values())
            report["cases"].append(item)
            write_json(folder / "report.json", report)
            await observe(app, case + "-after")
            await close_sdk()
            print(
                json.dumps(
                    {"case": case, "passed": item["passed"], "checks": item["checks"], "folder": str(folder)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        report["passed"] = all(item["passed"] for item in report["cases"])
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        await close_sdk()
        setDiagnosticHandler(None)
        if fixture_pid is None and (folder / "fixture.pid").exists():
            fixture_pid = int((folder / "fixture.pid").read_text())
        if fixture_pid is not None:
            write_json(folder / "command.json", {"command": "close"})
            await asyncio.sleep(0.2)
            try:
                os.kill(fixture_pid, 0)
                os.kill(fixture_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            await asyncio.sleep(0.1)
            try:
                os.kill(fixture_pid, 0)
                report["fixtureExited"] = False
            except ProcessLookupError:
                report["fixtureExited"] = True
        source_after = source_manifest()
        write_json(folder / "sources-after.json", source_after)
        report["sourceUnchanged"] = source_before["sha256"] == source_after["sha256"]
        report["sourceDrift"] = [
            key
            for key in sorted(source_before["files"].keys() | source_after["files"].keys())
            if source_before["files"].get(key) != source_after["files"].get(key)
        ]
        report["driverUnchanged"] = sha(driver) == report["driverSha256"]
        report["passed"] = (
            report["passed"]
            and report.get("fixtureExited", False)
            and report["sourceUnchanged"]
            and report["driverUnchanged"]
        )
        write_json(folder / "report.json", report)
        print(
            json.dumps(
                {
                    "finished": str(folder),
                    "passed": report["passed"],
                    "sourceUnchanged": report["sourceUnchanged"],
                    "driverUnchanged": report["driverUnchanged"],
                    "fixtureExited": report.get("fixtureExited"),
                }
            ),
            flush=True,
        )
    if not report["passed"] and not options.allow_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="start and click only this disposable fixture")
    parser.add_argument("--driver", type=Path, help="frozen native driver executable; default current build")
    parser.add_argument(
        "--desktop",
        choices=("shared", "exclusive"),
        default="shared",
        help="must match the explicitly configured host mode",
    )
    parser.add_argument(
        "--accepts-first-mouse",
        choices=("true", "false"),
        nargs="?",
        const="true",
        default="false",
        help="separate provider behavior; default keeps first-mouse refusal",
    )
    parser.add_argument("--cases", default=",".join(CASES))
    parser.add_argument(
        "--allow-failures", action="store_true", help="retain a failing baseline without a nonzero exit"
    )
    asyncio.run(main(parser.parse_args()))
