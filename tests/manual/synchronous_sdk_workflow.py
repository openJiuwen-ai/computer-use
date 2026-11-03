"""Verify ordinary synchronous Python against independent native callbacks."""

import argparse
import hashlib
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from computer_use import Computer
from d34_window_workflow import ROOT, actual_modules, build_fixture, released, source_fingerprint, window_with_content


def wait_state(folder, predicate):
    deadline = time.monotonic() + 8
    latest = None
    while time.monotonic() < deadline:
        try:
            latest = json.loads((folder / "state.json").read_text())
            if predicate(latest):
                return latest
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.025)
    raise AssertionError(latest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    options = parser.parse_args()
    driver = options.driver.resolve()
    folder = Path(tempfile.mkdtemp(prefix="sync-sdk-", dir=ROOT / ".artifacts"))
    report = {
        "source_before": source_fingerprint(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "passed": False,
    }
    fixture_pid = None
    try:
        bundle = "local.computer-use.sync." + uuid.uuid4().hex
        fixture = build_fixture(folder, bundle)
        subprocess.run(["open", str(fixture)], check=True)
        initial = wait_state(folder, lambda value: "A" in value["documents"])
        fixture_pid = initial["pid"]
        command = {"sequence": initial["sequence"] + 1, "action": "focus", "name": "A"}
        (folder / "command.json").write_text(json.dumps(command))
        wait_state(
            folder, lambda value: value["sequence"] == command["sequence"] and value["frontmostPID"] == fixture_pid
        )

        # This is an ordinary synchronous SDK script: no coroutine scheduler,
        # await, thread helper or Runtime is involved in the caller.
        with Computer() as computer:
            computer._controller.transport.driver = {"command": str(driver), "args": []}
            app = computer.app(bundle)
            observation = app.read(view="both")
            (folder / "initial.txt").write_text(observation.text)
            window = computer.window(window_with_content(observation.text, "Document A"))
            field = window.element("A input")
            field.type_text("Synchronous SDK", replace=True)
            field.press_key("Return")
            window.element("Count").click()
            final = wait_state(
                folder,
                lambda value: (
                    value["documents"]["A"]["field"] == "Synchronous SDK"
                    and value["documents"]["A"]["submits"] == 1
                    and value["documents"]["A"]["count"] == 1
                    and released(value)
                ),
            )
            assert final["documents"]["B"]["field"] == "" and final["documents"]["B"]["count"] == 0, final
            assert all(doc["guard"] == "Guard unchanged" for doc in final["documents"].values()), final
            report["final"] = final
        report["executor_closed"] = computer._executor._closed and not computer._executor._thread.is_alive()
        assert report["executor_closed"]
        report["passed"] = True
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        if fixture_pid is not None:
            os.kill(fixture_pid, signal.SIGTERM)
        report["source_after"] = source_fingerprint()
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["driver_unchanged"] = report["driver_sha256"] == hashlib.sha256(driver.read_bytes()).hexdigest()
        report["actual_modules"] = actual_modules()
        assert all(Path(path).is_relative_to(ROOT) for path in report["actual_modules"].values())
        report["passed"] &= report["source_unchanged"] and report["driver_unchanged"]
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        logging.getLogger(__name__).info("Synchronous SDK evidence: %s", folder)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
