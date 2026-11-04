# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-worker readiness, bounded stderr and process-exit evidence.

The native lifetime pipe/Job remains the session's cleanup barrier. Stderr is
diagnostic evidence only: an inherited stderr handle cannot hold that barrier
open, and arbitrary stderr/source lines are never copied into the journal.
"""

import asyncio
import builtins
import re

STDERR_BYTES = 16 * 1024
STDERR_SETTLE_SECONDS = 0.1
EXIT_SETTLE_SECONDS = 0.25
READY_ENV = "COMPUTER_USE_WORKER_READY"

_missing = re.compile(r"^ModuleNotFoundError: No module named '([A-Za-z_][\w.]{0,255})'$", re.MULTILINE)
_import = re.compile(
    r"^ImportError: cannot import name '([A-Za-z_]\w{0,127})' from '([A-Za-z_][\w.]{0,255})'", re.MULTILINE
)
_exception = re.compile(r"^([A-Za-z_]\w{0,79})(?::|$)", re.MULTILINE)
_frame = re.compile(
    r'^  File "[^"\r\n]*/([A-Za-z_]\w{0,127}\.py)", line ([0-9]{1,9}), in ([A-Za-z_]\w{0,127}|<module>)$'
)


class WorkerStartupFailure(RuntimeError):
    """Already reduced to a safe, bounded startup diagnosis."""


class WorkerLifecycle:
    def __init__(self, generation, timeout, track, record):
        loop = asyncio.get_running_loop()
        self.generation, self.deadline = generation, loop.time() + timeout
        self.track, self.record = track, record
        self.child = self.exited = self._drain = None
        self.ready = loop.create_future()
        self.started = self.failed = False
        self._tail = bytearray()
        self._bytes = 0
        self._complete = False

    def remaining(self):
        return max(0, self.deadline - asyncio.get_running_loop().time())

    def attach(self, child):
        self.child = child
        self._drain = self.track(self._read_stderr())
        self.exited = self.track(self._wait())

    def stop_startup(self):
        if not self.ready.done():
            self.ready.set_result(False)

    def mark_ready(self):
        if self.ready.done():
            raise ValueError("Execution worker returned duplicate or late readiness.")
        self.started = True
        self.ready.set_result(True)

    async def _read_stderr(self):
        if self.child.stderr is None:
            self._complete = True
            return
        while chunk := await self.child.stderr.read(4096):
            self._bytes += len(chunk)
            self._tail.extend(chunk)
            if len(self._tail) > STDERR_BYTES:
                del self._tail[:-STDERR_BYTES]
        self._complete = True

    async def _finish_stderr(self):
        done, _ = await asyncio.wait({self._drain}, timeout=STDERR_SETTLE_SECONDS)
        if not done:
            # Process.wait can otherwise wait for a descendant's inherited
            # stderr handle, even after this process has an exit status.
            self.child.stderr._transport.close()
            self._drain.cancel()
        await asyncio.gather(self._drain, return_exceptions=True)

    async def _wait(self):
        waiting = asyncio.create_task(self.child.wait())
        try:
            while self.child.returncode is None and not waiting.done():
                await asyncio.wait({waiting}, timeout=0.025)
            await self._finish_stderr()
            status = await waiting
            self.record(
                dict(
                    kind="worker_exit",
                    workerGeneration=self.generation,
                    workerPid=self.child.pid,
                    returncode=status,
                    ready=self.started,
                    stderrBytes=self._bytes,
                    stderrTruncated=self._bytes > len(self._tail),
                    stderrComplete=self._complete,
                    **self.diagnostic(),
                )
            )
            return status
        finally:
            if not waiting.done():
                waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    def diagnostic(self):
        value = self._tail.decode("utf-8", "replace")
        summary = None
        missing = list(_missing.finditer(value))
        imports = list(_import.finditer(value))
        if missing and not self.started:
            summary = "ModuleNotFoundError: No module named '" + missing[-1][1] + "'"
        elif imports and not self.started:
            summary = "ImportError: cannot import name '" + imports[-1][1] + "' from '" + imports[-1][2] + "'"
        else:
            for match in _exception.finditer(value):
                candidate = getattr(builtins, match[1], None)
                if isinstance(candidate, type) and issubclass(candidate, BaseException):
                    summary = match[1]
        # Keep useful bounded stack locations, without source code, arbitrary
        # exception messages, account paths or raw stderr (which may be input).
        frames = []
        for line in value.splitlines():
            if match := _frame.fullmatch(line):
                frames.append(dict(file=match[1], line=int(match[2]), function=match[3]))
        return dict(stderrSummary=summary, stderrFrames=frames[-32:])

    async def settle(self, timeout=EXIT_SETTLE_SECONDS):
        if self.exited is not None:
            await asyncio.wait({self.exited}, timeout=max(0, timeout))

    def failure(self, detail=None, *, sent=False):
        status = self.child.returncode if self.child is not None else None
        phase = "startup failed" if not self.started else "execution interrupted" if sent else "stopped between calls"
        state = (
            "process was not started"
            if self.child is None
            else f"exited (status {status})"
            if status is not None
            else "exit is not yet confirmed"
        )
        summary = self.diagnostic()["stderrSummary"]
        reason = f"Execution worker {phase}; {state}."
        if summary:
            reason += " Cause: " + summary + "."
        if detail:
            reason += " " + detail
        if not self.started:
            reason += " This call's code was not sent. Check the worker installation and session log before continuing."
        elif sent:
            reason += " Actions may already have executed; do not replay them automatically. Read again before acting."
        else:
            reason += " This call's code was not sent. Recreate variables and read again before acting."
        return reason
