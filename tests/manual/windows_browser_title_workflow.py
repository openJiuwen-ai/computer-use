# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify exact Windows title selection against two separate Edge profiles."""

import argparse
import asyncio
import base64
import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from ctypes import wintypes
from io import BytesIO, StringIO
from pathlib import Path

from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from PIL import Image, ImageChops
from windows_browser_fixture import SCOPE_COLORS, browser_fixture, scope_marker
from windows_browser_workflow import element_ref

from computer_use_agent.config import AgentConfig
from computer_use_agent.connection import McpConnection

WINDOW_REF = re.compile(r"(?m)^Window (\d+)[:,]|^Window reference: (\d+)\s*$")
READ_CODE = 'obs = app.read(view="both", full=True)'
TREE_READ_CODE = 'obs = app.read(view="tree", full=True, allow_foreground=False)'
STATUS_CODE = '\nprint("CUA_SCOPE_STATUS", action.status)'
DETAIL_PAGE_CHARACTERS = 16_384
DETAIL_LIMIT_CHARACTERS = 8 * 1024 * 1024


def fixture_windows(titles: set[str]) -> list[dict]:
    """Independently verify exact native captions and distinct owned fixture PIDs."""
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    found = []

    @callback_type
    def inspect(hwnd, _):
        name = ctypes.create_unicode_buffer(512)
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextW(hwnd, name, len(name)) and name.value in titles:
            process = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
            found.append({"hwnd": int(hwnd), "pid": process.value, "title": name.value})
        return True

    for _ in range(100):
        found.clear()
        if not user32.EnumWindows(inspect, 0):
            raise RuntimeError("Independent caption census failed")
        if len(found) == 2 and len({item["pid"] for item in found}) == 2:
            return found
        time.sleep(0.05)
    raise RuntimeError(f"Expected two independent Edge window captions for {titles!r}, got {found}")


def image_summary(png: bytes) -> dict:
    with Image.open(BytesIO(png)) as source, source.convert("RGB") as image:
        result = {"size": image.size, "sha256": hashlib.sha256(png).hexdigest(), "markers": {}}
        for role, rgb in SCOPE_COLORS.items():
            with Image.new("RGB", image.size, rgb) as marker, ImageChops.difference(image, marker) as difference:
                red, green, blue = difference.split()
                result["markers"][role] = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()[0]
        return result


def check_delivery(report: dict, phase: str, text: str, images: list[bytes], *, boundary: bool = False) -> None:
    tree_only = report.get("tree_only", False)
    refs = {int(a or b) for a, b in WINDOW_REF.findall(text)}
    step = {
        "phase": phase, "text_sha256": hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest(),
        "window_refs": sorted(refs), "image_count": len(images),
        "images": [] if tree_only else [image_summary(png) for png in images],
        "companion_content": report["companion_marker"] in text or (
            report["companion_title"] != report["title"] and report["companion_title"] in text
        ),
        "target_content": report["target_marker"] in text,
    }
    report["steps"].append(step)
    if tree_only and images:
        raise RuntimeError(f"{phase} unexpectedly delivered an image in the tree-only probe")
    if step["companion_content"] or any(
        label in text for label in ("Showing the visible desktop", "Desktop: full view", "Image: desktop")
    ):
        raise RuntimeError(f"{phase} delivered out-of-scope content")
    if boundary and (
        len(refs) != 1 or len(images) != (0 if tree_only else 1)
        or not step["target_content"] or report["title"] not in text
    ):
        raise RuntimeError(f"{phase} did not deliver the expected named target tree and image count")
    if boundary and "target_ref" not in report:
        report["target_ref"] = next(iter(refs))
    if refs and refs != {report.get("target_ref")}:
        raise RuntimeError(f"{phase} delivered another public window reference: {sorted(refs)}")
    if any(img["markers"]["companion"] >= 150 or img["markers"]["target"] < 150 for img in step["images"]):
        raise RuntimeError(f"{phase} delivered non-target or mixed image pixels")


def check_detail_metadata(report: dict, details: str) -> None:
    _, prefix, body = details.partition("Captured interface metadata: ")
    if not prefix:
        raise RuntimeError("The retained details did not deliver interface metadata")
    header, _ = json.JSONDecoder().raw_decode(body)
    windows = header["windows"]
    if len(windows) != 1 or (windows[0]["ref"], windows[0]["title"]) != (report["target_ref"], report["title"]):
        raise RuntimeError("The retained details did not publish only the exact selected window")
    window = windows[0]
    native_window = window["native_window"]
    application = header["directory"]["application"]
    directories = (header["directory"], window.get("source_directory"), window.get("rechecked_directory"))
    if any(
        directory["application"] != application
        or [item["window"] for item in directory["surfaces"]] != [native_window]
        or directory.get("focusedWindow") not in {None, native_window}
        or any(item["window"] != native_window for item in directory.get("endedWindows", ()))
        for directory in directories if directory is not None
    ):
        raise RuntimeError("A retained directory exposed another native window")
    source = window.get("source_applications")
    if source is not None:
        apps = source["applications"]
        if (
            len(apps) != 1 or apps[0]["id"] != application
            or [item["window"] for item in apps[0]["windows"]] != [native_window]
        ):
            raise RuntimeError("The retained discovery record exposed another application or native window")
    report["details_window"] = {"ref": window["ref"], "title": window["title"]}


async def read_details(call, report: dict) -> None:
    pages, cursor = [], None
    seen_cursors = set()
    total, consumed = None, 0
    while True:
        reply, images = await call(
            "page retained tree details",
            f"import base64, json\npage = retained.read_details(cursor={cursor!r}, length={DETAIL_PAGE_CHARACTERS})\n"
            'encoded = base64.b64encode(page.text.encode("utf-8", errors="surrogatepass")).decode("ascii")\n'
            'print("CUA_SCOPE_DETAILS", json.dumps([encoded, page.total, page.next]))',
        )
        if images:
            check_delivery(report, "detail page", reply, images)
        page = re.search(r"(?m)^CUA_SCOPE_DETAILS (.+)$", reply)
        if page is None:
            raise RuntimeError("A retained detail page was not delivered")
        encoded, declared_total, cursor = json.loads(page[1])
        if type(declared_total) is not int or not 0 < declared_total <= DETAIL_LIMIT_CHARACTERS:
            raise RuntimeError(f"Retained detail total {declared_total!r} is outside the probe's character budget")
        if total is None:
            total = declared_total
            report["details_total_chars"] = total
            report["details_expected_pages"] = (total + DETAIL_PAGE_CHARACTERS - 1) // DETAIL_PAGE_CHARACTERS
            sys.stderr.write(f"Retained details have {total} characters in {report['details_expected_pages']} pages\n")
            sys.stderr.flush()
        elif declared_total != total:
            raise RuntimeError(f"Retained detail total changed from {total} to {declared_total}")
        chunk = base64.b64decode(encoded, validate=True).decode("utf-8", errors="surrogatepass")
        if len(chunk) != min(DETAIL_PAGE_CHARACTERS, total - consumed):
            raise RuntimeError("A retained detail page did not advance by its declared character range")
        pages.append(chunk)
        consumed += len(chunk)
        report.update(details_pages=len(pages), details_chars=consumed)
        if consumed == total:
            if cursor is not None:
                raise RuntimeError("Retained detail cursor continued past the declared document end")
            break
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise RuntimeError("Retained detail cursor ended early or repeated before the declared document end")
        seen_cursors.add(cursor)
    details = "".join(pages)
    check_delivery(report, "retained details", details, [])
    check_detail_metadata(report, details)


async def exercise(call, report: dict) -> None:
    bind = f"app = computer.app(window_title={report['title']!r})"
    tree_only = report.get("tree_only", False)
    if report["duplicate"]:
        read = TREE_READ_CODE if tree_only else READ_CODE
        text, images = await call(
            "refuse duplicate caption",
            f"try:\n    {bind}\n    {read}\n"
            'except Exception as error:\n    print("CUA_SCOPE_ERROR", error.code, error.effect)',
        )
        check_delivery(report, "duplicate", text, images)
        report["duplicate_error"] = text.strip()
        if (
            images or WINDOW_REF.search(text) or report["target_marker"] in text
            or re.search(r"(?m)^AX:|^Tree:", text)
            or not re.search(r"(?m)^CUA_SCOPE_ERROR ambiguous_target none$", text)
        ):
            raise RuntimeError("Duplicate title did not refuse with ambiguous_target before delivering content")
        return

    if tree_only:
        text, images = await call(
            "read exact window tree", bind + "\n" + TREE_READ_CODE + "\nretained = obs\nprint(obs.text)",
        )
        check_delivery(report, "tree", text, images, boundary=True)
        await read_details(call, report)
        return

    before, images = await call(
        "read exact window", bind + '\nobs = app.read(view="both", full=True, allow_foreground=True)\nprint(obs.text)',
    )
    check_delivery(report, "before", before, images, boundary=True)
    editor = element_ref(before, r"^[ \t]*(\d+)\s+[^\n]*\(text-input\) Draft editor\b")
    button = element_ref(before, r"^[ \t]*(\d+)\s+[^\n]*\(read-only\) Apply draft$")
    for phase, code in (
        ("click editor", f"editor = app.element({editor})\naction = editor.click()"),
        ("select all", 'action = editor.press_key("ctrl+a")'),
        ("replace draft", f"action = editor.paste({report['draft']!r})"),
        ("apply draft", f"action = app.element({button}).click()"),
    ):
        text, images = await call(phase, code + STATUS_CODE)
        check_delivery(report, phase, text, images)
        receipt = re.search(r"(?m)^CUA_SCOPE_STATUS (dispatched|verified)$", text)
        if receipt is None:
            raise RuntimeError(f"{phase} did not return a successful dispatch receipt")
        report["steps"][-1]["status"] = receipt[1]
    after, images = await call("verify exact window", READ_CODE + "\nprint(obs.text)")
    check_delivery(report, "after", after, images, boundary=True)
    readback, images = await call(
        "read the original editor",
        'import json\nprint("CUA_SCOPE_READBACK", json.dumps(editor.read_text().text))',
    )
    check_delivery(report, "readback", readback, images)
    match = re.search(r"(?m)^CUA_SCOPE_READBACK (.+)$", readback)
    if match is None or json.loads(match[1]) != report["draft"]:
        raise RuntimeError("The original editor did not read back the exact draft")
    report["readback"] = json.loads(match[1])


def sdk_exercise(report: dict) -> None:
    with Computer() as computer:
        namespace = {"computer": computer}

        async def call(_label, code):
            namespace.pop("obs", None)
            namespace.pop("action", None)
            with redirect_stdout(StringIO()) as stream:
                exec(code, namespace)  # noqa: S102 - fixed test code with quoted fixture values.
            action = namespace.get("action")
            observation = namespace.get("obs") or getattr(action, "observation", None)
            if observation is None:
                return stream.getvalue(), []
            return stream.getvalue() + "\n" + observation.text, (
                [observation.screenshot] if observation.screenshot is not None else []
            )

        asyncio.run(exercise(call, report))


async def mcp_exercise(report: dict) -> None:
    connection = McpConnection(AgentConfig().mcp_config())
    try:
        async def call(label, code):
            response = await connection.call("computer_use", {"title": label, "code": code})
            text = "\n".join(block.text for block in response.content if block.type == "text")
            images = [
                b"" if report.get("tree_only") else base64.b64decode(block.data)
                for block in response.content if block.type == "image"
            ]
            if response.isError:
                check_delivery(report, label, text, images)
                raise RuntimeError(f"{label} failed before successful dispatch")
            return text, images

        await exercise(call, report)
    finally:
        await connection.close()


def run(*, mcp: bool, duplicate: bool, tree_only: bool = False) -> dict:
    target, companion = uuid.uuid4().hex[:10], uuid.uuid4().hex[:10]
    title = f"CUA Browser Draft {target}"
    report = {
        "pass": False, "transport": "mcp" if mcp else "sdk", "duplicate": duplicate, "tree_only": tree_only,
        "title": title, "companion_title": title if duplicate else f"CUA Browser Draft {companion}",
        "draft": f"CUA_{target}_browser\n中文草稿🙂",
        "target_marker": scope_marker("target", target), "companion_marker": scope_marker("companion", companion),
        "provenance": startup_provenance(native_driver()["command"]), "steps": [],
    }
    with ExitStack() as stack:
        target_state, companion_state = None, None
        try:
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="cua-edge-title-")))
            _, companion_state = stack.enter_context(browser_fixture(
                companion, folder / "companion", title=report["companion_title"], scope="companion",
            ))
            _, target_state = stack.enter_context(browser_fixture(target, folder / "target", title=title, scope="target"))
            report["before"] = {"target": target_state(), "companion": companion_state()}
            report["fixture_windows"] = fixture_windows({title, report["companion_title"]})
            with Computer() as setup:
                names = Counter(app.display_name for app in setup.list_apps()
                                if app.is_running and app.display_name.casefold() in {"msedge", "microsoft edge"})
            report["same_name_app_counts"] = dict(names)
            if not any(count >= 2 for count in names.values()):
                raise RuntimeError("Setup did not discover two running Edge Apps with the same display name")
            if mcp:
                asyncio.run(mcp_exercise(report))
            else:
                sdk_exercise(report)
            actual = {"target": target_state(), "companion": companion_state()}
            if actual["companion"] != report["before"]["companion"]:
                raise RuntimeError("Independent companion page received input")
            if duplicate or tree_only:
                if actual != report["before"]:
                    raise RuntimeError("Read-only selection changed a fixture page")
            else:
                effect = actual["target"]
                if not (
                    effect["text"] == effect["saved"] == report["draft"] and effect["applied"] == 1
                    and effect["other"] == "untouched" and effect["events"].count("insertFromPaste") == 1
                ):
                    raise RuntimeError("Independent target oracle found a missing or repeated effect")
            report["pass"] = True
        except (ComputerUseError, ExceptionGroup, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
        finally:
            report["actual"] = {
                "target": target_state() if target_state is not None else None,
                "companion": companion_state() if companion_state is not None else None,
            }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--duplicate", action="store_true", help="Verify exact title ambiguity without content delivery")
    parser.add_argument("--tree-only", action="store_true", help="Read the target tree and details without input or images")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt" or args.output.exists():
        parser.error("Requires Windows and a fresh output path")
    report = run(mcp=args.mcp, duplicate=args.duplicate, tree_only=args.tree_only)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{args.output} pass={report['pass']}\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
