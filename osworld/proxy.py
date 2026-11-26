# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The residential proxy for OSWorld tasks marked ``"proxy": true``.

With --proxy (on by default) run_task_sdk.py starts DesktopEnv with enable_proxy: OSWorld's setup puts the proxy
from PROXY_CONFIG_FILE into the guest (tinyproxy, Chrome started with --proxy-server) for those tasks; the others
run direct. Off, sites such as cars.com and drugs.com refuse a datacenter IP (chrome 82279c77, b070486d).
proxy_problem() checks the config before the VM starts, because DesktopEnv would only fail at a proxy task's setup.
Moved unchanged from the fork agent's run_switches.py. Standard library only: the runner's venv loads it.
"""
from __future__ import annotations

import json
import os

DEFAULT_PROXY_CONFIG_FILE = "evaluation_examples/settings/proxy/dataimpulse.json"  # desktop_env/controllers/setup.py


def proxy_config_path(cwd: str) -> str:
    return os.path.join(cwd, os.environ.get("PROXY_CONFIG_FILE", DEFAULT_PROXY_CONFIG_FILE))


def proxy_hosts(cwd: str) -> list[str]:
    """host:port of every proxy in the config file; raises ValueError when there is no usable one."""
    path = proxy_config_path(cwd)
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the proxy config {path}: {exc}") from exc
    if not isinstance(entries, list) or not all(isinstance(e, dict) and e.get("host") and e.get("port")
                                                for e in entries) or not entries:
        raise ValueError(f"{path} must be a non-empty JSON list of {{host, port, username, password}}")
    # The repo's own template (the default path) passes the shape check but holds your_username / your_password.
    if any(not e.get("username") or not e.get("password") or str(e["username"]).startswith("your_")
           or str(e["password"]).startswith("your_") for e in entries):
        raise ValueError(f"{path} has no real username and password (the repo's template holds placeholders)")
    return sorted(f"{e['host']}:{e['port']}" for e in entries)


def proxy_problem(cwd: str) -> str:
    """"" when --proxy can work from cwd, else why not (DesktopEnv would only fail at a proxy task's setup)."""
    try:
        proxy_hosts(cwd)
    except ValueError as exc:
        return f"--proxy is on by default: {exc}. Set PROXY_CONFIG_FILE, or pass --no-proxy."
    return ""


__all__ = ["DEFAULT_PROXY_CONFIG_FILE", "proxy_config_path", "proxy_hosts", "proxy_problem"]
