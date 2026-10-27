"""Opt-in background pointer/focus acceptance on a disposable AppKit fixture.

Run ``uv run python tests/sdk/desktop/click_focus_acceptance.py`` after building
the current driver. The fixture records received events independently of SDK
receipts, including first-mouse rejection and overlapping document windows.
"""

import asyncio
import json
import os
import plistlib
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from computer_use import ClickOption, Computer, ComputerUseError
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler

ROOT = Path(__file__).resolve().parents[3]


async def main() -> None:
    """Build isolated UI, exercise the public SDK and check actual delivery."""
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == "shared"
    folder = ROOT / ".artifacts" / "click-focus-acceptance" / str(time.time_ns())
    name = f"ClickFocusProbe-{time.time_ns()}"
    bundle = folder / f"{name}.app"
    executable = bundle / "Contents/MacOS/probe"
    executable.parent.mkdir(parents=True)
    subprocess.run(
        [
            resolve_executable("swiftc"),
            "-swift-version",
            "6",
            str(ROOT / "tests/fixtures/SyntheticFocusProbe.swift"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": f"local.computer-use.synthetic-focus-{time.time_ns()}",
                "CFBundleName": name,
                "CFBundleExecutable": "probe",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    subprocess.run(
        [resolve_executable("codesign"), "--force", "--sign", "-", str(bundle)], check=True, capture_output=True
    )
    subprocess.run([resolve_executable("open"), "-g", str(bundle)], check=True)
    computer = Computer()
    report: dict[str, Any] = {"passed": False}

    def state() -> dict[str, Any]:
        return json.loads((folder / "state.json").read_text())

    async def settled() -> dict[str, Any]:
        await asyncio.sleep(0.1)
        return state()

    def diagnostic(event: dict[str, Any]) -> None:
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    setDiagnosticHandler(diagnostic)
    try:
        for _ in range(50):
            if (folder / "state.json").exists():
                break
            await asyncio.sleep(0.05)
        before = state()
        entries = await computer.listApps()
        target = computer.app(next(a.id for a in entries if a.displayName == f"{name} — Synthetic target"))
        other = computer.app(next(a.id for a in entries if a.displayName == f"{name} — Synthetic other"))
        observed = await target.read(full=True, view="both")
        (folder / "initial.txt").write_text(observed.text)
        assert observed.screenshot is not None
        (folder / "initial.png").write_bytes(observed.screenshot)
        canvas = await target.element([{"identifier": "mouse-canvas"}])
        await canvas.click()
        first = await settled()
        assert first["downs"] == first["ups"] == 1, first
        await canvas.click(ClickOption(count=2))
        await canvas.click(ClickOption(button="right"))
        repeated = await settled()
        assert repeated["downs"] == repeated["ups"] == 3 and repeated["clicks"] == [1, 1, 2], repeated
        assert repeated["rightDowns"] == 1, repeated
        await (await target.element("Semantic action")).click()
        assert (await settled())["semantic"] == 1
        # The fixture's bottom button and upper canvas expose mirrored-Y errors.
        await target.click(at=(110, 312))
        assert (await settled())["semantic"] == 2
        await target.drag((70, 130), (170, 165))
        dragged = await settled()
        assert dragged["downs"] == dragged["ups"] == 4 and dragged["drags"] > 0, dragged
        try:
            await target.click(at=(-1, -1))
            raise AssertionError("out-of-image coordinate accepted")
        except ComputerUseError as error:
            assert error.effect == "none"
        await other.read(full=True)
        await (await other.element([{"identifier": "other-input"}])).typeText("other-safe", replace=True)
        await (await target.element([{"identifier": "target-input"}])).typeText("target-safe", replace=True)
        final = await settled()
        assert final["input"] == "target-safe" and final["otherInput"] == "other-safe", final
        assert final["downs"] == 4 and final["semantic"] == 2, final
        # A non-key child panel retains its own route and does not become main.
        await (await target.element("Show popup")).click()
        popup = await target.read(full=True)
        (folder / "popup.txt").write_text(popup.text)
        window_ref = re.search(r'^Window (\d+): "Synthetic popup"$', popup.text, re.MULTILINE)
        assert window_ref is not None, popup.text
        panel = target.window(int(window_ref.group(1)))
        panel_image = await panel.read(view="both", full=True)
        assert panel_image.screenshot is not None
        (folder / "popup.png").write_bytes(panel_image.screenshot)
        await panel.click(at=(110, 55))
        final = await settled()
        assert final["popupClicks"] == 1 and not final["popupVisible"], final
        assert final["foreground"] == before["foreground"], (before, final)
        report.update(passed=True, before=before, first=first, final=final)
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        setDiagnosticHandler(None)
        pid_file = folder / "fixture.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(folder)


if __name__ == "__main__":
    asyncio.run(main())
