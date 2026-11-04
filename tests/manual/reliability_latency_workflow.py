# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real MCP delivery, native work intervals and independent Qt callbacks.

Run serially after the driver build. Owns all windows and never submits a real
meeting. Failed writes are never replayed. The first list_apps includes cold
worker startup; no prewarm is performed. Fixture polling, saving and trace
analysis occur outside complete MCP timers. Native-work intervals are a lower
bound; a separate processing-span audit accounts for necessary Python work.

The full chain can start its offscreen acceptance with either control. The
separate offscreen_entry_workflow wrapper uses the same measurements and original
new-form entry while explicitly omitting the title/date/menu chain.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import re
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
                     FIXTURE.with_name("qt_foreground_continuity_probe.py"),
                     FIXTURE.with_name("qt_cocoa_date_probe.py"),
                     Path(__file__), Path(__file__).with_name("offscreen_entry_workflow.py"),
                     Path(measure_work.__code__.co_filename))
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


async def verify(folder, python, *, offscreen_first="cloud", offscreen_only=False):
    if offscreen_first not in ("cloud", "mute"):
        raise ValueError("offscreen_first must be cloud or mute")
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
        "acceptance_scope": "offscreen-entry-only" if offscreen_only else "full-prior-operation-chain",
        "full_chain": not offscreen_only,
        "offscreen_first": offscreen_first,
        "source_before": await asyncio.to_thread(fingerprint), "actual_modules_before": actual_modules(),
        "measurement": {
            "wall_boundary": "ExecutionSession.execute entry through complete MCP result",
            "cold_start": "Included in first list-before; no runtime prewarm",
            "latency": "Target around 1s; flag a single intent exceeding the 2s completion budget",
            "majority": (
                "Native interval union is a lower bound; values below 0.5 require the separate processing-span "
                "audit and do not prove that the remaining time was idle."
            ),
            "input_grant": "No global input; any globalHID effect fails acceptance",
            "independent_effects": "Qt model/signals and callback times, not SDK receipts",
            "multi_intent_timing": (
                "Four independent statements retain their whole-tool time; each intent is scored as lookup + write "
                "+ all non-SDK wall time, without splitting one click to evade the limit."
            ),
        },
    }
    child = None
    by_name = {}

    async def invoke(name, code, *, independent_intents=1):
        start = time.perf_counter_ns()
        epoch, monotonic = time.time_ns(), time.monotonic_ns()
        result = await session.execute(code, title=name)
        wall_ns, completed = time.perf_counter_ns() - start, time.time_ns()
        entry = {
            "name": name, "code": code, "wall_seconds": wall_ns / 1e9,
            "started_epoch_ns": epoch, "completed_epoch_ns": completed, "started_monotonic_ns": monotonic,
            "meta": result.get("_meta"), "error": result.get("isError", False),
            "cold_worker_start": not report["operations"], "latency_passed": wall_ns <= 2_000_000_000,
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
        writes = [item for item in entry["sdk_operations"] if item["type"] in ("ActOnElement", "UIPointer")]
        entry["write_observation_usable"] = all(
            item.get("observationStatus") in ("available", "partial")
            and item.get("contentAvailable") is not False
            and item.get("observationErrorCode") != "observation_timeout"
            and not item.get("overrun")
            for item in writes
        )
        entry["independent_intents"] = independent_intents
        entry["whole_tool_under_one_second"] = wall_ns <= 1_000_000_000
        entry["target_exceeded"] = wall_ns > 1_000_000_000
        if independent_intents > 1:
            # The four separate statements are four intentions. Keep the full
            # tool time, and conservatively charge ALL non-SDK time to EACH
            # intention rather than hiding lookup or final delivery overhead.
            sdk = entry["sdk_operations"]
            assert len(sdk) == independent_intents * 2 and all(
                (sdk[index]["type"], sdk[index + 1]["type"]) == ("ResolveElement", "ActOnElement")
                for index in range(0, len(sdk), 2)
            ), sdk
            assert not entry["native_work"].get("sdk_operation_gaps"), entry["native_work"]
            non_sdk_ns = max(0, wall_ns - sum(item["duration_ns"] for item in sdk))
            entry["intent_upper_bounds_ns"] = [
                sdk[index]["duration_ns"] + sdk[index + 1]["duration_ns"] + non_sdk_ns
                for index in range(0, len(sdk), 2)
            ]
            entry["latency_passed"] = all(value <= 2_000_000_000 for value in entry["intent_upper_bounds_ns"])
            entry["target_exceeded"] = any(value > 1_000_000_000 for value in entry["intent_upper_bounds_ns"])
            entry["latency_boundary"] = "lookup + write + all non-SDK tool time, charged to each independent intent"
        entry["text_bytes"] = len(texts(result).encode())
        entry["text_characters"] = len(texts(result))
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
            effect_start = entry["started_monotonic_ns"] / 1e9
            if entry["independent_intents"] > 1:
                # This call checks the final date write in the four-statement
                # script; earlier intentions remain scored independently above.
                assert callback == "date", (name, callback)
                last_lookup = entry["sdk_operations"][-2]
                effect_start += (last_lookup["start_epoch_ns"] - entry["started_epoch_ns"]) / 1e9
            entry["effect_latency_seconds"] = events[-1]["monotonic"] - effect_start
            entry["latency_passed"] &= entry["effect_latency_seconds"] <= 2
        assert value["guard"] == "unchanged" and not value["submissions"], value
        return value

    try:
        await invoke(
            "list-before", "import json, computer_use; print('SDK=' + computer_use.__file__); "
            "before_ids = {a.id for a in computer.list_apps() if a.is_running}; print(sorted(before_ids))",
        )
        with (folder / "fixture-stderr.log").open("w") as errors:
            child = await asyncio.create_subprocess_exec(
                str(python), "-X", "faulthandler", str(FIXTURE), "--output", str(fixture), "--title", title,
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
            report["application_candidates"] = choices
            # macOS may launch an AutoFill helper beside the disposable Python
            # fixture. Keep every candidate as evidence; bind only this known
            # executable and verify its unique title before sending input.
            owned = [choice for choice in choices if choice[1] == "python"]
            assert len(owned) == 1, choices
            report["selected_application"] = owned[0]
            launcher = await invoke("launcher-read", f"app = computer.app({owned[0][0]}); app.read(view='both')")
            assert title in texts(launcher), "Discovered fixture must show its exact owned title before input"
            opened = await invoke("open-form", "app.element('Open schedule').click()")
            assert "Meeting title" in texts(opened), "New form AX must arrive in the original click result"
            opened_state = await effect("open-form", lambda s: s["opened"] == 1 and s["visible"], callback="opened")
            if not offscreen_only:
                form_ref = window_with_content(texts(opened), "Meeting title")
                report["form_ref"] = form_ref
                # Use the original delivered image and new fixed Window directly,
                # before any preparatory SDK read of that Window.
                delivered_point = image_point(opened, opened_state["form_frame"], opened_state["inline_point"])
                report["first_delivered_window"] = {"ref": form_ref, "oracle": opened_state,
                                                     "delivered_point": delivered_point}
                clicked = await invoke(
                    "delivered-window-click", f"computer.window({form_ref}).click(at={delivered_point!r})"
                )
                await effect("delivered-window-click", lambda s: s["inline_clicks"] == 1, callback="inline")
                assert "Tree:" in texts(clicked) and "Tree: not requested" not in texts(clicked), texts(clicked)
                form_image = await invoke("form-image", f"form = computer.window({form_ref}); form.read(view='image')")
                before_inline = await state(fixture, process=child)
                inline_point = image_point(form_image, before_inline["form_frame"], before_inline["inline_point"])
                report["inline_geometry"] = {"oracle": before_inline, "delivered_point": inline_point}
                await invoke("inline-coordinate", f"form.click(at={inline_point!r})")
                await effect("inline-coordinate", lambda s: s["inline_clicks"] == 2, callback="inline")
                batched = await invoke(
                    "set-title-date-script",
                    "app.element('Meeting title').type_text('Intermediate title', replace=True)\n"
                    "app.element('Meeting title').type_text('Latency acceptance', replace=True)\n"
                    "app.element('Meeting date').click()\n"
                    "app.element('Meeting date').type_text('2026/11/9', replace=True)",
                independent_intents=4,
                )
                await effect("set-title-date-script",
                             lambda s: s["topic"] == "Latency acceptance" and s["date_model"] == "2026/11/9",
                             callback="date")
                assert "Tree: full" not in texts(batched), "Previously delivered form must use diffs in a combined call"
                assert len(re.findall(r"Action \[(?:dispatched|verified)\]", texts(batched))) == 4, texts(batched)
                assert texts(batched).count("Tree: diff") == 1, texts(batched)
                assert "Intermediate title" not in texts(batched), texts(batched)
                await invoke("open-hour", "app.element('08:00').click()")
                await effect("open-hour", lambda s: s["popup"] is not None)
                await invoke("choose-hour", "app.element('10:00').click()")
                await effect("choose-hour", lambda s: s["hour_activations"] == ["10:00"], callback="hour")
                reopened = await invoke("reopen-hour", "app.element('10:00').click()")
                popup_state = await effect("reopen-hour", lambda s: s["popup_geometry"] is not None)
                popup_refs = refs(texts(reopened)) - refs(texts(opened))
                assert len(popup_refs) == 1, ("Popup must expose one selectable window", popup_refs)
                popup_ref = next(iter(popup_refs))
                popup_image = await invoke(
                    "popup-image", f"popup = computer.window({popup_ref}); popup.read(view='image')"
                )
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
                await effect(
                    "choose-duration", lambda s: s["duration_activations"] == ["90 minutes"], callback="duration"
                )
                await invoke("open-repeat", "app.element('Never').click()")
                await effect("open-repeat", lambda s: s["popup"] is not None)
                await invoke("choose-repeat", "app.element('Every week').click()")
                await effect("choose-repeat", lambda s: s["repeat_activations"] == ["Every week"], callback="repeat")
            report["before_offscreen"] = await state(fixture, process=child)
            before_offscreen = report["before_offscreen"]
            assert before_offscreen["scroll_value"] == 0
            assert not before_offscreen["cloud"] and not before_offscreen["mute"]
            assert before_offscreen["microphone_live"]
            assert before_offscreen["cloud_events"] == [] and before_offscreen["mute_events"] == []
            viewport_bottom = before_offscreen["viewport_y"] + before_offscreen["viewport_height"]
            for name in ("cloud", "mute"):
                assert before_offscreen["offscreen_controls"][name]["frame"][1] >= viewport_bottom
                assert not before_offscreen["offscreen_controls"][name]["fully_in_viewport"]
            order = (offscreen_first, "mute" if offscreen_first == "cloud" else "cloud")
            report["offscreen_order"] = order
            labels = {"cloud": "Cloud recording", "mute": "Mute on entry"}
            for index, name in enumerate(order):
                if index:
                    adjacent = await state(fixture, process=child)
                    report["before_adjacent"] = adjacent
                    # A semantic AX action can legitimately complete the first
                    # offscreen click without scrolling. Record visibility;
                    # do not turn exposure into a universal input prerequisite.
                    report["adjacent_initially_visible"] = adjacent["offscreen_controls"][name]["fully_in_viewport"]
                operation = ("offscreen-" if not index else "adjacent-") + name
                await invoke(operation, f"app.element({labels[name]!r}).click()")
                value = await effect(
                    operation, lambda current, name=name: current[name] and current[name + "_events"] == [True],
                    callback=name,
                )
                if not index:
                    report["first_offscreen"] = value
                    report["first_offscreen_scrolled"] = value["scroll_value"] != before_offscreen["scroll_value"]
                    report["first_offscreen_became_visible"] = value["offscreen_controls"][name]["fully_in_viewport"]
                report["final"] = value
            assert report["final"]["cloud"] and report["final"]["mute"]
            assert not report["final"]["microphone_live"]
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
        report["write_observations_usable"] = all(e["write_observation_usable"] for e in report["operations"])
        report["majority_passed"] = all(e["majority_passed"] for e in report["operations"])
        report["efficiency_evidence"] = (
            "native_lower_bound_proves_majority" if report["majority_passed"] else "requires_processing_span_audit"
        )
        report["global_input_effects"] = [
            event for entry in report["operations"] for event in entry["native_work"].get("global_input_effects", ())
        ]
        report["passed"] = bool(report.get("functional_passed") and report["latency_passed"]
                                and report["write_observations_usable"]
                                and report["source_unchanged"]
                                and report["driver_unchanged"] and not report["global_input_effects"])
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    assert report["passed"], {
        "functional_passed": report.get("functional_passed"), "latency_passed": report["latency_passed"],
        "native_only_majority": report["majority_passed"],
        "write_observations_usable": report["write_observations_usable"],
        "source_unchanged": report["source_unchanged"], "driver_unchanged": report["driver_unchanged"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--offscreen-first", choices=("cloud", "mute"), default="cloud")
    options = parser.parse_args()
    folder = options.output or Path(tempfile.mkdtemp(prefix="reliability-latency-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(verify(folder, options.python, offscreen_first=options.offscreen_first))
    finally:
        LOG.info("Reliability/latency evidence: %s", folder)


if __name__ == "__main__":
    main()
