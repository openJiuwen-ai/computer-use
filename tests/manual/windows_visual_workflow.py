# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read a live empty-geometry HWND and click from its desktop fallback pixels.

Use an unlocked Windows test desktop. The independent fixture reports actual
click counts but supplies no coordinates. No screenshot is written to disk.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from contextlib import nullcontext
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image, ImageChops
from windows_paste_workflow import read_state, wait_state

FIXTURE = Path(__file__).with_name("windows_visual_fixture.cs")
MARKER = (13, 237, 101)


def build_fixture(exe: Path) -> None:
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    uia_references = [f"/reference:{framework / 'WPF' / name}" for name in (
        "UIAutomationProvider.dll", "UIAutomationTypes.dll", "WindowsBase.dll",
    )]
    subprocess.run(
        [
            str(framework / "csc.exe"), "/nologo", "/target:winexe", "/platform:x64", "/warnaserror+",
            "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
            "/reference:System.Web.Extensions.dll", *uia_references, f"/out:{exe}", str(FIXTURE),
            str(FIXTURE.with_name("windows_visual_blocking_provider.cs")),
        ],
        check=True,
    )


def capture_fault_driver(driver: Path, directory: Path) -> Path:
    """Inject two hanging helper replies in an isolated copy of the real driver."""
    directory.mkdir()
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    subprocess.run(
        [
            str(framework / "csc.exe"), "/nologo", "/target:exe", "/platform:x64", "/warnaserror+",
            "/reference:System.Web.Extensions.dll", f"/out:{directory / 'computer-use-capture.exe'}",
            str(FIXTURE.with_name("windows_capture_timeout_helper.cs")),
        ],
        check=True,
    )
    shutil.copy2(driver.with_name("Interop.NativeUIA.dll"), directory)
    return Path(shutil.copy2(driver, directory))


def verify_capture_fault(driver: Path, report: dict) -> None:
    routes = {}
    for route in ("wgc", "print"):
        evidence = driver.with_name(f"capture-timeout-{route}.json")
        if not evidence.is_file():
            raise RuntimeError(f"The isolated {route} helper did not receive its capture request")
        routes[route] = json.loads(evidence.read_text(encoding="utf-8-sig"))
    report["blocked_capture_helpers"] = routes
    if routes["wgc"]["pid"] == routes["print"]["pid"] or routes["wgc"]["handle"] != routes["print"]["handle"]:
        raise RuntimeError("The two capture attempts did not target the same HWND in separate helpers")
    pids = ",".join(str(int(value["pid"])) for value in routes.values())
    output = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         f"Get-Process | Where-Object {{ $_.Id -in @({pids}) }} | Select-Object -ExpandProperty Id"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    if output.stdout.strip():
        raise RuntimeError(f"Capture helpers still running after fallback: {output.stdout.strip()}")
    report["capture_helpers_exited"] = True


def marker_point(png: bytes) -> tuple[tuple[int, int], tuple[int, int]]:
    with Image.open(BytesIO(png)) as source, source.convert("RGB") as image:
        with Image.new("RGB", image.size, MARKER) as color, ImageChops.difference(image, color) as difference:
            red, green, blue = difference.split()
        mask = ImageChops.invert(ImageChops.lighter(ImageChops.lighter(red, green), blue)).point(
            lambda pixel: 255 if pixel == 255 else 0
        )
        box = mask.getbbox()
        if box is None:
            raise RuntimeError("Fixture marker was not visible in the returned screenshot")
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        if width < 12 or height < 12 or mask.histogram()[255] < width * height * 0.95:
            raise RuntimeError(f"Returned screenshot does not contain one solid fixture marker: {box}")
        return ((left + right) // 2, (top + bottom) // 2), image.size


def window_ref(catalogue: str, title: str) -> int:
    match = re.search(rf'(?m)^Window (\d+): "{re.escape(title)}"', catalogue)
    if match is None:
        raise RuntimeError(f"{title!r} was not present in the public window catalogue")
    return int(match[1])


def sample_after(path: Path) -> dict:
    after = read_state(path).get("sample", 0)
    return wait_state(path, lambda state: state.get("sample", 0) > after, 2)


def owned_native_workers() -> dict[int, int]:
    """Return isolated worker PID to supervisor PID within this probe's descendants."""
    query = rf"""
        $all = @(Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,Name,CommandLine)
        $owned = @{{ {os.getpid()} = $true }}
        do {{
            $count = $owned.Count
            foreach ($process in $all) {{
                if ($owned.ContainsKey([int]$process.ParentProcessId)) {{
                    $owned[[int]$process.ProcessId] = $true
                }}
            }}
        }} while ($owned.Count -ne $count)
        $workers = @($all | Where-Object {{
            $_.Name -eq 'computer-use-driver.exe' -and $owned.ContainsKey([int]$_.ProcessId) -and
            $_.CommandLine -match '(?:^|\s)--native-worker(?:\s|$)'
        }} | Select-Object ProcessId,ParentProcessId)
        ConvertTo-Json -InputObject $workers -Compress
    """
    output = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return {row["ProcessId"]: row["ParentProcessId"] for row in json.loads(output.stdout)}


def arm_blocking_provider(signal: Path | None, report: dict) -> dict[int, int]:
    if signal is None:
        return {}
    workers = owned_native_workers()
    if not workers:
        raise RuntimeError("The fixture read did not start an isolated native worker")
    report["workers_before"] = workers
    signal.with_suffix(signal.suffix + ".arm").touch()
    return workers


def verify_provider_recovery(signal: Path | None, before: dict[int, int], report: dict) -> None:
    if signal is None:
        return
    after = owned_native_workers()
    report["workers_after"] = after
    if not signal.exists():
        raise RuntimeError("The real UIA provider's blocking property was not entered")
    report["provider_evidence"] = signal.read_text(encoding="utf-8")
    if before.keys() & after.keys() or not set(before.values()) & set(after.values()):
        raise RuntimeError("The blocked native worker was not replaced under the same supervisor")


def check_preparation(app, catalogue: str, ready: dict, path: Path, report: dict) -> bool:
    ref = window_ref(catalogue, ready["restore_title"])
    initial = sample_after(path)
    if not initial.get("restore_minimized") or not initial.get("foreground_hwnd"):
        raise RuntimeError(f"Restore target is not minimized at the start of the read: {initial}")
    tree = app.read(view="tree", window=ref, full=True)
    after_tree = sample_after(path)
    image = app.read(view="image", window=ref, full=True)
    after_image = sample_after(path)
    report["ordinary"] = {"after_tree": after_tree, "after_image": after_image, "image_text": image.text}
    prepared = app.read(view="image", window=ref, allow_foreground=True, full=True)
    after_prepared = sample_after(path)
    receipt = dict(prepared.preparation or {})
    report["prepared"] = {"actual": after_prepared, "receipt": receipt, "text": prepared.text}
    return (
        tree.preparation is None
        and image.preparation is None
        and "Showing the visible desktop" in image.text
        and all(
            state.get("foreground_hwnd") == initial["foreground_hwnd"] and state.get("restore_minimized")
            and state.get("restore_activations") == initial.get("restore_activations")
            for state in (after_tree, after_image)
        )
        and receipt.get("activation") == "requested"
        and receipt.get("foreground") is True
        and receipt.get("redraw") == "unknown"
        and not after_prepared.get("restore_minimized")
        and after_prepared.get("restore_foreground") is True
        and prepared.screenshot is not None
        and "Showing the visible desktop" not in prepared.text
        and after_prepared.get("target_clicks") == after_prepared.get("bystander_clicks") == 0
    )


async def mcp_visual_click(app_name: str, ready: dict, report: dict, signal: Path | None, click_count: int) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp.server"],
        cwd=str(FIXTURE.resolve().parents[2]),
        env={"PYTHONSAFEPATH": "1"},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=40)) as client,
    ):
        await client.initialize()

        async def call(code):
            result = await client.call_tool("computer_use", {"code": code})
            text = "\n".join(block.text for block in result.content if block.type == "text")
            if result.isError:
                raise RuntimeError(text)
            return text, [block.model_dump(by_alias=True) for block in result.content if block.type == "image"]

        catalogue, _ = await call(f"app = computer.app({app_name!r})\nprint(app.read(view='image', full=True).text)")
        report["catalogue"] = catalogue
        ref = window_ref(catalogue, ready["zero_title"])
        before = arm_blocking_provider(signal, report)
        view = "both" if signal is not None else "image"
        started = time.monotonic()
        text, images = await call(f"obs = app.read(view={view!r}, window={ref}, full=True)\nprint(obs.text)")
        report["read_seconds"] = round(time.monotonic() - started, 3)
        report["observation"] = text
        verify_provider_recovery(signal, before, report)
        if "Showing the visible desktop" not in text or len(images) != 1:
            raise RuntimeError("Failed window capture did not deliver one desktop fallback image to MCP")
        caption = images[0].get("_meta", {}).get("computer-use", {}).get("caption", "")
        report["caption"] = caption
        point_example = "Image point: p = computer.point((x, y))"
        click_example = "Global click: computer.click(target=p)"
        if point_example not in caption or click_example not in caption:
            raise RuntimeError("MCP image caption did not supply the desktop point and input route")
        point, size = marker_point(base64.b64decode(images[0]["data"]))
        report["image_size"], report["image_point"] = size, point
        receipt, _ = await call(
            f"for _ in range({click_count}):\n"
            f"    p = computer.point({point!r})\n"
            "    result = computer.click(target=p)\n"
            "    print(result.status)"
        )
        statuses = re.findall(r"(?m)^(dispatched|verified)$", receipt)
        report["receipt"] = {"status": statuses[-1] if len(statuses) == click_count else None, "text": receipt}


def live_workflow(
    exe: Path, state_path: Path, *, preparation: bool, mcp: bool, blocking: bool, retained_image: bool,
    capture_timeout: bool, click_count: int,
) -> dict:
    command = [str(exe), str(state_path), uuid.uuid4().hex[:10]]
    signal = state_path.with_suffix(state_path.suffix + ".blocked") if blocking else None
    if preparation:
        command.append("prepare")
    elif blocking:
        command.append("block-provider")
    fixture = subprocess.Popen(command)
    try:
        def fixture_ready(state):
            return state.get("foreground_hwnd") and state.get("zero_visible") and state.get("zero_bounds_measured")

        ready = wait_state(state_path, fixture_ready, 8)
        if not fixture_ready(ready):
            raise RuntimeError(f"Fixture could not establish a visible zero-geometry window: {ready}")
        if ready["zero_width"] != 0 or ready["zero_height"] != 0:
            raise RuntimeError(f"Fixture did not reproduce empty geometry: {ready}")
        driver = Path(native_driver()["command"])
        fault_driver = capture_fault_driver(driver, state_path.parent / "capture-fault-driver") if capture_timeout else None
        report = {
            "module": computer_use.__file__,
            "driver": str(driver),
            "executed_driver": str(fault_driver or driver),
            "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "transport": "mcp" if mcp else "sdk",
            "blocking_provider": blocking,
            "retained_image": retained_image,
            "capture_timeout": capture_timeout,
            "requested_clicks": click_count,
            "initial": ready,
            "pass": False,
        }
        try:
            if not preparation and not ready.get("foreground"):
                # Set up only the disposable fixture. The measured read and
                # click still use their own SDK or stdio MCP session.
                with Computer() as computer:
                    app = computer.app(exe.stem)
                    ref = window_ref(app.read(view="image", full=True).text, ready["main_title"])
                    try:
                        setup = app.read(view="image", window=ref, allow_foreground=True, full=True)
                        report["fixture_setup"] = {"preparation": dict(setup.preparation or {})}
                    except ComputerUseError as error:
                        report["fixture_setup"] = {"error": error.detail}
                report["fixture_setup"]["actual"] = sample_after(state_path)
            if mcp:
                asyncio.run(mcp_visual_click(exe.stem, ready, report, signal, click_count))
            else:
                sdk_visual_click(
                    exe.stem, ready, state_path, report, signal, preparation=preparation,
                    retained_image=retained_image, fault_driver=fault_driver,
                )
                if preparation:
                    return report
        except (ComputerUseError, ExceptionGroup, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            detail = "".join(traceback.format_exception(error)) if isinstance(error, ExceptionGroup) else str(error)
            report["error"] = {"type": type(error).__name__, "detail": detail}
        actual = wait_state(state_path, lambda state: state.get("target_clicks", 0) >= click_count, 2)
        report["actual"] = actual
        report["pass"] = (
            report.get("receipt", {}).get("status") in {"dispatched", "verified"}
            and actual.get("target_clicks") == click_count
            and actual.get("bystander_clicks") == 0
        )
        return report
    finally:
        if fixture.poll() is None:
            fixture.terminate()
        fixture.wait(timeout=5)


def sdk_visual_click(
    app_name: str, ready: dict, state_path: Path, report: dict, signal: Path | None, *,
    preparation: bool, retained_image: bool, fault_driver: Path | None,
) -> None:
    driver_route = patch(
        "computer_use.native.connection.native_driver", return_value={"command": str(fault_driver), "args": []},
    ) if fault_driver is not None else nullcontext()
    with driver_route, Computer() as computer:
        app = computer.app(app_name)
        catalogue = app.read(view="image", full=True).text
        report["catalogue"] = catalogue
        if preparation:
            report["pass"] = check_preparation(app, catalogue, ready, state_path, report)
            return
        ref = window_ref(catalogue, ready["main_title"] if fault_driver is not None else ready["zero_title"])
        old_target = None
        if retained_image:
            main = window_ref(catalogue, ready["main_title"])
            old = app.read(view="image", window=main, full=True)
            if old.screenshot is None or "Showing the visible desktop" in old.text:
                raise RuntimeError("The original marker window did not deliver a window image")
            point, size = marker_point(old.screenshot)
            old_target = app.point(point, window=main)
            report["retained_window"] = {"ref": main, "image_size": size, "image_point": point}
        before = arm_blocking_provider(signal, report)
        started = time.monotonic()
        observation = app.read(view="both" if signal is not None else "image", window=ref, full=True)
        report["read_seconds"] = round(time.monotonic() - started, 3)
        report["observation"] = observation.text
        verify_provider_recovery(signal, before, report)
        if fault_driver is not None:
            verify_capture_fault(fault_driver, report)
        if "Showing the visible desktop" not in observation.text or observation.screenshot is None:
            raise RuntimeError("Failed window capture did not deliver the desktop fallback")
        point, size = marker_point(observation.screenshot)
        report["image_size"], report["image_point"] = size, point
        result = app.click(at=old_target, window=main) if old_target is not None else computer.click(
            target=computer.point(point)
        )
        report["receipt"] = {key: getattr(result, key) for key in ("status", "detail", "observation_status")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preparation", action="store_true", help="Compare ordinary and authorized window reads")
    mode.add_argument("--mcp", action="store_true", help="Run the visual click through a separate stdio MCP process")
    mode.add_argument("--capture-timeout", action="store_true", help="Hang both isolated capture helpers")
    parser.add_argument("--blocking-provider", action="store_true", help="Permanently block a real UIA property")
    parser.add_argument(
        "--retained-window-image", action="store_true", help="Use a window point delivered before worker replacement",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--click-count", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if args.preparation and args.blocking_provider:
        parser.error("--preparation and --blocking-provider require different fixtures")
    if args.capture_timeout and args.blocking_provider:
        parser.error("--capture-timeout and --blocking-provider are independent faults")
    if args.retained_window_image and (not args.blocking_provider or args.mcp):
        parser.error("--retained-window-image requires --blocking-provider in SDK mode")
    if args.click_count != 1 and not args.mcp:
        parser.error("--click-count=2 tests consecutive MCP points in one call")
    with tempfile.TemporaryDirectory(prefix="cua-visual-") as folder:
        exe = Path(folder) / "CuaVisualFixture.exe"
        build_fixture(exe)
        if args.build_only:
            return
        report = live_workflow(
            exe, Path(folder) / "state.json", preparation=args.preparation, mcp=args.mcp,
            blocking=args.blocking_provider, retained_image=args.retained_window_image,
            capture_timeout=args.capture_timeout, click_count=args.click_count,
        )
    serialized = json.dumps(report, ensure_ascii=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    sys.stdout.write(serialized + "\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
