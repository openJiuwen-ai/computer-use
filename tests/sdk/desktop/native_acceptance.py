import asyncio
import json
import os
import plistlib
import re
import subprocess
import time
from pathlib import Path

from computer_use import Computer, ComputerUseError
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler

"Opt-in migration acceptance on disposable macOS native fixtures only."


ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / ".artifacts/python-migration" / f"desktop-{time.time_ns()}"
DIRECTORY.mkdir(parents=True)


def build(name):
    folder = DIRECTORY / name
    bundle = folder / f"{name}.app"
    exe = bundle / "Contents/MacOS/probe"
    exe.parent.mkdir(parents=True)
    subprocess.run(
        [
            resolve_executable("swiftc"),
            "-swift-version",
            "6",
            str(ROOT / f"tests/fixtures/{name}.swift"),
            "-o",
            str(exe),
        ],
        check=True,
        capture_output=True,
    )
    identifier = f"local.computer-use.python-{name.lower()}-{time.time_ns()}"
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            dict(
                CFBundleIdentifier=identifier, CFBundleName=name, CFBundleExecutable="probe", CFBundlePackageType="APPL"
            )
        )
    )
    subprocess.run(
        [resolve_executable("codesign"), "--force", "--sign", "-", str(bundle)], check=True, capture_output=True
    )
    # Disposable bundles outside installation roots are not installed catalogue
    # entries. Start them as fixture setup; all tested UI input still uses SDK.
    subprocess.run([resolve_executable("open"), "-g", str(bundle)], check=True, capture_output=True)
    return folder, bundle, exe, identifier


def terminate(bundle):
    _, _, exe, identifier = bundle
    subprocess.run(
        [
            resolve_executable("swift"),
            "-e",
            (
                "import AppKit\nfor app in NSRunningApplication.runningApplications(withBundle"
                "Identifier: CommandLine.arguments[1]) where "
                "app.executableURL?.standardizedFileURL.path == CommandLine.arguments[2] { "
                "app.forceTerminate() "
                "}"
            ),
            identifier,
            str(exe),
        ],
        check=True,
        capture_output=True,
    )


async def main():
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == "shared"
    assert os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") != "1"
    action = build("SecondaryActionProbe")
    editor = build("ElementInputProbe")
    monitor = DIRECTORY / "desktop-state-probe"
    subprocess.run(
        [resolve_executable("swiftc"), str(ROOT / "tests/fixtures/DesktopStateProbe.swift"), "-o", str(monitor)],
        check=True,
        capture_output=True,
    )
    computer = Computer()
    report = {"passed": False, "directory": str(DIRECTORY)}
    samples = []
    sampler = await asyncio.create_subprocess_exec(
        str(monitor), stdout=asyncio.subprocess.PIPE, env={**os.environ, "COMPUTER_USE_TEST_MONITOR_SECONDS": "180"}
    )

    async def sample():
        while line := await sampler.stdout.readline():
            value = json.loads(line)
            value["receivedAt"] = time.monotonic()
            samples.append(value)

    sampling = asyncio.create_task(sample())
    await asyncio.sleep(0.12)
    setDiagnosticHandler(
        lambda event: (
            (DIRECTORY / "commands.jsonl").open("a").write(json.dumps(event, ensure_ascii=True, default=str) + "\n")
        )
    )

    async def observe(app, label):
        state = await app.read(full=True, view="both")
        (DIRECTORY / f"{label}.txt").write_text(state.text)
        if state.screenshot is not None:
            (DIRECTORY / f"{label}.png").write_bytes(state.screenshot)
        return state

    async def target(app, state, label, role=None):
        prefix = "^\\s*\\d+ " + (re.escape(role) + "\\b" if role else "")
        lines = [
            line_local
            for line_local in state.text.split("\nAdditional native facts", 1)[0].splitlines()
            if re.match(prefix, line_local) and label in line_local
        ]
        assert len(lines) == 1, (label, lines)
        return await app.element(int(lines[0].strip().split()[0]))

    try:
        app = computer.app(str(action[1]))
        state = await observe(app, "01-secondary")

        def actual():
            return json.loads((action[0] / "state.json").read_text())

        before = actual()
        item = await target(app, state, "Disposable item")
        assert "Secondary Actions:" in state.text
        for name in ("Delete", "unknown"):
            try:
                await item.performAction(name)
                raise AssertionError("unknown action accepted")
            except ComputerUseError as error:
                assert error.effect == "none" and error.code == "input_unavailable"
        assert actual() == before
        try:
            await (await target(app, state, "Duplicate actions")).performAction("Same")
            raise AssertionError("ambiguous action accepted")
        except ComputerUseError as error:
            assert error.effect == "none" and error.code == "ambiguous_target"
        line = next(
            text_line
            for text_line in state.text.split("\nAdditional native facts", 1)[0].splitlines()
            if "Duplicate actions" in text_line
        )
        names = [json.loads(m) for m in re.findall('name: ("(?:\\\\.|[^"\\\\])*")', line)]
        assert len(names) == 2
        await (await target(app, state, "Duplicate actions")).performAction(names[1])
        await item.performAction("置顶")
        await observe(app, "02-pinned")
        assert actual() == {**before, "alternate": 1, "pin": 1}
        try:
            await item.performAction("Uncertain")
        except ComputerUseError as error:
            assert error.effect == "possible"
        state = await observe(app, "03-uncertain")
        assert actual()["uncertain"] == 1
        await (await target(app, state, "Disposable item")).performAction("删除")
        state = await observe(app, "04-deleted")
        assert "Disposable item" not in state.text
        assert actual()["delete"] == 1
        assert actual()["primary"] == 0
        report["secondaryActions"] = actual()
        app = computer.app(str(editor[1]))
        state = await observe(app, "05-editor")

        async def field(label):
            return await target(app, state, label, "text field")

        async def verify(label, a, b):
            nonlocal state
            state = await observe(app, label)
            assert (await (await field("Field A")).readText()).text == a
            assert (await (await field("Field B")).readText()).text == b

        text = "Long input 0123456789 中文🙂 é"
        await (await field("Field B")).typeText(text, replace=True)
        await verify("06-unicode", "A:", text)
        await (await field("Field B")).typeText("abcdef", replace=True)
        await verify("07-replace", "A:", "abcdef")
        await (await field("Field B")).pressKey("super+Right")
        await app.pressKey("Left")
        await (await field("Field B")).typeText("X")
        await verify("08-caret", "A:", "abcdeXf")
        await app.pressKey("shift+Left")
        await (await field("Field B")).typeText("Y")
        await verify("09-selection", "A:", "abcdeYf")
        await (await field("Field B")).typeText("", replace=True)
        await (await field("Field B")).typeText("@path")
        await verify("10-autocomplete", "A:", "@path-suggestion")
        await (await field("Field A")).typeText("", replace=True)
        await verify("11-clear", "", "@path-suggestion")
        retained = await field("Field A")
        state = await observe(app, "12-retained")
        await retained.typeText("retained", replace=True)
        await verify("13-retained-result", "retained", "@path-suggestion")
        events_value = [json.loads(text_line) for text_line in (editor[0] / "events.jsonl").read_text().splitlines()]
        mouse = [e for e in events_value if e.get("kind") == "mouse"]
        # Window preparation may address the native activation point (or
        # confirmed empty chrome). It must not trial-click an input or content.
        assert all(not e["inContent"] and e["hit"] == "none" and e["finitePoint"] for e in mouse), mouse
        commands_value = [
            json.loads(text_line) for text_line in (DIRECTORY / "commands.jsonl").read_text().splitlines()
        ]
        window_preparations = sum(
            "native window activation point" in e.get("result", {}).get("preparation", [])
            for e in commands_value
            if e.get("kind") == "command_end"
        )
        assert len(mouse) == 2 * window_preparations, mouse
        for down, up in zip(mouse[::2], mouse[1::2]):
            assert (down["type"], up["type"]) == (1, 2) and (down["x"], down["y"]) == (up["x"], up["y"]), mouse
        report["editor"] = {
            "unicode": True,
            "replacement": True,
            "caret": True,
            "selection": True,
            "autocomplete": True,
            "clear": True,
            "retainedHandle": True,
            "noContentPreparationClick": True,
            "windowPreparationClicks": window_preparations,
        }
        report["functionalPassed"] = True
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        await asyncio.sleep(0.1)
        sampler.terminate()
        await sampler.wait()
        await sampling
        (DIRECTORY / "desktop-state.jsonl").write_text("".join(json.dumps(s) + "\n" for s in samples))
        report["desktop"] = {
            "samples": len(samples),
            "changes": {
                name: sum(any(b[k] != a[k] for k in keys) for a, b in zip(samples, samples[1:]))
                for name, keys in {
                    "foreground": ["pid"],
                    "pointer": ["x", "y"],
                    "modifiers": ["flags"],
                    "clipboard": ["clipboard"],
                }.items()
            },
        }
        report["passed"] = (
            report.get("functionalPassed") is True
            and len(samples) > 1
            and all(x == 0 for x in report["desktop"]["changes"].values())
        )
        for bundle in (action, editor):
            terminate(bundle)
        setDiagnosticHandler(None)
        (DIRECTORY / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps(report, indent=2, ensure_ascii=False))
    assert report["passed"], report


if __name__ == "__main__":
    asyncio.run(main())
