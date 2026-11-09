# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify MCP reset and worker loss during a held paste on an owned Windows fixture."""

import argparse
import asyncio
import ctypes
import inspect
import json
import subprocess
import sys
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

import computer_use
from computer_use import ComputerUseError
from computer_use.paths import native_driver
from windows_continuous_workflow import delivered_value, fixture_foreground
from windows_desktop_batch import ROOT, sha256
from windows_paste_workflow import paste_fixture
from windows_text_workflow import fixture_command, fixture_state

from computer_use_agent.config import McpConfig
from computer_use_agent.connection import McpConnection

RECEIPT = "CUA_RECOVERY_PASTE dispatched not_requested"
NO_CAPTURE = """from computer_use.native.connection import Connection
capture_attempts = 0
request_without_guard = Connection.request
async def veto_capture(self, method, params, options=None):
    global capture_attempts
    if method == 'capture':
        capture_attempts += 1
        raise AssertionError('Capture attempted in the image-free recovery test')
    return await request_without_guard(self, method, params, options)
Connection.request = veto_capture
"""


def records(logs: Path):
    for path in sorted(logs.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # The live supervisor may not have finished appending the final line.


def worker_code(title: str) -> str:
    return (
        NO_CAPTURE
        + "import ctypes, os, time\nfrom ctypes import wintypes\nfrom pathlib import Path\n"
        + inspect.getsource(fixture_foreground)
        + f"\napp = computer.app(window_title={title!r})\n"
        + "obs = app.read(view='tree', full=True, allow_foreground=False)\n"
        + "assert obs.screenshot is None and capture_attempts == 0\n"
        + "element = app.element('Paste target')\nmarker = 'old worker namespace'\n"
        + "print(obs.text)\nprint('CUA_RECOVERY_WORKER', os.getpid())"
    )


def paste_code(foreground: tuple[int, int, str], payload: str) -> str:
    return (
        f"fixture_foreground(*{foreground!r})\nr = computer.paste({payload!r})\n"
        "assert r.observation is None and capture_attempts == 0\n"
        "print('CUA_RECOVERY_PASTE', r.status, r.observation_status)"
    )


def tree_code() -> str:
    return (
        "obs = app.read(view='tree', full=True, allow_foreground=False)\n"
        "assert obs.screenshot is None and capture_attempts == 0\nprint(obs.text)"
    )


def worker_pid(lines: list[str]) -> int:
    prefix = "CUA_RECOVERY_WORKER "
    pids = [int(line.removeprefix(prefix)) for line in lines if line.startswith(prefix)]
    assert len(pids) == 1 and pids[0] > 0, lines
    return pids[0]


def driver_pid(logs: Path, driver: Path, old_pid: int | None = None) -> int:
    pids = {
        event["pid"]
        for event in records(logs)
        if event.get("kind") == "connection"
        and Path(event.get("executable", "")).resolve() == driver
        and event["pid"] != old_pid
    }
    assert len(pids) == 1, pids
    return pids.pop()


@contextmanager
def process_handles(pids: tuple[int, ...]):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handles = {}
    try:
        for pid in pids:
            handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            handles[pid] = handle

        def exited():
            statuses = {pid: kernel.WaitForSingleObject(handle, 0) for pid, handle in handles.items()}
            if not all(status in {0, 258} for status in statuses.values()):
                raise ctypes.WinError(ctypes.get_last_error())
            return {pid: status == 0 for pid, status in statuses.items()}

        assert not any(exited().values()), "The original owned worker or driver had already exited"
        yield exited
    finally:
        for handle in handles.values():
            kernel.CloseHandle(handle)


async def wait_for_retirement(exited) -> dict[int, bool]:
    async with asyncio.timeout(8):
        while True:
            facts = exited()
            if all(facts.values()):
                return facts
            await asyncio.sleep(0.025)


async def call(connection: McpConnection, code: str, *, error: bool = False) -> list[str]:
    async with asyncio.timeout(35):
        result = await connection.call("computer_use", {"title": "Verify owned fixture recovery", "code": code})
    assert all(item.type == "text" for item in result.content), "Unexpected non-text content in recovery test"
    lines = "\n".join(item.text for item in result.content).splitlines()
    assert bool(result.isError) == error, lines
    return lines


async def crash_after_receipt(connection, code: str, logs: Path, folder: Path, before_exit) -> list[str]:
    release = folder / "exit-worker"
    code += (
        f"\nrelease = Path({str(release)!r})\n"
        "until = time.monotonic() + 15\n"
        "while not release.exists() and time.monotonic() < until:\n    time.sleep(0.025)\n"
        "os._exit(73 if release.exists() else 74)"
    )
    task = asyncio.create_task(call(connection, code, error=True))
    try:
        async with asyncio.timeout(8):
            while not any(
                event.get("kind") == "output" and event.get("content", {}).get("text") == RECEIPT
                for event in records(logs)
            ):
                if task.done():
                    raise AssertionError(f"Worker stopped before the supervisor received its receipt: {task.result()}")
                await asyncio.sleep(0.04)
        await before_exit()
        release.touch(exist_ok=False)
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def actual(path: Path, count: int, consumed: int, text: str) -> dict:
    return await asyncio.to_thread(
        fixture_state,
        path,
        lambda s: (
            s.get("paste_count") == count
            and s.get("consumed") == consumed
            and s.get("pending") == count - consumed
            and s.get("text") == text
            and s.get("focused")
            and s.get("returns") == 0
        ),
    )


async def workflow(case: str, path: Path, foreground: tuple[int, int, str], folder: Path, report: dict) -> None:
    logs, title = folder / "mcp-logs", foreground[2]
    driver = Path(native_driver()["command"]).resolve()
    first, second = f"A {report['nonce']} 中文 😀", f" B {report['nonce']} 完成 🔎"
    connection = McpConnection(
        McpConfig(
            command=sys.executable,
            args=("-m", "computer_use_mcp"),
            cwd=str(ROOT),
            env={"COMPUTER_USE_LOG_DIR": str(logs)},
        )
    )
    try:
        report["before"] = await call(connection, worker_code(title))
        assert delivered_value("\n".join(report["before"])) == "", report["before"]
        old_worker, old_driver = worker_pid(report["before"]), driver_pid(logs, driver)
        report["old_identity"] = {"worker": old_worker, "driver": old_driver}

        async def held():
            report["held_before_transition"] = await actual(path, 1, 0, "")

        with process_handles((old_worker, old_driver)) as exited:
            code = paste_code(foreground, first)
            if case == "worker-exit":
                report["interrupted_result"] = await crash_after_receipt(connection, code, logs, folder, held)
                lines = report["interrupted_result"]
                assert lines.count(RECEIPT) == 1 and "Code dispatch: sent. Completion: unknown." in lines, lines
                failures = [event for event in records(logs) if event.get("kind") == "worker_failure"]
                assert len(failures) == 1 and failures[0]["phase"] == "execution", failures
                report["worker_failure"] = failures[0]
                exits = [event for event in records(logs) if event.get("kind") == "worker_exit"]
                assert len(exits) == 1 and exits[0]["returncode"] == 73, exits
                report["worker_exit"] = exits[0]
            else:
                report["paste_a"] = await call(connection, code)
                assert report["paste_a"].count(RECEIPT) == 1, report["paste_a"]
                await held()
                async with asyncio.timeout(15):
                    reset = await connection.call("computer_use_reset", {})
                assert not reset.isError and all(item.type == "text" for item in reset.content), reset
                report["reset"] = "\n".join(item.text for item in reset.content)
                assert "Python variables, App/Element handles and observations cleared" in report["reset"]
            report["retired"] = await wait_for_retirement(exited)
            code = "assert not {'app', 'element', 'obs', 'marker', 'r'} & globals().keys()\n" + worker_code(title)
            report["fresh_empty"] = await call(connection, code)
            assert delivered_value("\n".join(report["fresh_empty"])) == "", report["fresh_empty"]
            new_worker, new_driver = worker_pid(report["fresh_empty"]), driver_pid(logs, driver, old_driver)
            assert new_worker != old_worker and new_driver != old_driver
        report["new_identity"] = {"worker": new_worker, "driver": new_driver}
        report["held_after_transition"] = await actual(path, 1, 0, "")
        assert (await asyncio.to_thread(fixture_command, path, "sample"))["clipboard_sample"] == first
        await asyncio.to_thread(fixture_command, path, "consume")
        report["actual_a"] = await actual(path, 1, 1, first)
        report["fresh_a"] = await call(connection, tree_code())
        assert delivered_value("\n".join(report["fresh_a"])) == first, report["fresh_a"]

        report["paste_b"] = await call(connection, paste_code(foreground, second))
        assert report["paste_b"].count(RECEIPT) == 1, report["paste_b"]
        report["held_b"] = await actual(path, 2, 1, first)
        assert (await asyncio.to_thread(fixture_command, path, "sample"))["clipboard_sample"] == second
        await asyncio.to_thread(fixture_command, path, "consume")
        report["actual_b"] = await actual(path, 2, 2, first + second)
        report["fresh_b"] = await call(connection, tree_code())
        assert delivered_value("\n".join(report["fresh_b"])) == first + second, report["fresh_b"]
        with process_handles((new_worker, new_driver)) as exited:
            await connection.close()
            report["final_retired"] = await wait_for_retirement(exited)
    finally:
        await connection.close()


def run(case: str, folder: Path) -> dict:
    nonce = uuid.uuid4().hex[:10]
    title = f"CUA Paste Fixture {nonce}"
    driver = Path(native_driver()["command"]).resolve()
    report = {
        "case": case,
        "nonce": nonce,
        "module": computer_use.__file__,
        "driver_sha256": sha256(driver),
        "image_free": True,
        "pass": False,
    }
    try:
        with paste_fixture(0, nonce, True, "hold", require_natural_focus=True) as (path, initial):
            pid = initial["fixture_pid"]
            foreground = (fixture_foreground(None, pid, title), pid, title)
            report["fixture"] = {"hwnd": foreground[0], "pid": pid, "title": title}
            asyncio.run(workflow(case, path, foreground, folder, report))
        report["pass"] = True
    except (
        AssertionError,
        ComputerUseError,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("reset", "worker-exit"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    folder = output.with_suffix("")
    folder.mkdir(parents=True)
    report = run(args.case, folder)
    output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
