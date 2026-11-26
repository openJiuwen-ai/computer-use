#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build the agent environment that run_task_sdk.py unpacks into every OSWorld VM.

Starts an OSWorld-Verified VM (the same image the tasks run on), uploads this repo's committed HEAD and
build_agent_env.sh, runs it there, and downloads its output to osworld/vm_env/dist/: cu-agent-env.tar.gz (the
Python 3.12 venv with this repo's SDK, its [agent] extra and PyGObject, plus uv's Python), the SDK wheel and the
PyGObject/pycairo wheels. Run it again after changing the SDK or the agent; it needs network access in the VM and
takes about 10 minutes. The VM's disk is mounted read-only, so nothing persists there.

    $OSWORLD_VERIFIED_ROOT/.venv/Scripts/python.exe osworld/vm_env/build_agent_env.py
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
logger = logging.getLogger(__name__)

BUILD = "/home/user/cu-build"
DIST = os.path.join(HERE, "dist")
DEADLINE_S = 45 * 60


def upload(server: str, local: str, remote: str) -> None:
    with open(local, "rb") as f:
        resp = requests.post(f"{server}/setup/upload", data={"file_path": remote},
                             files={"file_data": (os.path.basename(local), f)}, timeout=600)
    resp.raise_for_status()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    # Appended: neither osworld nor desktop_env is installed in the OSWorld venv, so the checkouts are what import.
    sys.path.append(REPO_ROOT)
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    verified_root = os.path.abspath(os.environ.get("OSWORLD_VERIFIED_ROOT", ""))
    if not os.environ.get("OSWORLD_VERIFIED_ROOT") or not os.path.isdir(os.path.join(verified_root, "desktop_env")):
        raise SystemExit(
            "OSWORLD_VERIFIED_ROOT must name your OSWorld-Verified checkout (the folder holding desktop_env/).")
    sys.path.append(verified_root)
    os.chdir(verified_root)  # the docker provider keeps the VM image in ./docker_vm_data

    from desktop_env.desktop_env import DesktopEnv
    from osworld.ensure_kvm import ensure_kvm
    from osworld.guest import Guest

    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is not on PATH")
    os.makedirs(DIST, exist_ok=True)
    src = os.path.join(tempfile.mkdtemp(), "src.tar.gz")
    subprocess.run([git, "-C", REPO_ROOT, "archive", "--format=tar.gz", "-o", src, "HEAD"], check=True)
    if not ensure_kvm():
        raise SystemExit("/dev/kvm is not available to Docker")
    exists = os.path.exists

    def exists_or_kvm(p):
        return True if p == "/dev/kvm" else exists(p)

    os.path.exists = exists_or_kvm
    env = DesktopEnv(provider_name="docker", headless=True, action_space="pyautogui", client_password="password")
    try:
        server = f"http://localhost:{env.server_port}"
        guest = Guest(server)
        guest.sh(f"mkdir -p {BUILD}")
        upload(server, src, f"{BUILD}/src.tar.gz")
        upload(server, os.path.join(HERE, "build_agent_env.sh"), f"{BUILD}/build_agent_env.sh")
        guest.sh(f"cd {BUILD} && sed -i 's/\\r$//' build_agent_env.sh && "
                 f"{{ setsid bash build_agent_env.sh > {BUILD}/build.log 2>&1 < /dev/null & }}")
        started, done = time.monotonic(), ""
        while not done and time.monotonic() - started < DEADLINE_S:
            time.sleep(20)
            done = guest.sh(f"cat {BUILD}/DONE 2>/dev/null; true").strip()
            last = guest.sh(f"tail -1 {BUILD}/build.log 2>/dev/null; true").strip()[:120]
            logger.info("[%5.1f min] %s", (time.monotonic() - started) / 60, last)
        log = guest.sh(f"cat {BUILD}/build.log; true", timeout=60)
        with open(os.path.join(DIST, "build.log"), "w", encoding="utf-8") as f:
            f.write(log)
        if done != "0":
            raise SystemExit(f"build failed (DONE={done or 'timeout'}); see {os.path.join(DIST, 'build.log')}")
        for name in guest.sh(f"ls {BUILD}/out").split():
            resp = requests.post(f"{server}/file", data={"file_path": f"{BUILD}/out/{name}"}, timeout=600)
            resp.raise_for_status()
            with open(os.path.join(DIST, name), "wb") as f:
                f.write(resp.content)
            logger.info("downloaded %s (%d bytes)", name, len(resp.content))
    finally:
        env.close()


if __name__ == "__main__":
    main()
