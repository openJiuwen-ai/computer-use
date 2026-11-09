# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify consecutive Windows paste chains in one SDK or MCP session."""

import argparse
import asyncio
import ctypes
import hashlib
import inspect
import json
import re
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import computer_use
from artifact_json import captured_interface_metadata
from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from windows_paste_workflow import paste_fixture, read_state, wait_state

from computer_use_agent.config import AgentConfig
from computer_use_agent.connection import McpConnection

INTERFACE_METADATA_PREFIX = "Captured interface metadata: "
# The fixture is a .NET Framework multiline TextBox. Ctrl+A depends on its runtime compatibility switch.
IMAGE_FREE_SELECTION = ("ctrl+Home", "ctrl+shift+End")
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


def sdk_tree(observation, image_free: bool):
    if image_free:
        assert observation.screenshot is None, "The image-free SDK read returned a screenshot"
    return observation.text


def delivered_value(observed: str) -> str:
    values = set()
    for ref, raw in PASTE_VALUE.findall(observed):
        # The full tree can show the same control as a document region. Short
        # plain scalars are rendered bare, and values with commas are quoted.
        value = json.JSONDecoder().raw_decode(raw)[0] if raw.startswith('"') else raw.partition(", ")[0]
        values.add((ref, value))
    assert len(values) == 1, "The captured paste target value was not delivered unambiguously"
    return values.pop()[1]


def record_chain(report: dict, index: int, payload: str) -> dict:
    chain = {"index": index, "expected": payload, "passed": False}
    report["chains"].append(chain)
    return chain


def score_chain(chain: dict, path: Path, start: float) -> None:
    # The public observation confirms the current paste first. Independent
    # fixture state only scores the exact effect and any unintended events.
    index = chain["index"]
    state = wait_state(path, lambda s: s.get("fixture_error") or s.get("consumed", 0) >= index, 1)
    chain.update(actual=state, seconds=round(time.monotonic() - start, 3))
    assert not state.get("fixture_error"), state
    assert state.get("paste_count") == state.get("consumed") == index, state
    assert state.get("text") == chain["expected"] and state.get("bystander") == "untouched", state
    assert state.get("pending") == state.get("returns") == 0 and state.get("focused"), state
    chain["passed"] = True


def record_unconfirmed(chain: dict, path: Path, delivered: str | None) -> None:
    state = read_state(path)
    actual = state.pop("text", None)
    state.pop("clipboard_sample", None)
    bystander = state.pop("bystander", None)
    chain["failure_oracle"] = {
        **state,
        "actual_matches": actual == chain["expected"],
        "actual_length": len(actual) if actual is not None else None,
        "delivered_length": len(delivered) if delivered is not None else None,
        "delivered_matches_actual": delivered is not None and delivered == actual,
        "bystander_untouched": bystander == "untouched",
    }


def sdk_series(title: str, payloads: list[str], path: Path, report: dict) -> None:
    with Computer() as computer:
        foreground = report.get("foreground")
        image_free = foreground is not None
        app = computer.app("CuaPasteFixture")
        catalogue = app.read(view="tree", full=True)
        report["catalogue"] = sdk_tree(catalogue, image_free)
        ref = fixture_window(captured_interface_metadata(catalogue), title, only=image_free)
        report["window"] = ref
        keyboard = computer if image_free else app.element("Paste target", window=ref)
        for index, payload in enumerate(payloads, 1):
            start = time.monotonic()
            chain = record_chain(report, index, payload)
            if foreground is not None:
                chain["selection_keys"] = []
                for key in IMAGE_FREE_SELECTION:
                    hwnd = fixture_foreground(**foreground)
                    selection = keyboard.press_key(key)
                    chain["selection_keys"].append({"key": key, "hwnd": hwnd, "status": selection.status})
                    assert selection.status == "dispatched" and selection.observation_status == "not_requested"
                    assert selection.observation is None
                chain["paste_foreground_hwnd"] = fixture_foreground(**foreground)
            else:
                selection = keyboard.press_key("ctrl+a")
                chain["select_status"] = selection.status
                assert selection.status in {"dispatched", "verified"}, selection
            paste = computer.paste(payload)
            chain["paste_receipt"] = [paste.status, paste.observation_status]
            assert chain["paste_receipt"] == ["dispatched", "not_requested"], paste
            if image_free:
                assert paste.observation is None, paste
            until = time.monotonic() + 6
            delivered = None
            while time.monotonic() < until:
                observed = sdk_tree(app.read(view="tree", window=ref, full=True), image_free)
                delivered = delivered_value(observed)
                if delivered == payload:
                    chain["delivered"] = delivered
                    break
                time.sleep(0.05)
            else:
                record_unconfirmed(chain, path, delivered)
                raise AssertionError("SDK did not observe the exact pasted text")
            score_chain(chain, path, start)


async def mcp_series(title: str, payloads: list[str], path: Path, report: dict) -> None:
    connection = McpConnection(AgentConfig().mcp_config())
    foreground = report.get("foreground")
    image_free = foreground is not None

    async def call(code: str) -> str:
        async with asyncio.timeout(45):
            response = await connection.call("computer_use", {"title": "Verify consecutive paste", "code": code})
        output = "\n".join(item.text for item in response.content if item.type == "text")
        if image_free:
            assert all(item.type == "text" for item in response.content), "The image-free MCP call returned non-text"
        assert not response.isError, output
        return output

    try:
        catalogue = await call(
            'app = computer.app("CuaPasteFixture")\ncatalogue = app.read(view="tree", full=True)\n'
            'print(catalogue.text)\nprint(catalogue.read_details(length=16384).text)'
        )
        ref = mcp_window(catalogue, title, report)
        report["window"] = ref
        if foreground is not None:
            await call("import ctypes\nfrom ctypes import wintypes\n" + inspect.getsource(fixture_foreground))
            guard = f"fixture_foreground(**{foreground!r})"
            select_code = (
                f"for key in {IMAGE_FREE_SELECTION!r}:\n"
                f'    print("CUA_FOCUS_SELECT", key, {guard})\n'
                '    r = computer.press_key(key)\n'
                '    assert r.status == "dispatched" and r.observation_status == "not_requested"\n'
                '    assert r.observation is None\n'
            )
            paste_guard = f'print("CUA_FOCUS_PASTE", {guard})\n'
        else:
            await call(f'element = app.element("Paste target", window={ref})')
            select_code = 'r = element.press_key("ctrl+a")\n'
            paste_guard = ""
        for index, payload in enumerate(payloads, 1):
            start = time.monotonic()
            chain = record_chain(report, index, payload)
            output = await call(
                select_code + 'print("CUA_SELECT", r.status)\n'
                'assert r.status in ("dispatched", "verified")\n'
                + paste_guard
                + f'r = computer.paste({payload!r})\nprint("CUA_PASTE", r.status, r.observation_status)'
            )
            if foreground is not None:
                chain["foreground_checks"] = [line for line in output.splitlines() if line.startswith("CUA_FOCUS_")]
                assert chain["foreground_checks"] == [
                    *(f'CUA_FOCUS_SELECT {key} {foreground["hwnd"]}' for key in IMAGE_FREE_SELECTION),
                    f'CUA_FOCUS_PASTE {foreground["hwnd"]}',
                ], output
            chain["receipts"] = [line for line in output.splitlines() if line.startswith(("CUA_SELECT ", "CUA_PASTE "))]
            assert chain["receipts"] in (
                ["CUA_SELECT dispatched", "CUA_PASTE dispatched not_requested"],
                ["CUA_SELECT verified", "CUA_PASTE dispatched not_requested"],
            ), output
            until = time.monotonic() + 6
            delivered = None
            while time.monotonic() < until:
                observed = await call(f'print(app.read(view="tree", window={ref}, full=True).text)')
                delivered = delivered_value(observed)
                if delivered == payload:
                    chain["delivered"] = delivered
                    break
                await asyncio.sleep(0.05)
            else:
                record_unconfirmed(chain, path, delivered)
                raise AssertionError("MCP did not deliver the exact pasted text")
            score_chain(chain, path, start)
    finally:
        await connection.close()


def run(count: int, delay_ms: int, mcp: bool, *, image_free: bool = False) -> dict:
    nonce = uuid.uuid4().hex[:10]
    driver = Path(native_driver()["command"])
    payloads = [f"CUA {nonce}-{index:03d} 中文 😀" for index in range(1, count + 1)]
    title = f"CUA Paste Fixture {nonce}"
    report = {
        "passed": False,
        "module": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "transport": "mcp" if mcp else "sdk",
        "requested": count,
        "delay_ms": delay_ms,
        "image_free": image_free,
        "chains": [],
    }
    try:
        with paste_fixture(delay_ms, nonce, True, require_natural_focus=image_free) as (path, initial):
            report["initial"] = initial
            assert initial.get("paste_count") == initial.get("consumed") == 0, initial
            if image_free:
                pid = initial["fixture_pid"]
                report["foreground"] = {"hwnd": fixture_foreground(None, pid, title), "pid": pid, "title": title}
            if mcp:
                asyncio.run(mcp_series(title, payloads, path, report))
            else:
                sdk_series(title, payloads, path, report)
        report["passed"] = len(report["chains"]) == count and all(chain["passed"] for chain in report["chains"])
    except (AssertionError, ComputerUseError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--delay-ms", type=int, default=250)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--image-free", action="store_true", help="Require natural fixture focus and raw keys/paste")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 1 or args.delay_ms < 0:
        parser.error("count must be positive and delay must be nonnegative")
    report = run(args.count, args.delay_ms, args.mcp, image_free=args.image_free)
    serialized = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
