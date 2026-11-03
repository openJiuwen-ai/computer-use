# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use one default SDK tree read, then inspect the owned WK provider natively.

The normal SDK read initializes AX visibility. No write, focus, foreground
observation or input is requested. The unchanged read-only helper receives only
the owned child PID and this run's unique window title.
"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from keyboard_window_command_workflow import build_owned
from qt_cocoa_activation_workflow import ROOT, sha, state
from web_text_workflow import NAME, assert_original_document

LOG = logging.getLogger(__name__)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")


async def terminate(process):
    if process is not None and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except TimeoutError:
            process.kill()
            await process.wait()


async def verify(folder, app_path, probe):
    title = "CU Web Provider " + uuid4().hex[:12]
    binary = app_path / "Contents/MacOS" / NAME
    paths = (
        "tests/fixtures/WKWebTextProbe.swift",
        "tests/fixtures/WebTextProviderProbe.swift",
        "tests/manual/web_text_provider_diagnostic.py",
        "tests/manual/web_text_workflow.py",
        "tests/manual/keyboard_window_command_workflow.py",
        "tests/manual/qt_cocoa_activation_workflow.py",
        "os_adapter/macos/Sources/Windowing/NativeProcessIdentity.swift",
    )
    manifest = {name: sha(ROOT / name) for name in paths}
    manifest.update(
        {str(path.relative_to(ROOT)): sha(path) for path in sorted((ROOT / "computer_use_engine").rglob("*.py"))}
    )
    write_json(folder / "source-manifest.json", manifest)
    driver = Path(native_driver()["command"])
    report = {
        "diagnostic_only": True,
        "read_only_provider_inspection": True,
        "allow_global_input": False,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "probe": str(probe),
        "probe_sha256": sha(probe),
        "fixture": str(binary),
        "fixture_sha256": sha(binary),
        "window_title": title,
        "passed": False,
    }
    for name in ("source-manifest.json", "build-command.json", "build-result.json"):
        source = probe.parent / name
        if source.exists():
            report["probe_" + name.removesuffix(".json").replace("-", "_")] = json.loads(source.read_text())
    child = helper = None
    computer, events = Computer(allow_global_input=False), []
    with (
        (folder / "fixture-stderr.log").open("w") as errors,
        (folder / "probe.log").open("w") as probe_log,
        (folder / "diagnostics.jsonl").open("w") as journal,
    ):

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        def activities():
            return [
                event["value"]
                for event in events
                if event.get("kind") == "backend_diagnostic"
                and isinstance(event.get("value"), dict)
                and event["value"].get("kind") == "input_activity"
            ]

        set_diagnostic_handler(record)
        try:
            child = await asyncio.create_subprocess_exec(
                str(binary),
                "--output",
                str(folder),
                "--title",
                title,
                cwd=ROOT,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=errors,
                stderr=errors,
            )
            initial = await state(
                folder,
                lambda value: value["visible"] and value["page"].get("ready"),
                process=child,
                seconds=15,
            )
            report["before"] = initial
            assert initial["pid"] == child.pid and initial["title"] == title
            assert initial["bundleID"].startswith("local.computer-use.web-text.")
            assert_original_document(initial, initial)
            observation = await asyncio.to_thread(computer.app(initial["bundleID"]).read, view="tree")
            (folder / "sdk-read.txt").write_text(observation.text)
            report["sdk_read"] = {
                "view": "tree",
                "allow_foreground": False,
                "calls": 1,
                "preparation": observation.preparation,
                "title_observed": title in observation.text,
                "field_observed": "Web callback input" in observation.text,
            }
            assert title in observation.text and "Web callback input" in observation.text, observation.text
            report["after_sdk_read"] = await state(folder, process=child)
            assert_original_document(report["after_sdk_read"], initial)
            for name in ("active", "windowKey", "frontmostPID", "page"):
                assert report["after_sdk_read"][name] == initial[name], name
            assert not activities(), "Default tree read unexpectedly entered an input activity"
            command = [str(probe), "--pid", str(child.pid), "--title", title, "--output", str(folder / "provider.json")]
            report["probe_command"] = command
            helper = await asyncio.create_subprocess_exec(
                *command,
                cwd=ROOT,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=probe_log,
                stderr=probe_log,
            )
            report["probe_exit_code"] = await asyncio.wait_for(helper.wait(), 30)
            if (folder / "provider.json").exists():
                report["provider"] = json.loads((folder / "provider.json").read_text())
            assert report["probe_exit_code"] == 0, "Read-only helper did not complete; inspect provider.json/probe.log"
            assert report.get("provider", {}).get("passed") is True, "The helper did not finish the scoped observation"
            # The fixture publishes a passive DOM snapshot every 50 ms.
            await asyncio.sleep(0.15)
            current = await state(folder, process=child)
            report["after"] = current
            assert_original_document(current, initial)
            for name in ("pid", "title", "bundleID", "documentID", "active", "windowKey", "frontmostPID", "page"):
                assert current[name] == initial[name], (name, initial[name], current[name])
            report["passed"] = True
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
            raise
        finally:
            try:
                if (folder / "provider.json").exists():
                    report["provider"] = json.loads((folder / "provider.json").read_text())
                if (folder / "state.json").exists():
                    report["final"] = json.loads((folder / "state.json").read_text())
            except (OSError, ValueError) as error:
                report["final_evidence_error"] = str(error)
            finally:
                try:
                    await asyncio.to_thread(computer.close)
                finally:
                    set_diagnostic_handler(None)
                    report["native_activity"] = activities()
                    report["zero_input_activity"] = not report["native_activity"]
                    try:
                        await terminate(helper)
                    finally:
                        await terminate(child)
                        report["probe_terminated"] = helper is None or helper.returncode is not None
                        report["fixture_terminated"] = child is None or child.returncode is not None
                        report["source_drift"] = [
                            name for name, digest in manifest.items() if sha(ROOT / name) != digest
                        ]
                        if report["source_drift"] or not report["zero_input_activity"]:
                            report["passed"] = False
                        write_json(folder / "result.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This fixture requires macOS WebKit")
    if not args.build_only and (args.probe is None or not args.probe.is_file()):
        parser.error("--probe must name the independently compiled read-only helper")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = (
        args.output or Path(tempfile.mkdtemp(prefix="web-provider-diagnostic-", dir=ROOT / ".artifacts"))
    ).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        app = build_owned(folder, "local.computer-use.web-text." + uuid4().hex, NAME)
        if args.build_only:
            LOG.info("Built without launching: %s", app)
        else:
            asyncio.run(verify(folder, app, args.probe.resolve()))
    finally:
        LOG.info("Read-only Web provider evidence: %s", folder)


if __name__ == "__main__":
    main()
