# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise semantic, partial, and visual-only Windows editing through SDK and stdio MCP.

Use an unlocked, exclusive test desktop. Each attempt owns a fresh fixture
and client. The default profiles locate the target from the delivered image.
The opt-in tree profiles use exact public Elements.
The fixture's separate event log verifies input and submit effects in both.
"""

import argparse
import asyncio
import base64
import ctypes
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from ctypes import wintypes
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from artifact_json import captured_interface_metadata
from computer_use import Computer, ComputerUseError
from computer_use.native.connection import Connection
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image, ImageChops
from windows_browser_workflow import element_ref
from windows_desktop_batch import ROOT, manifest, sha256
from windows_focus_change_workflow import _CAPTURE_ATTEMPTS, read_delivery, tree_only_request
from windows_paste_workflow import read_state, wait_state
from windows_visual_workflow import marker_point

FIXTURE = Path(__file__).with_name("windows_edit_coverage_fixture.cs")
CONTEXT_LABEL = "CUA draft workspace context"
PAINTED_CONTENT = ("Visual edit target", "Protected companion", "Original draft value", "Untouched companion value")
SUBMITTED_COLOR = (147, 54, 235)
INTERFACE_METADATA_PREFIX = "Captured interface metadata: "
SELECT_ALL_KEY = "ctrl+a"
TREE_TIERS = ("full-tree", "partial-tree", "full-tree-event")
TREE_ACTION_PREFIX = "CUA_TREE_EDIT "
EDITABLE = "Editable draft"
COMPANION = "Protected companion"
ORIGINAL = "Original draft value"
UNTOUCHED = "Untouched companion value"
_EDIT_ROOT = None
_EDIT_WRITES = []


def build_fixture(exe: Path) -> None:
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run(
        [
            str(csc),
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
        timeout=30,
    )


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


def verify_ax(text: str, title: str, tier: str) -> dict:
    status = re.search(r"(?m)^AX: ([^;]+); Image: ([^.]+)\.$", text)
    if status is None or status[1] not in {"collected", "unavailable"}:
        raise RuntimeError("Observation did not report whether AX was collected")
    rows = re.findall(r"(?m)^[ \t]*\d+ [^\n]+", text)
    children = [row.strip() for row in rows if title not in row]
    rendered_rows = "\n".join(rows)
    if any(content in rendered_rows for content in PAINTED_CONTENT):
        raise RuntimeError("The editing target or companion was delivered through AX")
    if tier == "partial" and (status[1] != "collected" or not any(CONTEXT_LABEL in row for row in children)):
        raise RuntimeError("Partial observation did not deliver its native context label")
    if tier == "visual" and (children or CONTEXT_LABEL in text):
        raise RuntimeError(f"Visual-only observation delivered child AX rows: {children}")
    if any(CONTEXT_LABEL not in row for row in children):
        raise RuntimeError(f"Partial observation delivered additional child AX rows: {children}")
    return {"status": status[1], "tree_rows": rows, "child_rows": children}


def observe(report: dict, phase: str, text: str, png: bytes | None, caption: str = "") -> dict:
    evidence = {"text": text, "caption": caption}
    report[phase] = evidence
    evidence["ax"] = verify_ax(text, report["title"], report["tier"])
    if png is None:
        raise RuntimeError(f"No image delivered during {phase}")
    point, size = marker_point(png)
    with (
        Image.open(BytesIO(png)) as source,
        source.convert("RGB") as image,
        Image.new("RGB", image.size, SUBMITTED_COLOR) as color,
        ImageChops.difference(image, color) as difference,
    ):
        red, green, blue = difference.split()
        color_count = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()[0]
    evidence.update(
        image_point=point,
        image_size=size,
        image_sha256=hashlib.sha256(png).hexdigest(),
        image_scope="desktop" if "Showing the visible desktop" in text else "window",
        submitted_swatch_pixels=color_count,
    )
    if (phase == "before" and color_count) or (phase == "after" and color_count < 150):
        raise RuntimeError(f"Delivered {phase} image had unexpected submit swatch pixels: {color_count}")
    return evidence


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


def tree_action_delivery(app, ref, action, payload, root):
    global _EDIT_ROOT
    before = len(_EDIT_WRITES)
    _EDIT_ROOT = root
    try:
        element = app.element(ref)
        if action == "replace":
            result = element.type_text(payload, replace=True)
        elif action == "invoke":
            result = element.perform_action("Invoke.Invoke")
        else:
            result = element.click()
    finally:
        _EDIT_ROOT = None
    observed = result.observation
    return {
        "status": result.status,
        "detail": result.detail,
        "observation_status": result.observation_status,
        "observation_error": dict(result.observation_error) if result.observation_error is not None else None,
        "has_screenshot": observed is not None and observed.screenshot is not None,
        "capture_attempts": len(_CAPTURE_ATTEMPTS),
        "native_writes": _EDIT_WRITES[before:],
    }


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


def sdk_tree_edit(fixture: subprocess.Popen, report: dict) -> None:
    _CAPTURE_ATTEMPTS.clear()
    _EDIT_WRITES.clear()
    with patch.object(Connection, "request", tree_edit_request), Computer(allow_global_input=False) as computer:
        app = computer.app(window_title=report["title"])

        async def read(foreground):
            return read_delivery(app, foreground)

        async def act(ref, action, payload, root):
            return tree_action_delivery(app, ref, action, payload, root)

        asyncio.run(tree_edit(read, act, fixture, report))


def sdk_edit(app_name: str, fixture: subprocess.Popen, report: dict) -> None:
    if report["tier"] in TREE_TIERS:
        sdk_tree_edit(fixture, report)
        return
    with Computer() as computer:
        app = computer.app(app_name)
        ref = sdk_window_ref(report, app.read(view="tree", full=True))
        obs = app.read(view="both", window=ref, allow_foreground=True, full=True)
        evidence = observe(report, "before", obs.text, obs.screenshot)
        require_foreground(fixture)
        point = evidence["image_point"]
        if evidence["image_scope"] == "desktop":
            result = computer.click(target=computer.point(point))
        else:
            result = app.click(at=app.point(point, window=ref), window=ref)
        record_receipt(report, "click", result.status, result.detail)
        for action, execute, argument in (
            ("select_all", computer.press_key, SELECT_ALL_KEY),
            ("type_text", computer.type_text, report["payload"]),
            ("submit", computer.press_key, "Return"),
        ):
            require_foreground(fixture)
            result = execute(argument)
            record_receipt(report, action, result.status, result.detail)
        time.sleep(0.12)  # Let the application paint in response to Return.
        obs = app.read(view="both", window=ref, full=True)
        observe(report, "after", obs.text, obs.screenshot)


async def mcp_edit(app_name: str, fixture: subprocess.Popen, report: dict) -> None:
    args = ["-m", "computer_use_mcp.server"]
    if report["tier"] in TREE_TIERS:
        args.append("--no-allow-global-input")
    params = StdioServerParameters(
        command=sys.executable,
        args=args,
        cwd=str(ROOT),
        env={"PYTHONSAFEPATH": "1"},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=40)) as client,
    ):
        await client.initialize()

        async def call(code):
            result = await client.call_tool("computer_use", {"code": code})
            text = "\n".join(block.text for block in result.content if block.type == "text")
            if result.isError:
                raise RuntimeError(text)
            if report["tier"] in TREE_TIERS and any(block.type != "text" for block in result.content):
                raise RuntimeError("The tree-only edit unexpectedly delivered non-text MCP content")
            return text, [block.model_dump(by_alias=True) for block in result.content if block.type == "image"]

        if report["tier"] in TREE_TIERS:
            await call(
                "import json\nfrom computer_use.native.connection import Connection\n"
                "_ORIGINAL_REQUEST = Connection.request\n_CAPTURE_ATTEMPTS = []\n"
                + inspect.getsource(tree_only_request)
                + "\n_EDIT_ROOT = None\n_EDIT_WRITES = []\n"
                + inspect.getsource(tree_edit_request)
                + "\nConnection.request = tree_edit_request\n"
                + inspect.getsource(captured_interface_metadata)
                + "\n"
                + inspect.getsource(read_delivery)
                + "\n"
                + inspect.getsource(tree_action_delivery)
                + f"\napp = computer.app(window_title={report['title']!r})",
            )

            async def query(expression):
                text, _ = await call(f"print({TREE_ACTION_PREFIX!r} + json.dumps({expression}))")
                lines = [
                    line[len(TREE_ACTION_PREFIX) :] for line in text.splitlines() if line.startswith(TREE_ACTION_PREFIX)
                ]
                if len(lines) != 1:
                    raise RuntimeError("MCP did not deliver one exact tree-only edit result")
                return json.loads(lines[0])

            async def read(foreground):
                return await query(f"read_delivery(app, {foreground!r})")

            async def action(ref, kind, payload, root):
                return await query(f"tree_action_delivery(app, {ref!r}, {kind!r}, {payload!r}, {root!r})")

            await tree_edit(read, action, fixture, report)
            return

        async def capture(phase, code):
            text, images = await call(f"obs = {code}\nprint(obs.text)")
            if len(images) != 1:
                report[phase] = {"text": text, "image_count": len(images)}
                raise RuntimeError(f"Expected one delivered {phase} MCP image, got {len(images)}")
            caption = images[0].get("_meta", {}).get("computer-use", {}).get("caption", "")
            return observe(report, phase, text, base64.b64decode(images[0]["data"]), caption)

        async def act(action, code):
            require_foreground(fixture)
            text, _ = await call(f"result = {code}\nprint('CUA_EDIT_STATUS', result.status)")
            match = re.search(r"(?m)^CUA_EDIT_STATUS (\w+)$", text)
            record_receipt(report, action, match[1] if match else None, text)

        catalogue, _ = await call(
            f"app = computer.app({app_name!r})\n"
            "catalogue = app.read(full=True)\nprint(catalogue.text)\nprint(catalogue.read_details(length=16384).text)"
        )
        ref = mcp_window_ref(report, catalogue)
        evidence = await capture("before", f"app.read(view='both', window={ref}, allow_foreground=True, full=True)")
        point, caption = evidence["image_point"], evidence["caption"]
        if evidence["image_scope"] == "desktop":
            if "computer.point((x, y))" not in caption or "Global click: computer.click(target=p)" not in caption:
                raise RuntimeError("MCP desktop image did not advertise its input route")
            await act("click", f"computer.click(target=computer.point({point!r}))")
        else:
            if f"Image point for window={ref}:" not in caption or "App click:" not in caption:
                raise RuntimeError("MCP window image did not advertise its input route")
            await act("click", f"app.click(at=app.point({point!r}, window={ref}), window={ref})")
        await act("select_all", f"computer.press_key({SELECT_ALL_KEY!r})")
        await act("type_text", f"computer.type_text({report['payload']!r})")
        await act("submit", "computer.press_key('Return')")
        await asyncio.sleep(0.12)
        await capture("after", f"app.read(view='both', window={ref}, full=True)")


def score(actual: dict, payload: str) -> None:
    expected = {
        "active": "target",
        "target": {
            "text": payload,
            "submittedText": payload,
            "clicks": 1,
            "selections": 1,
            "units": len(payload.encode("utf-16-le")) // 2,
            "submits": 1,
        },
        "bystander": {
            "text": "Untouched companion value",
            "submittedText": None,
            "clicks": 0,
            "selections": 0,
            "units": 0,
            "submits": 0,
        },
    }
    if any(actual.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"Fixture scored a wrong target, missing effect, or duplicate input: {actual!r}")


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


def attempt(exe: Path, tier: str, entry: str, side: str) -> dict:
    nonce = uuid.uuid4().hex[:10]
    state = exe.with_name(f"oracle-{nonce}.json")
    report = {
        "tier": tier,
        "entry": entry,
        "nonce": nonce,
        "target_side": side,
        "title": f"CUA Edit Coverage {nonce}",
        "payload": f"{nonce} 中文🙂",
        "actions": [],
        "pass": False,
        "image_free": tier in TREE_TIERS,
    }
    started = time.monotonic()
    event_dependent = tier == "full-tree-event"
    args = [str(exe), str(state), report["title"], tier, side]
    if event_dependent:
        args.append("messages")  # Show passively with a collapsed selection.
    fixture = subprocess.Popen(args)
    report["fixture_pid"] = fixture.pid
    try:
        initial = wait_state(state, lambda current: current.get("sample", 0) >= 2, 8)
        if not initial:
            raise RuntimeError("Fixture did not become ready")
        if event_dependent:
            report["initial"] = initial
            if not (
                initial.get("text") == ORIGINAL
                and initial.get("submitEnabled") is False
                and initial.get("selectionLength") == initial.get("emSetSel") == initial.get("emReplaceSel") == 0
            ):
                raise RuntimeError("The event-dependent editor did not start disabled with a collapsed selection")
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
            report["desktop_before"] = [user32.GetForegroundWindow(), user32.GetClipboardSequenceNumber()]
        if entry == "mcp":
            asyncio.run(mcp_edit(exe.stem, fixture, report))
        else:
            sdk_edit(exe.stem, fixture, report)
        sample = read_state(state).get("sample", 0)
        actual = wait_state(state, lambda current: current.get("sample", 0) >= sample + 2, 2)
        report["actual"] = actual
        if tier in TREE_TIERS:
            score_tree(actual, report["payload"], event_dependent=event_dependent)
        else:
            score(actual, report["payload"])
        if event_dependent:
            report["desktop_after"] = [user32.GetForegroundWindow(), user32.GetClipboardSequenceNumber()]
            if report["desktop_before"] != report["desktop_after"]:
                raise RuntimeError("The exact event-dependent chain changed foreground or clipboard sequence")
        report["pass"] = True
    except (ComputerUseError, ExceptionGroup, OSError, RuntimeError, ValueError):
        report.setdefault("actual", read_state(state))
        report["error"] = traceback.format_exc()
    finally:
        if fixture.poll() is None:
            fixture.terminate()
        fixture.wait(timeout=5)
        report["seconds"] = round(time.monotonic() - started, 3)
    return report


def write_report(path: Path, report: dict) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(report, indent=2), encoding="utf-8")
    pending.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("partial", "visual", *TREE_TIERS), action="append")
    parser.add_argument("--entry", choices=("sdk", "mcp"), action="append")
    parser.add_argument("--repeat", type=int, default=1, help="Fresh attempts per selected tier and entry")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt" or args.repeat < 1:
        parser.error("Requires Windows and a positive repeat count")
    if args.output.exists():
        parser.error("Choose a new report path to preserve previous evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tiers, entries = args.tier or ("partial", "visual"), args.entry or ("sdk", "mcp")
    report = {
        "before": manifest(),
        "runner_sha256": sha256(Path(__file__)),
        "fixture_source_sha256": sha256(FIXTURE),
        "capture_veto_source_sha256": sha256(Path(inspect.getfile(tree_only_request))),
        "planned": [{"tier": tier, "entry": entry, "count": args.repeat} for tier in tiers for entry in entries],
        "attempts": [],
        "pass": False,
    }
    with tempfile.TemporaryDirectory(prefix="cua-edit-coverage-") as directory:
        exe = Path(directory) / "CuaEditCoverageFixture.exe"
        build_fixture(exe)
        report["fixture_exe_sha256"] = sha256(exe)
        for tier in tiers:
            for entry in entries:
                for index in range(args.repeat):
                    side = "right" if (index + (tier == "visual") + (entry == "mcp")) % 2 else "left"
                    result = attempt(exe, tier, entry, side)
                    report["attempts"].append(result)
                    sys.stdout.write(f"{tier}/{entry} {index + 1}/{args.repeat} pass={result['pass']}\n")
                    sys.stdout.flush()
                    write_report(args.output, report)
    report["after"] = manifest()
    report["provenance_unchanged"] = report["before"] == report["after"]
    report["pass"] = report["provenance_unchanged"] and all(item["pass"] for item in report["attempts"])
    write_report(args.output, report)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
