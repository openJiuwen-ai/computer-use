# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Measure complete MCP calls and confirm input through a disposable AppKit app.

Run with the target repository's interpreter. The model is not invoked.
Only the owned fixture receives input.
"""

import argparse
import asyncio
import hashlib
import json
import os
import plistlib
import re
import signal
import statistics
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/OperationLatencyProbe.swift"


def build_fixture(output):
    bundle = output / "OperationLatencyProbe.app"
    executable = bundle / "Contents/MacOS/probe"
    executable.parent.mkdir(parents=True)
    subprocess.run(
        ["xcrun", "swiftc", "-swift-version", "6", "-parse-as-library", str(FIXTURE), "-o", str(executable)],
        check=True,
        capture_output=True,
    )
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "local.computer-use.operation-latency." + uuid4().hex,
                "CFBundleExecutable": "probe",
                "CFBundleName": "OperationLatencyProbe",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True, capture_output=True)
    subprocess.run(["open", "-g", str(bundle)], check=True, capture_output=True)
    return executable


async def state_at(output, predicate):
    for _ in range(100):
        try:
            state = json.loads((output / "state.json").read_text())
            if predicate(state):
                return state
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError("Fixture did not confirm the expected state")


async def measure(args):
    import computer_use

    output = args.output.resolve()
    output.mkdir(parents=True)
    executable = await asyncio.to_thread(build_fixture, output)
    initial = await state_at(output, lambda state: len(state["documents"]) == 3)
    names = {"list": "list_apps", "text": "type_text", "key": "press_key"}
    report = {"sdk": computer_use.__file__, "python": sys.executable, "samples": [], "passed": False}
    driver = args.repository / ("os_adapter") / "macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"
    report["driver"] = str(driver)
    report["driver_sha256"] = hashlib.sha256(driver.read_bytes()).hexdigest()

    def save():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    async def invoke(session, category, code):
        start = perf_counter()
        reply = await session.call_tool("computer_use", arguments={"title": category, "code": code})
        elapsed = (perf_counter() - start) * 1000
        raw = reply.model_dump(mode="json", by_alias=True, exclude_none=True)
        metadata = raw.get("_meta", {}).get("computer-use", {})
        body = "\n".join(item["text"] for item in raw["content"] if item["type"] == "text")
        report["samples"].append(
            {
                "category": category,
                "duration_ms": elapsed,
                "execution_ms": metadata.get("durationMs"),
                "log": metadata.get("logPath"),
                "execution_id": metadata.get("executionId"),
                "error": bool(reply.isError),
                "text_characters": len(body),
                "images": sum(item["type"] == "image" for item in raw["content"]),
            }
        )
        save()
        print(category, f"{elapsed:.1f} ms", flush=True)
        if reply.isError:
            raise AssertionError(body[:2000])
        return body

    environment = {**os.environ, "COMPUTER_USE_DESKTOP": args.desktop, "COMPUTER_USE_LOG_DIR": str(output / "mcp-logs")}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "computer_use_mcp"], cwd=str(args.repository), env=environment
    )
    try:
        with (output / "stderr.log").open("w") as errors:
            async with stdio_client(params, errlog=errors) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    try:
                        await invoke(session, "startup", f"print(await computer.{names['list']}())")
                        for _ in range(args.iterations):
                            await invoke(session, "list_apps", f"print(await computer.{names['list']}())")
                        choices = await invoke(
                            session,
                            "fixture_discovery",
                            'base = computer.app("OperationLatencyProbe")\nawait base.read()',
                        )
                        selected = re.search(r"(?m)^(\d+) .* — Discovery document A['\"]; computer.app", choices)
                        assert selected, choices
                        await invoke(
                            session,
                            "fixture_bind",
                            f"probe = computer.app({selected[1]})\nawait probe.read()\nfield = await probe.element('Document input')\nbutton = await probe.element('Mark document')",
                        )
                        for index in range(args.iterations):
                            await invoke(session, "fixture_tree", "await probe.read()")
                            await invoke(session, "fixture_both", "await probe.read(view='both')")
                            await invoke(session, "element_click", "await button.click()")
                            await state_at(output, lambda state: state["documents"]["A"]["clicks"] == index + 1)
                            value = f"latency {index} 测试"
                            await invoke(
                                session, "element_replace", f"await field.{names['text']}({value!r}, replace=True)"
                            )
                            await state_at(output, lambda state: state["documents"]["A"]["value"] == value)
                            await invoke(session, "element_key", f"await field.{names['key']}('End')")
                            await invoke(session, "element_insert", f"await field.{names['text']}('x')")
                            await state_at(output, lambda state: state["documents"]["A"]["value"] == value + "x")
                            assert (await state_at(output, lambda _: True))["documents"]["B"]["value"] == "original B"
                        if args.system_settings:
                            choices = await invoke(
                                session,
                                "settings_discovery",
                                'settings_base = computer.app("系统设置")\nawait settings_base.read()',
                            )
                            candidates = re.findall(r"(?m)^(\d+) .*; computer.app\(\d+\)$", choices)
                            code = (
                                f"settings = computer.app({candidates[0]})"
                                if candidates
                                else "settings = settings_base"
                            )
                            await invoke(session, "settings_bind", code)
                            for _ in range(args.iterations):
                                await invoke(session, "settings_tree", "await settings.read()")
                                await invoke(session, "settings_both", "await settings.read(view='both')")
                        report["passed"] = True
                    finally:
                        reset = await session.call_tool("computer_use_reset", arguments={})
                        report["reset_ok"] = not reset.isError
    finally:
        report["state"] = json.loads((output / "state.json").read_text())
        summaries = {}
        for category in dict.fromkeys(sample["category"] for sample in report["samples"]):
            samples = sorted(sample["duration_ms"] for sample in report["samples"] if sample["category"] == category)
            summaries[category] = {
                "count": len(samples),
                "median_ms": statistics.median(samples),
                "p95_ms": samples[(95 * len(samples) + 99) // 100 - 1],
                "under_1s": sum(value < 1000 for value in samples),
            }
        report["summary"] = summaries
        command = await asyncio.to_thread(
            subprocess.run,
            ["ps", "-p", str(initial["pid"]), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
        if command.stdout.strip() == str(executable):
            os.kill(initial["pid"], signal.SIGTERM)
            report["fixture_terminated"] = True
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--system-settings", action="store_true")
    parser.add_argument("--desktop", choices=("exclusive",), default="exclusive")
    asyncio.run(measure(parser.parse_args()))
