# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WLIFE-005: save, close and reopen a disposable file through SDK or stdio MCP.

Run in an unlocked exclusive Windows test desktop. Raw paste/save require the
owned foreground HWND. The opt-in passive semantic profile uses only exact-
window Element input and a native Save action. Every observation is tree-only.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from textwrap import indent

from computer_use import Computer, ComputerUseError
from computer_use.native.connection import Connection
from windows_continuous_workflow import delivered_value, fixture_foreground
from windows_desktop_batch import manifest, sha256
from windows_paste_workflow import read_state, wait_state

from computer_use_agent.config import AgentConfig
from computer_use_agent.connection import McpConnection

FIXTURE = Path(__file__).with_name("windows_save_reopen_fixture.cs")
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


def capture_guard_environment(folder, audit, *, passive=False):
    folder.mkdir()
    # sitecustomize runs before the MCP server or worker imports its entry point.
    source = (
        "import os\ntry:\n    from pathlib import Path\n    from computer_use.native.connection import Connection\n"
        + indent(inspect.getsource(install_capture_veto), "    ")
        + '    role = "mcp_worker" if "COMPUTER_USE_WORKER_PORT" in os.environ else "mcp_server"\n'
        + f"    install_capture_veto(Path({str(audit)!r}), role, passive={passive!r})\n"
        + "except BaseException:\n    os._exit(91)\n"
    )
    (folder / "sitecustomize.py").write_text(source, encoding="utf-8")
    return os.pathsep.join(part for part in (str(folder), os.environ.get("PYTHONPATH")) if part)


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


async def exercise_transport(start, document, report, audit):
    allow_global_input = report["edit_route"] == "raw"
    passive = not allow_global_input

    async def record(label, invoke):
        step = {"label": label}
        report["steps"].append(step)
        started = time.monotonic()
        try:
            return await invoke(step)
        finally:
            step["seconds"] = round(time.monotonic() - started, 3)

    if report["entry"] == "mcp":
        config = AgentConfig(allow_global_input=allow_global_input).mcp_config()
        pythonpath = capture_guard_environment(audit.parent / "capture-guard", audit, passive=passive)
        connection = McpConnection(replace(config, env={**config.env, "PYTHONPATH": pythonpath}))
        try:

            async def call(label, code):
                async def invoke(step):
                    response = await connection.call("computer_use", {"title": label, "code": code})
                    types = [block.type for block in response.content]
                    text = "\n".join(block.text for block in response.content if block.type == "text")
                    step.update(content_types=types, text=text, is_error=bool(response.isError))
                    if any(kind != "text" for kind in types) or response.isError:
                        raise RuntimeError(f"{label} delivered non-text output or a tool error")
                    return text

                return await record(label, invoke)

            await exercise(call, start, document, report)
        finally:
            await connection.close()
    else:
        request = install_capture_veto(audit, "sdk", passive=passive)
        try:
            with Computer(allow_global_input=allow_global_input) as computer:
                namespace = {"computer": computer}

                async def call(label, code):
                    async def invoke(step):
                        with redirect_stdout(StringIO()) as stream:
                            try:
                                exec(code, namespace)  # noqa: S102 - fixed test code with quoted fixture values.
                            except ComputerUseError as error:
                                step.update(code=error.code, effect=error.effect, detail=error.detail)
                                assert error.observation is None or error.observation.screenshot is None
                                raise
                            finally:
                                step["text"] = stream.getvalue()
                        return step["text"]

                    return await record(label, invoke)

                await exercise(call, start, document, report)
        finally:
            Connection.request = request


def run(entry, *, semantic=False):
    nonce = uuid.uuid4().hex[:10]
    report = {
        "passed": False,
        "entry": entry,
        "edit_route": "semantic" if semantic else "raw",
        "allow_global_input": not semantic,
        "title": f"CUA Save Reopen {nonce}",
        "expected": f"CUA document {nonce} 中文 😀",
        "steps": [],
        "closed": [],
        "fixture_source_sha256": sha256(FIXTURE),
        "runner_sha256": sha256(Path(__file__)),
    }
    provenance = manifest()
    provenance.pop("files")
    report["provenance"] = provenance
    with tempfile.TemporaryDirectory(prefix="cua-save-reopen-") as folder:
        path = Path(folder)
        exe, document = path / "CuaSaveReopenFixture.exe", path / "test-document.txt"
        audit = path / "capture-attempts.log"
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        processes = []

        def start(mode):
            state_path = path / f"{mode}.json"
            process = subprocess.Popen(
                [str(exe), str(state_path), str(document), report["title"], mode, *(["--semantic"] if semantic else [])]
            )
            processes.append((mode, process, state_path))

            def ready(state):
                return state.get("hwnd") and (semantic or mode == "reopen" or state.get("focused"))

            state = wait_state(state_path, lambda state: ready(state) or process.poll() is not None, 6)
            if not ready(state) or (state.get("pid"), state.get("title")) != (process.pid, report["title"]):
                raise RuntimeError(f"The owned {mode} window was unavailable or lacked required editor focus: {state}")
            return process, state_path, state

        try:
            subprocess.run(
                [
                    str(compiler),
                    "/nologo",
                    "/target:winexe",
                    "/platform:x64",
                    "/warnaserror+",
                    "/reference:System.Windows.Forms.dll",
                    "/reference:System.Drawing.dll",
                    "/reference:System.Web.Extensions.dll",
                    f"/out:{exe}",
                    str(FIXTURE),
                ],
                check=True,
                timeout=20,
            )
            report["fixture_binary_sha256"] = sha256(exe)
            asyncio.run(exercise_transport(start, document, report, audit))
            report["capture_veto"] = capture_guard_result(audit, entry, passive=semantic)
            if not report["capture_veto"]["passed"]:
                raise RuntimeError("A prohibited native request was attempted or the veto could not be confirmed")
            report["passed"] = True
        except Exception as error:  # noqa: BLE001 - preserve any failed acceptance evidence before cleanup.
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
        finally:
            report.setdefault("capture_veto", capture_guard_result(audit, entry, passive=semantic))
            report["owned_process_cleanup"] = []
            for mode, process, state_path in processes:
                forced = process.poll() is None
                if forced:
                    process.terminate()
                exit_code = process.wait(timeout=5)
                report["owned_process_cleanup"].append(
                    {
                        "mode": mode,
                        "pid": process.pid,
                        "forced": forced,
                        "exit_code": exit_code,
                        "last_oracle": read_state(state_path),
                    }
                )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", choices=("sdk", "mcp"), default="sdk")
    parser.add_argument("--semantic", action="store_true", help="Use scoped Element replace and native Save Invoke")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This probe requires an unlocked Windows test desktop")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        report = run(args.entry, semantic=args.semantic)
        stream.write(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
