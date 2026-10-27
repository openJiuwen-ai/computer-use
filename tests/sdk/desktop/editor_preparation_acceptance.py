"""Opt-in real AppKit/PDFKit/WebKit tests; all tested input uses the public SDK.

The fixture's state files are read-only oracles. They are never input routes.
Run with COMPUTER_USE_DESKTOP=shared COMPUTER_USE_FOREGROUND_ASSIST=1.
"""

import asyncio
import json
import plistlib
import re
import subprocess
import time
from pathlib import Path

from computer_use import Computer
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler
from computer_use.errors import ComputerUseError

ROOT = Path(__file__).resolve().parents[3]


async def main():
    directory = ROOT / ".artifacts/editor-preparation" / str(time.time_ns())
    directory.mkdir(parents=True)
    bundles = []
    computer = Computer()
    report = {"passed": False, "directory": str(directory)}
    setDiagnosticHandler(
        lambda event: (
            (directory / "commands.jsonl").open("a").write(json.dumps(event, ensure_ascii=True, default=str) + "\n")
        )
    )

    def build(name, *, variant="", args=()):
        folder = directory / (name + variant)
        bundle = folder / (name + ".app")
        executable = bundle / "Contents/MacOS/probe"
        executable.parent.mkdir(parents=True)
        identifier = "local.computer-use.editor-" + name.lower() + "-" + str(time.time_ns())
        subprocess.run(
            [
                resolve_executable("swiftc"),
                "-swift-version",
                "6",
                str(ROOT / "tests/fixtures" / (name + ".swift")),
                "-o",
                str(executable),
            ],
            check=True,
            capture_output=True,
        )
        (bundle / "Contents/Info.plist").write_bytes(
            plistlib.dumps(
                dict(
                    CFBundleIdentifier=identifier,
                    CFBundleName=name,
                    CFBundleExecutable="probe",
                    CFBundlePackageType="APPL",
                )
            )
        )
        subprocess.run(
            [resolve_executable("codesign"), "--force", "--sign", "-", str(bundle)], check=True, capture_output=True
        )
        subprocess.run(
            [resolve_executable("open"), "-g", str(bundle), "--args", *args], check=True, capture_output=True
        )
        bundles.append((identifier, executable))
        return folder, bundle

    async def read(app, name):
        state = await app.read(full=True, view="both")
        (directory / (name + ".txt")).write_text(state.text)
        if state.screenshot is not None:
            (directory / (name + ".png")).write_bytes(state.screenshot)
        print(name, state.text[:250], flush=True)
        return state

    async def element(app, state, label, role):
        lines = [
            line for line in state.text.split("\n") if re.match("^\\s*\\d+ " + role + "\\b", line) and label in line
        ]
        assert len(lines) == 1, (label, lines, state.text)
        return await app.element(int(lines[0].strip().split()[0]))

    async def oracle(path, predicate):
        for _ in range(60):
            try:
                value = json.loads(path.read_text())
                if predicate(value):
                    return value
            except (FileNotFoundError, ValueError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError((str(path), value))

    try:
        monitor = ROOT / ".artifacts/react-backend-comparison-20261015/run-033331/observer"
        if monitor.exists():
            desktop = json.loads(subprocess.check_output([str(monitor)]))
            assert not desktop["locked"] and not desktop["asleep"], desktop
        folder, bundle = build("ReliabilityInputProbe")
        await oracle(folder / "state.json", lambda _: True)
        app = computer.app(str(bundle))
        state = await read(app, "00-edit-callback")
        await (await element(app, state, "Target field", "text field")).typeText("NORMAL EDIT 中文🙂", replace=True)
        report["editCallback"] = await oracle(
            folder / "state.json", lambda v: v["target"] == "NORMAL EDIT 中文🙂" and v["changes"] > 0
        )
        assert report["editCallback"]["guard"] == "GUARD"
        assert report["editCallback"]["submits"] == 0

        folder, bundle = build("BoundEditorProbe")
        await oracle(folder / "state.json", lambda _: True)
        app = computer.app(str(bundle))
        state = await read(app, "00-bound-editor")
        await (await element(app, state, "Bound input", "text field")).typeText("BOUND EDIT 中文🙂", replace=True)
        report["boundEditor"] = await oracle(
            folder / "state.json",
            lambda v: v["model"] == "BOUND EDIT 中文🙂" and v["display"] == v["model"] and v["changes"] > 0,
        )
        assert report["boundEditor"]["other"] == "untouched"
        await (await element(app, state, "Value-only input", "text field")).typeText(
            "VALUE ONLY ACCEPTED", replace=True
        )
        report["valueOnly"] = await oracle(folder / "state.json", lambda v: v["valueOnly"] == "VALUE ONLY ACCEPTED")
        assert report["valueOnly"]["model"] == "BOUND EDIT 中文🙂"

        folder, bundle = build("PDFFormProbe")
        await oracle(folder / "values.json", lambda _: True)
        app = computer.app(str(bundle))
        state = await read(app, "01-pdf-catalogue")
        if "Choose a window" in state.text or state.screenshot is None:
            ref = re.search('(\\d+) "PDF form A"', state.text)[1]
            app = app.window(int(ref))
            state = await read(app, "02-pdf-A")
        fields = re.findall("^\\s*(\\d+) text field\\b", state.text, re.M)
        assert len(fields) == 3, state.text
        field = await app.element(int(fields[0]))
        await field.typeText("Alice 中文🙂", replace=True)
        state = await read(app, "03-pdf-customer")
        await (await app.element(int(fields[1]))).typeText("alice@example.test", replace=True)
        await app.pressKey("Tab")
        report["pdf"] = await oracle(
            folder / "values.json",
            lambda v: v["A"]["Customer"] == "Alice 中文🙂" and v["A"]["Email"] == "alice@example.test",
        )
        assert report["pdf"]["A"]["Reference"] == "untouched-A"
        assert report["pdf"]["B"] == {"Customer": "", "Email": "", "Reference": "untouched-B"}

        folder, bundle = build("WebInputProbe")
        await oracle(folder / "state.json", lambda v: len(v["pages"]) == 3)
        app = computer.app(str(bundle))
        state = await read(app, "04-web")
        await (await element(app, state, "Editable Web Body", "text entry area")).typeText(
            "BODY ACCEPTED 中文🙂", replace=True
        )
        state = await read(app, "05-web-body")
        body = await element(app, state, "Editable Web Body", "text entry area")
        second = "SECOND BODY 中文🙂"
        await body.typeText(second, replace=True)
        report["webSecond"] = await oracle(folder / "state.json", lambda v: v["pages"]["body"]["bodyText"] == second)
        await body.pressKey("super+a")
        selection = await read(app, "05-web-body-selection")
        line = next(
            line for line in selection.text.splitlines() if "text entry area" in line and "Editable Web Body" in line
        )
        assert f"Selection: 0:{len(second.encode('utf-16-le')) // 2}" in line, line
        report["webWholeSelection"] = line.strip()
        await body.typeText("", replace=True)
        report["webCleared"] = await oracle(
            folder / "state.json", lambda v: v["pages"]["body"]["bodyText"] in ("", "\n")
        )
        # A logically cleared contenteditable can retain a generated <br> in
        # its AX extent. Preserve this known refusal rather than calling the
        # next ordinary insertion a successful whole-content replacement.
        try:
            await body.typeText("BODY ACCEPTED 中文🙂", replace=True)
        except ComputerUseError as error:
            assert error.code == "input_unavailable", error
            report["webEmptyReplacementRefusal"] = str(error)
            await oracle(folder / "state.json", lambda v: v["pages"]["body"]["bodyText"] in ("", "\n"))
            state = await read(app, "05-web-empty-refusal")
            body = await element(app, state, "Editable Web Body", "text entry area")
            await body.typeText("BODY ACCEPTED 中文🙂")
        await (await element(app, state, "Web Textarea", "text entry area")).typeText("TEXTAREA ACCEPTED", replace=True)
        report["web"] = await oracle(
            folder / "state.json",
            lambda v: (
                v["pages"]["body"]["bodyText"] == "BODY ACCEPTED 中文🙂"
                and v["pages"]["controls"]["textarea"] == "TEXTAREA ACCEPTED"
            ),
        )
        assert report["web"]["pages"]["controls"]["readonly"] == "LOCKED"
        assert "READONLY PAGE CONTENT" in report["web"]["pages"]["readonly"]["bodyText"]

        folder, bundle = build("WebInputProbe", variant="-markers", args=("--design-mode",))
        baseline = await oracle(folder / "state.json", lambda v: len(v["pages"]) == 3)
        app = computer.app(str(bundle))
        state = await read(app, "05a-web-markers")
        body = await element(app, state, "Design Mode Web Body", "HTML content")
        # These roots expose marker selection too, but are not whole-page
        # editors. Rejecting them must not modify their contained controls.
        for label in ("Editor Controls", "Read Only Web Page"):
            try:
                await (await element(app, state, label, "HTML content")).typeText("MUST NOT APPEAR", replace=True)
            except ComputerUseError as error:
                report.setdefault("markerRejections", {})[label] = str(error)
            else:
                raise AssertionError(f"Non-editable page accepted replacement: {label}")
        # typeText is deliberately single-line; the initial native document
        # spans two paragraphs, so replacement must still cover both blocks.
        text = "REPLACED 中文 é 🙂"
        await body.typeText(text, replace=True)
        report["markerReplace"] = await oracle(folder / "state.json", lambda v: v["pages"]["body"]["bodyText"] == text)
        state = await read(app, "05b-web-markers-replaced")
        await (await element(app, state, "Web Textarea", "text entry area")).typeText("ADJACENT EDITOR", replace=True)
        await body.typeText("FROM ANOTHER EDITOR", replace=True)
        await body.typeText("", replace=True)
        cleared = await oracle(folder / "state.json", lambda v: v["pages"]["body"]["bodyText"] in ("", "\n"))
        report["markerCleared"] = cleared["pages"]["body"]["bodyText"]
        await body.typeText("AFTER EMPTY 中文🙂", replace=True)
        report["markers"] = await oracle(
            folder / "state.json", lambda v: v["pages"]["body"]["bodyText"] == "AFTER EMPTY 中文🙂"
        )
        assert report["markers"]["pages"]["controls"]["textarea"] == "ADJACENT EDITOR"
        assert report["markers"]["pages"]["controls"]["rich"] == baseline["pages"]["controls"]["rich"]
        assert report["markers"]["pages"]["controls"]["readonly"] == "LOCKED"
        assert report["markers"]["pages"]["readonly"]["bodyHTML"] == baseline["pages"]["readonly"]["bodyHTML"]
        await read(app, "05c-web-markers-final")

        folder, bundle = build("PointerEditorProbe")
        await oracle(folder / "state.json", lambda _: True)
        app = computer.app(str(bundle))
        state = await read(app, "06-pointer-editor")
        await (await element(app, state, "Click-only editor", "text entry area")).paste("PREPARED\n中文🙂")
        report["pointerEditor"] = await oracle(folder / "state.json", lambda v: v["text"] == "PREPARED\n中文🙂")
        assert report["pointerEditor"]["other"] == "untouched"

        folder, bundle = build("InputClickProbe")
        await oracle(folder / "state.json", lambda _: True)
        app = computer.app(str(bundle))
        state = await read(app, "07-collection")
        # The presentation may compact row > cell > editor onto one line;
        # every displayed reference still addresses its original native node.
        cells = re.findall("(?:^\\s*| > )(\\d+) (?:cell|单元格)\\b", state.text, re.M)
        assert cells, state.text
        await (await app.element(int(cells[0]))).click()
        report["collection"] = await oracle(folder / "state.json", lambda v: v["selected"] == 0)
        assert report["collection"]["input"] == "Standalone"
        report["passed"] = True
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        setDiagnosticHandler(None)
        for identifier, executable in bundles:
            subprocess.run(
                [
                    resolve_executable("swift"),
                    "-e",
                    (
                        "import AppKit\nfor a in NSRunningApplication.runningApplications(withBundleId"
                        "entifier: CommandLine.arguments[1]) where a.executableURL?.path == "
                        "CommandLine.arguments[2] { a.terminate() "
                        "}"
                    ),
                    identifier,
                    str(executable),
                ],
                capture_output=True,
            )
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k not in ("web", "pdf")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
