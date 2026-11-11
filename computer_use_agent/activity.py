# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Optional desktop idle checks at the Agent's tool dispatch boundary."""

import asyncio
import ctypes
import math
import os
import sys
import time

_ACTIVE_WITHIN = 3.0
_RESUME_AFTER = 5.0
_MAX_WAIT = 60.0
_POLL_INTERVAL = 0.5
_INPUT_INTENTS = frozenset(("click", "drag", "scroll", "key", "insert", "paste", "replace"))


def _macos_probe():
    core_graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    seconds_since_event = core_graphics.CGEventSourceSecondsSinceLastEventType
    seconds_since_event.argtypes = (ctypes.c_int32, ctypes.c_uint32)
    seconds_since_event.restype = ctypes.c_double
    # kCGEventSourceStateHIDSystemState, kCGAnyInputEventType
    return lambda: seconds_since_event(1, 0xFFFFFFFF)


def _windows_probe():
    from ctypes import wintypes

    class LastInputInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.UINT), ("time", wintypes.DWORD)]

    last_input = ctypes.WinDLL("user32").GetLastInputInfo
    last_input.argtypes = (ctypes.POINTER(LastInputInfo),)
    last_input.restype = wintypes.BOOL
    ticks = ctypes.WinDLL("kernel32").GetTickCount
    ticks.argtypes = ()
    ticks.restype = wintypes.DWORD

    def read():
        info = LastInputInfo(size=ctypes.sizeof(LastInputInfo))
        if last_input(ctypes.byref(info)):
            return ((ticks() - info.time) & 0xFFFFFFFF) / 1000.0
        return None

    return read


def _x11_probe():
    class ScreenSaverInfo(ctypes.Structure):
        _fields_ = [
            ("window", ctypes.c_ulong),
            ("state", ctypes.c_int),
            ("kind", ctypes.c_int),
            ("til_or_since", ctypes.c_ulong),
            ("idle", ctypes.c_ulong),
            ("event_mask", ctypes.c_ulong),
        ]

    x11 = ctypes.CDLL("libX11.so.6")
    xss = ctypes.CDLL("libXss.so.1")
    x11.XOpenDisplay.argtypes = (ctypes.c_char_p,)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = (ctypes.c_void_p,)
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XCloseDisplay.argtypes = (ctypes.c_void_p,)
    x11.XCloseDisplay.restype = ctypes.c_int
    xss.XScreenSaverQueryInfo.argtypes = (ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ScreenSaverInfo))
    xss.XScreenSaverQueryInfo.restype = ctypes.c_int

    def read():
        display = x11.XOpenDisplay(None)
        if not display:
            return None
        try:
            info = ScreenSaverInfo()
            if xss.XScreenSaverQueryInfo(display, x11.XDefaultRootWindow(display), ctypes.byref(info)):
                return info.idle / 1000.0
            return None
        finally:
            x11.XCloseDisplay(display)

    return read


def build_idle_probe():
    try:
        if sys.platform == "darwin":
            return _macos_probe()
        if sys.platform == "win32":
            return _windows_probe()
        if sys.platform.startswith("linux") and os.getenv("DISPLAY"):
            if os.getenv("XDG_SESSION_TYPE") != "wayland" and not os.getenv("WAYLAND_DISPLAY"):
                return _x11_probe()
    except (OSError, AttributeError):
        pass
    return None


class DesktopPause:
    def __init__(self, probe):
        self._probe = probe
        self._last_input = None
        self._reported_unavailable = False

    def record_input(self, start, end, progress):
        # These receipts establish input took place during the call. Idle time
        # can only approximate attribution when other input overlaps that call.
        if any(
            receipt.get("status") in ("verified", "dispatched") and receipt.get("intent") in _INPUT_INTENTS
            for receipt in progress.get("receipts", ())
        ):
            self._last_input = (start, end)

    async def _idle(self):
        if self._probe is None:
            return None
        try:
            idle, observed_at = await asyncio.to_thread(self._sample)
        except (OSError, ValueError):
            return None
        if idle is None or not math.isfinite(idle) or idle < 0:
            return None
        last_event = observed_at - idle
        if self._last_input is not None and self._last_input[0] <= last_event <= self._last_input[1]:
            return math.inf
        return idle

    def _sample(self):
        return self._probe(), time.monotonic()

    async def wait(self, emit):
        """Return a model advisory when user activity supersedes the pending call."""
        idle = await self._idle()
        if idle is None:
            if not self._reported_unavailable:
                self._reported_unavailable = True
                emit("desktop_pause_unavailable", {})
            return None
        if idle >= _ACTIVE_WITHIN:
            return None

        emit("desktop_paused", {})
        deadline = time.monotonic() + _MAX_WAIT
        while idle is not None and idle < _RESUME_AFTER:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "The desktop is still busy. This call was not sent. Wait for the user before continuing."
            await asyncio.sleep(min(remaining, _POLL_INTERVAL))
            idle = await self._idle()
        if idle is None:
            return "The desktop was active and its activity can no longer be measured. This call was not sent."
        emit("desktop_resumed", {})
        return (
            "The desktop is idle again. This call was not sent because the user interacted with the desktop. "
            "Observe the affected interface again before sending more input."
        )
