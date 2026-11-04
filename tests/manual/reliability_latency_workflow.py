# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real MCP delivery and independent Qt callbacks, including a newly opened form.

Run serially after the driver build. Owns all windows and never submits a real
meeting. No write is replayed. Measurements include each complete MCP result;
fixture inspection and saving artifacts happen outside the operation timer.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from computer_use.paths import native_driver
from computer_use_mcp.session import ExecutionSession
from qt_cocoa_activation_workflow import DEFAULT_PYTHON, ROOT, sha, state

LOG = logging.getLogger(__name__)


def texts(result):
    return "\n".join(item["text"] for item in result.get("content", ()) if item["type"] == "text")


async def verify(folder, python):
    fixture = folder / "fixture"
    fixture.mkdir()
    title = "CU Latency " + uuid4().hex[:12]
    session = ExecutionSession(allow_global_input=False)
    driver = Path(native_driver()["command"])
    report = {
        "head": (await asyncio.to_thread(
            subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        )).strip(),
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "source_diff_sha256": hashlib.sha256(
            await asyncio.to_thread(subprocess.check_output, ["git", "diff", "HEAD", "--binary"], cwd=ROOT)
        ).hexdigest(),
        "title": title,
        "operations": [],
        "passed": False,
        "latency_note": "One second is recorded as a diagnostic target, not asserted as an agreed GA threshold.",
    }
    child = None

    async def invoke(name, code):
        start = time.perf_counter()
        result = await session.execute(code, title=name)
        wall = time.perf_counter() - start
        entry = {"name": name, "wall_seconds": wall, "meta": result.get("_meta"), "error": result.get("isError", False)}
        entry["text_characters"] = len(texts(result))
        images = [item for item in result.get("content", ()) if item["type"] == "image"]
        entry["image_blocks"] = len(images)
        for index, item in enumerate(images):
            (folder / f"{name}-{index}.png").write_bytes(base64.b64decode(item["data"]))
        report["operations"].append(entry)
        (folder / (name + ".txt")).write_text(texts(result))
        # Persist timing failures but finish checking the independent effect of
        # a completed action. A slow result must never authorize input replay.
        entry["latency_passed"] = wall <= 1
        assert not result.get("isError"), texts(result)
        return result

    try:
        # Resource initialization is recorded separately; it performs no UI op.
        report["initialization"] = await session.execute(
            "import json; print('Runtime ready')", title="Initialize runtime"
        )
        await invoke(
            "list-before",
            "before_ids = {a.id for a in computer.list_apps() if a.is_running}; print(sorted(before_ids))",
        )
        with (folder / "fixture-stderr.log").open("w") as errors:
            child = await asyncio.create_subprocess_exec(
                str(python),
                "-X", "faulthandler",
                str(ROOT / "tests/fixtures/reliability_latency_probe.py"),
                "--output",
                str(fixture),
                "--title",
                title,
                env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
                stdout=errors,
                stderr=errors,
            )
            await state(fixture, lambda s: s.get("launcher_visible"), process=child, seconds=10)
            discovered = await invoke(
                "list-fixture",
                "new_apps = [a for a in computer.list_apps() if a.is_running and a.id not in before_ids]; print('CANDIDATES=' + json.dumps([(a.id, a.display_name) for a in new_apps]))",
            )
            line = next(s for s in texts(discovered).splitlines() if s.startswith("CANDIDATES="))
            choices = json.loads(line.partition("=")[2])
            assert len(choices) == 1, choices
            await invoke("launcher-read", f"app = computer.app({choices[0][0]}); app.read(view='both')")
            opened = await invoke("open-form", "app.element('Open schedule').click()")
            assert "Meeting title" in texts(opened), texts(opened)
            await state(fixture, lambda s: s["opened"] == 1 and s["visible"], process=child)
            current = re.search(r"Window (\d+): [^\n]*\(current", texts(opened))
            assert current is not None, texts(opened)
            # The newly delivered 570x528 form image shows this button at
            # (284,88). No preparatory read of the new fixed Window is allowed.
            clicked = await invoke(
                "delivered-window-click", f"computer.window({int(current[1])}).click(at=(284,88))"
            )
            await state(fixture, lambda s: s["inline_clicks"] == 1, process=child)
            assert "Tree:" in texts(clicked) and "Tree: not requested" not in texts(clicked), texts(clicked)
            titled = await invoke(
                "set-title",
                "app.element('Meeting title').type_text('Intermediate title', replace=True); "
                "app.element('Meeting title').type_text('Latency acceptance', replace=True)",
            )
            assert len(re.findall(r"Action \[(?:dispatched|verified)\]", texts(titled))) == 2, texts(titled)
            assert texts(titled).count("Tree: diff") == 1, texts(titled)
            assert "Intermediate title" not in texts(titled), texts(titled)
            await state(fixture, lambda s: s["topic"] == "Latency acceptance", process=child)
            await invoke("date-click", "app.element('Meeting date').click()")
            await invoke("set-date", "app.element('Meeting date').type_text('2026/11/9', replace=True)")
            await state(fixture, lambda s: s["date_model"] == "2026/11/9", process=child)
            await invoke("open-hour", "app.element('08:00').click()")
            await state(fixture, lambda s: s["popup"] is not None, process=child)
            await invoke("choose-hour", "app.element('10:00').click()")
            await state(fixture, lambda s: s["hour_activations"] == ["10:00"], process=child)
            await invoke("reopen-hour", "app.element('10:00').click()")
            await state(fixture, lambda s: s["popup"] is not None, process=child)
            await invoke("choose-hour-again", "app.element('10:15').click()")
            await state(fixture, lambda s: s["hour_activations"] == ["10:00", "10:15"], process=child)
            report["before_offscreen"] = await state(fixture, process=child)
            assert report["before_offscreen"]["scroll_value"] == 0
            await invoke("offscreen-cloud", "app.element('Cloud recording').click()")
            report["final"] = await state(fixture, lambda s: s["cloud"] and s["cloud_events"] == [True], process=child)
            assert report["final"]["guard"] == "unchanged" and not report["final"]["submissions"]
            report["functional_passed"] = True
            report["latency_passed"] = all(e["latency_passed"] for e in report["operations"])
            report["passed"] = report["functional_passed"]
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        await session.close()
        if child is not None and child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 3)
            except TimeoutError:
                child.kill()
                await child.wait()
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
