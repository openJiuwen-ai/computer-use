# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize diagnostic evidence and persist the server-owned execution journal.

IPC and JSONL logs share dumps(). Pipeline traces flush together within one
loop turn; externally visible events and metadata flush all preceding traces.
"""

import json
import os
import re
import sys
import uuid
from asyncio import get_running_loop
from datetime import datetime, timezone
from pathlib import Path

from computer_use.diagnostics import SDK_VERSION
from computer_use.json import dumps as legacyDumps

_SURROGATES = re.compile(r"[\ud800-\udfff]")


def _trace_field(value):
    if value is None or type(value) in (bool, int):
        return True
    if isinstance(value, str):
        return value.isascii() or _SURROGATES.search(value) is None
    return isinstance(value, (list, tuple)) and all(
        isinstance(part, str) and (part.isascii() or _SURROGATES.search(part) is None) for part in value
    )


def dumps(value):
    event = value.get("diagnostic", value) if isinstance(value, dict) else None
    if (
        isinstance(event, dict)
        and event.get("kind") == "pipeline"
        and all(isinstance(key, str) and not key.isdecimal() and _trace_field(item) for key, item in event.items())
        and (
            event is value
            or all(
                isinstance(key, str) and not key.isdecimal() and (key == "diagnostic" or _trace_field(item))
                for key, item in value.items()
            )
        )
    ):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            pass
    if (
        isinstance(event, dict)
        and event.get("kind") == "command_end"
        and event.get("method") == "inspect"
        and isinstance(event.get("result"), dict)
        and event["result"].get("kind") == "observation"
    ):
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
        self._pending, self._pending_bytes, self._scheduled = [], 0, None
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
                    desktopMode=os.environ.get("COMPUTER_USE_DESKTOP", "exclusive"),
                )
            )
        except Exception as error:
            self._error = str(error)

    def record(self, event):
        if self._error is not None:
            return
        try:
            line = dumps({"timestamp": now(), **event}) + "\n"
            if event.get("kind") == "pipeline":
                try:
                    loop = get_running_loop()
                except RuntimeError:
                    self.flush(line)
                    return
                self._pending.append(line)
                self._pending_bytes += len(line.encode("utf-8", "backslashreplace"))
                if self._pending_bytes >= 64 * 1024:
                    self.flush()
                elif self._scheduled is None:
                    self._scheduled = loop.call_soon(self.flush)
            else:
                # Progress, outputs and execution_end publish every prior trace.
                self.flush(line)
        except Exception as error:
            self._error = str(error)

    def flush(self, line=""):
        if self._scheduled is not None:
            self._scheduled.cancel()
            self._scheduled = None
        payload = "".join(self._pending) + line
        self._pending.clear()
        self._pending_bytes = 0
        if not payload or self._error is not None:
            return
        try:
            with self.path.open("a", encoding="utf-8", errors="backslashreplace") as file:
                file.write(payload)
        except Exception as error:
            self._error = str(error)

    def metadata(self):
        self.flush()
        return dict(logError=self._error) if self._error is not None else dict(logPath=str(self.path))
