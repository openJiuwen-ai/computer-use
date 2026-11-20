# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Save, close and reopen transcript oracle exercised with an injected caller and document fixture."""

import asyncio
import hashlib
import inspect
import json
import re
import time
from pathlib import Path

from computer_use.native.connection import Connection

from .paste_observation import delivered_value, fixture_foreground, read_state, wait_state


INITIAL_DRAFT = "Draft before replacement"


SAVE_BUTTON = "Save document"


WINDOW_REF = re.compile(r"(?m)^Window (\d+)[:,]|^Window reference: (\d+)\s*$")


TREE = '.read(view="tree", full=True, allow_foreground=False)'


RUNTIME = """import ctypes, hashlib, json, computer_use
from ctypes import wintypes
from pathlib import Path
from computer_use.native.connection import Connection
from computer_use.paths import native_driver
if not getattr(Connection.request, "_cua_capture_veto", False):
    raise RuntimeError("Native capture veto was not installed in the executing process")
driver = Path(native_driver()["command"])
print("CUA_SAVE_RUNTIME", json.dumps([computer_use.__file__, str(driver), hashlib.sha256(driver.read_bytes()).hexdigest()]))
"""


def install_capture_veto(audit, role, *, passive=False):
    request = Connection.request

    def record(event):
        with audit.open("a", encoding="ascii") as stream:
            stream.write(f"{role} {event}\n")

    async def guarded(self, method, params, options=None):
        if method == "capture":
            record("attempt")
            raise RuntimeError("The image-free test vetoed a native capture request")
        if passive:
            command = params.get("command", {}) if method == "execute" else None
            if method == "prepareObservation" or command is not None and command.get("foreground") is not False:
                record("preparation_attempt")
                raise RuntimeError("The passive test vetoed native foreground preparation")
            if command is not None or method == "begin" and params.get("mode") == "write":
                binding = (command if command is not None else params).get("target", {})
                intent = params.get("intent", {})
                if command is not None:
                    allowed = command.get("kind") in ("native_text_edit", "native_action")
                else:
                    allowed = intent.get("kind") == "replace" or (
                        intent.get("kind") == "action"
                        and intent.get("parameters", {}).get("action") in ("Invoke.Invoke", "Window.Close")
                    )
                if not allowed or not binding.get("rootWindow") or binding.get("capture") is not None:
                    record("input_attempt")
                    raise RuntimeError("The passive test vetoed nonsemantic native input")
        return await request(self, method, params, options)

    guarded._cua_capture_veto = True
    guarded._cua_passive_veto = passive
    Connection.request = guarded
    record("installed_passive" if passive else "installed")
    return request


def capture_guard_result(audit, entry, *, passive=False):
    events = audit.read_text(encoding="ascii").splitlines() if audit.is_file() else []
    marker = "installed_passive" if passive else "installed"
    roles = ("sdk",) if entry == "sdk" else ("mcp_server", "mcp_worker")
    result = {
        "events": events,
        "attempts": sum(event.endswith(" attempt") for event in events),
        "passed": set(events) == {f"{role} {marker}" for role in roles},
    }
    if passive:
        result.update(
            preparation_attempts=sum(event.endswith(" preparation_attempt") for event in events),
            input_attempts=sum(event.endswith(" input_attempt") for event in events),
        )
    return result


def tree_ref(text, title):
    refs = {int(summary or detail) for summary, detail in WINDOW_REF.findall(text)}
    if len(refs) != 1 or title not in text or "Paste target" not in text:
        raise RuntimeError("The exact document title, one public window reference and editor must be delivered")
    if any(marker in text for marker in ("Showing the visible desktop", "Desktop: full view", "Image: desktop")):
        raise RuntimeError("The exact document observation expanded to the desktop")
    return refs.pop()


def verify_state(state, expected, *, pastes, saves, closes):
    if (
        state.get("text") != expected
        or state.get("bystander") != "untouched"
        or state.get("error") is not None
        or (state.get("pastes"), state.get("saves"), state.get("closes")) != (pastes, saves, closes)
        or state.get("closed") is not bool(closes)
    ):
        raise RuntimeError(f"The independent document fixture reported an unexpected effect: {state}")


def require_line(text, line):
    if text.splitlines().count(line) != 1:
        raise RuntimeError(f"The caller did not receive exactly one {line!r} line")


async def exercise(call, start, document, report):
    title, payload = report["title"], report["expected"]
    semantic = report.get("edit_route") == "semantic"
    initial_text = INITIAL_DRAFT if semantic else ""
    paste_count = 0 if semantic else 1
    expected_bytes = payload.encode("utf-8")
    preparation = (
        'if not getattr(Connection.request, "_cua_passive_veto", False):\n'
        '    raise RuntimeError("Passive native input veto was not installed")'
        if semantic
        else inspect.getsource(fixture_foreground)
    )
    runtime = await call("record resolved runtime", RUNTIME + "\n" + preparation)
    runtime_rows = [
        line.removeprefix("CUA_SAVE_RUNTIME ") for line in runtime.splitlines() if line.startswith("CUA_SAVE_RUNTIME ")
    ]
    if len(runtime_rows) != 1:
        raise RuntimeError("The caller did not receive runtime provenance")
    report["caller_runtime"] = json.loads(runtime_rows[0])
    provenance = report["provenance"]
    if report["caller_runtime"] != [
        provenance["module"],
        provenance["driver"],
        provenance["native_sha256"][Path(provenance["driver"]).name],
    ]:
        raise RuntimeError("The caller resolved a different SDK module or native driver")

    async def observe(label, name, *, bind=False):
        select = f"{name} = computer.app(window_title={title!r})\n" if bind else ""
        text = await call(label, select + f"obs = {name}{TREE}\nassert obs.screenshot is None\nprint(obs.text)")
        return tree_ref(text, title), text

    async def guarded(label, expression, identity):
        text = await call(
            label,
            f"hwnd = fixture_foreground(**{identity!r})\n"
            f"receipt = computer.{expression}\n"
            "assert receipt.observation is None\n"
            'print("CUA_SAVE_INPUT", hwnd, receipt.status, receipt.observation_status)',
        )
        require_line(text, f"CUA_SAVE_INPUT {identity['hwnd']} dispatched not_requested")

    async def semantic_write(label, ref, name, expression, intent):
        text = await call(
            label,
            f"receipt = original_app.element({name!r}, window={ref}).{expression}\n"
            "assert receipt.observation is None or receipt.observation.screenshot is None\n"
            'print("CUA_SAVE_SEMANTIC", receipt.intent, receipt.status)',
        )
        markers = [line for line in text.splitlines() if line.startswith("CUA_SAVE_SEMANTIC ")]
        if len(markers) != 1 or markers[0] not in {
            f"CUA_SAVE_SEMANTIC {intent} dispatched",
            f"CUA_SAVE_SEMANTIC {intent} verified",
        }:
            raise RuntimeError(f"{label} did not deliver one successful exact-window {intent} receipt")

    def verify_semantic(state, saves):
        if not semantic:
            return
        activation_hwnds = state.get("activation_foreground_hwnds")
        if not isinstance(activation_hwnds, list) or len(activation_hwnds) != state.get("activations"):
            raise RuntimeError(f"The passive document did not report each activation's foreground HWND: {state}")
        if (
            state.get("foreground") is not False
            or state.get("ever_foreground") is not False
            or state["hwnd"] == state.get("foreground_hwnd")
            or state["hwnd"] in activation_hwnds
        ):
            raise RuntimeError(f"The passive document reached system foreground: {state}")
        if (
            state.get("clipboard_sequence") != clipboard_sequence
            or state.get("shortcut_saves") != 0
            or state.get("button_saves") != saves
        ):
            raise RuntimeError(f"Unexpected clipboard change or save source in semantic profile: {state}")

    async def close(label, name, ref, process, state_path, *, pastes, saves):
        text = await call(
            label,
            f"receipt = {name}.close_window({ref})\n"
            "assert receipt.observation is None or receipt.observation.screenshot is None\n"
            'print("CUA_SAVE_CLOSE", receipt.intent, receipt.status, receipt.window_lifecycle)',
        )
        require_line(text, "CUA_SAVE_CLOSE closeWindow dispatched closed")
        exit_code = process.wait(timeout=8)
        state = read_state(state_path)
        report["closed"].append({"ref": ref, "exit_code": exit_code, "oracle": state})
        verify_state(state, payload, pastes=pastes, saves=saves, closes=1)
        verify_semantic(state, saves)
        if exit_code != 0 or document.read_bytes() != expected_bytes:
            raise RuntimeError("Normal close did not preserve the exact saved file and exit cleanly")

    first, first_path, original = start("new")
    verify_state(original, initial_text, pastes=0, saves=0, closes=0)
    clipboard_sequence = original.get("clipboard_sequence")
    if semantic and (type(clipboard_sequence) is not int or clipboard_sequence <= 0):
        raise RuntimeError("The owned fixture did not establish its initial clipboard sequence")
    verify_semantic(original, 0)
    label = "observe the original draft" if semantic else "observe the original blank document"
    old_ref, initial = await observe(label, "original_app", bind=True)
    if payload in initial or document.exists():
        raise RuntimeError("The original fixture already contains the test payload")
    report["original"] = {"public_ref": old_ref, "oracle": original}
    if semantic:
        if delivered_value(initial) != initial_text:
            raise RuntimeError("The exact document tree did not deliver the seeded draft")
        await semantic_write(
            "replace the document text", old_ref, "Paste target", f"type_text({payload!r}, replace=True)", "replace"
        )
    else:
        foreground = {key: original[key] for key in ("hwnd", "pid", "title")}
        await guarded("paste the document text", f"paste({payload!r})", foreground)
    until = time.monotonic() + 6
    while True:
        ref, edited = await observe("verify the edited document tree", "original_app")
        if ref != old_ref:
            raise RuntimeError("The edit changed the original window reference")
        if payload in edited and delivered_value(edited) == payload:
            break
        if time.monotonic() >= until:
            raise RuntimeError("The edited document value was not delivered exactly")
        await asyncio.sleep(0.05)
    if semantic:
        if SAVE_BUTTON not in edited:
            raise RuntimeError("The exact document tree did not deliver the Save button")
        await semantic_write(
            "invoke the Save button", old_ref, SAVE_BUTTON, 'perform_action("Invoke.Invoke")', "action"
        )
    else:
        await guarded("save the edited document", 'press_key("ctrl+s")', foreground)
    saved = wait_state(first_path, lambda state: state.get("saves") or state.get("error"), 5)
    verify_state(saved, payload, pastes=paste_count, saves=1, closes=0)
    verify_semantic(saved, 1)
    actual_bytes = document.read_bytes()
    report["save"] = {
        "oracle": saved,
        "bytes": len(actual_bytes),
        "sha256": hashlib.sha256(actual_bytes).hexdigest(),
    }
    if actual_bytes != expected_bytes:
        raise RuntimeError("The saved document bytes did not equal the original input")
    await close("close the saved document", "original_app", old_ref, first, first_path, pastes=paste_count, saves=1)

    second, second_path, reopened = start("reopen")
    new_ref, readback = await observe("read the reopened document", "reopened_app", bind=True)
    report["reopened"] = {"public_ref": new_ref, "oracle": reopened}
    if reopened["pid"] == original["pid"] or new_ref == old_ref:
        raise RuntimeError("The reopened document reused the previous process or public window lifetime")
    if delivered_value(readback) != payload:
        raise RuntimeError("The reopened tree did not deliver the exact saved text")
    verify_state(reopened, payload, pastes=0, saves=0, closes=0)
    verify_semantic(reopened, 0)
    stale = await call(
        "check the original pinned window after reopening",
        "from computer_use import ComputerUseError\n"
        f"try:\n    obs = original_app{TREE}\n    assert obs.screenshot is None\n"
        "except ComputerUseError as error:\n"
        "    assert error.observation is None or error.observation.screenshot is None\n"
        '    print("CUA_SAVE_STALE", error.code, error.effect)',
    )
    require_line(stale, "CUA_SAVE_STALE stale_target none")
    report["original_stale"] = True
    await close(
        "close the reopened fixture for cleanup", "reopened_app", new_ref, second, second_path, pastes=0, saves=0
    )
