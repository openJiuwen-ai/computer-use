# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Scoped Windows capture and click gate exercised with substitute native calls."""

import ctypes
from ctypes import wintypes

from computer_use.protocol.captures import capture_metadata


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
