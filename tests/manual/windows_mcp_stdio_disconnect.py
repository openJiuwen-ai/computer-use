# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cut an actual Windows MCP stdio transport during a no-UI tool execution."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import computer_use
import computer_use_mcp
from computer_use_mcp.windows_worker import WindowsJob, resume_suspended
from mcp.types import LATEST_PROTOCOL_VERSION
from windows_mcp_recovery_workflow import process_handles, records, wait_for_retirement


class StdioHost:
    def __init__(self, process, job):
        self.process, self.job = process, job
        self.frames = []
        self.reading = asyncio.create_task(self.read_output())

    async def read_output(self):
        while line := await self.process.stdout.readline():
            self.frames.append(json.loads(line))

    async def send(self, method, params=None, *, request_id=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def wait_frame(self, predicate):
        async with asyncio.timeout(12):
            while True:
                frame = next((frame for frame in self.frames if predicate(frame)), None)
                if frame is not None:
                    return frame
                if self.reading.done():
                    self.reading.result()
                    raise RuntimeError("MCP stdout ended before the expected frame")
                await asyncio.sleep(0.025)

    async def initialize(self):
        await self.send(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "owned-stdio-disconnect-probe", "version": "1"},
            },
            request_id=1,
        )
        frame = await self.wait_frame(lambda item: item.get("id") == 1)
        if frame.get("result", {}).get("serverInfo", {}).get("name") != "computer-use":
            raise RuntimeError("Could not initialize the actual computer-use MCP host")
        await self.send("notifications/initialized")

    async def tool(self, code, request_id, *, progress=None):
        params = {"name": "computer_use", "arguments": {"title": "Owned no-UI disconnect probe", "code": code}}
        if progress is not None:
            params["_meta"] = {"progressToken": progress}
        await self.send("tools/call", params, request_id=request_id)

    async def completed_tool(self, code, request_id):
        await self.tool(code, request_id)
        frame = await self.wait_frame(lambda item: item.get("id") == request_id)
        result = frame.get("result", {})
        if result.get("isError", True) or any(block["type"] != "text" for block in result["content"]):
            raise RuntimeError(f"Unexpected completed no-UI tool result: {frame!r}")
        return "\n".join(block["text"] for block in result["content"]).splitlines()

    async def eof(self):
        # Only the externally owned stdin pipe closes. No cancellation frame,
        # reset, worker exit command or process termination is sent here.
        self.process.stdin.close()
        async with asyncio.timeout(10):
            await self.process.wait()
            await self.reading
            await self.job.wait_empty()
        return self.process.returncode


@asynccontextmanager
async def host(root, logs):
    job, process, server = WindowsJob(), None, None
    logs.mkdir()
    try:
        with (logs.parent / "server-stderr.log").open("xb") as errors:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "computer_use_mcp",
                "--no-allow-global-input",
                cwd=root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=errors,
                env={**os.environ, "COMPUTER_USE_LOG_DIR": str(logs), "PYTHONSAFEPATH": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW | 4,  # CREATE_SUSPENDED until outer Job assignment.
            )
            job.assign(process.pid)
            resume_suspended(process.pid)
            server = StdioHost(process, job)
            await server.initialize()
            yield server
    finally:
        job.close()
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), 5)
        if server is not None:
            await asyncio.gather(server.reading, return_exceptions=True)


def setup_code(fixture, spawn_log, *, fresh):
    spawn_log.touch(exist_ok=False)  # Existing fixture stays alive after read cleanup when the log already exists.
    return (
        ("assert 'old_namespace' not in globals() and 'old_receipt' not in globals()\n" if fresh else "")
        + "import os, sys\nfrom pathlib import Path\n"
        + "transport = computer._controller.transport\n"
        + f"transport.driver = {{'command': sys._base_executable, 'args': [{str(fixture)!r}, {str(spawn_log)!r}]}}\n"
        + "assert computer.list_apps() == []\n"
        + ("" if fresh else "old_namespace = 'owned disconnected state'\n")
        + "print('CUA_STDIO_READY', os.getpid(), transport._child.pid)"
    )


def worker_and_driver(lines):
    (line,) = [line for line in lines if line.startswith("CUA_STDIO_READY ")]
    _, worker, driver = line.split()
    return int(worker), int(driver)


async def wait_receipt(logs, receipt):
    async with asyncio.timeout(8):
        while not any(
            item.get("kind") == "output" and item.get("content", {}).get("text") == receipt for item in records(logs)
        ):
            await asyncio.sleep(0.025)


async def exercise(root, output, fixture, report):
    nonce = uuid.uuid4().hex[:12]
    effect, tail = output / "owned-effect.txt", output / "must-not-complete.txt"
    receipt = f"CUA_STDIO_OWNED_FILE_EFFECT {nonce}"
    old, fresh = output / "old", output / "fresh"
    old_logs, fresh_logs = old / "logs", fresh / "logs"
    old.mkdir()
    fresh.mkdir()
    async with host(root, old_logs) as original:
        pids = worker_and_driver(
            await original.completed_tool(setup_code(fixture, old / "driver-pids", fresh=False), 2)
        )
        if not all(original.job.contains(pid) for pid in pids):
            raise RuntimeError("A worker or inert native peer escaped the owned host Job")
        report["old_pids"] = {"launcher": original.process.pid, "worker": pids[0], "inert_native_peer": pids[1]}
        code = (
            "import time\n"
            + f"with Path({str(effect)!r}).open('a', encoding='utf-8') as output:\n    output.write({(nonce + chr(10))!r})\n"
            + f"old_receipt = {receipt!r}\nprint(old_receipt)\n"
            + "assert computer.list_apps() == []\ntime.sleep(35)\n"
            + f"Path({str(tail)!r}).write_text('finished', encoding='utf-8')"
        )
        with process_handles(pids) as exited:
            await original.tool(code, 3, progress=nonce)
            progress = await original.wait_frame(
                lambda frame: (
                    frame.get("method") == "notifications/progress"
                    and frame.get("params", {}).get("progressToken") == nonce
                )
            )
            await wait_receipt(old_logs, receipt)
            if any(frame.get("id") == 3 for frame in original.frames) or tail.exists() or any(exited().values()):
                raise RuntimeError("The probe was no longer in flight when the transport was cut")
            report["progress_before_eof"] = progress["params"]
            report["external_cut"] = "client stdin EOF while tool request was still in flight"
            start = time.monotonic()
            report["old_exit"] = await original.eof()
            report["old_retired"] = await wait_for_retirement(exited)
            report["eof_to_retirement_ms"] = round((time.monotonic() - start) * 1000)
        report["old_job_empty_before_kill_on_close"] = True
        report["result_frame_after_eof"] = any(frame.get("id") == 3 for frame in original.frames)

    events = list(records(old_logs))
    (interrupted,) = [item for item in events if item.get("kind") == "execution_end" and item["executionId"] == 2]
    output_lines = [block["text"] for block in interrupted["content"] if block["type"] == "text"]
    (started,) = [item for item in events if item.get("kind") == "execution_start" and item["executionId"] == 2]
    streamed = [
        item for item in events if item.get("kind") == "output" and item.get("executionId") == started["executionId"]
    ]
    report["interrupted"] = {
        "journal_error": interrupted["isError"],
        "content": output_lines,
        "streamed_receipts": sum(item.get("content", {}).get("text") == receipt for item in streamed),
    }
    report["reset_reasons"] = [item["reason"] for item in events if item.get("kind") == "execution_reset"]
    if (
        report["old_exit"] != 0
        or not interrupted["isError"]
        or output_lines.count(receipt) != 1
        or not any("Code dispatch: sent. Completion: unknown." in line for line in output_lines)
        or report["interrupted"]["streamed_receipts"] != 1
        or tail.exists()
        or effect.read_text(encoding="utf-8").splitlines() != [nonce]
    ):
        raise RuntimeError("The disconnected execution did not preserve one effect and unknown completion")

    async with host(root, fresh_logs) as successor:
        new = worker_and_driver(
            await successor.completed_tool(setup_code(fixture, fresh / "driver-pids", fresh=True), 2)
        )
        if set(new) & set(pids) or not all(successor.job.contains(pid) for pid in new):
            raise RuntimeError("The new host did not establish an independent worker and inert native peer")
        report["fresh_pids"] = {"launcher": successor.process.pid, "worker": new[0], "inert_native_peer": new[1]}
        with process_handles(new) as exited:
            report["fresh_exit"] = await successor.eof()
            report["fresh_retired"] = await wait_for_retirement(exited)
        report["fresh_job_empty_before_kill_on_close"] = True
    report["effect_count"] = len(effect.read_text(encoding="utf-8").splitlines())
    report["completed_statement_exists"] = tail.exists()
    report["pass"] = report["fresh_exit"] == 0 and report["effect_count"] == 1 and not tail.exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root, output = Path.cwd(), args.output.resolve()
    fixture = root / "tests/fixtures/observation_session_driver.py"
    if sys.platform != "win32" or not fixture.is_file() or output.exists():
        parser.error("Requires the Windows runtime checkout and a fresh output directory")
    output.mkdir(parents=True)
    report = {
        "pass": False,
        "scope": "actual external MCP stdio EOF with inert protocol peer and an owned file effect",
        "sdk": computer_use.__file__,
        "mcp": computer_use_mcp.__file__,
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    try:
        asyncio.run(exercise(root, output, fixture, report))
    except Exception as error:  # noqa: BLE001 - retain actual partial evidence and cleanup outcome
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{output} pass={report['pass']}\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
