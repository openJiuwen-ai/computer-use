# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise SDK paste against a real, delayed clipboard consumer.

Run in an unlocked test desktop with permission to replace the clipboard.
The fixture records the actual WM_PASTE count and textbox content out of band.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver

FIXTURE = Path(__file__).with_name("windows_paste_fixture.cs")


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def wait_state(path: Path, condition, timeout: float) -> dict:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        state = read_state(path)
        if condition(state):
            return state
        time.sleep(0.05)
    return read_state(path)


@contextmanager
def paste_fixture(
    delay_ms: int, nonce: str, anonymous: bool, profile: str = "default", *, require_natural_focus: bool = False,
):
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    with tempfile.TemporaryDirectory(prefix="cua-paste-") as tmp:
        exe, state_path = (Path(tmp) / name for name in ("CuaPasteFixture.exe", "state.json"))
        subprocess.run(
            [
                str(framework / "csc.exe"), "/nologo", "/target:winexe", "/platform:x64",
                "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                "/reference:System.Web.Extensions.dll", f"/out:{exe}", str(FIXTURE),
            ],
            check=True,
        )
        fixture = subprocess.Popen([str(exe), str(state_path), str(delay_ms), nonce, str(anonymous), profile])
        try:
            state = wait_state(state_path, lambda s: s.get("focused"), 6 if require_natural_focus else 2)
            if state and not state.get("focused") and not require_natural_focus:
                # Initial focus is fixture setup, outside the input test session.
                with Computer() as setup:
                    app = setup.app("CuaPasteFixture")
                    app.read(view="tree")
                    app.element("Paste target").focus()
                state = wait_state(state_path, lambda s: s.get("focused"), 4)
            if not state.get("focused"):
                raise RuntimeError(f"Fixture could not establish initial focus: {state}")
            if require_natural_focus:
                state["fixture_pid"] = fixture.pid
            yield state_path, state
        finally:
            if fixture.poll() is None:
                fixture.terminate()
            fixture.wait(timeout=5)


async def mcp_paste_series(payloads: list[str], delay_ms: int, reset: bool) -> dict:
    from computer_use_agent.config import AgentConfig
    from computer_use_agent.connection import McpConnection

    connection = McpConnection(AgentConfig().mcp_config())

    async def call(title, code):
        result = await connection.call("computer_use", {"title": title, "code": code})
        output = "\n".join(getattr(content, "text", "") for content in result.content)
        if result.isError:
            raise RuntimeError(output)
        return output

    dispatched = []
    try:
        for index, payload in enumerate(payloads):
            receipt = await call("Send paste", f"r = computer.paste({payload!r}); print(r.status, r.observation_status)")
            if receipt.strip().splitlines()[-1:] != ["dispatched not_requested"]:
                return {"error": receipt, "dispatched": dispatched}
            dispatched.append(receipt.strip())
            if reset and index == 0:
                response = await connection.call("computer_use_reset", {})
                if response.isError:
                    return {"error": "MCP reset failed", "dispatched": dispatched}
            marker = payload.split(" ", 2)[1]
            until = time.monotonic() + delay_ms / 1000 + 6
            while time.monotonic() < until:
                observed = await call("Check paste effect", 'print(computer.app("CuaPasteFixture").read(full=True))')
                if marker in observed:
                    break
                await asyncio.sleep(0.1)
            else:
                return {"error": "No delivered observation confirmed the paste", "dispatched": dispatched}
        return {"status": "dispatched", "dispatched": dispatched}
    finally:
        await connection.close()


def sdk_paste_series(payloads: list[str], delay_ms: int, raw: bool, state: Path) -> dict:
    attempts = []
    expected = ""
    with Computer() as computer:
        receiver = computer
        try:
            if not raw:
                app = computer.app("CuaPasteFixture")
                app.read(view="tree")
                receiver = app.element("Paste target")
        except ComputerUseError as error:
            return {"error": str(error), "code": error.code, "effect": error.effect}
        for index, payload in enumerate(payloads, 1):
            started = time.monotonic()
            try:
                receipt = receiver.paste(payload)
            except ComputerUseError as error:
                return {"error": str(error), "code": error.code, "effect": error.effect, "attempts": attempts}
            facts = {key: getattr(receipt, key) for key in ("status", "detail", "observation_status")}
            if len(payloads) == 1:
                return facts
            expected += payload
            actual = wait_state(state, lambda value, index=index: value.get("consumed", 0) >= index, delay_ms / 1000 + 4)
            attempts.append({"index": index, "receipt": facts, "actual": actual,
                             "seconds": round(time.monotonic() - started, 3)})
            if (facts["status"] not in {"dispatched", "verified"} or actual.get("text") != expected
                or actual.get("paste_count") != index or actual.get("consumed") != index
                or actual.get("bystander") != "untouched"):
                return {"error": "Independent fixture did not confirm exactly one expected paste", "attempts": attempts}
    return {"status": "verified", "attempts": attempts}


def run(delay_ms: int, raw: bool, anonymous: bool, mcp: bool, repeat: int, reset: bool) -> dict:
    driver = Path(native_driver()["command"])
    nonce = uuid.uuid4().hex[:10]
    payloads = [f"CUA {nonce}-{index} 中文 😀\r\nsecond\tline" for index in range(repeat)]
    with paste_fixture(delay_ms, nonce, anonymous) as (state_path, _):
        started = time.monotonic()
        if mcp:
            call = asyncio.run(mcp_paste_series(payloads, delay_ms, reset))
        else:
            call = sdk_paste_series(payloads, delay_ms, raw, state_path)
        elapsed = time.monotonic() - started
        observed = wait_state(state_path, lambda s: s.get("consumed", 0) >= repeat, delay_ms / 1000 + 4)
        expected = "".join(payloads)
        return {
            "module": computer_use.__file__,
            "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "delay_ms": delay_ms,
            "raw": raw,
            "anonymous_consumer": anonymous,
            "mcp": mcp,
            "reset_after_first_dispatch": reset,
            "elapsed_seconds": round(elapsed, 3),
            "call": call,
            "actual": observed,
            "expected": expected,
            "pass": (
                call.get("status") in {"dispatched", "verified"}
                and observed.get("text") == expected
                and observed.get("paste_count") == observed.get("consumed") == repeat
                and observed.get("bystander") == "untouched"
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=int, default=3200)
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--anonymous-consumer", action="store_true")
    parser.add_argument("--mcp", action="store_true", help="Use a fresh MCP process for raw paste")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat after confirming the preceding effect")
    parser.add_argument("--reset-after-first", action="store_true", help="Reset after the first MCP dispatch")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.delay_ms < 0 or args.repeat < 1 or (args.reset_after_first and not args.mcp):
        parser.error("nonnegative delay and positive repeat required; reset requires --mcp")
    report = run(args.delay_ms, args.raw or args.mcp, args.anonymous_consumer, args.mcp, args.repeat, args.reset_after_first)
    serialized = json.dumps(report, ensure_ascii=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    sys.stdout.write(serialized + "\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
