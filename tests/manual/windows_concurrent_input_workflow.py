# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify Windows SDK/MCP write serialization and crash recovery on an owned fixture."""

import argparse
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from computer_use_mcp.windows_worker import WindowsJob, resume_suspended
from windows_continuous_workflow import delivered_value
from windows_desktop_batch import ROOT
from windows_lifecycle_soak import Sampler
from windows_paste_workflow import paste_fixture
from windows_text_workflow import fixture_command, fixture_state, receipt_data

from computer_use_agent.config import McpConfig
from computer_use_agent.connection import McpConnection

CASES = ("contention", "crash-recovery")
WRITE_OK = "CUA_WRITER dispatched not_requested"


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=True) + "\n", encoding="utf-8")


def sdk_worker(case: str, folder: Path, payload: str) -> None:
    admitted = set()
    begin = None
    with Computer() as computer:
        if case == "crash-recovery":
            save(folder / "paste.json", receipt_data(computer.paste(payload)))

        def gate(event):
            nonlocal begin
            method, kind = event.get("method"), event["kind"]
            if method == "begin" and kind == "command_start" and event["params"]["mode"] == "write":
                admitted.add(event["traceId"])
            if (
                method == "begin" and kind == "command_end" and event.get("traceId") in admitted
                and event.get("status") == "ok"
            ):
                begin = {"trace": event["traceId"], "operation": event["result"]["operation"]}
            if method == "execute" and kind == "command_start" and begin is not None:
                if event["params"]["operation"] != begin["operation"]:
                    return
                save(folder / "gate.json", {"begin": begin, "before_execute": event["traceId"], "pid": os.getpid()})
                until = time.monotonic() + 15
                while not (folder / "release").exists():
                    if time.monotonic() >= until:
                        # End the isolated writer instead of accidentally
                        # dispatching after the harness has stopped waiting.
                        os._exit(80)
                    time.sleep(0.01)

        set_diagnostic_handler(gate)
        try:
            result = computer.type_text(payload) if case == "contention" else computer.press_key("Left")
            save(folder / "sdk-result.json", receipt_data(result))
        finally:
            set_diagnostic_handler(None)


def terminate_owned_job(job: WindowsJob, exit_code: int) -> None:
    kill = job.kernel.TerminateJobObject
    kill.argtypes, kill.restype = (ctypes.c_void_p, ctypes.c_uint32), ctypes.c_int
    if not kill(job.handle, exit_code):
        raise ctypes.WinError(ctypes.get_last_error())


async def reap_job(job: WindowsJob, process: subprocess.Popen) -> int:
    async with asyncio.timeout(6):
        await job.wait_empty()
        return await asyncio.to_thread(process.wait, 1)


@contextmanager
def owned_writer(case: str, folder: Path, payload: str, report: dict):
    job = WindowsJob()
    process = None
    try:
        with (folder / "sdk.log").open("xb") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker", case, str(folder), payload],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW | 4,
            )
            job.assign(process.pid)
            resume_suspended(process.pid)
            yield job, process
    finally:
        job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            report["sdk_process_exit"] = process.wait(timeout=5)
        log_path = folder / "sdk.log"
        if log_path.is_file():
            report["sdk_log"] = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]


async def await_json(path: Path, process: subprocess.Popen) -> dict:
    async with asyncio.timeout(8):
        while True:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if process.poll() is not None:
                    raise RuntimeError(f"The owned SDK process exited before publishing {path.name}") from None
                await asyncio.sleep(0.02)


def begin_evidence(logs: Path, payload: str) -> tuple[dict | None, dict | None]:
    begin, end = None, None
    for file in logs.glob("*.jsonl"):
        for line in file.read_text(encoding="utf-8").splitlines():
            if '"command_start"' not in line and '"command_end"' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue  # The active MCP process may be appending this line.
            if event.get("method") != "begin":
                continue
            if event["kind"] == "command_start":
                params = event.get("params", {})
                intent = params.get("intent", {})
                if params.get("mode") == "write" and intent.get("parameters", {}).get("text") == payload:
                    begin = {key: event[key] for key in ("traceId", "timestamp")}
            elif begin and event.get("traceId") == begin["traceId"]:
                end = {key: event[key] for key in ("status", "timestamp", "durationMs")}
    return begin, end


async def await_competing_begin(logs: Path, payload: str, task: asyncio.Task) -> dict:
    async with asyncio.timeout(5):
        while True:
            begin, _ = begin_evidence(logs, payload)
            if begin:
                await asyncio.sleep(0.12)
                _, end = begin_evidence(logs, payload)
                if task.done():
                    raise RuntimeError(f"MCP competing write ended while the SDK lease was held: {task.result()}")
                assert end is None, end
                return begin
            if task.done():
                raise RuntimeError(f"MCP competing write returned without its native admission: {task.result()}")
            await asyncio.sleep(0.03)


def modifier_state() -> list[int]:
    return [key for key in (0x01, 0x02, 0x04, 0x10, 0x11, 0x12, 0x5B, 0x5C)
            if ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000]


async def workflow(case: str, title: str, fixture: Path, folder: Path, nonce: str, report: dict) -> None:
    first, second = f"CUA-A-{nonce} 中文 😀 ", f"CUA-B-{nonce} 完成 🔎"
    report.update(first_payload=first, second_payload=second)
    logs = folder / "mcp-logs"
    config = McpConfig(command=sys.executable, args=("-m", "computer_use_mcp.server", "--allow-global-input"),
                       env={"COMPUTER_USE_LOG_DIR": str(logs)}, cwd=str(ROOT))
    connection = McpConnection(config)

    async def call(code: str) -> str:
        async with asyncio.timeout(25):
            result = await connection.call("computer_use", {"title": "Verify independent Windows sessions", "code": code})
        text = "\n".join(item.text for item in result.content if item.type == "text")
        assert not result.isError, text
        return text

    async def read_scope() -> str:
        text = await call('print(app.read(view="tree", full=True).text)')
        assert title in text and "Paste target" in text and "Bystander" in text, text
        return text

    try:
        await call('app = computer.app("CuaPasteFixture")')
        report["before"] = await read_scope()
        assert delivered_value(report["before"]) == "", report["before"]
        report["held_before"] = modifier_state()
        assert not report["held_before"], report["held_before"]
        with owned_writer(case, folder, first, report) as (job, process):
            report["sdk_gate"] = await await_json(folder / "gate.json", process)
            if case == "crash-recovery":
                receipt = await await_json(folder / "paste.json", process)
                assert (receipt["status"], receipt["observation_status"]) == ("dispatched", "not_requested"), receipt
                report["pending_paste"] = {"receipt": receipt, "consumer_effect": "unknown", "replayed": False}
            pending = int(case == "crash-recovery")
            report["read_while_held"] = await read_scope()
            assert delivered_value(report["read_while_held"]) == "", report["read_while_held"]
            writer = asyncio.create_task(call(
                f'r = computer.type_text({second!r})\nprint("CUA_WRITER", r.status, r.observation_status)'
            ))
            try:
                report["mcp_waiting_begin"] = await await_competing_begin(logs, second, writer)
                report["blocked_effects"] = fixture_state(
                    fixture, lambda state: state.get("pending") == pending and state.get("paste_count") == pending,
                )
                assert report["blocked_effects"]["text"] == "" and report["blocked_effects"]["consumed"] == 0
                assert report["blocked_effects"]["focused"], report["blocked_effects"]
                if case == "crash-recovery":
                    sampler = Sampler()
                    report["crash_job"] = Sampler(census=lambda: sampler.pids(job.handle)).sample()
                    driver_name = Path(native_driver()["command"]).name.casefold()
                    owners = [item for item in report["crash_job"]["processes"] if item.get("name", "").casefold() == driver_name]
                    assert len(owners) >= 2, report["crash_job"]
                    terminate_owned_job(job, 91)
                    report["sdk_job_empty"] = await reap_job(job, process) == 91
                    assert report["sdk_job_empty"] and not (folder / "sdk-result.json").exists()
                else:
                    (folder / "release").touch(exist_ok=False)
                    receipt = await await_json(folder / "sdk-result.json", process)
                    assert (receipt["status"], receipt["observation_status"]) == ("dispatched", "not_requested"), receipt
                    report["sdk_receipt"] = receipt
                    report["sdk_job_empty"] = await reap_job(job, process) == 0
                    assert report["sdk_job_empty"]
                report["mcp_write"] = await writer
                assert report["mcp_write"].splitlines().count(WRITE_OK) == 1, report["mcp_write"]
                _, report["mcp_admitted"] = begin_evidence(logs, second)
                assert report["mcp_admitted"] and report["mcp_admitted"]["status"] == "ok", report["mcp_admitted"]
            finally:
                if not writer.done():
                    writer.cancel()
                    await asyncio.gather(writer, return_exceptions=True)

        expected = first + second if case == "contention" else second
        report["expected"] = expected
        for _ in range(3):
            report["final_observation"] = await read_scope()
            if delivered_value(report["final_observation"]) == expected:
                break
        else:
            raise AssertionError("MCP did not deliver the exact final value in the same scope")
        report["actual"] = fixture_state(fixture, lambda state: state.get("text") == expected)
        actual = report["actual"]
        assert actual["paste_count"] == actual["pending"] == pending and actual["consumed"] == actual["returns"] == 0, actual
        if case == "crash-recovery":
            report["clipboard_after_recovery"] = fixture_command(fixture, "sample")["clipboard_sample"]
            assert report["clipboard_after_recovery"] == first, report["clipboard_after_recovery"]
        report["held_after"] = modifier_state()
        assert report["held_after"] == report["held_before"], report["held_after"]
    finally:
        await connection.close()
        report["mcp_session_closed"] = True


def run(case: str) -> dict:
    nonce = uuid.uuid4().hex[:10]
    report = {"case": case, "pass": False, "provenance": startup_provenance(native_driver()["command"])}
    try:
        profile = "hold" if case == "crash-recovery" else "default"
        with tempfile.TemporaryDirectory(prefix="cua-concurrent-input-") as folder, paste_fixture(
            0, nonce, True, profile,
        ) as (fixture, initial):
            report["initial"] = initial
            assert initial["focused"] and initial["paste_count"] == initial["consumed"] == 0, initial
            asyncio.run(workflow(case, f"CUA Paste Fixture {nonce}", fixture, Path(folder), nonce, report))
        report["pass"] = True
    except (AssertionError, ComputerUseError, OSError, RuntimeError, TimeoutError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    return report


def main() -> None:
    if len(sys.argv) == 5 and sys.argv[1] == "--worker" and sys.argv[2] in CASES:
        sdk_worker(sys.argv[2], Path(sys.argv[3]), sys.argv[4])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, report)
    sys.stdout.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
