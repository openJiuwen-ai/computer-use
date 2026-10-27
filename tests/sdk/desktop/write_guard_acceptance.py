"""Opt-in real object/contextual editing with an explicitly injected query fault.

COMPUTER_USE_DESKTOP=shared COMPUTER_USE_FOREGROUND_ASSIST=0 uv run python
tests/sdk/desktop/write_guard_acceptance.py

Only the optional editor-graph query is faulted; native inputContext, native
dispatch, scope checks, screenshots and fixture state are real. No UI data is
fabricated. Each tested write uses the public Element API in a fresh SDK process.
"""

import asyncio
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler
from computer_use.paths import nativeDriver

ROOT = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main():
    assert sys.platform == "darwin"
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == "shared"
    assert os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST", "0") == "0"
    assert Path(computer_use.__file__).resolve() == ROOT / "computer_use_engine/__init__.py"
    folder = ROOT / ".artifacts/write-guard-audit-20261022" / ("native-" + str(time.time_ns()))
    bundle = folder / "WriteGuardProbe.app"
    executable = bundle / "Contents/MacOS/probe"
    executable.parent.mkdir(parents=True)
    subprocess.run(
        [
            resolve_executable("swiftc"),
            "-swift-version",
            "6",
            str(ROOT / "tests/fixtures/WriteGuardProbe.swift"),
            "-o",
            str(executable),
        ],
        capture_output=True,
        check=True,
    )
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            dict(
                CFBundleIdentifier="local.write-guard." + folder.name,
                CFBundleName="WriteGuardProbe",
                CFBundleExecutable="probe",
                CFBundlePackageType="APPL",
            )
        )
    )
    subprocess.run(
        [resolve_executable("codesign"), "--force", "--sign", "-", str(bundle)], capture_output=True, check=True
    )
    monitor = folder / "desktop-monitor"
    subprocess.run(
        [resolve_executable("swiftc"), str(ROOT / "tests/fixtures/DesktopStateProbe.swift"), "-o", str(monitor)],
        capture_output=True,
        check=True,
    )
    driver = Path(nativeDriver()["command"])
    sources = [p for p in (ROOT / "computer_use_engine").rglob("*") if p.suffix in (".py", ".swift", ".cs") and ".build" not in p.parts]
    sources += [Path(__file__), ROOT / "tests/fixtures/WriteGuardProbe.swift"]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    manifest = dict(
        head=subprocess.check_output(
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
        gitStatus=subprocess.check_output(
            [
                resolve_executable(
                    "git",
                    cwd=ROOT,
                ),
                "status",
                "--porcelain",
            ],
            cwd=ROOT,
            text=True,
        ),
        sourceBefore=hashes,
        sdkModule=computer_use.__file__,
        driver=dict(path=str(driver), sha256=sha(driver)),
        fixture=dict(path=str(executable), sha256=sha(executable)),
        desktopMode="shared",
        foregroundAssist=False,
    )
    computer = Computer()
    commands = []
    samples = []
    fixture = None
    sampler = None
    sampling = None
    report = dict(passed=False, folder=str(folder), cases={})

    def diagnostic(event):
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        if event.get("kind") == "command_start":
            commands.append(event)
        if event.get("kind") == "connection":
            manifest["actualConnection"] = event

    async def state(**expected):
        value = None
        for _ in range(80):
            try:
                value = json.loads((folder / "state.json").read_text())
                if all(value.get(k) == v for k, v in expected.items()):
                    return value
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError((value, expected))

    async def observe(app, label):
        value = await app.read(full=True, view="both")
        (folder / (label + ".txt")).write_text(value.text)
        if value.screenshot is not None:
            (folder / (label + ".png")).write_bytes(value.screenshot)
        return value

    async def sample():
        while line := await sampler.stdout.readline():
            samples.append(json.loads(line))

    graph_queries = []

    async def unavailable(operation, target, *args, **kwargs):
        graph_queries.append(target.node)
        raise ComputerUseError(
            dict(
                code="observation_incomplete",
                effect="none",
                target="fixture editor graph",
                detail="Injected unavailable editor metadata; original native input context remains real.",
                next="Inspect the target.",
            )
        )

    setDiagnosticHandler(diagnostic)
    try:
        subprocess.run([resolve_executable("open"), "-g", str(bundle)], capture_output=True, check=True)
        before = await state()
        fixture = before["pid"]
        sampler = await asyncio.create_subprocess_exec(
            str(monitor), stdout=asyncio.subprocess.PIPE, env={**os.environ, "COMPUTER_USE_TEST_MONITOR_SECONDS": "180"}
        )
        sampling = asyncio.create_task(sample())
        await asyncio.sleep(0.15)
        app = computer.app(str(bundle))
        await observe(app, "00-before")
        object_field = await app.element("Object input")
        contextual = await app.element("Context input")
        port = computer._controller.port
        begin = len(commands)
        with patch.object(port, "nodes", unavailable):
            await object_field.typeText("OBJECT ACCEPTED 中文🙂", replace=True)
        assert graph_queries == [], graph_queries
        report["cases"]["independentObject"] = await state(
            object="OBJECT ACCEPTED 中文🙂", writes=1, editor="EDITOR ORIGINAL", guard="GUARD", changes=0, submits=0
        )
        execution = [e["params"]["command"] for e in commands[begin:] if e["method"] == "execute"]
        assert len(execution) == 1 and execution[0]["kind"] == "native_text_edit" and not execution[0]["foreground"], (
            execution
        )
        report["independentCommands"] = execution
        # The same fault really stops a contextual route before any preparation.
        begin = len(commands)
        with patch.object(port, "nodes", unavailable):
            try:
                await contextual.typeText("MUST NOT APPEAR", replace=True)
            except ComputerUseError as error:
                assert error.code == "observation_incomplete" and error.effect == "none", error
                report["cases"]["contextRequiresEvidence"] = dict(code=error.code, effect=error.effect)
            else:
                raise AssertionError("Contextual input ignored required editing evidence")
        assert len(graph_queries) == 1
        assert not any(e["method"] == "execute" for e in commands[begin:])
        await state(editor="EDITOR ORIGINAL", guard="GUARD", writes=1)
        await contextual.typeText("CONTEXT ACCEPTED 中文🙂", replace=True)
        await contextual.pressKey("Return")
        actual = await state(
            object="OBJECT ACCEPTED 中文🙂", writes=1, editor="CONTEXT ACCEPTED 中文🙂", guard="GUARD", submits=1
        )
        assert actual["changes"] > 0, actual
        report["cases"]["contextRecoversAndSubmits"] = actual
        after = await observe(app, "01-after")
        assert "OBJECT ACCEPTED" in after.text and "CONTEXT ACCEPTED" in after.text, after.text
        report["functionalPassed"] = True
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        manifest["driverHello"] = computer._controller.transport._hello
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        if sampler is not None:
            if sampler.returncode is None:
                sampler.terminate()
            await sampler.wait()
        if sampling is not None:
            await sampling
        setDiagnosticHandler(None)
        if fixture is not None:
            try:
                os.kill(fixture, 15)
            except ProcessLookupError:
                pass
            for _ in range(40):
                try:
                    os.kill(fixture, 0)
                except ProcessLookupError:
                    report["fixtureTerminated"] = True
                    break
                await asyncio.sleep(0.05)
        (folder / "desktop-state.jsonl").write_text("".join(json.dumps(v) + "\n" for v in samples))
        report["desktop"] = dict(
            samples=len(samples),
            changes={
                name: sum(any(a[k] != b[k] for k in keys) for a, b in zip(samples, samples[1:]))
                for name, keys in dict(
                    foreground=("pid",), pointer=("x", "y"), modifiers=("flags",), clipboard=("clipboard",)
                ).items()
            },
        )
        manifest["sourceAfter"] = {str(p.relative_to(ROOT)): sha(p) for p in sources}
        report["sourceStable"] = manifest["sourceAfter"] == hashes and sha(driver) == manifest["driver"]["sha256"]
        report["passed"] = bool(
            report.get("functionalPassed")
            and report.get("fixtureTerminated")
            and report["sourceStable"]
            and len(samples) > 0
            and not any(report["desktop"]["changes"].values())
        )
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps(report, indent=2, ensure_ascii=False))
    assert report["passed"], report


if __name__ == "__main__":
    asyncio.run(main())
