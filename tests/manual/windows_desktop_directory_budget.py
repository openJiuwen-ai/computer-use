# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read and click from a live desktop capture when its directory helper hangs."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from computer_use.protocol.captures import capture_metadata
from computer_use.session.operations import operation_budget
from windows_paste_workflow import wait_state
from windows_visual_workflow import build_fixture, marker_point, sample_after, window_ref

ROOT = Path(__file__).resolve().parents[2]
ENTRY = Path(__file__).with_name("windows_directory_timeout_driver.cs")


def build_fault_driver(directory: Path) -> tuple[Path, str]:
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    references = (
        "System.dll", "System.Core.dll", "System.Web.Extensions.dll", "System.Drawing.dll",
        "System.Windows.Forms.dll", "WPF/WindowsBase.dll", "WPF/UIAutomationClient.dll", "WPF/UIAutomationTypes.dll",
    )
    installed = Path(native_driver()["command"])
    interop = Path(shutil.copy2(installed.with_name("Interop.NativeUIA.dll"), directory))
    driver = directory / installed.name
    sources = sorted((ROOT / "os_adapter/windows/Sources").rglob("*.cs")) + [ENTRY]
    manifest = "\n".join(f"{source.relative_to(ROOT).as_posix()} {hashlib.sha256(source.read_bytes()).hexdigest()}"
                         for source in sources)
    response = directory / "build.rsp"
    arguments = [
        "/nologo", "/target:exe", "/platform:x64", "/optimize+", "/warnaserror+",
        "/main:ComputerUse.Windows.WindowsDirectoryTimeoutDriver", f"/out:{driver}",
        *(f"/reference:{framework / name}" for name in references), f"/reference:{interop}", *sources,
    ]
    response.write_text("\n".join(f'"{arg}"' for arg in arguments), encoding="utf-8")
    subprocess.run([str(framework / "csc.exe"), f"@{response}"], check=True)
    return driver, hashlib.sha256(manifest.encode()).hexdigest()


async def read_with_budget(computer: Computer, budget_ms: int):
    # The host supplies the enclosing deadline. The observed image still comes
    # from the normal public read, decoder, delivery and desktop point binding.
    with operation_budget(budget_ms) as budget:
        observation = await asyncio.to_thread(computer.read, view="image", full=True)
        return observation, round(budget.elapsed_ms, 3)


def verify_fault(signal: Path, skip: bool, report: dict) -> None:
    if skip:
        if signal.exists():
            raise RuntimeError("The directory helper received work despite the tight screenshot budget")
        return
    if not signal.is_file():
        raise RuntimeError("The real isolated directory child did not receive a request")
    fault = json.loads(signal.read_text(encoding="utf-8-sig"))
    report["directory_child"] = fault
    output = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         f"@(Get-Process -Id {int(fault['pid'])} -ErrorAction SilentlyContinue).Count"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    if output.stdout.strip() != "0":
        raise RuntimeError("The directory helper was still running after screenshot delivery")
    report["directory_child_exited"] = True


def verify_application_name(computer: Computer, executable: str, title: str, signal: Path, report: dict) -> None:
    directories = []

    def observe_directory(event: dict) -> None:
        result = event.get("result")
        if event.get("kind") != "command_end" or not isinstance(result, dict) or result.get("kind") != "applications":
            return
        app_ids = [row["id"] for row in result["applications"] if row["displayName"] == executable]
        directories.append({
            "application_ids": app_ids,
            "primary_names": [row for row in result.get("nativeProcessNames", ()) if row["name"] == executable],
            "supplement_timed_out": any("its isolated 1200 ms budget" in reason for reason in result["stopReasons"]),
        })

    report["application_directories"] = directories
    set_diagnostic_handler(observe_directory)
    try:
        choices = [app for app in computer.list_apps() if app.display_name == executable and app.is_running]
        if len(choices) != 1:
            raise RuntimeError("The fixture did not have a unique delivered application entry")
        by_name = computer.app(executable).read(view="image", full=True)
        named_window = window_ref(by_name.text, title)
        by_alias = computer.app(choices[0].id).read(view="image", full=True)
        alias_window = window_ref(by_alias.text, title)
    finally:
        set_diagnostic_handler(None)
    report["application_reference"] = choices[0].id
    report["named_window"] = named_window
    report["alias_window"] = alias_window
    verify_fault(signal, False, report)
    report["pass"] = (
        len(directories) == 2 and named_window == alias_window
        and directories[0]["application_ids"] == directories[1]["application_ids"]
        and len(directories[0]["application_ids"]) == 1
        and all(row["supplement_timed_out"] for row in directories)
        and all(
            row["primary_names"] == [{"application": row["application_ids"][0], "name": executable}]
            for row in directories
        )
    )


def run_probe(exe: Path, driver: Path, budget_ms: int, skip: bool, application_name: bool, report: dict) -> None:
    state = exe.with_suffix(".json")
    signal = driver.with_name("directory-request.json")
    fixture = subprocess.Popen([str(exe), str(state), uuid.uuid4().hex[:10]])
    try:
        ready = wait_state(state, lambda value: value.get("foreground_hwnd") and value.get("zero_visible"), 8)
        report["initial"] = ready
        if not ready.get("main_title") or not ready.get("zero_visible"):
            raise RuntimeError("The visual fixture did not become ready")
        if not ready.get("foreground"):
            # Establish only the fixture's foreground in an independent, ordinary session.
            with Computer() as setup:
                app = setup.app(exe.stem)
                ref = window_ref(app.read(view="image", full=True).text, ready["main_title"])
                app.read(view="tree", window=ref, allow_foreground=True, full=True)
        driver_spec = {"command": str(driver), "args": []}
        with patch("computer_use.native.connection.native_driver", return_value=driver_spec), Computer() as computer:
            if application_name:
                verify_application_name(computer, exe.stem, ready["main_title"], signal, report)
                return
            computer._executor.run(lambda: computer._controller.port.connect(5000))
            observation, report["read_ms"] = asyncio.run(read_with_budget(computer, budget_ms))
            # Keep evidence scoped to the test fixture and the public fault note.
            lines = observation.text.splitlines()
            note = "UIA desktop metadata supplement skipped" if skip else "exceeded its capture metadata budget"
            report["catalogue_note"] = next((line for line in lines if note in line), None)
            if observation.screenshot is None or report["catalogue_note"] is None:
                raise RuntimeError("The SDK did not deliver both a desktop image and the expected catalogue fault note")
            report["native_fixture_row"] = any(ready["main_title"] in line and "window=" in line for line in lines)
            if not report["native_fixture_row"]:
                raise RuntimeError("The independent Win32 catalogue omitted the fixture's window reference")
            point, size = marker_point(observation.screenshot)
            metadata = capture_metadata(observation.screenshot)
            geometry = f"Screenshot: desktop {size[0]}x{size[1]} px; origin=top-left"
            if metadata is None or (metadata["kind"], metadata["width"], metadata["height"]) != ("desktop", *size):
                raise RuntimeError("Delivered pixels do not have matching desktop capture metadata")
            if geometry not in lines:
                raise RuntimeError("The delivered screenshot geometry does not match its decoded PNG")
            report["image"] = {
                "sha256": hashlib.sha256(observation.screenshot).hexdigest(), "size": size, "point": point,
                "kind": metadata["kind"], "geometry": geometry,
            }
            verify_fault(signal, skip, report)
            result = computer.click(target=computer.point(point))
            report["receipt"] = {"status": result.status, "detail": result.detail}
        actual = sample_after(state)
        report["actual"] = {key: actual[key] for key in ("target_clicks", "bystander_clicks")}
        report["pass"] = (
            result.status in {"dispatched", "verified"}
            and actual["target_clicks"] == 1 and actual["bystander_clicks"] == 0
        )
    finally:
        if fixture.poll() is None:
            fixture.terminate()
        fixture.wait(timeout=5)
        if signal.is_file() and "directory_child" not in report:
            report["directory_child"] = json.loads(signal.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-ms", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--skip", action="store_true", help="Require tight-budget omission before helper dispatch")
    mode.add_argument("--application-name", action="store_true", help="Compare a fresh process name and delivered app ID")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    budget = args.budget_ms if args.budget_ms is not None else (1000 if args.skip else 2300)
    report = {
        "budget_ms": None if args.application_name else budget, "expect_skip": args.skip,
        "application_name": args.application_name, "module": computer_use.__file__, "pass": False,
    }
    with tempfile.TemporaryDirectory(prefix="cua-directory-budget-") as folder:
        directory = Path(folder)
        fixture = directory / "CuaVisualFixture.exe"
        build_fixture(fixture)
        driver, manifest = build_fault_driver(directory)
        if args.build_only:
            return
        report.update(driver_sha256=hashlib.sha256(driver.read_bytes()).hexdigest(), native_source_manifest=manifest)
        try:
            run_probe(fixture, driver, budget, args.skip, args.application_name, report)
        except (ComputerUseError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
    serialized = json.dumps(report, ensure_ascii=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    sys.stdout.write(serialized + "\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
