#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Preflight: make sure Docker Desktop's Linux VM has /dev/kvm before a run.

The WSL kernel builds KVM as a module (CONFIG_KVM_INTEL=m) and nothing loads
it after Docker Desktop / WSL restarts. Without /dev/kvm the docker provider
silently falls back to unaccelerated QEMU, the guest never boots in time and
every task dies with "VM failed to become ready" (sweeps 0929/0930).

This loads the module when /dev/kvm is missing and reports whether it is there.
Only Windows (Docker Desktop on WSL) is handled; elsewhere it is a no-op.

Usage::

    uv run --no-sync python osworld/ensure_kvm.py     # exit 0 = /dev/kvm present
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

DISTRO = "docker-desktop"


def _wsl(*cmd: str, root: bool = False) -> int:
    args = ["wsl", "-d", DISTRO] + (["-u", "root"] if root else []) + ["-e", *cmd]
    try:
        return subprocess.run(args, capture_output=True, timeout=60).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def _kvm_present() -> bool:
    return _wsl("test", "-c", "/dev/kvm") == 0


def ensure_kvm() -> bool:
    """Return True when /dev/kvm is available to Docker (or the check does not apply)."""
    if os.name != "nt":
        return True
    if _kvm_present():
        logger.info("[kvm] /dev/kvm present")
        return True
    logger.info("[kvm] /dev/kvm missing in the docker-desktop VM; loading the kvm module...")
    _wsl("sh", "-c", "modprobe kvm_intel || modprobe kvm_amd", root=True)
    if _kvm_present():
        logger.info("[kvm] /dev/kvm loaded")
        return True
    logger.error("[kvm] ERROR: /dev/kvm still missing. Is Docker Desktop running? Try: "
                 "wsl -d docker-desktop -u root -e modprobe kvm_intel")
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    sys.exit(0 if ensure_kvm() else 1)
