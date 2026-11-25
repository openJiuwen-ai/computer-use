#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run this repo's computer_use agent on one OSWorld-Verified task, inside the task's VM.

This process (your OSWorld checkout's .venv) owns DesktopEnv: the task's setup, the VM and the grader, all
OSWorld-Verified's own. The agent is this repo's ComputerUseAgent with the OSWorld tools and rails
(osworld/vm_agent.py), running in the VM on this repo's SDK, from the agent environment archive that
osworld/vm_env/build_agent_env.py builds once (Python 3.12 venv with the SDK wheel, its [agent] extra and PyGObject
for the VM's libraries) and this osworld package, uploaded each run. The SDK acts on the VM's own desktop.

    $OSWORLD_VERIFIED_ROOT/.venv/Scripts/python.exe osworld/run_task_sdk.py --domain chrome --task-id <uuid>
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shlex
import sys
import tarfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)  # the osworld package

from dotenv import load_dotenv

load_dotenv(os.path.join(REPO_ROOT, ".env"))  # model keys, OSWORLD_VERIFIED_ROOT
VERIFIED_ROOT = os.path.abspath(os.environ.get("OSWORLD_VERIFIED_ROOT", ""))
if not os.environ.get("OSWORLD_VERIFIED_ROOT") or not os.path.isdir(os.path.join(VERIFIED_ROOT, "desktop_env")):
    raise SystemExit("OSWORLD_VERIFIED_ROOT must name your OSWorld-Verified checkout (the folder holding "
                     "desktop_env/); set it in the environment or in .env (see osworld/docs/JIUWEN_SETUP.md).")
sys.path.insert(0, VERIFIED_ROOT)
# The docker provider keeps the VM image in ./docker_vm_data.
os.chdir(VERIFIED_ROOT)

import requests

from desktop_env.controllers import setup as verified_setup
from desktop_env.desktop_env import DesktopEnv
from osworld.guest import Guest
from osworld.ensure_guest_tools import ensure_guest_tools
from osworld.ensure_kvm import ensure_kvm
from osworld.proxy import proxy_problem
from osworld.verified_patches import slow_scripted_waits, tolerate_setup_tempdir_cleanup

tolerate_setup_tempdir_cleanup(verified_setup)
slow_scripted_waits(DesktopEnv, verified_setup.SetupController)

VERIFIED_VM_PASSWORD = "password"  # Verified's docker image user password (DesktopEnv's default)
DEFAULT_ARCHIVE = os.path.join(REPO_ROOT, "osworld", "vm_env", "dist", "cu-agent-env.tar.gz")
VM_HOME = "/home/user"
VM_RUN = f"{VM_HOME}/cu-run"  # the agent's config, instruction, log and exit code
VM_ARCHIVE = f"{VM_HOME}/cu-agent-env.tar.gz"
VM_PACKAGE = f"{VM_RUN}/pkg"  # this osworld package, on the agent's PYTHONPATH
AGENT_PYTHON = f"{VM_HOME}/cu-agent/bin/python"
INFEASIBLE_MARKER = "infeasible.json"  # osworld.tools.infeasible.MARKER_NAME (that module needs openjiuwen)
# Files of the agent's results dir in the VM copied back to the run's results dir.
VM_RESULT_FILES = ("trajectory.jsonl", "events.jsonl", "env_snapshot.txt", INFEASIBLE_MARKER,
                   "integrity_blocks.jsonl", "gate.json", "gate_trajectory.jsonl")
# Not shipped to the VM: records, tests, build outputs, the agent-core runner's guest .debs.
_PACKAGE_SKIP = {"docs", "tests", "vm_env", "guest_debs", "task_lists", "__pycache__"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--env-archive", default=DEFAULT_ARCHIVE, help="The agent environment for the VM.")
    parser.add_argument("--max-iterations", type=int, default=0, help="Agent step cap; 0 = the agent's default.")
    parser.add_argument("--time-budget", type=float, default=1800.0, help="Agent wall-clock seconds; 0 = no limit.")
    parser.add_argument("--boot-wait-seconds", type=float, default=60.0)
    parser.add_argument("--proxy", action=argparse.BooleanOptionalAction, default=True,
                        help="Tasks marked proxy: true go through the residential proxy in PROXY_CONFIG_FILE.")
    parser.add_argument("--max-gui-steps", type=int, default=100,
                        help="Stop the run after this many GUI actions (leaderboard step budget); 0 = no cap.")
    parser.add_argument("--autosave-every", type=int, default=6,
                        help="Remind the model to save after this many GUI edits in one window; 0 = off.")
    parser.add_argument("--gate", action=argparse.BooleanOptionalAction, default=True,
                        help="Run the read-only pre-task check (skills, feasibility) before the agent.")
    parser.add_argument("--gate-binding", action=argparse.BooleanOptionalAction, default=True,
                        help="With --gate: an INFEASIBLE verdict with evidence ends the task without the agent.")
    return parser.parse_args()


def upload(server: str, local: str, remote: str) -> None:
    with open(local, "rb") as f:
        resp = requests.post(f"{server}/setup/upload", data={"file_path": remote},
                             files={"file_data": (os.path.basename(local), f)}, timeout=600)
    resp.raise_for_status()


def write_vm_file(guest: Guest, path: str, text: str) -> None:
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    guest.sh(f"mkdir -p {shlex.quote(os.path.dirname(path))} && printf %s {b64} | base64 -d > {shlex.quote(path)}")


def package_archive() -> bytes:
    """This osworld package as a tar.gz, without what the agent does not import."""
    root = os.path.dirname(os.path.abspath(__file__))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _PACKAGE_SKIP]
            for name in files:
                if name.endswith((".py", ".md")):
                    path = os.path.join(folder, name)
                    tar.add(path, arcname=os.path.join("osworld", os.path.relpath(path, root)).replace(os.sep, "/"))
    return buf.getvalue()


def agent_config(args: argparse.Namespace) -> dict:
    """computer_use_agent's model configuration, OpenAI-compatible through OpenRouter."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set. Add it to .env (see osworld/docs/JIUWEN_SETUP.md).")
    config = {"model": os.environ.get("MODEL_NAME") or "anthropic/claude-opus-5", "provider": "OpenAI",
              "api_base": os.environ.get("OPENROUTER_API_BASE") or "https://openrouter.ai/api/v1", "api_key": key,
              "allow_global_input": True, "pause_on_user_input": False}
    if args.max_iterations:
        config["max_iterations"] = args.max_iterations
    return config


def run_agent(guest: Guest, instruction: str, args: argparse.Namespace, results_dir: str) -> str:
    """Start the agent in the VM, wait for it; return 'completed', 'failed (exit N)' or 'timeout'."""
    write_vm_file(guest, f"{VM_RUN}/config.json", json.dumps(agent_config(args)))
    write_vm_file(guest, f"{VM_RUN}/instruction.txt", instruction)
    # Detached from /execute's shell, which must return now. The agent inherits the desktop session's display and
    # D-Bus; COMPUTER_USE_PYTHON is the venv's Python, which has PyGObject for the VM's AT-SPI.
    guest.sh(
        f"cd {VM_RUN} && rm -f exit_code && {{ setsid bash -c "
        + shlex.quote(
            f"export DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus "
            f"COMPUTER_USE_PYTHON={AGENT_PYTHON} PYTHONIOENCODING=utf-8 PYTHONPATH={VM_PACKAGE} "
            f"OSWORLD_CLIENT_PASSWORD={VERIFIED_VM_PASSWORD}"
            + (f" OPENJIUWEN_BENCHMARK_INTEGRITY_MODE={shlex.quote(os.environ['OPENJIUWEN_BENCHMARK_INTEGRITY_MODE'])}"
               if os.environ.get("OPENJIUWEN_BENCHMARK_INTEGRITY_MODE") else "")
            + f"; {AGENT_PYTHON} -m osworld.vm_agent --config {VM_RUN}/config.json "
            f"--instruction-file {VM_RUN}/instruction.txt --results-dir {VM_RUN}/results "
            f"--max-iterations {args.max_iterations} --max-gui-steps {args.max_gui_steps} "
            f"--autosave-every {args.autosave_every}"
            + ("" if args.gate else " --no-gate") + ("" if args.gate_binding else " --no-gate-binding")
            + f" > {VM_RUN}/agent.log 2>&1; "
            f"echo $? > {VM_RUN}/exit_code")
        + " > /dev/null 2>&1 < /dev/null & }")
    started, status = time.monotonic(), None
    while status is None:
        time.sleep(5)
        try:
            code = guest.sh(f"cat {VM_RUN}/exit_code 2>/dev/null; true").strip()
        except (RuntimeError, requests.RequestException) as exc:
            # The guest server also serves the agent's own calls (inspect_file's /run_python), so a status check can
            # go unanswered for a while; that says nothing about the agent. Ask again, within the time budget.
            print(f"Agent status check failed, retrying: {exc}", flush=True)
            code = ""
        if code:
            status = "completed" if code == "0" else f"failed (exit {code})"
        elif args.time_budget and time.monotonic() - started > args.time_budget:
            guest.sh("pkill -f 'osworld.vm_agent'; true")
            status = "timeout"
    try:
        log = guest.sh(f"cat {VM_RUN}/agent.log", timeout=60)
    except RuntimeError as exc:
        log = f"(agent.log unavailable: {exc})"
    with open(os.path.join(results_dir, "agent.log"), "w", encoding="utf-8") as f:
        f.write(log)
    for name in VM_RESULT_FILES:
        try:
            text = guest.sh(f"cat {VM_RUN}/results/{name}", timeout=120)
        except RuntimeError:
            continue  # not written by this run
        with open(os.path.join(results_dir, name), "w", encoding="utf-8") as f:
            f.write(text)
    print(f"Agent {status} after {time.monotonic() - started:.0f}s; log: {os.path.join(results_dir, 'agent.log')}",
          flush=True)
    return status


def main() -> None:
    args = parse_args()
    if not os.path.isfile(args.env_archive):
        raise SystemExit(f"{args.env_archive} not found; build it with osworld/vm_env/build_agent_env.py.")
    task_path = os.path.join(VERIFIED_ROOT, "evaluation_examples", "examples", args.domain, f"{args.task_id}.json")
    with open(task_path, encoding="utf-8") as f:
        example = json.load(f)
    instruction = example["instruction"]
    print(f"Task {args.domain}/{args.task_id} (OSWorld-Verified): {instruction}", flush=True)
    if not ensure_kvm():
        raise SystemExit("Aborting: /dev/kvm is not available to Docker.")
    # Verified's docker provider tests os.path.exists("/dev/kvm") on the HOST, which is never true on Windows;
    # ensure_kvm() just proved the Docker daemon has it.
    exists = os.path.exists
    os.path.exists = lambda p: True if p == "/dev/kvm" else exists(p)
    # --proxy: tasks marked "proxy": true get the residential proxy from PROXY_CONFIG_FILE (tinyproxy in the guest,
    # Chrome started with --proxy-server); others run direct (osworld/proxy.py).
    if args.proxy and proxy_problem(os.getcwd()):
        raise SystemExit(proxy_problem(os.getcwd()))
    env = DesktopEnv(provider_name="docker", headless=True, action_space="pyautogui",
                     client_password=VERIFIED_VM_PASSWORD, enable_proxy=args.proxy)
    try:
        server = f"http://localhost:{env.server_port}"
        print(f"Container up. server_port={env.server_port}; watch: http://localhost:{env.vnc_port}/vnc.html",
              flush=True)
        env.reset(task_config=example)
        print(f"Waiting {args.boot_wait_seconds:.0f}s for the guest desktop to settle...", flush=True)
        time.sleep(args.boot_wait_seconds)
        guest = Guest(server)
        # The Verified image lacks xdotool, which the environment snapshot's window list runs on.
        still_missing = ensure_guest_tools(guest, VERIFIED_VM_PASSWORD)
        if still_missing:
            raise SystemExit(f"Aborting: guest is missing {', '.join(still_missing)} after install.")
        upload(server, args.env_archive, VM_ARCHIVE)
        guest.sh(f"tar -xzf {VM_ARCHIVE} -C {VM_HOME} && rm -f {VM_ARCHIVE}", timeout=300)
        package = os.path.join(os.path.dirname(os.path.abspath(args.env_archive)), "osworld-package.tar.gz")
        with open(package, "wb") as f:
            f.write(package_archive())
        upload(server, package, f"{VM_RUN}/osworld-package.tar.gz")
        guest.sh(f"mkdir -p {VM_PACKAGE} && tar -xzf {VM_RUN}/osworld-package.tar.gz -C {VM_PACKAGE}")
        results_dir = os.path.join(REPO_ROOT, "results", "sdk_agent", args.run_name or f"{args.domain}_{args.task_id[:8]}")
        os.makedirs(results_dir, exist_ok=True)
        started = time.monotonic()
        for name in VM_RESULT_FILES:  # a rerun into the same directory starts clean
            if os.path.exists(os.path.join(results_dir, name)):
                os.remove(os.path.join(results_dir, name))
        status = run_agent(guest, instruction, args, results_dir)
        declared_infeasible = os.path.isfile(os.path.join(results_dir, INFEASIBLE_MARKER))
        if declared_infeasible:
            # OSWorld scores an infeasible task 1 only when the env's last recorded action is FAIL.
            env.step("FAIL", pause=0)
            print("Agent declared the task infeasible; recorded FAIL.", flush=True)
        print("Evaluating...", flush=True)
        score = env.evaluate()
        print(f"RESULT score={score} agent={status}", flush=True)
        record = {"task_id": args.task_id, "domain": args.domain, "agent": "computer_use_agent",
                  "model": os.environ.get("MODEL_NAME") or "anthropic/claude-opus-5", "score": score,
                  "agent_status": status, "agent_seconds": round(time.monotonic() - started),
                  "declared_infeasible": declared_infeasible, "max_gui_steps": args.max_gui_steps,
                  "proxy": args.proxy, "gate": args.gate, "gate_binding": args.gate_binding,
                  "env_archive": os.path.basename(args.env_archive), "benchmark": "osworld-verified",
                  "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with open(os.path.join(results_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, default=str)
    finally:
        env.close()


if __name__ == "__main__":
    main()
