# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise local window-only pixels and points on an owned synthetic Windows grid."""

import argparse
import asyncio
import base64
import ctypes
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from ctypes import wintypes
from datetime import timedelta
from pathlib import Path

from computer_use import Computer
from computer_use.paths import native_driver
from computer_use.protocol.captures import capture_metadata
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from windows_coordinate_grid_workflow import FIXTURE, build_fixture, record_image
from windows_paste_workflow import read_state, wait_state

GATE_SOURCE = Path(__file__).with_name("windows_owned_capture_gate.cs")


def require_owned_window(expected, *, foreground=True):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    hwnd = expected["hwnd"]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetPropW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
    user32.GetPropW.restype = wintypes.HANDLE
    if (
        not hwnd
        or user32.GetAncestor(hwnd, 2) != hwnd
        or user32.GetWindow(hwnd, 4)
        or not user32.IsWindowVisible(hwnd)
        or user32.IsIconic(hwnd)
        or user32.GetPropW(hwnd, expected["property"]) != 1
    ):
        raise RuntimeError("The visible synthetic root or its lifetime marker is unconfirmed")
    if foreground and user32.GetForegroundWindow() != hwnd:
        raise RuntimeError("The owned fixture root is not the current foreground window")
    pid, caption = wintypes.DWORD(), ctypes.create_unicode_buffer(256)
    if (
        not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        or pid.value != expected["pid"]
        or not user32.GetWindowTextW(hwnd, caption, len(caption))
        or caption.value != expected["title"]
    ):
        raise RuntimeError("The owned fixture's process or caption changed")
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    times = (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *times)
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if not handle:
        raise RuntimeError("The owned fixture process could not be opened")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            raise RuntimeError("The owned fixture process lifetime could not be read")
        creation = values[0]
        ticks = (creation.dwHighDateTime << 32) + creation.dwLowDateTime + 504911232000000000
        if str(ticks) != expected["started"]:
            raise RuntimeError("The owned fixture process was replaced")
    finally:
        kernel.CloseHandle(handle)


def require_reachable_point(expected, point):
    require_owned_window(expected, foreground=False)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetThreadDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    active = user32.GetForegroundWindow()
    if not active or (active != expected["hwnd"] and user32.IsWindowVisible(active)):
        raise RuntimeError("The passive click probe requires its own window or a hidden foreground window")
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise RuntimeError("Physical hit-test DPI awareness could not be established")
    try:
        hit = user32.WindowFromPoint(wintypes.POINT(*point))
        if not hit or user32.GetAncestor(hit, 2) != expected["hwnd"]:
            raise RuntimeError("The delivered screen point does not hit the exact owned window")
    finally:
        if not user32.SetThreadDpiAwarenessContext(previous):
            raise RuntimeError("The previous hit-test DPI awareness could not be restored")


class OwnedCaptureRequests:
    """Allow only the proven strict root's window capture before the wire send."""

    def __init__(self, connection, expected, driver, *, observe_only=False, natural=False, prepare_foreground=False):
        if connection._child is not None or connection.session_identity is not None:
            raise RuntimeError("The capture gate must be installed before native startup")
        self.connection, self.expected, self.original = connection, expected, connection.request
        self.selection, self.binding = None, None
        self.operations, self.approved, self.denied = set(), 0, 0
        self.observe_only, self.denied_actions = observe_only, 0
        self.allow_inactive = natural or prepare_foreground
        self.prepare_foreground = prepare_foreground
        self.require_foreground = not (observe_only or self.allow_inactive)
        self.captures, self.writes, self.approved_clicks = {}, {}, 0
        self.preparation_offers, self.click_attempts = {}, []
        self.click_target, self.click_size = None, None
        self.click_origin = None
        connection.driver = {"command": driver, "args": []}
        connection.request = self.request

    def arm_clicks(self, proof):
        capture = proof["capture"]
        size = self.captures.get(capture)
        if self.observe_only or self.binding is None or size != tuple(proof["native_dimensions"]):
            raise RuntimeError("The delivered point proof does not identify an approved owned capture")
        self.click_target = {**self.binding, "capture": capture}
        self.click_size = size
        if self.allow_inactive:
            matrix = proof["coordinates"]["toNative"]
            if matrix[:4] != [1, 0, 0, 1] or any(int(value) != value for value in matrix[4:]):
                raise RuntimeError("The passive click probe requires an exact physical window translation")
            self.click_origin = tuple(int(value) for value in matrix[4:])

    def require_click(self, method, params):
        if self.click_target is None or self.connection.session != self.click_target["session"]:
            raise RuntimeError("No delivered owned capture authorized this input")
        physical = None
        if method == "begin":
            intent = params.get("intent", {})
            options = intent.get("parameters", {})
            if (
                params.get("mode") != "write"
                or params.get("target") != self.click_target
                or set(intent) != {"kind", "parameters"}
                or intent["kind"] != "click"
                or set(options) != {"button", "count", "x", "y"}
                or options["button"] != "left"
                or type(options["count"]) is not int
                or options["count"] != 1
                or any(
                    type(options[key]) not in (int, float) or not 0 <= options[key] < bound
                    for key, bound in zip(("x", "y"), self.click_size)
                )
            ):
                raise RuntimeError("The native begin did not address one left click in the owned capture")
            if self.allow_inactive:
                if self.click_origin is None or any(int(options[key]) != options[key] for key in ("x", "y")):
                    raise RuntimeError("The passive click point is not an exact physical pixel")
                physical = tuple(origin + int(options[key]) for origin, key in zip(self.click_origin, ("x", "y")))
        else:
            command = params.get("command", {})
            recorded = self.writes.get(params.get("operation"))
            if (
                recorded is None
                or recorded[0] != self.click_target
                or command.get("target") != self.click_target
                or command.get("kind") != "pointer_sequence"
                or set(command) != {"kind", "primitiveId", "target", "foreground"}
                or not isinstance(command["primitiveId"], str)
                or not command["primitiveId"]
                or command.get("foreground") is not self.prepare_foreground
                or type(params.get("step")) is not int
                or params["step"] != 1
            ):
                raise RuntimeError("The native execute changed the owned click or its foreground preparation")
            if self.prepare_foreground and self.preparation_offers.get(params["operation"]) != command["primitiveId"]:
                raise RuntimeError("The native execute lacks the exact foreground preparation offer")
            physical = recorded[1]
        if self.allow_inactive:
            if physical is None:
                raise RuntimeError("The passive native click has no independently verified point")
            require_reachable_point(self.expected, physical)
        else:
            require_owned_window(self.expected)
        return physical

    async def request(self, method, params, options=None):
        if self.observe_only and (
            method in {"execute", "prepareObservation"} or (method == "begin" and params.get("mode") != "read")
        ):
            self.denied_actions += 1
            raise RuntimeError("The observe-only probe refused native preparation or input before send")
        if self.allow_inactive and method == "prepareObservation":
            self.denied_actions += 1
            raise RuntimeError("The passive click probe refused observation preparation before send")
        write_begin = method == "begin" and params.get("mode") != "read"
        physical = None
        if write_begin or method == "execute":
            try:
                physical = self.require_click(method, params)
            except RuntimeError:
                self.denied_actions += 1
                raise
            if method == "execute":
                self.writes.pop(params["operation"])
                self.approved_clicks += 1
        if method == "capture":
            try:
                if (
                    params.get("kind") != "window"
                    or self.binding is None
                    or params.get("target") != self.binding
                    or params.get("operation") not in self.operations
                    or self.connection.session != self.binding["session"]
                ):
                    raise RuntimeError("Capture scope refused before native send")
                require_owned_window(self.expected, foreground=self.require_foreground)
            except RuntimeError:
                self.denied += 1
                raise
            self.approved += 1
        result = await self.original(method, params, options)
        query = params.get("query", {})
        if method == "inspect" and "windowTitle" in query:
            require_owned_window(self.expected, foreground=self.require_foreground)
            if (
                query["windowTitle"] != self.expected["title"]
                or result.get("windowTitle") != {"title": self.expected["title"], "complete": True}
                or result.get("session") != self.connection.session
            ):
                raise RuntimeError("The exact fixture caption is not uniquely proven by native discovery")
            ((application, window),) = [(app, item) for app in result["applications"] for item in app["windows"]]
            self.selection = (result["session"], application["id"], window["window"], window["selector"])
        elif method == "inspect" and self.selection is not None and query.get("kind") == "surfaces":
            session, application, root, instance = self.selection
            if query.get("application") == application and result.get("session") == session:
                require_owned_window(self.expected, foreground=self.require_foreground)
                (row,) = [
                    item
                    for item in result["surfaces"]
                    if (
                        item["window"] == root
                        and item["instance"] == instance
                        and item["title"] == self.expected["title"]
                        and item["nativeWindow"]
                        and item["parent"] is None
                    )
                ]
                binding = {
                    "session": session,
                    "instance": instance,
                    "rootWindow": root,
                    "surface": row["surface"],
                    "node": None,
                    "capture": None,
                }
                if self.binding is not None and self.binding != binding:
                    raise RuntimeError("The owned root changed its native capture binding")
                self.binding = binding
        elif method == "begin" and params.get("mode") == "read" and self.binding is not None:
            if params.get("target") == self.binding and result.get("target") == self.binding:
                self.operations.add(result["operation"])
        elif write_begin:
            if result.get("target") != self.click_target:
                raise RuntimeError("The native write lease did not retain the exact owned capture")
            self.writes[result["operation"]] = (self.click_target, physical)
        elif self.prepare_foreground and method == "inspect" and query.get("kind") == "inputContext":
            operation = params.get("operation")
            if operation in self.writes and result.get("binding") == self.click_target:
                eligible = [
                    row["id"]
                    for row in result["primitives"]
                    if row["unit"] == "pointer_sequence"
                    and (row.get("click") or {}).get("routeFamily") == "windows.sendInput"
                    and row["click"]["admission"] == "preparationRequired"
                    and row["click"]["preparationOptions"] == ["foreground"]
                ]
                if len(eligible) == 1:
                    self.preparation_offers[operation] = eligible[0]
        elif method == "execute" and self.allow_inactive:
            self.click_attempts.append(
                {key: result.get(key) for key in ("dispatch", "confirmation", "preparation", "cleanup", "click")}
            )
        elif method == "capture" and not self.observe_only:
            image = capture_metadata(result["image"])
            if (
                result.get("kind") != "capture"
                or result.get("binding") != self.binding
                or image is None
                or image["kind"] != "window"
                or image["scope"] != self.binding["surface"]
            ):
                raise RuntimeError("The native capture reply did not retain the owned window scope")
            self.captures[image["id"]] = (image["width"], image["height"])
        elif method == "finish":
            self.operations.discard(params.get("operation"))
            self.writes.pop(params.get("operation"), None)
            self.preparation_offers.pop(params.get("operation"), None)
        return result


def gated_driver(output: Path, driver: Path) -> dict:
    """Keep the driver binary exact. Add two guards to the isolated worker source copy."""
    output.mkdir()
    native = driver.parents[1]
    program = native / "Workers/Capture/Program.cs"
    source = program.read_text(encoding="utf-8-sig")
    for before, guard in (
        ('var h = new IntPtr(Convert.ToInt64(p["handle"]));', "OwnedCaptureGate.Require(p);"),
        ("if (Bounds(h) != before || IsIconic(h))", "OwnedCaptureGate.VerifyPixels(p, bitmap);"),
    ):
        if source.count(before) != 1:
            raise RuntimeError("The capture worker source changed; review the test-only injection boundary")
        source = source.replace(before, guard + "\n                " + before, 1)
    guarded_source = output / "GuardedCaptureProgram.cs"
    guarded_source.write_text(source, encoding="utf-8")
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    metadata = framework.parents[2] / "System32/WinMetadata"
    refs = [
        *(
            framework / name
            for name in (
                "System.dll",
                "System.Core.dll",
                "System.Web.Extensions.dll",
                "System.Drawing.dll",
                "System.Runtime.WindowsRuntime.dll",
                "System.Runtime.dll",
            )
        ),
        *(
            metadata / f"Windows.{name}.winmd"
            for name in ("Foundation", "Graphics", "Storage", "Media", "Globalization")
        ),
    ]
    isolated = Path(shutil.copy2(driver, output))
    shutil.copy2(driver.with_name("Interop.NativeUIA.dll"), output)
    capture = output / "computer-use-capture.exe"
    subprocess.run(
        [
            str(framework / "csc.exe"),
            "/nologo",
            "/target:exe",
            "/platform:x64",
            "/optimize+",
            "/warnaserror+",
            f"/out:{capture}",
            *(f"/reference:{path}" for path in refs),
            str(guarded_source),
            str(GATE_SOURCE),
            str(native / "Workers/Capture/Ocr.cs"),
            str(native / "Sources/Contracts/Wire.cs"),
            str(native / "Sources/Capture/NativeCaptureGeometry.cs"),
        ],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    return {
        "driver": str(isolated),
        "driver_sha256": hashlib.sha256(isolated.read_bytes()).hexdigest(),
        "helper_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "original_helper_sha256": hashlib.sha256(driver.with_name(capture.name).read_bytes()).hexdigest(),
        "worker_source_sha256": hashlib.sha256(program.read_bytes()).hexdigest(),
        "gate_source_sha256": hashlib.sha256(GATE_SOURCE.read_bytes()).hexdigest(),
    }


def point_evidence(observation, gate, targets):
    prefix = "Captured image metadata: "
    content, cursor = "", None
    for _ in range(16):
        page = observation.read_details(cursor=cursor, length=16384)
        content += page.text
        if prefix in content:
            try:
                image, _ = json.JSONDecoder().raw_decode(content.split(prefix, 1)[1])
            except json.JSONDecodeError:
                pass
            else:
                break
        cursor = page.next
        if cursor is None:
            raise RuntimeError("No captured point metadata was delivered")
    else:
        raise RuntimeError("Captured point metadata exceeded the probe's detail budget")
    (capture,) = image["captures"]
    binding, coordinates, native_image = gate.binding, capture["coordinates"], capture["capture"]
    if (
        image["kind"] != "single"
        or not image["mapping_available"]
        or image["windows"] != [capture["ref"]]
        or not capture["displayed"]
        or not capture["scopeConfirmed"]
        or capture["window"] != binding["rootWindow"]
        or native_image["kind"] != "window"
        or native_image["scope"] != binding["surface"]
        or coordinates["space"] != "window-pixels"
        or coordinates["nativeSpace"] != "screen-pixels"
        or coordinates["origin"] != "top-left"
        or len(coordinates["toNative"]) != 6
    ):
        raise RuntimeError("The delivered image has no confirmed mapping for the owned root")
    expected = (binding["session"], binding["instance"], binding["rootWindow"], binding["surface"], native_image["id"])
    native_size = [native_image["width"], native_image["height"]]
    delivery = targets[0]._image.delivered_coordinates
    for target in targets:
        value = target._binding
        if (
            value.session,
            value.instance,
            value.root_window,
            value.surface,
            value.capture,
        ) != expected or target._image.delivered_coordinates != delivery:
            raise RuntimeError("A retained point is bound to another capture or window")
    return {
        "ref": capture["ref"],
        "capture": native_image["id"],
        "native_dimensions": native_size,
        "dimensions": list(delivery.delivered_size) if delivery is not None else native_size,
        "coordinates": coordinates,
        "points": len(targets),
    }


def sdk_grid(expected, driver, count, report, *, observe_only, natural, prepare_foreground):
    with Computer(allow_global_input=not observe_only) as computer:
        gate = OwnedCaptureRequests(
            computer._controller.transport,
            expected,
            driver,
            observe_only=observe_only,
            natural=natural,
            prepare_foreground=prepare_foreground,
        )
        try:
            app = computer.app(window_title=expected["title"])
            observation = app.read(view="image", full=True)
            if observation.screenshot is None:
                raise RuntimeError(observation.text)
            points = record_image(report, bytes(observation.screenshot), centered=report["centered"])
            targets = [app.point(point) for point in points[:count]]
            proof = report["point_evidence"] = point_evidence(observation, gate, targets)
            if not observe_only:
                gate.arm_clicks(proof)
                for target in targets:
                    require_owned_window(expected, foreground=gate.require_foreground)
                    result = app.click(at=target)
                    report["receipts"].append(result.status)
                    if gate.allow_inactive:
                        report["click_details"].append(result.detail)
        finally:
            report.update(
                approved_requests=gate.approved,
                denied_requests=gate.denied,
                denied_actions=gate.denied_actions,
                approved_click_requests=gate.approved_clicks,
                click_attempts=gate.click_attempts,
            )


async def mcp_grid(expected, driver, count, report, log_dir, *, observe_only, natural, prepare_foreground):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp", *(["--no-allow-global-input"] if observe_only else [])],
        cwd=Path.cwd(),
        env={"PYTHONSAFEPATH": "1", "COMPUTER_USE_LOG_DIR": str(log_dir)},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=max(50, count * 3))) as client,
    ):
        await client.initialize()

        async def call(title, code):
            response = await client.call_tool("computer_use", {"title": title, "code": code})
            text = "\n".join(part.text for part in response.content if part.type == "text")
            if response.isError:
                raise RuntimeError(text)
            return text, [part for part in response.content if part.type == "image"]

        _, images = await call(
            "Read only the owned synthetic grid window",
            "import ctypes, json\nfrom ctypes import wintypes\nfrom computer_use.protocol.captures import capture_metadata\n"
            + inspect.getsource(require_owned_window)
            + "\n"
            + inspect.getsource(require_reachable_point)
            + "\n"
            + inspect.getsource(OwnedCaptureRequests)
            + "\n"
            + inspect.getsource(point_evidence)
            + f"\nexpected = {expected!r}\n"
            + f"gate = OwnedCaptureRequests(computer._controller.transport, expected, {driver!r}, "
            f"observe_only={observe_only!r}, natural={natural!r}, prepare_foreground={prepare_foreground!r})\n"
            + "app = computer.app(window_title=expected['title'])\nobs = app.read(view='image', full=True)\nobs",
        )
        (image,) = images
        caption = image.model_dump(by_alias=True).get("_meta", {}).get("computer-use", {}).get("caption", "")
        if "Image point for window=" not in caption or "App click:" not in caption or "Global click:" in caption:
            raise RuntimeError("MCP did not deliver the window image's scoped point route")
        report["caption"] = caption
        points = record_image(report, base64.b64decode(image.data, validate=True), centered=report["centered"])
        use_targets = (
            ""
            if observe_only
            else "gate.arm_clicks(proof)\nfor target in targets:\n"
            "    require_owned_window(expected, foreground=gate.require_foreground)\n"
            "    result = app.click(at=target)\n"
            "    print('CUA_GRID_RECEIPT', result.status)\n"
            "    if gate.allow_inactive:\n        print('CUA_GRID_DETAIL', json.dumps(result.detail))\n"
        )
        text, _ = await call(
            "Retain owned window points" if observe_only else "Click only owned window points",
            f"targets = [app.point(p) for p in {points[:count]!r}]\n"
            "proof = point_evidence(obs, gate, targets)\nprint('CUA_GRID_POINTS', json.dumps(proof))\n"
            + use_targets
            + "print('CUA_GRID_ATTEMPTS', json.dumps(gate.click_attempts))\n"
            "print('CUA_GRID_GATE', gate.approved, gate.denied, gate.denied_actions, gate.approved_clicks)",
        )
        report["receipts"] = re.findall(r"(?m)^CUA_GRID_RECEIPT (\w+)$", text)
        report["click_details"] = [json.loads(detail) for detail in re.findall(r"(?m)^CUA_GRID_DETAIL (.+)$", text)]
        attempts = re.search(r"(?m)^CUA_GRID_ATTEMPTS (.+)$", text)
        if attempts is None:
            raise RuntimeError("MCP omitted the native click attempt evidence")
        report["click_attempts"] = json.loads(attempts[1])
        metadata = re.search(r"(?m)^CUA_GRID_POINTS (.+)$", text)
        if metadata is None:
            raise RuntimeError("MCP omitted the retained point mapping evidence")
        report["point_evidence"] = json.loads(metadata[1])
        counts = re.search(r"(?m)^CUA_GRID_GATE (\d+) (\d+) (\d+) (\d+)$", text)
        if counts is None:
            raise RuntimeError("MCP omitted the actual wire gate counts")
        report.update(
            approved_requests=int(counts[1]),
            denied_requests=int(counts[2]),
            denied_actions=int(counts[3]),
            approved_click_requests=int(counts[4]),
        )


def mouse_buttons_released():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return all(not user32.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 4, 5, 6))


def run(
    entry,
    count,
    output,
    *,
    observe_only=False,
    foreground_wait=None,
    natural=False,
    centered=False,
    prepare_foreground=False,
):
    hits = 0 if observe_only else count
    allow_inactive = natural or prepare_foreground
    report = {
        "entry": entry,
        "requested": count,
        "requested_hits": hits,
        "observe_only": observe_only,
        "natural": natural,
        "centered": centered,
        "prepare_foreground": prepare_foreground,
        "receipts": [],
        "click_details": [],
        "pass": False,
    }
    with tempfile.TemporaryDirectory(prefix="cua-owned-window-") as temporary:
        folder = Path(temporary)
        exe, state_path = folder / "CuaOwnedGrid.exe", folder / "oracle.json"
        driver = gated_driver(folder / "driver", Path(native_driver()["command"]))
        report["runtime"] = driver
        report["fixture_source_sha256"] = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        build_fixture(exe)
        nonce = uuid.uuid4().hex[:12]
        profile = "owned-window"
        if observe_only:
            profile = "observe-window"
        elif foreground_wait is not None:
            profile = "manual-window"
        elif allow_inactive:
            profile = "natural-window"
        title = f"CUA Coordinate Grid {nonce}"
        fixture = subprocess.Popen([str(exe), str(state_path), nonce, "0", profile])
        try:
            if foreground_wait is not None:
                sys.stdout.write(f'Waiting for fixture "{title}"\n')
                sys.stdout.flush()

            def ready(row):
                return (
                    row.get("owned_window")
                    and (observe_only or row.get("foreground") is (not allow_inactive))
                    and (foreground_wait is None or mouse_buttons_released())
                )

            state = wait_state(state_path, ready, foreground_wait or 6)
            if not ready(state) or state.get("pid") != fixture.pid or state.get("hit_count") != 0:
                raise RuntimeError("The independently owned grid is not ready")
            expected = {
                "hwnd": state["hwnd"],
                "pid": fixture.pid,
                "started": state["started"],
                "title": title,
                "property": f"CUA_GRID_{nonce}",
            }
            require_owned_window(expected, foreground=not (observe_only or allow_inactive))
            if foreground_wait is not None:
                sample = state["sample"]
                state = wait_state(state_path, lambda row: ready(row) and row.get("sample", 0) >= sample + 2, 2)
                if not ready(state) or state["sample"] < sample + 2:
                    raise RuntimeError("The manual fixture selection did not settle")
                state_path.with_suffix(state_path.suffix + ".arm").write_text("armed", encoding="utf-8")
                state = wait_state(state_path, lambda row: row.get("armed") and ready(row), 2)
                if (
                    not state.get("armed")
                    or not ready(state)
                    or any(state[key] for key in ("downs", "ups", "hit_count"))
                ):
                    raise RuntimeError("The manually selected fixture did not arm with zero input counts")
                require_owned_window(expected)
            report.update(initial=state, expected=expected)
            (folder / "driver/fixture.json").write_text(json.dumps(expected), encoding="utf-8")
            if entry == "sdk":
                sdk_grid(
                    expected,
                    driver["driver"],
                    count,
                    report,
                    observe_only=observe_only,
                    natural=natural,
                    prepare_foreground=prepare_foreground,
                )
            else:
                asyncio.run(
                    mcp_grid(
                        expected,
                        driver["driver"],
                        count,
                        report,
                        output.with_suffix(".mcp"),
                        observe_only=observe_only,
                        natural=natural,
                        prepare_foreground=prepare_foreground,
                    )
                )
            sample = read_state(state_path).get("sample", 0)
            actual = wait_state(
                state_path, lambda row: row.get("sample", 0) > sample and row.get("hit_count", 0) >= hits, 3
            )
            report["actual"] = actual
            require_owned_window(expected, foreground=not observe_only)
            audit = [
                json.loads(line) for line in (folder / "driver/accepted.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            report["native_palette_verified"] = audit
            mapped = report["point_evidence"]["points"] == count and report["point_evidence"]["dimensions"] == list(
                report["image"]["size"]
            )
            preparation_confirmed = any(
                effect["kind"] == "activation"
                and effect["dispatch"] == "dispatched"
                and effect["wait"] == "satisfied"
                and effect["observations"] == ["foreground:confirmed"]
                for attempt in report["click_attempts"]
                for effect in (attempt.get("click") or {}).get("preparationEvidence", [])
            )
            report["preparation_confirmed"] = preparation_confirmed
            report["pass"] = (
                mapped
                and actual["sample"] > sample
                and actual["hit_count"] == actual["downs"] == actual["ups"] == hits
                and not actual["wrong_clicks"]
                and [hit["id"] for hit in actual["hits"]] == list(range(hits))
                and len(report["receipts"]) == hits
                and all(s in ("dispatched", "verified") for s in report["receipts"])
                and (not prepare_foreground or preparation_confirmed)
                and (
                    not natural
                    or (
                        len(report["click_details"]) == hits
                        and "reachable inactive original window" in (report["click_details"][0] or "")
                    )
                )
                and 0 < len(audit) == report["approved_requests"]
                and report["denied_requests"] == 0
                and report["denied_actions"] == 0
                and report["approved_click_requests"] == hits
                and all(all(row[key] == expected[key] for key in ("hwnd", "pid", "started")) for row in audit)
            )
        except Exception as error:  # noqa: BLE001 - preserve fail-closed evidence
            report.update(error={"type": type(error).__name__, "detail": str(error)}, actual=read_state(state_path))
        finally:
            if fixture.poll() is None:
                fixture.terminate()
            fixture.wait(timeout=5)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", choices=("sdk", "mcp"), required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument(
        "--observe-only", action="store_true", help="Verify a visible owned window and point map without input"
    )
    parser.add_argument("--foreground-wait", type=int, help="Wait up to 180 seconds for manual fixture activation")
    parser.add_argument("--natural", action="store_true", help="Allow one scoped click to activate the passive grid")
    parser.add_argument("--centered", action="store_true", help="Sample marker centers in the passive click diagnostic")
    parser.add_argument("--prepare-foreground", action="store_true", help="Allow one scoped foreground-prepared click")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or not 1 <= args.count <= 50 or args.output.exists():
        parser.error("Requires local Windows, 1–50 points and a fresh output")
    if args.foreground_wait is not None and (args.observe_only or not 1 <= args.foreground_wait <= 180):
        parser.error("--foreground-wait requires strict click mode and 1–180 seconds")
    if args.natural and (args.observe_only or args.foreground_wait is not None):
        parser.error("--natural uses the passive grid without manual activation or explicit foreground preparation")
    if args.prepare_foreground and (
        args.observe_only or args.foreground_wait is not None or args.natural or args.count != 1
    ):
        parser.error("--prepare-foreground uses one click on the passive grid with no separate observation preparation")
    if args.centered and not (args.natural or args.prepare_foreground):
        parser.error("--centered is only for the passive click diagnostic")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = run(
        args.entry,
        args.count,
        args.output,
        observe_only=args.observe_only,
        foreground_wait=args.foreground_wait,
        natural=args.natural,
        centered=args.centered,
        prepare_foreground=args.prepare_foreground,
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{args.output} pass={report['pass']}\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
