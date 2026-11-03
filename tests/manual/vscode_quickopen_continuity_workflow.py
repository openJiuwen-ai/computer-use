# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Quick Open round-trip in one owned VS Code window while another app is used.

Creates two disposable text files, opens only those in a new Code window, and
uses three SDK writes per transition: Cmd+P, query input, Return. A verified
physical click in the owned external fixture separates query from Return. No
reopening, focus repair, global SDK input, user-document changes or downloads.
The default tests type_text(replace=True); --input-method paste separately tests
Element.paste only into an observed empty query, without clearing it first.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture
from qt_cocoa_activation_workflow import ROOT, save_observation, sha, state

DEFAULT_CODE = Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")
LOG = logging.getLogger(__name__)


def owned_window_ref(text, marker):
    matches = owned_window_refs(text, marker)
    return matches[0] if len(matches) == 1 else None


def owned_window_refs(text, marker):
    headers = list(re.finditer(r"(?m)^Window (\d+):", text))
    matches = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        if marker in text[header.end() : end]:
            matches.append(int(header[1]))
    return matches


def quickopen_ref(text):
    # This selects an input reference actually emitted by this observation;
    # list-box captions with the same accessible name are not input targets.
    labels = ("Search files by name", "Type the name of a file", "按名称搜索文件", "输入文件名")
    candidates = set()
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)\s+(.+)", line)
        if not match or "text-input" not in line:
            continue
        if any(label in line for label in labels):
            candidates.add(int(match[1]))
    # Presentation failure still delivers numbered native facts. Consume only
    # that published reference and its actual role/name fields, never infer an
    # input from the similarly named list-box or private provider identifiers.
    records = list(re.finditer(r"(?m)^(\d+) native element\s*$", text))
    for index, record in enumerate(records):
        end = records[index + 1].start() if index + 1 < len(records) else len(text)
        fields = {}
        for field in re.finditer(
            r'(?m)^\s*"(role|name|description|placeholder)":\s*("(?:[^"\\]|\\.)*")\s*$',
            text[record.end() : end],
        ):
            fields[field[1]] = json.loads(field[2])
        if fields.get("role") not in ("text_field", "search_field", "combo_box"):
            continue
        if any(label in fields.get(key, "") for label in labels for key in ("name", "description", "placeholder")):
            candidates.add(int(record[1]))
    if len(candidates) != 1:
        raise AssertionError(f"Expected one observed Quick Open input, got {candidates}")
    return next(iter(candidates))


def observed_value(text, reference):
    """Read Value on one delivered reference, never a sibling caption/tab."""
    records = list(re.finditer(r"(?m)^(\d+) native element\s*$", text))
    for index, record in enumerate(records):
        if int(record[1]) != reference:
            continue
        end = records[index + 1].start() if index + 1 < len(records) else len(text)
        match = re.search(r'(?m)^\s*"control_value\.value":\s*(.+)$', text[record.end() : end])
        if match:
            value = json.loads(match[1])
            assert isinstance(value, str), (reference, value)
            return value
        raise AssertionError(f"Native input {reference} did not publish a control value")
    # Compact rows can place multiple references on the same visual line.
    pattern = rf"(?:^|[\s|:]){reference}\s+([^\n|]*)"
    for match in re.finditer(pattern, text):
        value_field = re.search(r"(?:^|,?\s)Value: (.+)$", match[1])
        if value_field is None:
            continue
        raw = value_field[1].strip()
        if raw.startswith('"'):
            value, _ = json.JSONDecoder().raw_decode(raw)
        else:
            value = re.split(r", (?:Selection|Selected text|Secondary Actions|Placeholder|Description):", raw)[0]
        assert isinstance(value, str), (reference, value)
        return value
    raise AssertionError(f"Input {reference} did not publish a complete Value field")


async def verify(folder, code, input_method="type"):
    if sys.platform != "darwin" or not code.is_file():
        raise RuntimeError(f"Requires macOS and existing VS Code CLI: {code}; nothing is downloaded")
    marker = "cu-continuity-" + uuid4().hex[:12]
    document_folder, external_folder = folder / marker, folder / "external"
    document_folder.mkdir()
    external_folder.mkdir()
    documents = [document_folder / f"{marker}-{suffix}.txt" for suffix in ("a", "b")]
    tokens = {str(path): "CU-CONTINUITY-CONTENT-" + uuid4().hex for path in documents}
    for path in documents:
        path.write_text(tokens[str(path)] + "\n")
    driver = Path(native_driver()["command"])
    head = await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
    report = {
        "head": head.strip(),
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "code_cli": str(code),
        "documents": tokens,
        "allow_global_input": False,
        "input_method": input_method,
        "query_api": "Element.paste" if input_method == "paste" else "Element.type_text(replace=True)",
        "actions": [],
        "presentation_fallbacks": [],
        "passed": False,
    }
    source_paths = [
        path
        for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
        for path in sorted((ROOT / directory).rglob(pattern))
    ]
    source_paths += [Path(__file__), ROOT / "tests/fixtures/ForegroundContinuityProbe.swift"]
    (folder / "source-manifest.json").write_text(json.dumps({str(p.relative_to(ROOT)): sha(p) for p in source_paths}))
    computer = Computer(allow_global_input=False)
    events, external_pid, cli, window, close_button = [], None, None, None, None
    bundle = "local.computer-use.continuity." + uuid4().hex
    with (folder / "diagnostics.jsonl").open("w") as journal, (folder / "code-cli.log").open("w") as cli_log:

        def record(value):
            events.append(value)
            journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        def save(name, observation):
            save_observation(folder, name, observation)
            if observation is not None:
                fallbacks = [line for line in observation.text.splitlines() if line.startswith("Presentation fallback")]
                if fallbacks:
                    report["presentation_fallbacks"].append({"observation": name, "failures": fallbacks})

        set_diagnostic_handler(record)
        try:
            build_fixture(external_folder, bundle, "ForegroundContinuityProbe")
            external_pid = (await state(external_folder))["pid"]
            # Opening files rather than a folder avoids altering workspace trust.
            # --wait is an owned-window/file lifetime check, not a process kill.
            command = [str(code), "--new-window", "--wait", *(str(path) for path in documents)]
            report["launch"] = command
            cli = await asyncio.create_subprocess_exec(*command, stdout=cli_log, stderr=cli_log)
            deadline = time.monotonic() + 20
            discovery_started = time.monotonic()
            report["discovery_scans"] = []
            while time.monotonic() < deadline:
                # Multiple Code entries can legitimately share a bundle name.
                # Resolve public application IDs independently, then use this
                # invocation's unique filename marker and published Window ID.
                choices = await asyncio.to_thread(computer.list_apps)
                scan_index = len(report["discovery_scans"])
                scan = {
                    "elapsed_seconds": time.monotonic() - discovery_started,
                    "applications": [dict(choice) for choice in choices],
                    "reads": [],
                }
                report["discovery_scans"].append(scan)
                matches = {}
                complete = True
                for choice in choices:
                    if not choice.is_running or choice.display_name != "Code":
                        continue
                    if time.monotonic() >= deadline:
                        complete = False
                        break
                    name = f"discovery-{scan_index:02d}-app-{choice.id}"
                    app = computer.app(choice.id)
                    attempt = {"application_id": choice.id, "observation": name}
                    scan["reads"].append(attempt)
                    try:
                        observed = await asyncio.to_thread(app.read, full=True)
                    except ComputerUseError as error:
                        attempt["error"] = {"code": error.code, "effect": error.effect, "detail": error.detail}
                        raise
                    save(name, observed)
                    refs = owned_window_refs(observed.text, marker)
                    attempt["matching_window_refs"] = refs
                    for ref in refs:
                        # Published Window refs are canonical for this Computer
                        # session, including aliases across application entries.
                        matches.setdefault(ref, app)
                scan["complete"] = complete
                scan["matching_window_refs"] = list(matches)
                if not complete:
                    break
                if len(matches) > 1:
                    raise AssertionError(
                        f"Multiple distinct observed windows contain this run's marker: {list(matches)}"
                    )
                if len(matches) == 1:
                    ref, app = next(iter(matches.items()))
                    window = app.window(ref)
                    report["owned_window_ref"] = ref
                    break
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(min(0.2, remaining))
            if window is None:
                raise AssertionError(
                    "The uniquely named owned Code window was not discovered within the launch deadline; no input sent"
                )
            observed = await asyncio.to_thread(window.read, view="both", full=True)
            save("initial", observed)
            assert marker in observed.text
            initially_visible = [path for path in documents if tokens[str(path)] in observed.text]
            assert len(initially_visible) == 1, "Initial editor must expose exactly one owned document's content"
            destinations = [path for path in documents if path != initially_visible[0]] + initially_visible
            report["round_trip"] = [str(path) for path in destinations]
            close_refs = re.findall(r"(?m)^\s*(\d+) (?:close button|关闭按钮)\b", observed.text)
            if len(close_refs) == 1:
                close_button = await asyncio.to_thread(window.element, int(close_refs[0]))
            seed = {"before": await state(external_folder)}
            report["foreground_seed"] = seed
            started = time.monotonic()
            await asyncio.to_thread(
                subprocess.run, ["open", "-a", str(external_folder / "ForegroundContinuityProbe.app")], check=True
            )
            seed["after"] = await state(
                external_folder, lambda value: value["uptime"] >= started and value["foregroundPID"] == external_pid
            )

            async def foreground():
                moment = time.monotonic()
                current = await state(external_folder, lambda value: value["uptime"] >= moment)
                assert current["foregroundPID"] == external_pid, current
                return current

            async def invoke(name, operation):
                started, first = time.monotonic(), len(events)
                action = {"name": name, "before_external": await foreground()}
                report["actions"].append(action)
                try:
                    result = await asyncio.to_thread(operation)
                    action["result"] = result
                    assert result.status in ("verified", "dispatched"), result
                    save(name, result.observation)
                    action["after_external"] = await foreground()
                    return result
                finally:
                    action["elapsed_seconds"] = time.monotonic() - started
                    action["native_effects"] = [
                        event["value"]
                        for event in events[first:]
                        if event.get("kind") == "backend_diagnostic"
                        and isinstance(event.get("value"), dict)
                        and event["value"].get("kind") == "input_activity"
                        and event["value"].get("phase") == "willCall"
                    ]

            for index, path in enumerate(destinations):
                prefix = f"transition-{index}"
                await invoke(prefix + "-open", lambda: window.press_key("super+p"))
                opened = await asyncio.to_thread(window.read, view="both", full=True)
                save(prefix + "-opened", opened)
                opened_ref = quickopen_ref(opened.text)
                field = await asyncio.to_thread(window.element, opened_ref)
                if input_method == "paste":
                    before_value = observed_value(opened.text, opened_ref)
                    report[prefix + "-before-paste"] = {"reference": opened_ref, "value": before_value}
                    assert before_value == "", (
                        "Paste acceptance requires an observed empty query; no clearing input sent"
                    )
                    await invoke(prefix + "-query-paste", lambda field=field, path=path: field.paste(str(path)))
                else:
                    await invoke(
                        prefix + "-query-type", lambda field=field, path=path: field.type_text(str(path), replace=True)
                    )
                filled = await asyncio.to_thread(window.read, view="both", full=True)
                save(prefix + "-filled", filled)
                query_ref = quickopen_ref(filled.text)
                query_value = observed_value(filled.text, query_ref)
                report[prefix + "-query-value"] = {"reference": query_ref, "actual": query_value, "expected": str(path)}
                assert query_value == str(path), (
                    "Quick Open query did not receive the exact requested path; no external click or Return sent",
                    report[prefix + "-query-value"],
                )
                before = await state(external_folder)
                helper = external_folder / "ForegroundContinuityProbe.app/Contents/MacOS/ForegroundContinuityProbe"
                await asyncio.to_thread(
                    subprocess.check_output, [str(helper), "--click", str(external_pid), bundle], text=True
                )
                report[prefix + "-external-click"] = await state(
                    external_folder, lambda value, before=before: value["clicks"] == before["clicks"] + 1
                )
                await asyncio.sleep(0.3)
                retained = await asyncio.to_thread(window.read, view="both", full=True)
                save(prefix + "-after-external", retained)
                quickopen_ref(retained.text)
                await invoke(prefix + "-return", lambda field=field: field.press_key("Return"))
                deadline = time.monotonic() + 5
                current = None
                while time.monotonic() < deadline:
                    current = await asyncio.to_thread(window.read, view="both", full=True)
                    if tokens[str(path)] in current.text:
                        break
                    await asyncio.sleep(0.15)
                save(prefix + "-document", current)
                assert tokens[str(path)] in current.text, (
                    "Return did not expose the requested document's unique content"
                )
                await foreground()
            assert not [
                effect
                for action in report["actions"]
                for effect in action["native_effects"]
                if effect.get("transport") == "globalHID"
            ]
            assert all(path.read_text() == tokens[str(path)] + "\n" for path in documents), "Owned files were edited"
            report["passed"] = True
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
            raise
        finally:
            if window is None and cli is not None:
                report["unbound_window_cleanup"] = {
                    "marker": marker,
                    "reason": "No uniquely bound window; no close input sent. Inspect only this marker for later cleanup.",
                }
            try:
                if window is not None:
                    # Only the retained owned window is closed. Never quit Code
                    # or target whichever window is globally active at cleanup.
                    result = await asyncio.to_thread(
                        close_button.click if close_button is not None else lambda: window.press_key("super+shift+w")
                    )
                    report["owned_window_close"] = result
                    if cli is not None:
                        await asyncio.wait_for(cli.wait(), 10)
            except Exception as error:
                report["cleanup_error"] = {"type": type(error).__name__, "detail": str(error)}
                report["passed"] = False
                LOG.exception("Could not close the retained owned Code window")
            finally:
                await asyncio.to_thread(computer.close)
                set_diagnostic_handler(None)
                if external_pid is not None:
                    os.kill(external_pid, signal.SIGTERM)
                if cli is not None and cli.returncode is None:
                    cli.terminate()  # Only our waiting CLI child, not the Code application.
                    await cli.wait()
                (folder / "result.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
                )
    if not report["passed"]:
        raise AssertionError(report.get("cleanup_error", report.get("error")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", type=Path, default=DEFAULT_CODE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input-method", choices=["type", "paste"], default="type")
    parser.add_argument("--external-click", action="store_true")
    args = parser.parse_args()
    if not args.external_click:
        parser.error("Explicit --external-click is required for the owned fixture physical click")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = args.output or Path(tempfile.mkdtemp(prefix="vscode-continuity-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(verify(folder.resolve(), args.code.absolute(), args.input_method))
    finally:
        LOG.info("VS Code Quick Open continuity evidence: %s", folder)


if __name__ == "__main__":
    main()
