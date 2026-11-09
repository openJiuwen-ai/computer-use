# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One episode's evidence, using the existing Journal and SDK callbacks.

This is a recorder, not a task executor: no retries, input, or target bindings.
Raw evidence is private; public summaries contain digests, not task text.
"""

import asyncio
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Literal

from computer_use.errors import ComputerUseError
from computer_use.session.events import observation_delivered
from computer_use.session.observation import observation_document, peek_observation_text
from computer_use_mcp.diagnostics import Journal


class EnvironmentBlocked(RuntimeError):
    """An external prerequisite is unavailable; this episode still counts."""


@dataclass(frozen=True)
class Check:
    value: Literal["TRUE", "FALSE", "UNKNOWN"]
    scope: str
    evidence: dict

    def __post_init__(self):
        if self.value not in ("TRUE", "FALSE", "UNKNOWN") or not self.scope:
            raise ValueError("A check requires a three-valued result and explicit scope")


class Episode:
    def __init__(self, directory: Path, metadata: dict, *, clock=monotonic):
        self.id = str(uuid.uuid4())
        self.directory = directory / self.id
        self.directory.mkdir(parents=True, mode=0o700)
        self.journal = Journal(self.directory)
        self.clock, self.started = clock, clock()
        self.metadata = metadata
        self.current_step = None
        self.steps = []
        self.failure = None
        self.finished = False
        self.record("episode_start", metadata=metadata)
        self.require_log()

    def record(self, kind, **fields):
        self.journal.record(
            {
                "kind": kind,
                "schema_version": 1,
                "episode_id": self.id,
                "elapsed_s": self.clock() - self.started,
                "step": self.current_step,
                **fields,
            }
        )

    def require_log(self):
        if "logError" in self.journal.metadata():
            raise RuntimeError("Episode evidence could not be persisted; stop before the next action")

    def diagnostic(self, event):
        self.record("diagnostic", diagnostic=event)

    def action(self, receipt):
        self.record("action_receipt", receipt=receipt)

    def observation(self, event):
        kind = event["kind"]
        if kind == "action-observation":
            result = event["result"]
            self.record(
                "related_observation",
                status=result.observation_status,
                error=result.observation_error,
                window_lifecycle=result.window_lifecycle,
            )
            return
        if kind != "observed":
            self.record("observation_lifecycle", change=kind)
            if kind == "before-write":
                self.require_log()
            return

        # Preserve prepared evidence without rendering the tree or logging live
        # owners and element bindings.
        captured = event["observation"]
        state = captured["state"]
        row = {
            "scope": captured["scope"],
            "view": captured["view"],
            "element_refs": list(captured["refs"]),
            "observed": captured.get("observed"),
        }
        materialized = peek_observation_text(state)
        if materialized is not None:
            row["text"] = materialized
        else:
            row["text_materialized"] = False
            document = captured.get("document") or observation_document(state)
            summary = getattr(document, "summary_data", None)
            if summary is not None:
                row["presentation"] = summary()
        self.record("observation", **row)
        if "logError" not in self.journal.metadata():
            observation_delivered(state)

    @contextmanager
    def step(self, name):
        if self.finished or self.current_step is not None or self.failure is not None:
            raise RuntimeError("An episode cannot overlap, resume failed steps, or run after completion")
        self.require_log()
        self.current_step = name
        started = self.clock()
        row = {"name": name}
        try:
            self.record("step_start")
            self.require_log()
            yield
        except BaseException as error:
            row.update(status="error", error_type=type(error).__name__)
            self.failed(error)
            raise
        else:
            row["status"] = "returned"
        finally:
            row["duration_s"] = self.clock() - started
            self.record("step_end", result=row)
            self.steps.append(row)
            self.current_step = None

    def failed(self, error):
        # Generic exceptions/cancellation do not prove that no input was sent.
        result = {
            "outcome": "blocked"
            if isinstance(error, EnvironmentBlocked)
            else "cancelled"
            if isinstance(error, asyncio.CancelledError)
            else "timeout"
            if isinstance(error, TimeoutError)
            else "error",
            "error_type": type(error).__name__,
            "effect": error.effect if isinstance(error, ComputerUseError) else "unknown",
            "effect_scope": "failed_operation",
            "step": self.current_step,
        }
        if self.failure is None:
            self.failure = result
            self.record(
                "episode_error",
                failure=result,
                error={
                    "detail": str(error),
                    "notes": getattr(error, "__notes__", []),
                    "code": getattr(error, "code", None),
                },
            )

    def finish(self, acceptance: Check, *, reset_duration_s: float, cleanup: dict) -> dict:
        if self.finished or self.current_step is not None:
            raise RuntimeError("Cannot finish an active step or finish twice")
        outcome = (
            self.failure["outcome"]
            if self.failure
            else {
                "TRUE": "success",
                "FALSE": "failure",
                "UNKNOWN": "unknown",
            }[acceptance.value]
        )
        elapsed = self.clock() - self.started
        cleanup_duration = cleanup.get("duration_s", 0.0)
        if reset_duration_s < 0 or cleanup_duration < 0 or reset_duration_s + cleanup_duration > elapsed:
            raise ValueError("Reset and cleanup durations must fit inside this episode")
        result = {
            "schema_version": 1,
            "episode_id": self.id,
            "metadata": self.metadata,
            "outcome": outcome,
            "acceptance": asdict(acceptance),
            "failure": self.failure,
            "steps": self.steps,
            "duration_s": elapsed - reset_duration_s - cleanup_duration,
            "wall_duration_s": elapsed,
            "reset_duration_s": reset_duration_s,
            "cleanup": cleanup,
            "model_usage": {"input_tokens": None, "output_tokens": None, "cost_usd": None},
        }
        self.record("episode_end", result=result)
        result["journal"] = self.journal.metadata()
        result["recording_complete"] = "logError" not in result["journal"]
        self.finished = True
        # A missing summary remains an incomplete episode; consumers enumerate
        # directories, not just successful summaries. Never overwrite evidence.
        fd = os.open(self.directory / "summary.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        return result


def enumerate_episodes(directory: Path) -> list[dict]:
    """Keep failed, interrupted and corrupt episodes in the denominator."""
    results = []
    for folder in sorted(path for path in directory.iterdir() if path.is_dir()):
        try:
            summary = json.loads((folder / "summary.json").read_text())
            if summary.get("schema_version") != 1 or summary.get("episode_id") != folder.name:
                raise ValueError("Unsupported or mismatched episode summary")
        except (OSError, ValueError, AttributeError):
            summary = {"episode_id": folder.name, "outcome": "incomplete", "recording_complete": False}
        results.append(summary)
    return results
