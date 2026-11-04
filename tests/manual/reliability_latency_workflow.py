# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real MCP delivery, native work intervals and independent Qt callbacks.

Run serially after the driver build. Owns all windows and never submits a real
meeting. Failed writes are never replayed. The first list_apps includes cold
worker startup; no prewarm is performed. Fixture polling, saving and trace
analysis occur outside complete MCP timers. Actual native-work intervals must
occupy more than half each complete call; uninstrumented work stays explicit.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from computer_use.paths import native_driver
from computer_use_mcp.session import ExecutionSession
from d34_window_workflow import actual_modules, refs, source_fingerprint, window_point, window_with_content
from qt_cocoa_activation_workflow import DEFAULT_PYTHON, ROOT, sha, state
from reliability_latency_trace import measure_work

LOG = logging.getLogger(__name__)
FIXTURE = ROOT / "tests/fixtures/reliability_latency_probe.py"


def texts(result):
    return "\n".join(item["text"] for item in result.get("content", ()) if item["type"] == "text")


def screenshots(result):
    return [base64.b64decode(item["data"]) for item in result.get("content", ()) if item["type"] == "image"]


def fingerprint():
    value = source_fingerprint()
    value["acceptance_files"] = {
        str(path.relative_to(ROOT)): sha(path)
        for path in (FIXTURE, FIXTURE.with_name("mouse_button_counter.py"),
                     Path(__file__), Path(measure_work.__code__.co_filename))
    }
    return value


async def git_head():
    child = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD", cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    output, error = await child.communicate()
    if child.returncode:
        raise RuntimeError(error.decode())
    return output.decode().strip()


def image_point(result, geometry, point):
    pictures = screenshots(result)
    assert len(pictures) == 1, "A fixed-window image must deliver one usable canvas"
    return window_point(SimpleNamespace(screenshot=pictures[0]), {"frame": geometry, "countPoint": point})


async def verify(folder, python):
    if sys.platform != "darwin":
        raise RuntimeError("This acceptance workflow requires macOS Cocoa")
    if not python.is_file():
        raise FileNotFoundError(f"Optional PySide6 interpreter not found: {python}; use --python")
    fixture = folder / "fixture"
    fixture.mkdir()
    title = "CU Latency " + uuid4().hex[:12]
    session = ExecutionSession(allow_global_input=False)
    driver = Path(native_driver()["command"])
    report = {
        "head": await git_head(), "driver": str(driver), "driver_sha256": sha(driver),
        "fixture_python": str(python), "title": title, "operations": [], "passed": False,
        "source_before": await asyncio.to_thread(fingerprint), "actual_modules_before": actual_modules(),
        "measurement": {
            "wall_boundary": "ExecutionSession.execute entry through complete MCP result",
            "cold_start": "Included in first list-before; no runtime prewarm",
            "majority": "Measured native-work interval union / complete MCP wall time > 0.5",
            "input_grant": "No global input; any globalHID effect fails acceptance",
            "independent_effects": "Qt model/signals and callback times, not SDK receipts",
        },
    }
    child = None
    by_name = {}

    async def invoke(name, code):
        start = time.perf_counter_ns()
        epoch, monotonic = time.time_ns(), time.monotonic_ns()
        result = await session.execute(code, title=name)
        wall_ns, completed = time.perf_counter_ns() - start, time.time_ns()
        entry = {
            "name": name, "code": code, "wall_seconds": wall_ns / 1e9,
            "started_epoch_ns": epoch, "completed_epoch_ns": completed, "started_monotonic_ns": monotonic,
            "meta": result.get("_meta"), "error": result.get("isError", False),
            "cold_worker_start": not report["operations"], "latency_passed": wall_ns <= 1_000_000_000,
        }
        report["operations"].append(entry)
        by_name[name] = entry
        log_path = result.get("_meta", {}).get("computer-use", {}).get("logPath")
        entry["native_work"] = measure_work(
            Path(log_path) if log_path else None, title=name, wall_ns=wall_ns,
            started_epoch_ns=epoch, completed_epoch_ns=completed,
        )
        entry["majority_passed"] = entry["native_work"]["majority_passed"]
        entry["sdk_operations"] = entry["native_work"].get("sdk_operations", [])
        entry["final_delivery_tail_ns"] = entry["native_work"].get("final_delivery_tail_ns")
        entry["text_bytes"] = len(texts(result).encode())
        entry["image_count"] = len(screenshots(result))
        entry["full_tree_headers"] = texts(result).count("Tree: full")
        entry["diff_tree_headers"] = texts(result).count("Tree: diff")
        (folder / (name + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        (folder / (name + ".txt")).write_text(texts(result))
        for index, image in enumerate(screenshots(result), 1):
            (folder / f"{name}-{index}.png").write_bytes(image)
        LOG.info("%s: %.3fs, measured native work %.1f%%", name, wall_ns / 1e9,
                 100 * entry["native_work"]["fraction_of_full_wall"])
        # Timing failures remain scored while independent effects are checked.
        assert not result.get("isError"), texts(result)
        return result

    async def effect(name, predicate, *, callback=None):
        value = await state(fixture, predicate, process=child)
        entry = by_name[name]
        entry["independent_state"], entry["effect_verified"] = value, True
        if callback is not None:
            events = [event for event in value["callbacks"] if event["kind"] == callback
                      and event["monotonic"] >= entry["started_monotonic_ns"] / 1e9]
            assert events, (name, "Required independent callback was not recorded", value)
            entry["effect_latency_seconds"] = events[-1]["monotonic"] - entry["started_monotonic_ns"] / 1e9
            entry["latency_passed"] &= entry["effect_latency_seconds"] <= 1
        assert value["guard"] == "unchanged" and not value["submissions"], value
        return value

    try:
        await invoke(
            "list-before", "import json, computer_use; print('SDK=' + computer_use.__file__); "
            "before_ids = {a.id for a in computer.list_apps() if a.is_running}; print(sorted(before_ids))",
        )
        with (folder / "fixture-stderr.log").open("w") as errors:
            child = await asyncio.create_subprocess_exec(
                str(python), str(FIXTURE), "--output", str(fixture), "--title", title,
                env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
                stdin=asyncio.subprocess.DEVNULL, stdout=errors, stderr=errors,
            )
            report["initial"] = await state(fixture, lambda s: s.get("launcher_visible"), process=child, seconds=10)
            assert report["initial"]["pid"] == child.pid and report["initial"]["platform"] == "cocoa"
            discovered = await invoke(
                "list-fixture",
                "new_apps = [a for a in computer.list_apps() if a.is_running and a.id not in before_ids]; "
                "print('CANDIDATES=' + json.dumps([(a.id, a.display_name) for a in new_apps]))",
            )
            line = next(s for s in texts(discovered).splitlines() if s.startswith("CANDIDATES="))
            choices = json.loads(line.partition("=")[2])
            assert len(choices) == 1, choices
            report["selected_application"] = choices[0]
            await invoke("launcher-read", f"app = computer.app({choices[0][0]}); app.read(view='both')")
            opened = await invoke("open-form", "app.element('Open schedule').click()")
            assert "Meeting title" in texts(opened), "New form AX must arrive in the original click result"
            await effect("open-form", lambda s: s["opened"] == 1 and s["visible"], callback="opened")
            form_ref = window_with_content(texts(opened), "Meeting title")
            report["form_ref"] = form_ref
            form_image = await invoke("form-image", f"form = computer.window({form_ref}); form.read(view='image')")
            before_inline = await state(fixture, process=child)
            inline_point = image_point(form_image, before_inline["form_frame"], before_inline["inline_point"])
            report["inline_geometry"] = {"oracle": before_inline, "delivered_point": inline_point}
            await invoke("inline-coordinate", f"form.click(at={inline_point!r})")
            await effect("inline-coordinate", lambda s: s["inline_clicks"] == 1, callback="inline")
            batched = await invoke(
                "set-title-date-script",
                "app.element('Meeting title').type_text('Latency acceptance', replace=True)\n"
                "app.element('Meeting date').click()\n"
                "app.element('Meeting date').type_text('2026/11/9', replace=True)",
            )
            await effect("set-title-date-script",
                         lambda s: s["topic"] == "Latency acceptance" and s["date_model"] == "2026/11/9",
                         callback="date")
            assert "Tree: full" not in texts(batched), "Previously delivered form must use diffs in a combined call"
            await invoke("open-hour", "app.element('08:00').click()")
            await effect("open-hour", lambda s: s["popup"] is not None)
            await invoke("choose-hour", "app.element('10:00').click()")
            await effect("choose-hour", lambda s: s["hour_activations"] == ["10:00"], callback="hour")
            reopened = await invoke("reopen-hour", "app.element('10:00').click()")
            popup_state = await effect("reopen-hour", lambda s: s["popup_geometry"] is not None)
            popup_refs = refs(texts(reopened)) - refs(texts(opened))
            assert len(popup_refs) == 1, ("Popup must expose one selectable window", popup_refs)
            popup_ref = next(iter(popup_refs))
            popup_image = await invoke("popup-image", f"popup = computer.window({popup_ref}); popup.read(view='image')")
            geometry = popup_state["popup_geometry"]
            choice = geometry["choices"].get("10:15")
            assert choice and choice["visible"], geometry
            popup_point = image_point(popup_image, geometry["frame"], choice["point"])
            report["popup_geometry"] = {"ref": popup_ref, "oracle": geometry, "delivered_point": popup_point}
            await invoke("popup-coordinate", f"popup.click(at={popup_point!r})")
            await effect("popup-coordinate", lambda s: s["hour_activations"] == ["10:00", "10:15"], callback="hour")
            await invoke("open-duration", "app.element('30 minutes').click()")
            await effect("open-duration", lambda s: s["popup"] is not None)
            await invoke("choose-duration", "app.element('90 minutes').click()")
            await effect("choose-duration", lambda s: s["duration_activations"] == ["90 minutes"], callback="duration")
            await invoke("open-repeat", "app.element('Never').click()")
            await effect("open-repeat", lambda s: s["popup"] is not None)
            await invoke("choose-repeat", "app.element('Every week').click()")
            await effect("choose-repeat", lambda s: s["repeat_activations"] == ["Every week"], callback="repeat")
            report["before_offscreen"] = await state(fixture, process=child)
            before_offscreen = report["before_offscreen"]
            assert before_offscreen["scroll_value"] == 0
            assert before_offscreen["cloud_y"] >= before_offscreen["viewport_y"] + before_offscreen["viewport_height"]
            await invoke("offscreen-cloud", "app.element('Cloud recording').click()")
            report["final"] = await effect("offscreen-cloud",
                                           lambda s: s["cloud"] and s["cloud_events"] == [True], callback="cloud")
            report["functional_passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if child is not None and child.returncode is None:
            report["final_oracle"] = await state(fixture, process=child)
        await session.close()
        if child is not None and child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 3)
            except TimeoutError:
                child.kill()
                await child.wait()
        report["source_after"] = await asyncio.to_thread(fingerprint)
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["driver_unchanged"] = report["driver_sha256"] == sha(driver)
        report["latency_passed"] = all(e["latency_passed"] for e in report["operations"])
        report["majority_passed"] = all(e["majority_passed"] for e in report["operations"])
        report["global_input_effects"] = [
            event for entry in report["operations"] for event in entry["native_work"].get("global_input_effects", ())
        ]
        report["passed"] = bool(report.get("functional_passed") and report["latency_passed"]
                                and report["majority_passed"] and report["source_unchanged"]
                                and report["driver_unchanged"] and not report["global_input_effects"])
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    assert report["passed"], report["operations"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    folder = options.output or Path(tempfile.mkdtemp(prefix="reliability-latency-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(verify(folder, options.python))
    finally:
        LOG.info("Reliability/latency evidence: %s", folder)


if __name__ == "__main__":
    main()
