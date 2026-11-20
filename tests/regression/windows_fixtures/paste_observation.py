# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Independent exact-title and edit-value oracles for Windows regression fixtures."""

import ctypes
import json
import re
import time
from ctypes import wintypes
from pathlib import Path


INTERFACE_METADATA_PREFIX = "Captured interface metadata: "


PASTE_VALUE = re.compile(
    r"(?m)^[ \t]*(?:Region:[ \t]+)?(\d+)[ \t]+[^\n]+\) Paste target, (?:Value|Document text): ([^\r\n]+)$"
)


def fixture_window(metadata: dict, title: str, *, only: bool = False) -> int:
    if only:
        assert len(metadata["windows"]) == 1, "The image-free catalogue must have only the fixture window"
    refs = [int(window["ref"]) for window in metadata["windows"] if window["title"] == title]
    assert len(refs) == 1, "The test window was not delivered unambiguously"
    return refs[0]


def mcp_window(observed: str, title: str, report: dict) -> int:
    visible, prefix, details = observed.partition(INTERFACE_METADATA_PREFIX)
    report["catalogue"] = visible.rstrip()
    assert prefix, "MCP did not deliver captured interface metadata"
    metadata, _ = json.JSONDecoder().raw_decode(details)
    return fixture_window(metadata, title, only=report.get("image_free", False))


def fixture_foreground(hwnd: int | None, pid: int, title: str) -> int:
    """Sample Win32 identity without acquiring a window image or UI tree."""
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    current = user32.GetForegroundWindow()
    if not current or (hwnd is not None and current != hwnd):
        raise RuntimeError("The exact paste fixture HWND is not foreground")
    caption, owner = ctypes.create_unicode_buffer(512), wintypes.DWORD()
    user32.GetWindowTextW(current, caption, len(caption))
    user32.GetWindowThreadProcessId(current, ctypes.byref(owner))
    if owner.value != pid or caption.value != title:
        raise RuntimeError("The foreground PID or caption does not belong to the paste fixture")
    return int(current)


def delivered_value(observed: str) -> str:
    values = set()
    for ref, raw in PASTE_VALUE.findall(observed):
        # The full tree can show the same control as a document region. Short
        # plain scalars are rendered bare, and values with commas are quoted.
        value = json.JSONDecoder().raw_decode(raw)[0] if raw.startswith('"') else raw.partition(", ")[0]
        values.add((ref, value))
    assert len(values) == 1, "The captured paste target value was not delivered unambiguously"
    return values.pop()[1]


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def wait_state(path: Path, condition, timeout: float) -> dict:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        state = read_state(path)
        if condition(state):
            return state
        time.sleep(0.05)
    return read_state(path)
