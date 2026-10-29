# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize diagnostic evidence and persist the server-owned execution journal.

IPC and JSONL logs share dumps(). Importing this module performs no file I/O;
only constructing Journal creates a private log. This module does not own UI
presentation, history cursors, worker transport, or execution state.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from computer_use.diagnostics import SDK_VERSION
from computer_use.json import dumps as legacyDumps


def _is_native_observation(event):
    if not isinstance(event, dict):
        return False
    result = event.get("result")
    if not isinstance(result, dict):
        return False
    return (
        event.get("kind") == "command_end" and event.get("method") == "inspect" and result.get("kind") == "observation"
    )


def dumps(value):
    """Encode validated native evidence directly, retaining the general-record fallback."""
    event = value.get("diagnostic", value) if isinstance(value, dict) else None
    if _is_native_observation(event):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            # Keep the diagnostic callback tolerant of non-native/custom
            # records rather than losing evidence to an optimization.
            pass
    return legacyDumps(value)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Journal:
    def __init__(self):
        directory = Path(os.environ.get("COMPUTER_USE_LOG_DIR", str(Path.home() / ".computer-use/logs")))
        self.path = directory / (now().replace(":", "-").replace(".", "-") + "-" + str(uuid.uuid4()) + ".jsonl")
        self._error = None
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            self.record(
                dict(
                    kind="session",
                    sdkVersion=SDK_VERSION,
                    platform=sys.platform,
                    python=sys.version.split()[0],
                    desktopMode=os.environ.get("COMPUTER_USE_DESKTOP", "shared"),
                )
            )
        except Exception as error:
            self._error = str(error)

    def record(self, event):
        if self._error is not None:
            return
        try:
            with self.path.open("a", encoding="utf-8", errors="backslashreplace") as file:
                file.write(dumps({"timestamp": now(), **event}) + "\n")
        except Exception as error:
            self._error = str(error)

    def metadata(self):
        return dict(logError=self._error) if self._error is not None else dict(logPath=str(self.path))
