# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Conversation records with screenshots stored separately from JSONL."""

import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class Journal:
    def __init__(self, root: Path, *, secrets=()):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.directory = root / f"{stamp}-{uuid4().hex[:8]}"
        self.directory.mkdir(parents=True, mode=0o700)
        self._secrets = tuple(secret for secret in secrets if secret)
        self._image = 0
        self._stream = (self.directory / "events.jsonl").open("x", encoding="utf-8")
        (self.directory / "events.jsonl").chmod(0o600)

    def redact(self, text):
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def snapshot(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            if value.get("type") == "image" and "data" in value:
                self._image += 1
                name = f"image-{self._image:04d}.png"
                target = self.directory / name
                target.write_bytes(base64.b64decode(value["data"], validate=True))
                target.chmod(0o600)
                return {key: content for key, content in value.items() if key != "data"} | {"file": name}
            return {
                key: "[REDACTED]"
                if re.fullmatch(r"(?i)(api[_-]?key|authorization|password|secret|token)", str(key))
                else self.snapshot(content)
                for key, content in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.snapshot(item) for item in value]
        return value

    def record(self, kind, data):
        event = {"time": datetime.now(UTC).isoformat(), "kind": kind, "data": self.snapshot(data)}
        self._stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self._stream.flush()

    def close(self):
        self._stream.close()
