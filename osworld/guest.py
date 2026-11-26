# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Blocking client for the OSWorld guest server (/execute, /screen_size, /screenshot).

Moved from osworld/cua_bridge/desktop.py unchanged; its window model went with the agent-core cua backend.
"""
from __future__ import annotations

from typing import Tuple

import requests

_HTTP_GRACE_SECONDS = 5


class Guest:
    """Blocking client for the OSWorld guest server."""

    def __init__(self, http_server: str) -> None:
        self.http_server = http_server.rstrip("/")

    def sh(self, script: str, timeout: int = 30) -> str:
        """Run a bash script in the VM with DISPLAY set; return stdout.

        Raises on transport failure or a non-zero exit.
        """
        payload = {"command": ["bash", "-c", "export DISPLAY=:0\n" + script], "shell": False, "timeout": timeout}
        resp = requests.post(f"{self.http_server}/execute", json=payload, timeout=timeout + _HTTP_GRACE_SECONDS)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or body.get("status") != "success":
            raise RuntimeError(f"guest /execute failed: {body.get('message') or resp.text[:300]}")
        if int(body.get("returncode", -1)) != 0:
            raise RuntimeError(f"guest command exited {body.get('returncode')}: {(body.get('error') or '')[:300]}")
        return body.get("output") or ""

    def screen_size(self) -> Tuple[int, int]:
        resp = requests.post(f"{self.http_server}/screen_size", timeout=30)
        resp.raise_for_status()
        body = resp.json()
        return int(body["width"]), int(body["height"])

    def screenshot(self) -> bytes:
        """Full-screen PNG."""
        resp = requests.get(f"{self.http_server}/screenshot", timeout=30)
        resp.raise_for_status()
        if not resp.content:
            raise RuntimeError("the guest returned an empty screenshot")
        return resp.content


__all__ = ["Guest"]
