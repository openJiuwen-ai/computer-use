# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Edit and submission transcript checks for Windows capture and tree regressions."""

import ctypes
import json
import re
import subprocess
from ctypes import wintypes

from computer_use.native.connection import Connection


_ORIGINAL_REQUEST = Connection.request


_CAPTURE_ATTEMPTS = []


async def tree_only_request(self, method, *args, **kwargs):
    if method == "capture":
        _CAPTURE_ATTEMPTS.append(method)
        raise AssertionError("The focus-change probe prohibits native capture")
    return await _ORIGINAL_REQUEST(self, method, *args, **kwargs)


def captured_interface_metadata(observation):
    """Read one observation's retained window catalogue without another UI read."""
    prefix = "Captured interface metadata: "
    content, cursor = "", None
    for _ in range(16):
        page = observation.read_details(cursor=cursor, length=16384)
        content += page.text
        if prefix in content:
            try:
                value, _ = json.JSONDecoder().raw_decode(content.split(prefix, 1)[1])
            except json.JSONDecodeError:
                pass
            else:
                return value
        cursor = page.next
        if cursor is None:
            break
    raise RuntimeError("No complete captured interface metadata within the bounded detail budget; no input sent")


def element_ref(text: str, expression: str) -> int:
    matches = set(re.findall(expression, text, re.MULTILINE))
    if len(matches) != 1:
        raise RuntimeError("The fixture control could not be uniquely located in delivered UIA facts")
    return int(next(iter(matches)))


INTERFACE_METADATA_PREFIX = "Captured interface metadata: "


SELECT_ALL_KEY = "ctrl+a"


EDITABLE = "Editable draft"


COMPANION = "Protected companion"


ORIGINAL = "Original draft value"


UNTOUCHED = "Untouched companion value"


_EDIT_ROOT = None


_EDIT_WRITES = []


def require_foreground(fixture: subprocess.Popen) -> None:
    """Verify raw input would go to this attempt's still-live process."""
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    actual = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(actual))
    if actual.value != fixture.pid or fixture.poll() is not None:
        raise RuntimeError(f"Expected fixture PID {fixture.pid} in foreground, saw PID {actual.value}")


def require_owned_fixture(fixture: subprocess.Popen, title: str) -> None:
    """Verify the exact owned window while letting the targeted operation prepare it."""
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    hwnd, actual = user32.FindWindowW(None, title), wintypes.DWORD()
    if hwnd:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(actual))
    if not hwnd or not user32.IsWindowVisible(hwnd) or actual.value != fixture.pid or fixture.poll() is not None:
        raise RuntimeError("The exact edit window is no longer visible and owned by this fixture")


def select_fixture_window(report: dict, metadata: dict) -> int:
    windows = [{"ref": item["ref"], "title": item["title"]} for item in metadata["windows"]]
    report["catalogue_windows"] = windows
    matches = [item["ref"] for item in windows if item["title"] == report["title"]]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one exact window {report['title']!r}, got {matches}")
    report["window_ref"] = matches[0]
    return matches[0]


def sdk_window_ref(report: dict, catalogue) -> int:
    report["catalogue"] = catalogue.text
    return select_fixture_window(report, captured_interface_metadata(catalogue))


def mcp_window_ref(report: dict, response: str) -> int:
    catalogue, prefix, details = response.partition(INTERFACE_METADATA_PREFIX)
    report["catalogue"] = catalogue.rstrip()
    if not prefix:
        raise RuntimeError("MCP did not deliver captured interface metadata")
    metadata, _ = json.JSONDecoder().raw_decode(details)
    return select_fixture_window(report, metadata)


def record_receipt(report: dict, action: str, status: str | None, detail: str) -> None:
    report["actions"].append({"action": action, "status": status, "detail": detail})
    if status not in {"dispatched", "verified"}:
        raise RuntimeError(f"{action} returned {status}: {detail}")


def tree_ref(text: str, label: str) -> int:
    # The public tree can compact adjacent controls with a pipe and omit empty flags.
    rows = re.sub(r"[ \t]+\|[ \t]+(?=\d+[ \t])", "\n", text)
    role = r"(?:[^\r\n()]+[ \t]+\([^\r\n()]+\)|[^\s()]+)"
    return element_ref(rows, rf"^[ \t]*(?:Region:[ \t]+)?(\d+)[ \t]+{role}[ \t]+{re.escape(label)}(?:, [^\r\n]+)?$")


def tree_value(text: str, label: str) -> tuple[int, str]:
    matches = set(
        re.findall(
            rf"(?m)^[ \t]*(?:Region:[ \t]+)?(\d+)[ \t]+[^\r\n]+\) {re.escape(label)}, "
            r"(?:Value|Document text): ([^\r\n]+)$",
            text,
        )
    )
    if len(matches) != 1:
        raise RuntimeError(f"The complete value of {label!r} was not delivered unambiguously")
    ref, raw = matches.pop()
    value = json.JSONDecoder().raw_decode(raw)[0] if raw.startswith('"') else raw.partition(", ")[0]
    return int(ref), value


async def tree_edit_request(self, method, params, options=None):
    if method == "execute" or (method == "begin" and params.get("mode") == "write"):
        command = params.get("command", {})
        target = (command if method == "execute" else params).get("target", {})
        if _EDIT_ROOT is None or target.get("rootWindow") != _EDIT_ROOT or target.get("global"):
            raise AssertionError("The tree edit tried to write outside its exact owned window")
        _EDIT_WRITES.append({"method": method, "root": _EDIT_ROOT, "unit": command.get("kind")})
    return await tree_only_request(self, method, params, options)


async def tree_edit(read, act, fixture: subprocess.Popen, report: dict) -> None:
    window_id = None
    observed_ids = set()
    event_dependent = report["tier"] == "full-tree-event"

    async def check(phase, expected, status, *, foreground=False):
        nonlocal window_id
        delivery = await read(foreground)
        report[phase] = delivery
        if delivery["has_screenshot"] or delivery["capture_attempts"]:
            raise RuntimeError(f"{phase} attempted native capture or delivered pixels")
        text, metadata = delivery["text"], delivery["metadata"]
        (window,) = metadata["windows"]
        actual_id = window["ref"], window["native_window"]
        if window["title"] != report["title"] or (window_id is not None and window_id != actual_id):
            raise RuntimeError("Semantic edit changed its exact window identity")
        window_id = actual_id
        directory = metadata["directory"]
        observation_id = directory["observed"]["id"]
        if observation_id in observed_ids or [row["window"] for row in directory["surfaces"]] != [window_id[1]]:
            raise RuntimeError("The semantic edit did not receive a fresh exact-window read")
        observed_ids.add(observation_id)
        if "AX: collected; Image: not requested." not in text:
            raise RuntimeError("Expected delivered AX without an image request")
        ref, value = tree_value(text, EDITABLE)
        if value != expected:
            raise RuntimeError(f"Expected exact publicly delivered draft {expected!r}, received {value!r}")
        tree_ref(text, status)
        if report["tier"] != "partial-tree":
            _, companion = tree_value(text, COMPANION)
            if companion != UNTOUCHED:
                raise RuntimeError("The publicly delivered companion value changed")
        elif COMPANION in text or UNTOUCHED in text:
            raise RuntimeError("The deliberately painted companion unexpectedly appeared in AX")
        return ref, tree_ref(text, "Submit draft")

    async def apply(action, ref):
        require_owned_fixture(fixture, report["title"])
        result = await act(ref, action, report["payload"], window_id[1])
        record_receipt(report, action, result["status"], result["detail"])
        report["actions"][-1].update(result, element_ref=ref)
        if result["has_screenshot"] or result["capture_attempts"]:
            raise RuntimeError(f"{action} attempted native capture or delivered pixels")
        if result["observation_status"] not in {"available", "partial"} or result["observation_error"] is not None:
            raise RuntimeError(f"{action} did not deliver its scoped tree suffix: {result!r}")
        writes = result["native_writes"]
        if {event["method"] for event in writes} != {"begin", "execute"} or any(
            event["root"] != window_id[1] for event in writes
        ):
            raise RuntimeError(f"{action} did not preserve the exact target through native write execution")
        if event_dependent:
            unit = "native_text_edit" if action == "replace" else "native_action"
            if [(event["method"], event["unit"]) for event in writes] != [("begin", None), ("execute", unit)]:
                raise RuntimeError(f"{action} did not select one native object route")

    edit, _ = await check("before", ORIGINAL, "Draft pending", foreground=not event_dependent)
    await apply("replace", edit)
    _, submit = await check("replaced", report["payload"], "Draft pending")
    await apply("invoke" if event_dependent else "submit", submit)
    await check("after", report["payload"], "Draft submitted 1")


def score_tree(actual: dict, payload: str, *, event_dependent: bool = False) -> None:
    if not (
        actual.get("text") == actual.get("submitted") == payload
        and actual.get("submits") == 1
        and actual.get("changes", 0) > 0
        and actual.get("bystander") == UNTOUCHED
        and actual.get("bystanderChanges") == 0
    ):
        raise RuntimeError(f"Fixture did not confirm one exact semantic submit and untouched companion: {actual!r}")
    if event_dependent:
        if not (
            actual.get("submitEnabled") is True
            and actual.get("enChange", 0) > 0
            and actual.get("wmSetText") == actual.get("wmPaste") == 0
            and actual.get("emSetSel") == actual.get("emReplaceSel") == 1
        ):
            raise RuntimeError(
                f"The editor did not receive one native selection replacement and its events: {actual!r}"
            )
    elif actual.get("paintedBystander") != {
        "text": UNTOUCHED,
        "submittedText": None,
        "clicks": 0,
        "selections": 0,
        "units": 0,
        "submits": 0,
    }:
        raise RuntimeError(f"Fixture recorded a change to the painted companion: {actual!r}")
