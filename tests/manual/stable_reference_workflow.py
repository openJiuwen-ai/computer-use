# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise retained refs and error observations in an owned AppKit fixture.

Fixture commands change UI state independently of SDK input. Success is judged
by the fixture's button identity and callback counter, never just an AX receipt.
No global input is allowed. --build-only opens no windows; --sdk-only omits the
MCP delivery phase. Failures are collected so one red case cannot hide others.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import plistlib
import re
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from computer_use_mcp.runtime import Runtime

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
NAME = "StableReferenceProbe"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprints():
    return {
        str(path.relative_to(ROOT)): digest(path)
        for directory, suffix in (("computer_use_engine", ".py"), ("interface_adapter/mcp", ".py"))
        for path in (ROOT / directory).rglob("*" + suffix)
    }


def native_fingerprints():
    return {str(path.relative_to(ROOT)): digest(path) for path in (ROOT / "os_adapter/macos/Sources").rglob("*.swift")}


def build(folder, bundle):
    app = folder / (NAME + ".app")
    binary = app / "Contents/MacOS" / NAME
    binary.parent.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": bundle,
                "CFBundleExecutable": NAME,
                "CFBundleName": NAME,
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
            }
        )
    )
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "6",
            "-warnings-as-errors",
            "-parse-as-library",
            str(ROOT / "tests/fixtures/StableReferenceProbe.swift"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    probe = folder / "stable-reference-ax-probe"
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "6",
            "-warnings-as-errors",
            "-parse-as-library",
            str(ROOT / "tests/fixtures/StableReferenceAXProbe.swift"),
            "-o",
            str(probe),
        ],
        check=True,
    )
    return app, probe


class AXProbe:
    def __init__(self, process):
        self.process = process

    async def sample(self, *, capture=False):
        self.process.stdin.write(b"capture\n" if capture else b"sample\n")
        await self.process.stdin.drain()
        raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=10)
        if not raw:
            raise RuntimeError("Read-only AX helper exited")
        return json.loads(raw)

    async def close(self):
        self.process.stdin.close()
        await self.process.wait()


async def state(folder):
    for _ in range(100):
        try:
            return json.loads((folder / "state.json").read_text())
        except FileNotFoundError:
            await asyncio.sleep(0.05)
    raise TimeoutError("Owned fixture did not publish state")


async def command(folder, action):
    sequence = (await state(folder))["sequence"] + 1
    (folder / "command.json").write_text(json.dumps({"sequence": sequence, "action": action}))
    for _ in range(100):
        current = await state(folder)
        if current["sequence"] == sequence:
            assert current["commandError"] is None, current
            return current
        await asyncio.sleep(0.05)
    raise TimeoutError("Fixture command was not consumed")


async def observe_result(operation):
    started = time.monotonic()
    try:
        value = await asyncio.to_thread(operation)
        return {"receipt": dict(value), "elapsed": time.monotonic() - started}
    except ComputerUseError as error:
        result = {
            key: getattr(error, key, None)
            for key in (
                "code",
                "effect",
                "target",
                "detail",
                "next",
                "observation_status",
                "window_lifecycle",
                "choices",
            )
        }
        observation = getattr(error, "observation", None)
        result["observation_text"] = observation.text if observation is not None else None
        result["observation_error"] = getattr(error, "observation_error", None)
        return {"error": result, "elapsed": time.monotonic() - started}


def target_ref(text):
    # AppKit can expose an ignored NSButton view as an AXUnknown wrapper when
    # its AX parent is customized. The actionable AXButton is the actual cell.
    rows = [line for line in text.splitlines() if re.match(r"\s*\d+\s+button\b", line) and "Retained button" in line]
    candidates = [re.match(r"\s*(\d+)\s", line) for line in rows]
    ids = [int(match[1]) for match in candidates if match]
    assert len(ids) == 1, rows
    return ids[0]


async def verify(folder, fixture, helper, bundle, options):
    driver = Path(options.driver or native_driver()["command"]).resolve()
    report = {
        "passed": False,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": digest(driver),
        "python_sources_before": fingerprints(),
        "native_sources_before": native_fingerprints(),
        "fixture_sha256": digest(ROOT / "tests/fixtures/StableReferenceProbe.swift"),
        "ax_probe_sha256": digest(ROOT / "tests/fixtures/StableReferenceAXProbe.swift"),
        "workflow_sha256": digest(__file__),
        "cases": [],
        "limitations": ["MCP Runtime delivery exercised; installed Codex host not exercised"],
    }
    journal = (folder / "diagnostics.jsonl").open("w")
    computer, runtime, initial, probe = None, None, None, None

    def diagnostic(event):
        journal.write(json.dumps(event, ensure_ascii=False, default=encode_artifact) + "\n")
        journal.flush()

    def new_computer():
        value = Computer(allow_global_input=False)
        value._controller.transport.driver = {"command": str(driver), "args": []}
        return value

    async def sdk_case(name, mutation, *, reread=False, should_survive=True, use_ref=False):
        nonlocal computer
        computer = new_computer()
        case = {"name": name, "mutation": mutation, "explicit_read_after_mutation": reread, "passed": False}
        report["cases"].append(case)
        try:
            app = computer.app(bundle)
            first = await asyncio.to_thread(app.read, view="tree", full=True)
            (folder / (name + "-before.txt")).write_text(first.text)
            ref = target_ref(first.text)
            held = await asyncio.to_thread(
                app.element, ref if use_ref else [{"role": "button", "name": "Retained button"}]
            )
            case["ref"] = ref
            # Diagnostic provenance only: public input below still uses the
            # original Element or integer ref, never a handcrafted native token.
            case["native_binding_node"] = held._snapshot["binding"].node
            case["ax_before"] = await probe.sample(capture=True)
            before = await state(folder)
            changed = await command(folder, mutation)
            case["ax_changed"] = await probe.sample()
            case.update(before=before, changed=changed)
            if reread:
                observation = await asyncio.to_thread(app.read, view="tree")
                (folder / (name + "-intermediate.txt")).write_text(observation.text)
                case["ax_after_sdk_read"] = await probe.sample()
            outcome = await observe_result(lambda: (app.element(ref) if use_ref else held).click())
            after = await state(folder)
            case.update(outcome=outcome, after=after)
            if should_survive:
                case["passed"] = (
                    "receipt" in outcome
                    and after["clicks"] == before["clicks"] + 1
                    and changed["buttonID"] == before["buttonID"]
                    and after["callbackGenerations"][-1] == before["generation"]
                    and case["ax_changed"]["sameButton"] is True
                    and case["ax_changed"]["originalButton"]["AXRole"]["status"] == 0
                )
            else:
                error = outcome.get("error", {})
                case["passed"] = (
                    error.get("effect") == "none"
                    and after["clicks"] == before["clicks"]
                    and changed["marker"] in (error.get("observation_text") or "")
                )
                if mutation == "physical-reattach":
                    case["passed"] &= case["ax_changed"]["originalButton"]["AXRole"]["status"] == -25202
        except (AssertionError, ComputerUseError, RuntimeError, ValueError) as error:
            case["exception"] = repr(error)
        finally:
            await asyncio.to_thread(computer.close)
            computer = None

    try:
        assert Path(computer_use.__file__).resolve().is_relative_to(ROOT), computer_use.__file__
        await asyncio.to_thread(subprocess.run, ["open", "-g", str(fixture)], check=True)
        initial = await state(folder)
        report["initial"] = initial
        probe = AXProbe(
            await asyncio.create_subprocess_exec(
                str(helper),
                str(initial["pid"]),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
        )
        set_diagnostic_handler(diagnostic)
        await sdk_case("unrelated-sibling", "sibling", use_ref=True)
        await sdk_case("layout-metadata-no-read", "metadata")
        await sdk_case("layout-replaced-no-read", "rewrap", use_ref=True)
        await sdk_case("layout-replaced-after-read", "rewrap", reread=True)
        await sdk_case("physical-reattachment-invalidates-ax", "physical-reattach", should_survive=False)
        await sdk_case("element-removed", "replace-button", should_survive=False)
        await sdk_case("window-reopened", "reopen-window", should_survive=False)
        await sdk_case("numeric-ref-window-reopened", "reopen-window", should_survive=False, use_ref=True)

        if not options.sdk_only:
            runtime = Runtime(diagnostic, computer_factory=new_computer)
            execution = 0

            async def mcp(name, code):
                nonlocal execution
                execution += 1
                result = await runtime.execute(
                    {
                        "code": code,
                        "executionId": f"stable-{execution}",
                        "history": {"resetText": False, "resetImages": False, "incremental": False},
                    }
                )
                text = "\n".join(item["text"] for item in result["content"] if item["type"] == "text")
                (folder / (name + ".txt")).write_text(text)
                return result, text

            prepared, _ = await mcp(
                "mcp-initial",
                f"app = computer.app({bundle!r})\nfirst = app.read(view='tree')",
            )
            assert not prepared.get("isError"), prepared
            # Name lookup consumes an observation actually delivered in the
            # preceding tool result, not a pending read in this same script.
            prepared, _ = await mcp("mcp-select", "held = app.element([{'role': 'button', 'name': 'Retained button'}])")
            assert not prepared.get("isError"), prepared
            before = await state(folder)
            ax_before = await probe.sample(capture=True)
            changed = await command(folder, "metadata")
            ax_changed = await probe.sample()
            result, text = await mcp("mcp-retained", "held.click()")
            after = await state(folder)
            report["cases"].append(
                {
                    "name": "mcp-retained",
                    "before": before,
                    "changed": changed,
                    "after": after,
                    "tool_error": result.get("isError", False),
                    "text": text,
                    "ax_before": ax_before,
                    "ax_changed": ax_changed,
                    "passed": not result.get("isError")
                    and after["clicks"] == before["clicks"] + 1
                    and ax_changed["sameButton"] is True
                    and ax_changed["originalButton"]["AXRole"]["status"] == 0,
                }
            )
            # Refresh selection before a genuine target deletion; the failed
            # operation itself must provide the replacement observation.
            prepared, _ = await mcp(
                "mcp-before-removal",
                "app.read(view='tree')\nheld = app.element([{'role': 'button', 'name': 'Retained button'}])",
            )
            assert not prepared.get("isError"), prepared
            before = await state(folder)
            changed = await command(folder, "replace-button")
            result, text = await mcp("mcp-removed", "held.click()")
            after = await state(folder)
            report["cases"].append(
                {
                    "name": "mcp-removed",
                    "before": before,
                    "changed": changed,
                    "after": after,
                    "tool_error": result.get("isError", False),
                    "text": text,
                    "passed": bool(result.get("isError"))
                    and changed["marker"] in text
                    and after["clicks"] == before["clicks"],
                }
            )
        report["passed"] = all(case["passed"] for case in report["cases"])
    finally:
        report["python_sources_after"] = fingerprints()
        report["python_source_unchanged"] = report["python_sources_before"] == report["python_sources_after"]
        report["native_sources_after"] = native_fingerprints()
        report["native_source_unchanged"] = report["native_sources_before"] == report["native_sources_after"]
        report["driver_unchanged"] = report["driver_sha256"] == digest(driver)
        if (
            not report["python_source_unchanged"]
            or not report["native_source_unchanged"]
            or not report["driver_unchanged"]
        ):
            report["passed"] = False
            report["validation_invalidated"] = "Executed source or driver changed during the run"
        if initial is not None:
            report["final"] = await state(folder)
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
        if computer is not None:
            await asyncio.to_thread(computer.close)
        if runtime is not None:
            await runtime.close()
        if probe is not None:
            await probe.close()
        set_diagnostic_handler(None)
        journal.close()
        if initial is not None:
            try:
                os.kill(initial["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
    for case in report["cases"]:
        LOG.info("%s: %s", case["name"], "PASS" if case["passed"] else "FAIL")
    assert report["passed"], "See result.json for independent callback and delivery evidence"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--sdk-only", action="store_true")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="stable-reference-", dir=ROOT / ".artifacts"))
    bundle = "local.computer-use.stable-reference." + uuid.uuid4().hex
    try:
        app, helper = build(folder, bundle)
        if not options.build_only:
            asyncio.run(verify(folder, app, helper, bundle, options))
    finally:
        LOG.info("Stable reference evidence: %s", folder)


if __name__ == "__main__":
    main()
