# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify Windows focus changes with exact title handles and tree-only reads."""

import argparse
import asyncio
import ctypes
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from ctypes import wintypes
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import computer_use
from artifact_json import captured_interface_metadata
from computer_use import Computer
from computer_use.native.connection import Connection
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from windows_continuous_workflow import fixture_foreground
from windows_paste_workflow import wait_state
from windows_visual_workflow import FIXTURE, build_fixture

RESTORE_CONTENT = "Restored fixture content"
DELIVERY_PREFIX = "CUA_FOCUS_DELIVERY "
_ORIGINAL_REQUEST = Connection.request
_CAPTURE_ATTEMPTS = []


async def tree_only_request(self, method, *args, **kwargs):
    if method == "capture":
        _CAPTURE_ATTEMPTS.append(method)
        raise AssertionError("The focus-change probe prohibits native capture")
    return await _ORIGINAL_REQUEST(self, method, *args, **kwargs)


def read_delivery(app, foreground):
    observation = app.read(view="tree", full=True, allow_foreground=foreground)
    text = observation.text
    metadata = captured_interface_metadata(observation)
    return {
        "text": text,
        "metadata": metadata,
        "preparation": dict(observation.preparation) if observation.preparation is not None else None,
        "has_screenshot": observation.screenshot is not None,
        "capture_attempts": len(_CAPTURE_ATTEMPTS),
    }


@asynccontextmanager
async def open_channel(entry, titles, log_dir):
    if entry == "sdk":
        _CAPTURE_ATTEMPTS.clear()
        with patch.object(Connection, "request", tree_only_request), Computer() as computer:
            apps = {role: computer.app(window_title=title) for role, title in titles.items()}

            async def read(role, foreground):
                return read_delivery(apps[role], foreground)

            yield read
        return

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp"],
        cwd=Path.cwd(),
        env={"PYTHONSAFEPATH": "1", "COMPUTER_USE_LOG_DIR": str(log_dir)},
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=50)) as client,
    ):
        await client.initialize()

        async def call(title, code):
            result = await client.call_tool("computer_use", {"title": title, "code": code})
            if any(part.type != "text" for part in result.content):
                raise RuntimeError("The tree-only probe received non-text MCP content")
            text = "\n".join(part.text for part in result.content)
            if result.isError:
                raise RuntimeError(text)
            return text

        await call(
            "Bind the two owned fixture windows",
            "import json\nfrom computer_use.native.connection import Connection\n"
            "_ORIGINAL_REQUEST = Connection.request\n_CAPTURE_ATTEMPTS = []\n"
            + inspect.getsource(tree_only_request)
            + "\nConnection.request = tree_only_request\n"
            + inspect.getsource(captured_interface_metadata)
            + "\n"
            + inspect.getsource(read_delivery)
            + f"\napps = {{role: computer.app(window_title=title) for role, title in {titles!r}.items()}}",
        )

        async def read(role, foreground):
            text = await call(
                f"Read owned fixture window {role} with foreground={foreground}",
                f"print({DELIVERY_PREFIX!r} + json.dumps(read_delivery(apps[{role!r}], {foreground!r})))",
            )
            lines = [line[len(DELIVERY_PREFIX) :] for line in text.splitlines() if line.startswith(DELIVERY_PREFIX)]
            if len(lines) != 1:
                raise RuntimeError("MCP did not deliver one exact focus probe result")
            return json.loads(lines[0])

        yield read


def verify_delivery(delivery, title, other_title, focused, prepared, identities, seen_reads):
    if delivery["has_screenshot"] or delivery["capture_attempts"]:
        raise RuntimeError("A focus read attempted capture or delivered pixels")
    if title not in delivery["text"] or other_title in delivery["text"]:
        raise RuntimeError("The public tree did not stay on the requested fixture window")
    metadata = delivery["metadata"]
    (window,) = metadata["windows"]
    if window["title"] != title:
        raise RuntimeError("The retained window caption does not match the requested title")
    ref, native = window["ref"], window["native_window"]
    if identities.setdefault(title, (ref, native)) != (ref, native):
        raise RuntimeError("The retained exact window was rebound during a focus change")
    if other_title in identities and (ref == identities[other_title][0] or native == identities[other_title][1]):
        raise RuntimeError("The two fixture windows shared one public reference or native identity")
    directory = metadata["directory"]
    for source in (directory, window.get("source_directory"), window.get("rechecked_directory")):
        if source is not None and [row["window"] for row in source["surfaces"]] != [native]:
            raise RuntimeError("A delivered directory exposed another window")
    observation_id = directory["observed"]["id"]
    if observation_id in seen_reads:
        raise RuntimeError("The focus check received previously retained directory evidence")
    seen_reads.add(observation_id)
    reported = (metadata["input_state"], metadata["reported_input"])
    expected = ("member", ref) if focused else ("outside", None)
    # Windows directory results may omit the optional native focusedWindow fact.
    # The independent foreground guard and preparation receipt still prove focus.
    if reported not in (expected, ("unknown", None)):
        raise RuntimeError("The published input window does not match the independent focus state")
    receipt = delivery["preparation"]
    if (prepared and (receipt is None or receipt.get("foreground") is not True)) or (
        not prepared and receipt is not None
    ):
        raise RuntimeError("The public preparation receipt disagrees with the requested scope")
    if focused and title.startswith("CUA restore target") and RESTORE_CONTENT not in delivery["text"]:
        raise RuntimeError("The restored window did not deliver its document text")
    if title.startswith("CUA Visual Fixture") and RESTORE_CONTENT in delivery["text"]:
        raise RuntimeError("The companion window delivered the restored window's content")


def foreground_facts():
    """Sample foreground identity without reading the caption or acquiring pixels."""
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "pid": None, "visible": None, "class": None}
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ctypes.create_unicode_buffer(256)
    has_class = user32.GetClassNameW(hwnd, name, len(name))
    return {
        "hwnd": int(hwnd),
        "pid": pid.value or None,
        "visible": bool(user32.IsWindowVisible(hwnd)) if pid.value else None,
        "class": name.value if has_class else None,
    }


def exception_leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in exception_leaves(child)]
    return [{"type": type(error).__name__, "detail": str(error)}]


async def exercise(entry, fixture_pid, state_path, titles, count, report, log_dir):
    identities, seen_reads, hwnds = {}, set(), {}
    state = wait_state(state_path, lambda row: row.get("restore_minimized") and row.get("foreground_hwnd"), 5)
    if not state.get("restore_minimized"):
        raise RuntimeError(f"Fixture did not start with the second window minimized: {state}")
    report["initial"] = state
    report["stage"] = "setup"

    async with open_channel(entry, titles, log_dir) as read:

        async def step(evidence, role, foreground, expected_foreground):
            nonlocal state
            start = time.monotonic()
            record = {
                "role": role,
                "allow_foreground": foreground,
                "expected_foreground": expected_foreground,
                "stage": "read",
                "foreground_before": foreground_facts(),
            }
            evidence.append(record)
            try:
                delivery = record["delivery"] = await read(role, foreground)
            finally:
                record["foreground_after"] = foreground_facts()
            record["stage"] = "foreground_guard"
            hwnd = fixture_foreground(hwnds.get(expected_foreground), fixture_pid, titles[expected_foreground])
            hwnds.setdefault(expected_foreground, hwnd)
            record["stage"] = "fixture_oracle"
            next_state = wait_state(
                state_path,
                lambda row: row.get("sample", 0) > state["sample"] and row.get("foreground_hwnd") == hwnd,
                2,
            )
            record.update(seconds=round(time.monotonic() - start, 3), actual=next_state, foreground_hwnd=hwnd)
            if (
                next_state.get("sample", 0) <= state["sample"]
                or next_state.get("foreground_hwnd") != hwnd
                or next_state.get("target_clicks") != 0
                or next_state.get("bystander_clicks") != 0
            ):
                raise RuntimeError("The fixture did not confirm the independent foreground and bystander state")
            other = "B" if role == "A" else "A"
            record["stage"] = "publication"
            verify_delivery(
                delivery, titles[role], titles[other], role == expected_foreground, foreground, identities, seen_reads
            )
            state = next_state
            record["stage"] = "complete"

        await step(report["setup"], "A", True, "A")
        initial_activations = state["restore_activations"]
        if not state["restore_minimized"]:
            raise RuntimeError("Preparing the main window affected the minimized peer")
        report["stage"] = "chains"
        for index in range(1, count + 1):
            chain = {"index": index, "steps": [], "pass": False}
            report["chains"].append(chain)
            started = time.monotonic()
            await step(chain["steps"], "B", False, "A")
            if state["restore_activations"] != initial_activations + index - 1 or state["restore_minimized"] != (
                index == 1
            ):
                raise RuntimeError("An unapproved tree read activated or restored the other window")
            await step(chain["steps"], "B", True, "B")
            if state["restore_minimized"] or state["restore_activations"] != initial_activations + index:
                raise RuntimeError("Explicit preparation did not activate the second window exactly once")
            await step(chain["steps"], "A", True, "A")
            if state["restore_activations"] != initial_activations + index or len(set(hwnds.values())) != 2:
                raise RuntimeError("Returning to the first window caused an extra activation or reused one HWND")
            chain.update(seconds=round(time.monotonic() - started, 3), **{"pass": True})
        report.update(identities=identities, hwnds=hwnds, fresh_public_reads=len(seen_reads))


def run(entry, count, output):
    nonce = uuid.uuid4().hex[:12]
    titles = {"A": f"CUA Visual Fixture {nonce}", "B": f"CUA restore target {nonce}"}
    driver = native_driver("win32")["command"]
    report = {
        "entry": entry,
        "requested": count,
        "titles": titles,
        "chains": [],
        "setup": [],
        "stage": "fixture_startup",
        "pass": False,
        "sdk": computer_use.__file__,
        "runtime": startup_provenance(driver),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_source_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
    }
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="cua-focus-change-") as temporary:
            exe, state = (Path(temporary) / name for name in ("CuaFocusChangeFixture.exe", "state.json"))
            build_fixture(exe)
            report["fixture_sha256"] = hashlib.sha256(exe.read_bytes()).hexdigest()
            fixture = subprocess.Popen([str(exe), str(state), nonce, "prepare"])
            try:
                asyncio.run(exercise(entry, fixture.pid, state, titles, count, report, output.with_suffix(".mcp")))
            finally:
                if fixture.poll() is None:
                    fixture.terminate()
                fixture.wait(timeout=5)
                report["fixture_exited"] = True
        report["pass"] = True
    except Exception as error:  # noqa: BLE001 - preserve red evidence and the achieved denominator
        report["error"] = {
            "type": type(error).__name__,
            "detail": str(error),
            "stage": report["stage"],
            "leaves": exception_leaves(error),
        }
    finally:
        report["attempted"] = len(report["chains"])
        report["passed"] = sum(chain["pass"] for chain in report["chains"])
        report["seconds"] = round(time.monotonic() - started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", choices=("sdk", "mcp"), required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt" or not 1 <= args.count <= 100 or args.output.exists():
        parser.error("Requires Windows, 1–100 chains and a fresh output file")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = run(args.entry, args.count, args.output)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{args.output} {report['passed']}/{args.count}\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
