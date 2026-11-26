#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run run_task_sdk.py across several OSWorld-Verified tasks (<domain>/<uuid>) in parallel, bounded by
--max-parallel, and print a scoreboard. Cross-platform (stdlib subprocess; no
PowerShell), so it works the same on Windows (Docker Desktop/WSL2) and Linux.
Each run uses your OSWorld checkout's .venv (OSWORLD_VERIFIED_ROOT) and works from that checkout.

Each task gets its own container (the docker provider assigns free ports) and
its own log; each run's env.close() removes only its own container, so there is
NO blanket `docker rm` here -- that would kill running siblings.

Because each guest is a full VM (~4-5 GiB RAM, ~2 CPU cores at peak on the
tasks measured), keep --max-parallel small; on Docker Desktop the WSL2 VM's
memory cap is the real ceiling, on Linux it is host RAM.

    <osworld>/.venv/Scripts/python.exe osworld/run_parallel.py --max-parallel 2 --tag smoke \
        chrome/06fe7178-4491-4589-810f-2e2bc9502122 gimp/045bf3ff-9077-4b86-b483-a1040a949cff
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(REPO_ROOT)
from osworld.proxy import proxy_problem  # noqa: E402

logger = logging.getLogger("osworld.run_parallel")  # named, not __name__: this file runs as a script


def _now() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).astimezone().strftime("%H:%M:%S")


def scrape_score(log_path: str) -> str:
    try:
        text = open(log_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return "log-unreadable"
    # Evaluators return either a bare float ("RESULT score=0.4") or a
    # safety-wrapped dict ("RESULT score={'score': 0.0, 'safety': {...}}");
    # match the numeric score in both forms.
    scores = re.findall(r"RESULT score=(?:\{'score':\s*)?([0-9.]+)", text)
    if scores:
        return scores[-1]
    if "Traceback (most recent call last)" in text:
        return "crashed"
    return "no-result"


def scrape_agent_status(log_path: str) -> str | None:
    """The agent's status from the RESULT line ('completed', 'failed (exit 1)', 'timeout', ...)."""
    try:
        text = open(log_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    statuses = re.findall(r"^RESULT score=.* agent=(.+)$", text, re.MULTILINE)
    return statuses[-1].strip() if statuses else None


def split_spec(spec: str) -> tuple[str | None, str]:
    """'chrome/06fe7178-...' -> ('chrome', '06fe7178-...'); a bare 2.x id '014' -> (None, '014').

    OSWorld 1.0 ids are uuids filed under a domain directory, so they need the domain.
    """
    domain, sep, task_id = spec.partition("/")
    return (domain, task_id) if sep else (None, spec)


def base_name(spec: str) -> str:
    """Results-dir stem: the 2.x id as before, '<domain>_<uuid[:8]>' for a 1.0 task."""
    domain, task_id = split_spec(spec)
    return f"{domain}_{task_id[:8]}" if domain else task_id


def default_model(env_file: str) -> str:
    """MODEL_NAME from the environment or env_file, else Sonnet 5.

    The value is handed to every run as MODEL_NAME, and each run's load_dotenv
    does not override an inherited variable -- so unless .env is read here, a
    MODEL_NAME set only in .env never takes effect (10-04/10-05 sweeps ran
    Sonnet 5 while .env said Opus 5).
    """
    from dotenv import dotenv_values
    value = os.environ.get("MODEL_NAME") or dotenv_values(env_file).get("MODEL_NAME")
    return (value or "anthropic/claude-sonnet-5").strip().strip('"')


def task_cmd(python: str, runner: str, spec: str, run_name: str, args: argparse.Namespace) -> list[str]:
    domain, task_id = split_spec(spec)
    if not domain:
        raise ValueError(f"{spec!r}: an OSWorld-Verified task is <domain>/<uuid>")
    cmd = [python, runner, "--domain", domain, "--task-id", task_id, "--run-name", run_name,
           "--max-iterations", str(args.max_iterations)]
    if args.time_budget is not None:
        cmd += ["--time-budget", str(args.time_budget)]
    if args.max_gui_steps is not None:
        cmd += ["--max-gui-steps", str(args.max_gui_steps)]
    if not getattr(args, "proxy", True):
        cmd.append("--no-proxy")
    return cmd


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task_ids", nargs="+", help="OSWorld-Verified tasks as <domain>/<uuid>.")
    ap.add_argument("--time-budget", type=float, default=None,
                    help="Agent wall-clock seconds per run (run_task_sdk.py's default when omitted).")
    ap.add_argument("--max-parallel", type=int, default=2, help="Concurrent runs (default 2).")
    ap.add_argument("--trials", type=int, default=1,
                    help="Runs per task; each writes results/sdk_agent/<id>_t<k>.")
    ap.add_argument("--tag", default="",
                    help="Suffix for this sweep's run names, e.g. 'sweep0922' -> results/sdk_agent/<id>_sweep0922, "
                         "so a re-run does not overwrite an earlier result of the same task.")
    ap.add_argument("--max-iterations", type=int, default=None,
                    help="0 = the agent's default (the default).")
    ap.add_argument("--proxy", action=argparse.BooleanOptionalAction, default=True,
                    help="Tasks marked proxy: true go through the residential proxy in PROXY_CONFIG_FILE (default on).")
    ap.add_argument("--max-gui-steps", type=int, default=None,
                    help="GUI action budget per run; 0 = no cap (run_task_sdk.py's default, 100, when omitted).")
    ap.add_argument("--model", default=default_model(os.path.join(REPO_ROOT, ".env")),
                    help="Default: MODEL_NAME from the environment, then .env, then anthropic/claude-sonnet-5.")
    ap.add_argument("--log-dir", default=None, help="Default results/sdk_agent/_parallel_logs.")
    args = ap.parse_args()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("osworld").addHandler(handler)
    logging.getLogger("osworld").setLevel(logging.INFO)
    if args.max_iterations is None:
        args.max_iterations = 0
    if args.log_dir is None:
        args.log_dir = os.path.join(REPO_ROOT, "results", "sdk_agent",
                                    "_parallel_logs" + (f"_{args.tag}" if args.tag else ""))
    bad = [t for t in args.task_ids if not split_spec(t)[0]]
    if bad:
        sys.exit(f"OSWorld-Verified tasks are <domain>/<uuid>; got {', '.join(bad)}")

    repo = REPO_ROOT
    os.makedirs(args.log_dir, exist_ok=True)
    env = dict(os.environ)
    env["MODEL_NAME"] = args.model
    # Windows' default cp1252 crashes the evaluator's postconfig on non-ASCII guest output
    # (desktop_env/controllers/setup.py writes it with open(..., "w")).
    env["PYTHONUTF8"] = "1"
    # Verified's docker provider keeps its VM image in ./docker_vm_data, so run from that checkout, with its venv.
    from dotenv import dotenv_values
    task_cwd = os.environ.get("OSWORLD_VERIFIED_ROOT") or dotenv_values(os.path.join(repo, ".env")).get(
        "OSWORLD_VERIFIED_ROOT")
    if not task_cwd or not os.path.isdir(os.path.join(task_cwd, "desktop_env")):
        sys.exit("OSWORLD_VERIFIED_ROOT must name your OSWorld-Verified checkout (the folder holding "
                 "desktop_env/); set it in the environment or in .env.")
    task_cwd = env["OSWORLD_VERIFIED_ROOT"] = os.path.abspath(task_cwd)
    python = os.path.join(task_cwd, ".venv", *(("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")))
    runner = os.path.join(repo, "osworld", "run_task_sdk.py")
    if args.proxy:
        # Every run would stop at the same check; say it once, before any VM starts.
        configured = os.environ.get("PROXY_CONFIG_FILE") or dotenv_values(os.path.join(repo, ".env")).get(
            "PROXY_CONFIG_FILE")
        if configured:
            os.environ["PROXY_CONFIG_FILE"] = env["PROXY_CONFIG_FILE"] = configured
        problem = proxy_problem(task_cwd)
        if problem:
            sys.exit(problem)

    # One entry per run: the task id, plus _<tag> for a named sweep and _t<k> when repeating a task.
    def run_name(task_id: str, k: int) -> str:
        name = base_name(task_id) + (f"_{args.tag}" if args.tag else "")
        return f"{name}_t{k}" if args.trials > 1 else name
    runs = [(t, run_name(t, k)) for t in args.task_ids for k in range(1, args.trials + 1)]
    pending = list(runs)
    running: dict[str, tuple] = {}   # tid -> (task_id, proc, file, log_path, start)
    results: dict[str, tuple] = {}   # tid -> (score, duration_s, returncode)

    logger.info("[%s] %d runs, max-parallel=%s, model=%s", _now(), len(pending), args.max_parallel, args.model)
    while pending or running:
        while pending and len(running) < args.max_parallel:
            # Never run two trials of one task at once: DesktopEnv keeps its
            # setup and evaluator files in cache/<task_id>, shared between them.
            busy = {task for task, *_ in running.values()}
            nxt = next((r for r in pending if r[0] not in busy), None)
            if nxt is None:
                break
            pending.remove(nxt)
            task_id, tid = nxt
            log_path = os.path.join(args.log_dir, f"task_{tid}.log")
            fh = open(log_path, "w", encoding="utf-8")
            cmd = task_cmd(python, runner, task_id, tid, args)
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=task_cwd)
            running[tid] = (task_id, proc, fh, log_path, time.time())
            logger.info("[%s] launched task %s (pid %s) -> %s", _now(), tid, proc.pid, log_path)

        for tid in [t for t, (_task, p, *_) in running.items() if p.poll() is not None]:
            _task, proc, fh, log_path, start = running.pop(tid)
            fh.close()
            score, status = scrape_score(log_path), scrape_agent_status(log_path)
            dur = time.time() - start
            results[tid] = (score, dur, proc.returncode, status)
            logger.info("[%s] done task %s: score=%s agent=%s (%.1f min, exit %s)",
                        _now(), tid, score, status, dur / 60, proc.returncode)

        if pending or running:
            time.sleep(5)

    logger.info("\n=== scoreboard ===")
    for _task_id, tid in runs:
        score, dur, rc, status = results.get(tid, ("missing", 0.0, -1, None))
        logger.info("task %s: score=%s  agent=%s  (%.1f min, exit %s)", tid, score, status, dur / 60, rc)
    unscored = unscored_runs({tid: results.get(tid, ("missing",))[0] for _task, tid in runs})
    faulted = agent_faults({tid: results[tid][3] for _task, tid in runs
                            if tid in results and tid not in unscored})
    if unscored:
        logger.warning("\n%d run(s) produced no score (setup failure or crash): %s",
                       len(unscored), ", ".join(unscored))
    if faulted:
        logger.warning("\n%d run(s) were scored but the agent did not finish normally; their score is "
                       "the VM as it was left, not the agent's answer: %s",
                       len(faulted), ", ".join(f"{tid} ({status})" for tid, status in faulted.items()))
    if unscored or faulted:
        sys.exit(1)


def unscored_runs(scores: dict[str, str]) -> list[str]:
    """Runs whose log has no RESULT line.

    These are harness faults, not zeros: a setup that dies before the agent starts
    must not pass for a finished sweep.
    """
    return [tid for tid, score in scores.items() if not re.fullmatch(r"[0-9.]+", score)]


def agent_faults(statuses: dict[str, str | None]) -> dict[str, str]:
    """Scored runs whose agent crashed, timed out or stopped on an error.

    The VM is graded whatever happened to the agent, so these zeros are harness faults
    until shown otherwise; averaging them in as capability failures understates the agent.
    """
    return {tid: status or "unknown" for tid, status in statuses.items() if status != "completed"}


if __name__ == "__main__":
    main()
