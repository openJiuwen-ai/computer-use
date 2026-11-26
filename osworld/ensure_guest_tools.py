#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Preflight: make sure the guest has the programs the cua backend drives.

run_task_sdk.py runs it as run_task_verified.py did; this repo's agent uses xdotool through the environment
snapshot's open-window list (osworld/env_snapshot.py), which picks the skills the agent gets.

osworld/cua_bridge/backend.py finds and raises windows with wmctrl, xprop and
xwininfo, acts through pyautogui, and types non-ASCII text with xdotool,
because pyautogui types ASCII only. This repo's V2 image ships all of them.
The upstream OSWorld-Verified image does not ship xdotool (88/120 Verified
runs on 10-05 hit "xdotool: command not found" under the old MCP bridge).

xdotool is installed offline first, from the two jammy .debs in
osworld/guest_debs (sha256 checked against Ubuntu's Packages index, 2026-10-09):
apt-get over the network timed out at 120 s with three guests booting at once
(chrome 82bc8d6a, 2026-10-09) and aborted the run. Anything still missing is
then apt-installed, and checked again. A window tool still missing afterwards
is a harness fault, so the caller aborts instead of handing the agent a broken
toolset. A missing xdotool only fails non-ASCII type_text (with a ❌ the agent
sees), so it is reported and the run goes ahead.
"""
from __future__ import annotations

import base64
import logging
import os

from osworld.guest import Guest

logger = logging.getLogger(__name__)

DEBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guest_debs")
# Install order: the library before the program that needs it.
XDOTOOL_DEBS = ("libxdo3_3.20160805.1-4_amd64.deb", "xdotool_3.20160805.1-4_amd64.deb")
# Needed only for non-ASCII type_text; its absence does not abort a run.
OPTIONAL_TOOLS = {"xdotool"}

# Command the backend runs -> the apt package that provides it.
GUEST_TOOLS = {
    "xdotool": "xdotool",
    "wmctrl": "wmctrl",
    "xprop": "x11-utils",
    "xwininfo": "x11-utils",
}
INSTALL_TIMEOUT_S = 300

_CHECK = "; ".join(f"command -v {cmd} >/dev/null || echo {cmd}" for cmd in GUEST_TOOLS)


def missing_guest_tools(guest: Guest) -> list[str]:
    """Names of the backend's guest programs that are not installed."""
    return guest.sh(_CHECK + "; true").split()


def install_xdotool_offline(guest: Guest, password: str) -> None:
    """Copy the bundled .debs into the guest and dpkg -i them; no network, about a second."""
    paths = []
    for name in XDOTOOL_DEBS:
        with open(os.path.join(DEBS_DIR, name), "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")  # 28-56 kB: well under one argv string's limit
        paths.append(f"/tmp/{name}")
        guest.sh(f"printf %s {b64} | base64 -d > /tmp/{name}")
    guest.sh(f"printf '%s\\n' '{password}' | sudo -S dpkg -i {' '.join(paths)}", timeout=60)


def ensure_guest_tools(guest: Guest, password: str) -> list[str]:
    """Install whatever is missing; return what the run still lacks afterwards (empty = ready)."""
    missing = missing_guest_tools(guest)
    if "xdotool" in missing:
        logger.info("Guest is missing xdotool; installing the bundled .debs...")
        try:
            install_xdotool_offline(guest, password)
        except (RuntimeError, OSError) as exc:
            logger.warning("Offline xdotool install failed: %s", exc)
        missing = missing_guest_tools(guest)
    if not missing:
        return []
    packages = sorted({GUEST_TOOLS[name] for name in missing})
    logger.info("Guest is missing %s; installing %s...", ", ".join(missing), " ".join(packages))
    try:
        guest.sh(f"printf '%s\\n' '{password}' | sudo -S env DEBIAN_FRONTEND=noninteractive "
                 f"apt-get install -y -q {' '.join(packages)}", timeout=INSTALL_TIMEOUT_S)
    except RuntimeError as exc:
        logger.warning("Guest tool install failed: %s", exc)
    still = missing_guest_tools(guest)
    if OPTIONAL_TOOLS & set(still):
        logger.warning("xdotool is still missing; type_text will fail on non-ASCII text.")
        still = [t for t in still if t not in OPTIONAL_TOOLS]
    return still
