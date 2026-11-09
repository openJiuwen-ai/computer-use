# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WLIFE-009: fresh session cleanup and sustained input on an owned Windows fixture.

An outer Job contains the workload and its descendants. Natural teardown is
measured before the harness can terminate that Job. The worker streams resource
samples to disk so retained test results cannot imitate workload memory growth.
"""

import argparse
import asyncio
import base64
import ctypes
import hashlib
import hmac
import inspect
import json
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, asynccontextmanager, redirect_stdout
from datetime import timedelta
from io import StringIO
from itertools import pairwise
from pathlib import Path
from statistics import median

from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use_mcp.windows_worker import WindowsJob, resume_suspended
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from windows_continuous_workflow import IMAGE_FREE_SELECTION, delivered_value, fixture_foreground, mcp_window
from windows_desktop_batch import ROOT, manifest, sha256
from windows_paste_workflow import paste_fixture, wait_state
from windows_visual_workflow import build_fixture, marker_point, window_ref

U32, SIZE, HANDLE, BOOL = ctypes.c_uint32, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int
RESOURCES = ("threads", "handles", "private_bytes", "working_set_bytes")
CENSUS_PORT = "CUA_SOAK_CENSUS_PORT"
CENSUS_TOKEN = "CUA_SOAK_CENSUS_TOKEN"
JOB_CENSUS_ATTEMPTS = 4
IMAGE_FREE_SDK_BLOCKING_WORKERS = 1


class JobProcesses(ctypes.Structure):
    _fields_ = [("assigned", U32), ("returned", U32), ("pids", SIZE * 4096)]


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", U32),
        ("usage", U32),
        ("pid", U32),
        ("heap", SIZE),
        ("module", U32),
        ("threads", U32),
        ("parent", U32),
        ("priority", ctypes.c_int32),
        ("flags", U32),
        ("name", ctypes.c_wchar * 260),
    ]


class ProcessMemory(ctypes.Structure):
    _fields_ = [("size", U32), ("faults", U32)] + [
        (name, SIZE)
        for name in (
            "peak_working_set",
            "working_set",
            "peak_paged_pool",
            "paged_pool",
            "peak_nonpaged_pool",
            "nonpaged_pool",
            "pagefile",
            "peak_pagefile",
            "private",
        )
    ]


class Sampler:
    def __init__(self, census=None, *, record_census=None):
        self.census = census
        self.record_census = record_census
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        times = ctypes.POINTER(ctypes.c_uint64)
        for name, args, result in (
            ("QueryInformationJobObject", (HANDLE, ctypes.c_int, HANDLE, U32, HANDLE), BOOL),
            ("CreateToolhelp32Snapshot", (U32, U32), HANDLE),
            ("Process32FirstW", (HANDLE, ctypes.POINTER(ProcessEntry)), BOOL),
            ("Process32NextW", (HANDLE, ctypes.POINTER(ProcessEntry)), BOOL),
            ("OpenProcess", (U32, BOOL, U32), HANDLE),
            ("GetProcessTimes", (HANDLE, times, times, times, times), BOOL),
            ("GetProcessHandleCount", (HANDLE, ctypes.POINTER(U32)), BOOL),
            ("K32GetProcessMemoryInfo", (HANDLE, ctypes.POINTER(ProcessMemory), U32), BOOL),
            ("CloseHandle", (HANDLE,), BOOL),
        ):
            method = getattr(self.kernel, name)
            method.argtypes, method.restype = args, result

    def pids(self, job=None):
        if self.census is not None:
            return self.census()
        if job is None:
            raise ValueError("A local census requires the exact outer Job handle")
        for attempt in range(1, JOB_CENSUS_ATTEMPTS + 1):
            state = JobProcesses()
            success = self.kernel.QueryInformationJobObject(job, 3, ctypes.byref(state), ctypes.sizeof(state), None)
            winerror = None if success else ctypes.get_last_error()
            assigned, returned = state.assigned, state.returned
            if not success:
                status = "native_error"
            elif returned > assigned:
                status = "invalid_counts"
            elif assigned > len(state.pids):
                status = "capacity_exceeded"
            elif returned < assigned:
                status = "incomplete"
            else:
                pids = set(state.pids[:returned])
                status = "invalid_pids" if 0 in pids or len(pids) != returned else "complete"
            if self.record_census is not None and (attempt > 1 or status != "complete"):
                self.record_census(
                    {
                        "attempt": attempt,
                        "assigned": assigned,
                        "returned": returned,
                        "winerror": winerror,
                        "status": status,
                    }
                )
            if status == "complete":
                return pids
            if not success:
                raise ctypes.WinError(winerror)
            if status != "incomplete" or attempt == JOB_CENSUS_ATTEMPTS:
                raise RuntimeError(
                    f"Job process census {status} on attempt {attempt}: assigned={assigned}, returned={returned}"
                )
            # Discard the partial list and query again while Job membership can change.
            time.sleep(0.01)

    def _details(self, entry):
        handle = self.kernel.OpenProcess(0x1010, False, entry.pid)  # QUERY_LIMITED_INFORMATION | VM_READ
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            times = [ctypes.c_uint64() for _ in range(4)]
            handles, memory = U32(), ProcessMemory()
            memory.size = ctypes.sizeof(memory)
            if not (
                self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))
                and self.kernel.GetProcessHandleCount(handle, ctypes.byref(handles))
                and self.kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return {
                "pid": entry.pid,
                "parent": entry.parent,
                "name": entry.name,
                "creation_filetime": times[0].value,
                "threads": entry.threads,
                "handles": handles.value,
                "private_bytes": memory.private,
                "working_set_bytes": memory.working_set,
            }
        finally:
            self.kernel.CloseHandle(handle)

    def sample(self):
        owned = self.pids()
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        found = {}
        try:
            entry = ProcessEntry()
            entry.size = ctypes.sizeof(entry)
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            if not more:
                raise ctypes.WinError(ctypes.get_last_error())
            while more:
                if entry.pid in owned:
                    try:
                        found[entry.pid] = self._details(entry)
                    except OSError as error:
                        found[entry.pid] = {"pid": entry.pid, "name": entry.name, "query_error": error.winerror}
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        # Ignore a PID that raced an exit while the process snapshot was sampled.
        stable = self.pids() & owned
        return {
            "job_pids": sorted(stable),
            "processes": [found.get(pid, {"pid": pid, "query_error": "not_in_snapshot"}) for pid in sorted(stable)],
        }


class CensusRequest(socketserver.StreamRequestHandler):
    timeout = 1

    def handle(self):
        if not hmac.compare_digest(self.rfile.readline(65), self.server.token + b"\n"):
            return
        try:
            response = {"pids": sorted(self.server.sampler.pids(self.server.job))}
        except (OSError, RuntimeError) as error:
            response = {"error": str(error)}
        self.wfile.write(json.dumps(response).encode("ascii") + b"\n")


class JobCensus(socketserver.TCPServer):
    timeout = 0.25

    def __init__(self, job, *, record_census=None):
        super().__init__(("127.0.0.1", 0), CensusRequest)
        self.job = job
        self.sampler = Sampler(record_census=record_census)
        self.token = secrets.token_hex(32).encode("ascii")

    def worker_environment(self):
        return {**os.environ, CENSUS_PORT: str(self.server_address[1]), CENSUS_TOKEN: self.token.decode("ascii")}


def census_client():
    address = ("127.0.0.1", int(os.environ.pop(CENSUS_PORT)))
    token = os.environ.pop(CENSUS_TOKEN).encode("ascii")

    def pids():
        # The worker never holds a Job handle, including when the supervisor is killed.
        with socket.create_connection(address, timeout=2) as channel, channel.makefile("rb") as reply:
            channel.sendall(token + b"\n")
            frame = json.loads(reply.readline(64 * 1024))
        if "error" in frame:
            raise RuntimeError(frame["error"])
        return set(frame["pids"])

    return pids


def startup_job(sampler, host_pid, launcher_pid):
    startup = sampler.sample()
    pids = set(startup["job_pids"])
    expected = {host_pid, launcher_pid}
    # A venv redirector can remain, along with its actual console host.
    unexpected = [
        process for process in startup["processes"]
        if process["pid"] not in expected
        and (process.get("name", "").casefold() != "conhost.exe" or process.get("parent") not in expected)
    ]
    if host_pid not in pids or unexpected:
        raise RuntimeError(f"Unexpected outer Job startup census: {startup}")
    return pids


def event(stream, kind, **facts):
    stream.write(json.dumps({"kind": kind, "utc_unix": time.time(), **facts}) + "\n")
    stream.flush()


def sdk_step(computer, app, ref):
    observed = app.read(view="image", window=ref)
    if observed.screenshot is None:
        raise RuntimeError("The fixture image was unavailable")
    point, size = marker_point(observed.screenshot)
    if "Showing the visible desktop" in observed.text:
        receipt = computer.click(target=computer.point(point))
    else:
        receipt = app.click(at=app.point(point, window=ref), window=ref)
    return {
        "status": receipt.status,
        "point": point,
        "image_size": size,
        "image_sha256": hashlib.sha256(observed.screenshot).hexdigest(),
        "observation": receipt.observation_status,
    }


async def raw_session(call, app_name, title, foreground):
    catalogue = await call(
        "import ctypes\nfrom ctypes import wintypes\n" + inspect.getsource(fixture_foreground)
        + f"\napp = computer.app({app_name!r})\nobs = app.read(view='tree', full=True)\n"
        "assert obs.screenshot is None\nprint(obs.text)\nprint(obs.read_details(length=16384).text)"
    )
    ref = mcp_window(catalogue, title, {"image_free": True})
    guard = f"fixture_foreground(**{foreground!r})"
    dispatched = "assert r.status == 'dispatched' and r.observation_status == 'not_requested' and r.observation is None\n"

    async def step(payload):
        receipt = await call(
            f"for key in {IMAGE_FREE_SELECTION!r}:\n    {guard}\n    r = computer.press_key(key)\n    {dispatched}"
            f"print('CUA_SOAK_FOREGROUND', {guard})\nr = computer.paste({payload!r})\n{dispatched}"
            "print('CUA_SOAK_RECEIPT', r.status, r.observation_status)"
        )
        if "CUA_SOAK_RECEIPT dispatched not_requested" not in receipt.splitlines() or (
            f'CUA_SOAK_FOREGROUND {foreground["hwnd"]}' not in receipt.splitlines()
        ):
            raise RuntimeError("The image-free paste did not return its exact foreground and dispatch receipt")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            text = await call(
                f"obs = app.read(view='tree', window={ref}, full=True)\nassert obs.screenshot is None\nprint(obs.text)"
            )
            if delivered_value(text) == payload:
                return {"status": "dispatched", "observation": "not_requested", "window": ref, "readback": payload}
            await asyncio.sleep(0.05)
        raise RuntimeError("The image-free tree did not deliver the exact pasted payload")

    return step


@asynccontextmanager
async def open_session(entry, app_name, title, logs, foreground=None):
    if entry == "sdk":
        computer = await asyncio.to_thread(Computer)
        try:
            if foreground is not None:
                namespace = {"computer": computer}

                def execute(code):
                    with redirect_stdout(StringIO()) as output:
                        exec(code, namespace)  # noqa: S102 - fixed test calls and quoted fixture values
                    return output.getvalue()

                async def call(code):
                    return await asyncio.to_thread(execute, code)

                yield await raw_session(call, app_name, title, foreground)
                return

            app = computer.app(app_name)
            initial = await asyncio.to_thread(app.read, view="image", full=True)
            ref = window_ref(initial.text, title)
            await asyncio.to_thread(app.read, view="image", window=ref, allow_foreground=True)

            async def step():
                return await asyncio.to_thread(sdk_step, computer, app, ref)

            yield step
        finally:
            await asyncio.to_thread(computer.close)
        return

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp"],
        cwd=str(ROOT),
        env={"COMPUTER_USE_LOG_DIR": str(logs), "PYTHONSAFEPATH": "1"},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=40)) as client,
    ):
        await client.initialize()

        async def call(code):
            label = "Paste in owned fixture" if foreground is not None else "Observe and click owned fixture"
            result = await client.call_tool("computer_use", {"title": label, "code": code})
            if foreground is not None and any(part.type != "text" for part in result.content):
                raise RuntimeError("The image-free MCP call delivered non-text content")
            texts = [part.text for part in result.content if part.type == "text"]
            if result.isError:
                raise RuntimeError("\n".join(texts))
            return "\n".join(texts), [part for part in result.content if part.type == "image"]

        if foreground is not None:
            async def text_call(code):
                text, _ = await call(code)
                return text

            yield await raw_session(text_call, app_name, title, foreground)
            return

        initial, _ = await call(f"app = computer.app({app_name!r}); print(app.read(view='image', full=True).text)")
        ref = window_ref(initial, title)
        await call(f"app.read(view='image', window={ref}, allow_foreground=True)")

        async def step():
            text, images = await call(f"app.read(view='image', window={ref})")
            if len(images) != 1:
                raise RuntimeError(f"Expected one delivered fixture image, got {len(images)}")
            image = base64.b64decode(images[0].data)
            point, size = marker_point(image)
            click = (
                f"computer.click(target=computer.point({point!r}))"
                if "Showing the visible desktop" in text
                else f"app.click(at=app.point({point!r}, window={ref}), window={ref})"
            )
            receipt, _ = await call(f"r = {click}; print('CUA_SOAK_RECEIPT', r.status)")
            prefix = "CUA_SOAK_RECEIPT "
            statuses = [line.removeprefix(prefix) for line in receipt.splitlines() if line.startswith(prefix)]
            if len(statuses) != 1:
                raise RuntimeError("The MCP tool did not deliver exactly one click status")
            return {
                "status": statuses[0],
                "point": point,
                "image_size": size,
                "image_sha256": hashlib.sha256(image).hexdigest(),
            }

        yield step


async def wait_owned_exit(sampler, expected, deadline):
    while True:
        live = sampler.pids()
        if live == expected:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Owned process census after cleanup: {sorted(live)}; expected {sorted(expected)}")
        await asyncio.sleep(0.025)


@asynccontextmanager
async def workload_fixture(output, nonce, image_free):
    if image_free:
        title = f"CUA Paste Fixture {nonce}"
        with paste_fixture(0, nonce, True, require_natural_focus=True) as (state_path, ready):
            pid = ready["fixture_pid"]
            foreground = {"hwnd": fixture_foreground(None, pid, title), "pid": pid, "title": title}
            yield state_path.with_name("CuaPasteFixture.exe"), state_path, pid, title, ready, foreground
        return
    exe, state_path = output / "CuaSoakFixture.exe", output / "fixture-state.json"
    await asyncio.to_thread(build_fixture, exe)
    fixture = await asyncio.create_subprocess_exec(str(exe), str(state_path), nonce)
    try:
        ready = await asyncio.to_thread(wait_state, state_path, lambda value: bool(value.get("main_title")), 8)
        yield exe, state_path, fixture.pid, ready["main_title"], ready, None
    finally:
        if fixture.returncode is None:
            fixture.terminate()
        await asyncio.wait_for(fixture.wait(), 5)


async def worker(args):
    if args.image_free and args.entry == "sdk":
        # These blocking SDK calls are serial. Keep the measuring host's pool
        # capacity fixed across cycles so it cannot imitate session retention.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(
            max_workers=IMAGE_FREE_SDK_BLOCKING_WORKERS, thread_name_prefix="cua-lifecycle-sdk",
        ))
    sampler = Sampler(census_client())
    host_pid = os.getpid()
    startup_pids = startup_job(sampler, host_pid, os.getppid())
    output = args.output
    nonce = uuid.uuid4().hex[:10]
    fixture_scope = AsyncExitStack()
    try:
        exe, state_path, fixture_pid, title, ready, foreground = await fixture_scope.enter_async_context(
            workload_fixture(output, nonce, args.image_free)
        )
        steady_pids = startup_pids | {fixture_pid}
        counter = "consumed" if args.image_free else "target_clicks"
        expected = ready[counter]
        live_pids = sampler.pids()
        if live_pids != steady_pids:
            raise RuntimeError(f"Outer Job after fixture startup: {sorted(live_pids)}; expected {sorted(steady_pids)}")
        if expected != 0 or (ready["paste_count"] if args.image_free else ready["bystander_clicks"]) != 0:
            raise RuntimeError("The owned fixture did not start with zero input")
        with (
            (output / "progress.jsonl").open("x", encoding="utf-8") as progress,
            (output / "samples.jsonl").open("x", encoding="utf-8") as samples,
            (output / "sdk-startups.jsonl").open("x", encoding="utf-8") as startup_log,
        ):
            phase, index = "start", 0
            set_diagnostic_handler(
                lambda record: (
                    event(startup_log, "native", record=record)
                    if record["kind"] in {"connection", "protocol_hello"}
                    else None
                )
            )

            def sample(label, **timing):
                event(samples, "resources", phase=phase, index=index, label=label, **timing, **sampler.sample())

            async def sampling():
                while True:
                    sample("periodic")
                    await asyncio.sleep(args.sample_interval)

            async def task(step, sequence):
                nonlocal expected
                started = time.monotonic()
                payload = f"CUA {nonce}-{expected + 1:06d} 中文"
                receipt = await step(payload) if args.image_free else await step()
                expected += 1
                actual = await asyncio.to_thread(
                    wait_state,
                    state_path,
                    lambda s: s.get(counter, 0) >= expected,
                    2,
                )
                ok = receipt["status"] in {"dispatched", "verified"} and actual.get(counter) == expected
                if args.image_free:
                    ok = (
                        ok and actual.get("text") == receipt["readback"] == payload
                        and actual.get("paste_count") == expected and actual.get("bystander") == "untouched"
                        and actual.get("pending") == actual.get("returns") == 0
                        and not actual.get("fixture_error") and actual.get("focused")
                    )
                else:
                    ok = ok and actual.get("bystander_clicks") == 0
                event(
                    progress,
                    "effect",
                    phase=phase,
                    index=index,
                    step=sequence,
                    expected=expected,
                    actual=actual,
                    receipt=receipt,
                    seconds=time.monotonic() - started,
                    passed=ok,
                )
                if not ok:
                    raise RuntimeError(f"The owned fixture did not confirm exactly one effect for {phase}/{sequence}")

            async def exercise(duration):
                close_at = None
                try:
                    async with open_session(args.entry, exe.stem, title, output / "mcp-logs", foreground) as step:
                        started = time.monotonic()
                        sequence = 0
                        try:
                            while True:
                                sequence += 1
                                await task(step, sequence)
                                elapsed = time.monotonic() - started
                                sample("after_effect", phase_seconds=elapsed)
                                if elapsed >= duration:
                                    break
                                await asyncio.sleep(min(args.interval, duration - elapsed))
                            return elapsed
                        finally:
                            close_at = time.monotonic()
                finally:
                    if close_at is not None:
                        await wait_owned_exit(sampler, steady_pids, close_at + 5)
                        elapsed_close = time.monotonic() - close_at
                        sample("after_close", cleanup_seconds=elapsed_close)
                        event(
                            progress,
                            "cleanup",
                            phase=phase,
                            index=index,
                            seconds=elapsed_close,
                            passed=elapsed_close <= 5,
                        )
                        if elapsed_close > 5:
                            raise TimeoutError(f"{phase}/{index} session cleanup exceeded five seconds")

            event(
                progress,
                "fixture",
                host_pid=host_pid,
                startup_pids=sorted(startup_pids),
                fixture_pid=fixture_pid,
                fixture_sha256=sha256(exe),
                title=title,
                foreground=foreground,
            )
            sample("initial")
            observer = asyncio.create_task(sampling())
            try:
                phase = "cycle"
                for index in range(1, args.sessions + 1):
                    event(progress, "begin", phase=phase, index=index)
                    await exercise(0)
                if args.soak_seconds:
                    phase, index = "soak", 0
                    event(progress, "begin", phase=phase, index=index)
                    elapsed = await exercise(args.soak_seconds)
                    event(progress, "soak_complete", seconds=elapsed)
            finally:
                observer.cancel()
                try:
                    await observer
                except asyncio.CancelledError:
                    pass
                finally:
                    set_diagnostic_handler(None)
    finally:
        await fixture_scope.aclose()
        await wait_owned_exit(sampler, startup_pids, time.monotonic() + 5)


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def trend(values):
    """Compare five median bins. Report sustained rises for evidence review."""
    if len(values) < 15:
        return {"samples": len(values), "status": "insufficient"}
    bins = [median(values[i * len(values) // 5 : (i + 1) * len(values) // 5]) for i in range(5)]
    changes = [right - left for left, right in pairwise(bins)]
    increasing = bins[-1] > bins[0] and sum(change > 0 for change in changes) >= 3
    return {
        "samples": len(values),
        "median_bins": bins,
        "delta": bins[-1] - bins[0],
        "status": "increasing" if increasing else "no_sustained_rise",
    }


def summarize(output):
    events = records(output / "progress.jsonl")
    samples = records(output / "samples.jsonl")
    cases = [
        (
            "cycle",
            [row for row in samples if row["phase"] == "cycle" and row["label"] == "after_close" and row["index"] > 10],
        ),
        (
            "soak",
            [
                row
                for row in samples
                if row["phase"] == "soak" and row["label"] == "after_effect" and row.get("phase_seconds", 0) >= 300
            ],
        ),
    ]
    result = {
        "trends": {},
        "native_images_seen": sorted(
            {
                process.get("name")
                for row in samples
                for process in row["processes"]
                if process.get("name", "").startswith("computer-use-")
            }
        ),
    }
    for name, phase in cases:
        if not phase:
            continue
        complete = [
            row for row in phase if all(metric in process for metric in RESOURCES for process in row["processes"])
        ]
        summaries = {
            metric: trend([sum(p[metric] for p in row["processes"]) for row in complete]) for metric in RESOURCES
        }
        summaries["process_count"] = trend([len(row["job_pids"]) for row in phase])
        by_identity = [{(p["pid"], p["creation_filetime"]): p for p in row["processes"]} for row in complete]
        persistent = set.intersection(*(set(row) for row in by_identity)) if by_identity else set()
        individual = [
            {
                "pid": identity[0],
                "creation_filetime": identity[1],
                "name": by_identity[0][identity]["name"],
                "metrics": {metric: trend([row[identity][metric] for row in by_identity]) for metric in RESOURCES},
            }
            for identity in sorted(persistent)
        ]
        result["trends"][name] = {
            "total_samples": len(phase),
            "usable_samples": len(complete),
            "metrics": summaries,
            "persistent_processes": individual,
        }
    result["cycles_passed"] = sum(
        row["kind"] == "cleanup" and row["phase"] == "cycle" and row["passed"] for row in events
    )
    result["soak_seconds"] = max((row["seconds"] for row in events if row["kind"] == "soak_complete"), default=0)
    result["confirmed_effects"] = sum(row["kind"] == "effect" and row["passed"] for row in events)
    return result


def run(args):
    output = args.output
    before = manifest()
    (output / "manifest.json").write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--entry",
        args.entry,
        "--sessions",
        str(args.sessions),
        "--soak-seconds",
        str(args.soak_seconds),
        "--interval",
        str(args.interval),
        "--sample-interval",
        str(args.sample_interval),
        "--output",
        str(output),
        "--worker",
    ]
    if args.image_free:
        command.append("--image-free")
    job, process = WindowsJob(), None
    report = {
        "command": command,
        "entry": args.entry,
        "requested_cycles": args.sessions,
        "requested_soak_seconds": args.soak_seconds,
        "profile": "image_free_driver_worker" if args.image_free else "visual",
        "natural_job_exit": False,
        "job_census_anomalies": [],
    }
    if args.image_free and args.entry == "sdk":
        report["blocking_executor_workers"] = IMAGE_FREE_SDK_BLOCKING_WORKERS

    def record_census(source, facts):
        report["job_census_anomalies"].append({"source": source, "utc_unix": time.time(), **facts})

    try:
        with (
            JobCensus(job.handle, record_census=lambda facts: record_census("worker", facts)) as census,
            (output / "worker.log").open("x", encoding="utf-8") as log,
        ):
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=census.worker_environment(),
                creationflags=subprocess.CREATE_NO_WINDOW | 4,
            )
            report["job_root_pid"] = process.pid
            job.assign(process.pid)
            resume_suspended(process.pid)
            progress = output / "progress.jsonl"
            previous, last = None, time.monotonic()
            deadline = last + args.sessions * 100 + args.soak_seconds + 120
            while process.poll() is None:
                current = progress.stat().st_size if progress.exists() else None
                now = time.monotonic()
                if current != previous:
                    previous, last = current, now
                if now - last > 90 or now > deadline:
                    raise TimeoutError("The isolated workload stopped reporting completed steps")
                census.handle_request()
            report["worker_exit"] = process.returncode
        until = time.monotonic() + 5
        sampler = Sampler(record_census=lambda facts: record_census("final_exit", facts))
        while remaining := sampler.pids(job.handle):
            if time.monotonic() >= until:
                raise TimeoutError(f"Job still contains owned PIDs after worker exit: {sorted(remaining)}")
            time.sleep(0.025)
        report["natural_job_exit"] = True
    except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    finally:
        # Kill-on-close is the final guard, after the natural cleanup verdict.
        job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    after = manifest()
    (output / "manifest-after.json").write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")
    report["stable_source_and_native"] = all(before[key] == after[key] for key in ("head", "sources", "native_sha256"))
    try:
        report.update(summarize(output))
    except (OSError, ValueError) as error:
        report.update(evidence_error=str(error), trends={}, cycles_passed=0, soak_seconds=0, confirmed_effects=0)
    metrics = [metric for phase in report["trends"].values() for metric in phase["metrics"].values()]
    metrics.extend(
        metric
        for phase in report["trends"].values()
        for process in phase["persistent_processes"]
        for metric in process["metrics"].values()
    )
    trend_clear = (
        set(report["trends"]) == {"cycle", "soak"}
        and all(metric["status"] == "no_sustained_rise" for metric in metrics)
        and all(phase["total_samples"] == phase["usable_samples"] for phase in report["trends"].values())
    )
    report["configured_run_passed"] = (
        report.get("worker_exit") == 0
        and report["natural_job_exit"]
        and report["stable_source_and_native"]
        and report["cycles_passed"] == args.sessions
        and report["soak_seconds"] >= args.soak_seconds
        and (not args.image_free or "computer-use-capture.exe" not in report.get("native_images_seen", ()))
    )
    report["full_profile"] = not args.image_free and args.sessions >= 100 and args.soak_seconds >= 7200
    report["wlife_009_passed"] = report["configured_run_passed"] and report["full_profile"] and trend_clear
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return report["wlife_009_passed"] if report["full_profile"] else report["configured_run_passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", choices=("sdk", "mcp"), default="sdk")
    parser.add_argument("--sessions", type=int, default=100)
    parser.add_argument("--soak-seconds", type=int, help="Defaults to 7200 for visual, 0 for image-free")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--sample-interval", type=float, default=0.5)
    parser.add_argument("--image-free", action="store_true", help="Use raw paste and trees for driver/worker evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.soak_seconds is None:
        args.soak_seconds = 0 if args.image_free else 7200
    if os.name != "nt":
        parser.error("This workflow requires an unlocked Windows test desktop")
    if (
        min(args.sessions, args.soak_seconds) < 0
        or not (args.sessions or args.soak_seconds)
        or not 0.1 <= args.interval <= 30
        or not 0.1 <= args.sample_interval <= 10
    ):
        parser.error("Set a nonzero workload and bounded sampling and repetition intervals")
    args.output = args.output.resolve()
    if args.worker:
        asyncio.run(worker(args))
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        raise SystemExit(0 if run(args) else 1)


if __name__ == "__main__":
    main()
