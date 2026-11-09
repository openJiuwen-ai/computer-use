# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Contrast whole-value replacement and real paste in an event-dependent owned editor."""

import argparse
import ctypes
import os
import re
import subprocess
import tempfile
import uuid
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

import computer_use
import windows_edit_coverage_workflow as editing
from computer_use import Computer, ComputerUseError
from computer_use.native.connection import Connection
from computer_use.paths import native_driver
from windows_continuous_workflow import fixture_foreground
from windows_edit_coverage_workflow import (
    EDITABLE,
    FIXTURE,
    ORIGINAL,
    UNTOUCHED,
    build_fixture,
    require_owned_fixture,
    sha256,
    tree_action_delivery,
    tree_edit_request,
    tree_ref,
    tree_value,
    write_report,
)
from windows_focus_change_workflow import _CAPTURE_ATTEMPTS, read_delivery, tree_only_request
from windows_paste_budget_workflow import clipboard_sequence
from windows_paste_workflow import read_state, wait_state

FORMS_EDIT = re.compile(r"WindowsForms10\.EDIT\.app\.[0-9]+\.[0-9a-f]+_r[0-9]+_ad[0-9]+", re.IGNORECASE)
EM_GETSEL, EM_SETSEL, EM_REPLACESEL = 0x00B0, 0x00B1, 0x00C2
EDIT_MESSAGE_FLAGS = 0x0002 | 0x0020  # SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT


def read_owned(app, title, text):
    delivery = read_delivery(app, False)
    assert not delivery["has_screenshot"] and delivery["capture_attempts"] == 0
    metadata = delivery["metadata"]
    (window,) = metadata["windows"]
    assert window["title"] == title and metadata["scope"] == "window"
    root = window["native_window"]
    assert [row["window"] for row in metadata["directory"]["surfaces"]] == [root]
    body = delivery["text"]
    edit, value = tree_value(body, EDITABLE)
    assert value == text
    return root, edit, tree_ref(body, "Submit draft")


def assert_companion(state):
    assert state["bystander"] == UNTOUCHED and state["bystanderChanges"] == 0, state


def score_setter(state, payload):
    assert state["text"] == payload and state["submits"] == 0 and state["submitted"] is None, state
    assert not state["submitEnabled"] and state["changes"] == state["enChange"] == state["wmPaste"] == 0, state
    assert_companion(state)


def score_paste(state, payload):
    assert state["text"] == payload and state["submits"] == 0 and state["submitted"] is None, state
    assert state["submitEnabled"] and state["changes"] > 0 and state["enChange"] > 0 and state["wmPaste"] == 1, state
    assert_companion(state)


def score_messages(state, payload):
    assert state["text"] == payload and state["submitEnabled"], state
    assert state["changes"] > 0 and state["enChange"] > 0, state
    assert state["emSetSel"] == state["emReplaceSel"] == 1, state
    assert state["wmSetText"] == state["wmPaste"] == 0, state
    assert_companion(state)


def owned_edit(api, fixture, title, state):
    """Cross-check the fixture's child HWND witness before each directed mutation."""
    root, edit, companion = (state[key] for key in ("rootHwnd", "editHwnd", "companionHwnd"))
    assert root and edit and companion and len({root, edit, companion}) == 3
    api.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    api.FindWindowW.restype = wintypes.HWND
    api.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    api.GetParent.argtypes = (wintypes.HWND,)
    api.GetParent.restype = wintypes.HWND
    api.IsWindowEnabled.argtypes = api.IsWindowVisible.argtypes = api.IsWindowUnicode.argtypes = (wintypes.HWND,)
    api.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    api.RealGetWindowClassW.argtypes = (wintypes.HWND, wintypes.LPWSTR, wintypes.UINT)
    api.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    api.GetWindowLongPtrW.restype = wintypes.LPARAM
    assert fixture.poll() is None and api.FindWindowW(None, title) == root and api.IsWindowVisible(root)
    for hwnd in (root, edit, companion):
        owner = wintypes.DWORD()
        assert api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)) and owner.value == fixture.pid
    assert api.GetParent(edit) == api.GetParent(companion) == root
    assert api.IsWindowEnabled(root) and api.IsWindowEnabled(edit) and api.IsWindowVisible(edit)
    assert api.IsWindowUnicode(edit)
    name, real = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
    assert api.GetClassNameW(edit, name, len(name)) and api.RealGetWindowClassW(edit, real, len(real))
    assert FORMS_EDIT.fullmatch(name.value) and real.value.casefold() in {"edit", name.value.casefold()}
    style = api.GetWindowLongPtrW(edit, -16)
    assert (style & 0x40000004) == 0x40000004 and not style & (0x800 | 0x20)  # WS_CHILD | ES_MULTILINE
    return {
        "root": root,
        "edit": edit,
        "companion": companion,
        "pid": fixture.pid,
        "class": name.value,
        "real_class": real.value,
        "style": style,
    }


def send_edit_message(api, hwnd, message, wparam, lparam, timeout):
    send = api.SendMessageTimeoutW
    send.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    )
    send.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    ctypes.set_last_error(0)
    if not send(hwnd, message, wparam, lparam, EDIT_MESSAGE_FLAGS, timeout, ctypes.byref(result)):
        raise RuntimeError(f"Edit message 0x{message:04x} failed ({ctypes.get_last_error()}); no retry")


def edit_selection(api, hwnd):
    start, end = wintypes.DWORD(), wintypes.DWORD()
    send_edit_message(api, hwnd, EM_GETSEL, ctypes.addressof(start), ctypes.addressof(end), 100)
    return start.value, end.value


def replace_by_messages(fixture, title, initial, payload, report):
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetForegroundWindow.restype = wintypes.HWND
    identity = report["edit_identity"] = owned_edit(api, fixture, title, initial)
    edit = identity["edit"]
    report["foreground_before"] = int(api.GetForegroundWindow() or 0)
    assert report["foreground_before"] != identity["root"], "Message comparison requires a background fixture"
    report["clipboard_before"] = clipboard_sequence()
    report["selection_before"] = edit_selection(api, edit)
    assert report["selection_before"][0] == report["selection_before"][1] and initial["selectionLength"] == 0
    assert owned_edit(api, fixture, title, initial) == identity
    send_edit_message(api, edit, EM_SETSEL, 0, -1, 100)
    report["selection_before_replace"] = edit_selection(api, edit)
    assert report["selection_before_replace"] == (0, len(ORIGINAL.encode("utf-16-le")) // 2)
    assert owned_edit(api, fixture, title, initial) == identity
    buffer = ctypes.create_unicode_buffer(payload)
    send_edit_message(api, edit, EM_REPLACESEL, 1, ctypes.addressof(buffer), 250)


def exercise(app, computer, fixture, path, report):
    title, payload, route = report["title"], report["payload"], report["route"]
    root, edit, submit = read_owned(app, title, ORIGINAL)
    require_owned_fixture(fixture, title)
    if route == "setter":
        result = report["receipt"] = tree_action_delivery(app, edit, "replace", payload, root)
        assert result["status"] == "verified" and not result["has_screenshot"] and result["capture_attempts"] == 0
        assert [(event["method"], event["unit"]) for event in result["native_writes"]] == [
            ("begin", None),
            ("execute", "native_text_edit"),
        ], result
    elif route == "messages":
        replace_by_messages(fixture, title, report["initial"], payload, report)
    else:
        initial = wait_state(path, lambda state: state.get("focused"), 3)
        report["before_paste"] = initial
        assert initial.get("focused") and initial.get("selectionLength") == len(ORIGINAL), initial
        report["foreground_hwnd"] = fixture_foreground(None, fixture.pid, title)
        result = computer.paste(payload)
        report["receipt"] = {"status": result.status, "observation_status": result.observation_status}
        assert report["receipt"] == {"status": "dispatched", "observation_status": "not_requested"}
        assert result.observation is None

    state = report["after_edit"] = wait_state(path, lambda row: row.get("text") == payload, 5)
    assert read_owned(app, title, payload) == (root, edit, submit)
    require_owned_fixture(fixture, title)
    if route == "setter":
        score_setter(state, payload)
        try:
            result = tree_action_delivery(app, submit, "submit", payload, root)
        except ComputerUseError as error:
            assert error.effect == "none", error
            report["submit_denial"] = {"code": error.code, "effect": error.effect}
        else:
            raise AssertionError(f"Disabled Submit admitted a write: {result!r}")
        sample = wait_state(path, lambda row: "sample" in row, 1)["sample"]
        state = report["after_submit"] = wait_state(path, lambda row: row.get("sample", 0) > sample, 2)
        assert state.get("sample", 0) > sample
        score_setter(state, payload)
        report["semantic_completion"] = False
    elif route == "messages":
        score_messages(state, payload)
        assert state["submits"] == 0 and state["submitted"] is None, state
        before = len(editing._EDIT_WRITES)
        with patch.object(Connection, "request", tree_edit_request), patch.object(editing, "_EDIT_ROOT", root):
            result = app.element(submit).perform_action("Invoke.Invoke")
        writes = editing._EDIT_WRITES[before:]
        report["submit_receipt"] = {"status": result.status, "native_writes": writes}
        assert result.status == "dispatched" and (result.observation is None or result.observation.screenshot is None)
        assert [(event["method"], event["unit"]) for event in writes] == [
            ("begin", None),
            ("execute", "native_action"),
        ], writes
        state = report["after_submit"] = wait_state(path, lambda row: row.get("submits", 0) >= 1, 5)
        score_messages(state, payload)
        assert state["submits"] == 1 and state["submitted"] == payload, state
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetForegroundWindow.restype = wintypes.HWND
        assert owned_edit(api, fixture, title, state) == report["edit_identity"]
        report["foreground_after"] = int(api.GetForegroundWindow() or 0)
        report["clipboard_after"] = clipboard_sequence()
        assert report["foreground_after"] == report["foreground_before"]
        assert report["clipboard_after"] == report["clipboard_before"]
        report["semantic_completion"] = True
    else:
        score_paste(state, payload)
        with patch.object(Connection, "request", tree_edit_request):
            result = report["submit_receipt"] = tree_action_delivery(app, submit, "submit", payload, root)
        assert result["status"] in {"dispatched", "verified"} and not result["has_screenshot"]
        state = report["after_submit"] = wait_state(path, lambda row: row.get("submits", 0) >= 1, 5)
        assert state["submits"] == state["wmPaste"] == 1 and state["text"] == state["submitted"] == payload, state
        assert_companion(state)
        report["semantic_completion"] = True


def attempt(exe, route, payload):
    nonce = uuid.uuid4().hex[:12]
    path = exe.with_name(f"oracle-{nonce}.json")
    title = f"CUA Edit Event {nonce}"
    report = {"route": route, "title": title, "payload": payload, "evidence_pass": False}
    arguments = [str(exe), str(path), title, "full-tree-event", "left"]
    if route == "messages":
        arguments.append("messages")
    fixture = subprocess.Popen(arguments)
    report["fixture_pid"] = fixture.pid
    _CAPTURE_ATTEMPTS.clear()
    request = tree_edit_request if route == "setter" else tree_only_request
    try:
        initial = report["initial"] = wait_state(path, lambda state: state.get("sample", 0) >= 2, 8)
        score_setter(initial, ORIGINAL)
        assert initial["wmSetText"] == 0, initial
        with patch.object(Connection, "request", request), Computer(allow_global_input=route == "paste") as computer:
            exercise(computer.app(window_title=title), computer, fixture, path, report)
        assert not _CAPTURE_ATTEMPTS
        report["evidence_pass"] = True
    except (AssertionError, ComputerUseError, KeyError, OSError, RuntimeError, ValueError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        report["last_state"] = read_state(path)
    finally:
        report["capture_attempts"] = len(_CAPTURE_ATTEMPTS)
        if fixture.poll() is None:
            fixture.terminate()
        report["fixture_exit"] = fixture.wait(timeout=5)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--setter-only", action="store_true", help="Measure the owned UIA setter without input")
    mode.add_argument("--message-only", action="store_true", help="Measure the exact background Edit message route")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt" or args.output.exists():
        parser.error("Requires Windows and a fresh report path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    routes = ("messages",) if args.message_only else ("setter",) if args.setter_only else ("setter", "paste")
    driver = Path(native_driver()["command"])
    report = {
        "module": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": sha256(driver),
        "runner_sha256": sha256(Path(__file__)),
        "fixture_source_sha256": sha256(FIXTURE),
        "profile": "full-tree-event",
        "routes": routes,
        "attempts": [],
        "evidence_pass": False,
    }
    payload = f"Event canary {uuid.uuid4().hex[:10]} 中文🙂"
    with tempfile.TemporaryDirectory(prefix="cua-edit-event-") as tmp:
        exe = Path(tmp) / "CuaEditCoverageFixture.exe"
        build_fixture(exe)
        report["fixture_sha256"] = sha256(exe)
        for route in routes:
            report["attempts"].append(attempt(exe, route, payload))
            write_report(args.output, report)
    report["evidence_pass"] = all(item["evidence_pass"] for item in report["attempts"])
    write_report(args.output, report)
    raise SystemExit(0 if report["evidence_pass"] else 1)


if __name__ == "__main__":
    main()
