# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run repeatable SDK/MCP desktop probes serially and retain every attempt."""

import argparse
import asyncio
import ctypes
import hashlib
import json
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path

import computer_use
from computer_use.paths import native_driver
from computer_use_mcp.windows_worker import WindowsJob, resume_suspended

MANUAL = Path(__file__).resolve().parent
ROOT = MANUAL.parents[1]
CASES = {
    "sdk-close": ("windows_close_workflow.py",),
    "sdk-last-close": ("windows_close_workflow.py", "--last"),
    "sdk-edit": ("windows_text_workflow.py", "--case", "selection", "--case", "replace"),
    "sdk-browser": ("windows_browser_workflow.py",),
    "sdk-calculator": ("windows_calculator_workflow.py",),
    "sdk-terminal": ("windows_terminal_workflow.py", "--route", "element-insert"),
    "sdk-continuous": ("windows_continuous_workflow.py",),
    "sdk-slow-paste": ("windows_paste_workflow.py", "--raw", "--delay-ms", "3200", "--anonymous-consumer"),
    "sdk-visual": ("windows_visual_workflow.py",),
    "sdk-blocked-uia": ("windows_visual_workflow.py", "--blocking-provider"),
    "mcp-close": ("windows_close_workflow.py", "--mcp"),
    "mcp-last-close": ("windows_close_workflow.py", "--mcp", "--last"),
    "mcp-edit": ("windows_text_workflow.py", "--mcp", "--case", "selection", "--case", "replace"),
    "mcp-visual": ("windows_visual_workflow.py", "--mcp"),
    "mcp-browser": ("windows_browser_workflow.py", "--mcp"),
    "mcp-calculator": ("windows_calculator_workflow.py", "--mcp"),
    "mcp-terminal": ("windows_terminal_workflow.py", "--mcp", "--route", "element-insert"),
    "mcp-continuous": ("windows_continuous_workflow.py", "--mcp"),
    "sdk-mcp-contention": ("windows_concurrent_input_workflow.py", "--case", "contention"),
    "sdk-mcp-crash-recovery": ("windows_concurrent_input_workflow.py", "--case", "crash-recovery"),
    "mcp-blocked-uia": ("windows_visual_workflow.py", "--blocking-provider", "--mcp"),
    "mcp-slow-paste": (
        "windows_paste_workflow.py", "--mcp", "--delay-ms", "3200", "--anonymous-consumer",
        "--repeat", "2", "--reset-after-first",
    ),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def manifest() -> dict:
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = {name: sha256(ROOT / name) for name in sorted(set(names)) if name and (ROOT / name).is_file()}
    driver = Path(native_driver()["command"])
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sources": hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest(),
        "files": files,
        "python": sys.version,
        "executable": sys.executable,
        "module": computer_use.__file__,
        "platform": platform.version(),
        "display_count": user32.GetSystemMetrics(80),  # SM_CMONITORS
        "system_dpi": user32.GetDpiForSystem(),
        "driver": str(driver),
        "native_sha256": {
            path.name: sha256(path)
            for path in (driver, driver.with_name("computer-use-capture.exe"), driver.with_name("Interop.NativeUIA.dll"))
        },
    }


async def wait_for_cleanup(job: WindowsJob) -> None:
    async with asyncio.timeout(8):
        await job.wait_empty()


def attempt(case: str, output: Path, timeout: int) -> dict:
    name, *options = CASES[case]
    nonce = uuid.uuid4().hex[:10]
    evidence = output / f"{case}-{nonce}.json"
    log = evidence.with_suffix(".log")
    command = [sys.executable, str(MANUAL / name), *options, "--output", str(evidence)]
    report = {"case": case, "attempt": nonce, "command": command, "evidence": str(evidence), "log": str(log)}
    started = time.monotonic()
    job = WindowsJob()
    process = None
    try:
        with log.open("xb") as stream:
            process = subprocess.Popen(
                command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW | 4,
            )
            job.assign(process.pid)
            resume_suspended(process.pid)
            report["exit"] = process.wait(timeout=timeout)
        asyncio.run(wait_for_cleanup(job))
        report["clean_exit"] = True
    except (OSError, TimeoutError, subprocess.SubprocessError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    report["seconds"] = round(time.monotonic() - started, 3)
    report["pass"] = report.get("exit") == 0 and report.get("clean_exit") is True and evidence.is_file()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=CASES, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout < 1:
        parser.error("repeat and timeout must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    initial = manifest()
    results = []
    with (output / "attempts.jsonl").open("x", encoding="utf-8") as evidence:
        (output / "manifest.json").write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
        for _ in range(args.repeat):
            for case in args.case:
                result = attempt(case, output, args.timeout)
                results.append(result)
                evidence.write(json.dumps(result) + "\n")
                evidence.flush()
                sys.stdout.write(f"{case} {result['attempt']} {result['pass']} ({result['seconds']}s)\n")
                sys.stdout.flush()
    final = manifest()
    unchanged = all(final[key] == initial[key] for key in ("head", "sources", "native_sha256"))
    totals = {
        case: {"passed": sum(r["pass"] for r in results if r["case"] == case),
               "attempted": sum(r["case"] == case for r in results)}
        for case in args.case
    }
    summary = {"stable_source_and_native": unchanged, "totals": totals}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(summary) + "\n")
    raise SystemExit(0 if unchanged and all(result["pass"] for result in results) else 1)


if __name__ == "__main__":
    main()
