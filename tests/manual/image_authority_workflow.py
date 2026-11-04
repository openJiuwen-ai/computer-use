# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real cross-App image retention through 100 unrelated native captures.

Owns two independent Qt processes. The original launcher click creates A's form;
its first delivered Window reference is used directly, then one fixed-Window
image establishes the retained coordinate basis. B remains in another process
and performs 100 separately measured image-only reads. A is never reread or
reprinted during that interval. A final public coordinate click must still use
its delivered image and produce exactly one new independent Qt callback.

Run serially after rebuilding the macOS driver. No failed write is retried, no
private binding is used, no global input is authorized, and startup is measured
without prewarming. The complete stress script is not a single SDK operation;
every MCP call has its own complete-result timer, a 1s target and a 2s completion bound.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from computer_use.paths import native_driver
from computer_use_mcp.session import ExecutionSession
from d34_window_workflow import actual_modules, source_fingerprint, window_with_content
from qt_cocoa_activation_workflow import DEFAULT_PYTHON, ROOT, sha, state
from reliability_latency_trace import measure_work
from reliability_latency_workflow import git_head, image_point, screenshots, texts

LOG = logging.getLogger(__name__)
FIXTURE = ROOT / "tests/fixtures/reliability_latency_probe.py"
CAPTURE_LINE = re.compile(r'(?m)^Capture: ("(?:[^"\\]|\\.)*")$')


def fingerprint():
    result = source_fingerprint()
    fixtures = tuple((ROOT / "tests/fixtures").glob("*.py"))
    result["acceptance_files"] = {
        str(path.relative_to(ROOT)): sha(path)
        for path in (*fixtures, Path(__file__), Path(measure_work.__code__.co_filename),
                     ROOT / "tests/manual/reliability_latency_workflow.py")
    }
    return result


def captures(result):
    """Read public delivery provenance, without inspecting private SDK bindings."""
    return tuple(dict.fromkeys(json.loads(match) for match in CAPTURE_LINE.findall(texts(result))))


def summarized_result(result):
    """Keep the complete text/metadata and image digests without 100 PNG copies."""
    content = []
    for item in result.get("content", ()):
        if item.get("type") != "image":
            content.append(item)
            continue
        # The same public image decoder is used for the optional PNG artifacts.
        image = screenshots({"content": [item]})[0]
        content.append({
            **{key: value for key, value in item.items() if key != "data"},
            "data_omitted": True, "png_bytes": len(image), "png_sha256": hashlib.sha256(image).hexdigest(),
        })
    return {**result, "content": content}


async def verify(folder, python, *, capture_count=100):
    if sys.platform != "darwin":
        raise RuntimeError("This acceptance workflow requires macOS Cocoa")
    if not python.is_file():
        raise FileNotFoundError(f"Optional PySide6 interpreter not found: {python}; use --python")
    if capture_count < 100:
        raise ValueError("Cross-App retention acceptance requires at least 100 unrelated native captures")
    session = ExecutionSession(allow_global_input=False)
    driver = Path(native_driver()["command"])
    suffix = uuid4().hex[:12]
    report = {
        "head": await git_head(), "driver": str(driver), "driver_sha256": sha(driver),
        "fixture_python": str(python), "capture_count": capture_count,
        "source_before": await asyncio.to_thread(fingerprint), "actual_modules_before": actual_modules(),
        "operations": [], "applications": {}, "passed": False, "functional_passed": False,
        "acceptance_scope": "two-independent-apps-delivered-image-retention",
        "measurement": {
            "wall_boundary": "ExecutionSession.execute entry through complete MCP result",
            "cold_start": "Included in first list-before-a; no runtime prewarm",
            "native_work": "Measured interval union is a lower bound; no missing work is estimated",
            "input_grant": "No global input; every recorded globalHID effect fails acceptance",
            "capture_proof": "Distinct public Capture provenance plus fresh native_pixels intervals for every B read",
            "retention": "No A read, print or coordinate operation between the retained image and final click",
            "independent_effects": "Separate fixture PIDs, unique titles, Qt callbacks and values",
            "stress_script": "100 captures are 100 separately timed operations, not one operation",
        },
    }
    children, streams, by_name = {}, [], {}

    async def invoke(name, code, *, save_images=False):
        start = time.perf_counter_ns()
        epoch, monotonic = time.time_ns(), time.monotonic_ns()
        result = await session.execute(code, title=name)
        wall_ns, completed = time.perf_counter_ns() - start, time.time_ns()
        entry = {
            "name": name, "code": code, "wall_seconds": wall_ns / 1e9,
            "started_epoch_ns": epoch, "completed_epoch_ns": completed, "started_monotonic_ns": monotonic,
            "meta": result.get("_meta"), "error": bool(result.get("isError")),
            "cold_worker_start": not report["operations"], "latency_passed": wall_ns <= 2_000_000_000,
            "target_exceeded": wall_ns > 1_000_000_000,
            "source_sha256": report["source_before"]["sha256"], "driver_sha256": report["driver_sha256"],
            "capture_ids": captures(result),
        }
        report["operations"].append(entry)
        by_name[name] = entry
        log_path = result.get("_meta", {}).get("computer-use", {}).get("logPath")
        work = measure_work(Path(log_path) if log_path else None, title=name, wall_ns=wall_ns,
                            started_epoch_ns=epoch, completed_epoch_ns=completed)
        entry.update(native_work=work, majority_passed=work["majority_passed"],
                     sdk_operations=work.get("sdk_operations", []),
                     final_delivery_tail_ns=work.get("final_delivery_tail_ns"))
        pictures = screenshots(result)
        entry["image_sha256"] = [hashlib.sha256(image).hexdigest() for image in pictures]
        entry["image_count"] = len(pictures)
        entry["text_bytes"] = len(texts(result).encode())
        (folder / (name + ".json")).write_text(json.dumps(summarized_result(result), ensure_ascii=False, indent=2))
        (folder / (name + ".txt")).write_text(texts(result))
        if save_images:
            for index, image in enumerate(pictures, 1):
                (folder / f"{name}-{index}.png").write_bytes(image)
        LOG.info("%s: %.3fs, native work %.1f%%", name, wall_ns / 1e9,
                 100 * work["fraction_of_full_wall"])
        assert not result.get("isError"), texts(result)
        return result

    async def launch(key):
        fixture = folder / ("fixture-" + key)
        fixture.mkdir()
        title = f"CU Image {key.upper()} {suffix}"
        errors = (fixture / "stderr.log").open("w")
        streams.append(errors)
        child = await asyncio.create_subprocess_exec(
            str(python), "-X", "faulthandler", str(FIXTURE), "--output", str(fixture), "--title", title,
            env={**os.environ, "QT_QPA_PLATFORM": "cocoa"}, stdin=asyncio.subprocess.DEVNULL,
            stdout=errors, stderr=errors,
        )
        children[key] = child
        initial = await state(fixture, lambda s: s.get("launcher_visible"), process=child, seconds=10)
        assert initial["pid"] == child.pid and initial["platform"] == "cocoa", initial
        report["applications"][key] = {"pid": child.pid, "title": title, "initial": initial}
        result = await invoke(
            "discover-" + key,
            f"choices_{key} = [a for a in computer.list_apps() if a.is_running and a.id not in before_{key}]; "
            f"print('CANDIDATES=' + json.dumps([(a.id, a.display_name) for a in choices_{key}]))",
        )
        line = next(line for line in texts(result).splitlines() if line.startswith("CANDIDATES="))
        candidates = json.loads(line.partition("=")[2])
        owned = [item for item in candidates if item[1] == "python"]
        report["applications"][key]["candidates"] = candidates
        assert len(owned) == 1, candidates
        reference = owned[0][0]
        report["applications"][key]["app_reference"] = reference
        read = await invoke("launcher-" + key, f"app_{key} = computer.app({reference}); app_{key}.read(view='both')")
        assert title in texts(read), "The public App must show its exact owned title before any input"
        return fixture, read

    async def click_effect(name, fixture, expected):
        value = await state(fixture, lambda s: s["inline_clicks"] == expected, process=children["a"])
        events = [event for event in value["callbacks"] if event["kind"] == "inline"
                  and event["monotonic"] >= by_name[name]["started_monotonic_ns"] / 1e9]
        assert len(events) == 1, (name, "The requested click must produce exactly one new callback", events)
        assert value["guard"] == "unchanged" and not value["submissions"], value
        entry = by_name[name]
        entry.update(effect_verified=True, independent_state=value,
                     effect_latency_seconds=events[0]["monotonic"] - entry["started_monotonic_ns"] / 1e9)
        entry["latency_passed"] &= entry["effect_latency_seconds"] <= 2
        return value

    try:
        await invoke(
            "list-before-a", "import json, computer_use, computer_use_mcp; "
            "print('WORKER_SOURCES=' + json.dumps({'sdk': computer_use.__file__, 'mcp': computer_use_mcp.__file__})); "
            "before_a = {a.id for a in computer.list_apps() if a.is_running}",
        )
        fixture_a, _launcher_a = await launch("a")
        opened = await invoke("open-form-a", "app_a.element('Open schedule').click()", save_images=True)
        assert "Meeting title" in texts(opened), "New form AX must arrive in the original click result"
        a = await state(fixture_a, lambda s: s["opened"] == 1 and s["visible"], process=children["a"])
        ref_a = window_with_content(texts(opened), "Meeting title")
        original_point = image_point(opened, a["form_frame"], a["inline_point"])
        report["first_delivered_window"] = {"ref": ref_a, "point": original_point, "oracle": a}
        await invoke("first-delivered-window-click", f"computer.window({ref_a}).click(at={original_point!r})")
        a = await click_effect("first-delivered-window-click", fixture_a, 1)

        # This is the sole retained A image for the cross-App stress interval.
        retained = await invoke("retained-a-image", f"form_a = computer.window({ref_a}); form_a.read(view='image')",
                                save_images=True)
        point_a = image_point(retained, a["form_frame"], a["inline_point"])
        assert len(captures(retained)) == 1, texts(retained)
        report["retained_a_image"] = {"ref": ref_a, "point": point_a, "capture_ids": captures(retained),
                                      "png_sha256": by_name["retained-a-image"]["image_sha256"], "oracle": a}
        await invoke("list-before-b", "before_b = {a.id for a in computer.list_apps() if a.is_running}")
        fixture_b, launcher_b = await launch("b")
        assert children["a"].pid != children["b"].pid
        assert report["applications"]["a"]["app_reference"] != report["applications"]["b"]["app_reference"]
        ref_b = window_with_content(texts(launcher_b), "Open schedule")
        assert ref_b != ref_a, "Independent App instances must not share a public Window reference"
        report["applications"]["b"]["window_reference"] = ref_b
        seen = set()
        for index in range(capture_count):
            label = f"b-image-{index + 1:03d}"
            code = f"form_b = computer.window({ref_b}); form_b.read(view='image')" if index == 0 else \
                "form_b.read(view='image')"
            result = await invoke(label, code, save_images=index in (0, capture_count - 1))
            tokens = captures(result)
            assert len(tokens) == 1 and tokens[0] not in seen, (label, "Expected a fresh public capture", tokens)
            assert by_name[label]["native_work"]["categories"].get("native_pixels", {}).get("interval_count", 0) > 0, (
                label, "Every stress read must perform a real native capture", by_name[label]["native_work"]
            )
            assert by_name[label]["image_count"] == 1, (label, "Image-only read must deliver one usable canvas")
            seen.add(tokens[0])
        report["distinct_b_captures"] = len(seen)
        report["retention_interval_operation_names"] = [
            operation["name"] for operation in report["operations"]
            if operation["started_monotonic_ns"] > by_name["retained-a-image"]["started_monotonic_ns"]
        ]
        before_click = await state(fixture_a, process=children["a"])
        assert before_click["inline_clicks"] == 1, before_click
        report["a_before_retained_click"] = before_click
        # Window translation is a normal state change. The old local pixel
        # still identifies this button when its size/layout is unchanged.
        assert before_click["form_frame"][2:] == a["form_frame"][2:], "A's image-relevant size changed during stress"
        assert before_click["inline_point"] == a["inline_point"], "A's target layout changed during stress"
        await invoke("retained-a-coordinate-click", f"form_a.click(at={point_a!r})", save_images=True)
        report["final_a"] = await click_effect("retained-a-coordinate-click", fixture_a, 2)
        report["final_b"] = await state(fixture_b, process=children["b"])
        assert report["final_b"]["opened"] == 0 and report["final_b"]["inline_clicks"] == 0, report["final_b"]
        assert not report["final_b"]["submissions"] and report["final_b"]["guard"] == "unchanged"
        report["functional_passed"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        LOG.exception("Image authority acceptance failed")
    finally:
        for key, child in children.items():
            try:
                report["applications"][key]["final_oracle"] = await state(folder / ("fixture-" + key), seconds=.2)
            except (AssertionError, FileNotFoundError) as error:
                report["applications"][key]["final_oracle_error"] = str(error)
        try:
            await session.close()
        except Exception as error:
            report["session_cleanup_error"] = {"type": type(error).__name__, "detail": str(error)}
        finally:
            for child in reversed(tuple(children.values())):
                if child.returncode is None:
                    child.terminate()
                    try:
                        await asyncio.wait_for(child.wait(), 3)
                    except TimeoutError:
                        child.kill()
                        await child.wait()
            for stream in streams:
                stream.close()
        report["cleanup_complete"] = not report.get("session_cleanup_error") and all(
            child.returncode is not None for child in children.values()
        )
        report["source_after"] = await asyncio.to_thread(fingerprint)
        report["actual_modules_after"] = actual_modules()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["driver_unchanged"] = report["driver_sha256"] == sha(driver)
        report["latency_passed"] = all(item["latency_passed"] for item in report["operations"])
        report["majority_passed"] = all(item["majority_passed"] for item in report["operations"])
        report["efficiency_evidence"] = (
            "native_lower_bound_proves_majority" if report["majority_passed"] else "requires_processing_span_audit"
        )
        report["global_input_effects"] = [
            event for item in report["operations"] for event in item["native_work"].get("global_input_effects", ())
        ]
        report["slow_calls"] = [
            {"name": item["name"], "wall_seconds": item["wall_seconds"]}
            for item in report["operations"] if not item["latency_passed"]
        ]
        report["passed"] = bool(report["functional_passed"] and report["latency_passed"]
                                and report["source_unchanged"]
                                and report["driver_unchanged"] and report["cleanup_complete"]
                                and not report["global_input_effects"])
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert report["passed"], {
        key: report.get(key) for key in ("error", "functional_passed", "latency_passed", "majority_passed",
                                        "source_unchanged", "driver_unchanged", "slow_calls")
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--captures", type=int, default=100)
    options = parser.parse_args()
    folder = options.output or Path(tempfile.mkdtemp(prefix="image-authority-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        asyncio.run(verify(folder, options.python, capture_count=options.captures))
    finally:
        LOG.info("Cross-App image authority evidence: %s", folder)


if __name__ == "__main__":
    main()
