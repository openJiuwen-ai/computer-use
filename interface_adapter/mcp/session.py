# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parent-process supervisor: worker lifetime, execution watchdog, history gate, and journal."""

import asyncio
import logging
import os
import socket
import sys

from computer_use.native.preview import is_preview_event
from computer_use.session.operations import eager

from .history import HISTORY_KEY, HistoryGate
from .diagnostics import Journal
from .output import OutputBuffer, is_output
from .worker_lifecycle import EXIT_SETTLE_SECONDS, READY_ENV, WorkerLifecycle, WorkerStartupFailure
from .worker_transport import FRAMED_PROTOCOL, read_message, write_message

CLEANUP_TIMEOUT_SECONDS = 5

RESET_NOTICE = (
    "Execution session cleared. Recreate App variables and read again. Actions "
    "may already have executed; do not replay them "
    "automatically."
)

_LOG = logging.getLogger(__name__)


def execution_error(message):
    return dict(content=[dict(type="text", text=message)], isError=True)


class ExecutionSession:
    def __init__(self, timeout_ms=45000, on_preview=None, demo_foreground=False):
        self.demo_foreground = demo_foreground
        self.timeout_ms, self.on_preview = timeout_ms, on_preview
        self._child = self._pending = self._retiring = self._resetting = self._exited = None
        self._writer = self._reader = None
        self._closed = self._starting = False
        self._start_task = None
        self._worker = None
        self._generation = 0
        self._next_id = 1
        self._journal, self._history = Journal(), HistoryGate()
        self._tasks = set()

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @eager
    async def execute(self, code, signal=None, history=None, title=None, on_progress=None):
        if self._closed:
            return execution_error("Execution session is closed.")
        if self._resetting is not None:
            result = await asyncio.shield(self._resetting)
            return result if result.get("isError") else await self.execute(code, signal, history, title, on_progress)
        if self._retiring is not None:
            return execution_error(
                ("Native cleanup is still pending. Call computer_use_reset before continuing; no code was executed.")
            )
        if self._pending is not None or self._starting:
            return execution_error("Another call is running; await it before starting the next.")
        if signal is not None and signal.is_set():
            return execution_error("Call cancelled before execution.")
        self._starting = True
        generation = self._generation
        starting = self._track(self._start())
        self._start_task = starting

        def started(_):
            if self._start_task is starting:
                self._start_task = None
                self._starting = False

        starting.add_done_callback(started)
        startup_cancel = None
        if signal is not None:

            async def cancel_startup():
                await signal.wait()
                self.reset("Call cancelled before code was sent.")

            startup_cancel = self._track(cancel_startup())
        try:
            await asyncio.shield(starting)
            worker = self._worker
            if (
                generation == self._generation
                and worker is not None
                and (worker.failed or self._writer is None or self._writer.is_closing())
            ):
                worker.failed = True
                await worker.settle(min(EXIT_SETTLE_SECONDS, worker.remaining()))
                raise WorkerStartupFailure(worker.failure("The worker channel closed before this call was sent."))
        except asyncio.CancelledError:
            await asyncio.shield(self.reset("Call cancelled."))
            raise
        except Exception as error:
            worker = self._worker
            reason = (
                str(error)
                if isinstance(error, WorkerStartupFailure)
                else worker.failure(f"Cannot start worker ({type(error).__name__}).")
                if worker is not None
                else f"Execution worker startup failed ({type(error).__name__}). This call's code was not sent."
            )
            if generation == self._generation:
                reset = await self.reset(reason)
            else:
                reset = await asyncio.shield(self._resetting) if self._resetting is not None else None
            result = execution_error(
                reason + ("\n" + reset["content"][0]["text"] if reset is not None and reset.get("isError") else "")
            )
            result["_meta"] = {"computer-use": self._journal.metadata()}
            return result
        finally:
            if startup_cancel is not None:
                startup_cancel.cancel()
        if generation != self._generation or self._closed:
            reset = await asyncio.shield(self._resetting) if self._resetting is not None else None
            reason = (
                "Call cancelled before code was sent. "
                if signal is not None and signal.is_set()
                else "Execution was reset before code was sent. "
            )
            return execution_error(
                reason
                + "No code was executed for this call; recreate variables before continuing."
                + ("\n" + reset["content"][0]["text"] if reset is not None and reset.get("isError") else "")
            )
        if signal is not None and signal.is_set():
            return execution_error("Call cancelled before execution.")
        id_ = self._next_id
        self._next_id += 1
        policy = self._history.begin(history)
        self._journal.record(
            dict(
                kind="execution_start",
                executionId=id_,
                title=title,
                code=code,
                history=policy,
            )
        )
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pending = dict(
            id=id_,
            title=title,
            startedAt=loop.time(),
            output=OutputBuffer(),
            future=future,
            timer=None,
            cancel=None,
            sent=False,
            deadline=loop.time() + self.timeout_ms / 1000,
        )

        def stalled():
            self.reset(f"Execution exceeded {self.timeout_ms} ms without native command progress.")

        def progress():
            if pending["timer"] is not None:
                pending["timer"].cancel()
            pending["timer"] = loop.call_later(self.timeout_ms / 1000, stalled)
            pending["deadline"] = loop.time() + self.timeout_ms / 1000
            try:
                if on_progress is not None:
                    result = on_progress()
                    if hasattr(result, "__await__"):
                        self._track(result)
            except Exception:
                _LOG.debug("Worker cleanup or notification did not complete.", exc_info=True)

        def cleanup():
            if pending["timer"] is not None:
                pending["timer"].cancel()
            if pending["cancel"] is not None:
                pending["cancel"].cancel()

        pending.update(progress=progress, cleanup=cleanup)
        pending["timer"] = loop.call_later(self.timeout_ms / 1000, stalled)
        self._pending = pending
        if signal is not None:

            async def cancel():
                await signal.wait()
                self.reset("Call cancelled.")

            pending["cancel"] = self._track(cancel())
        try:
            try:
                # A write may partially dispatch before raising. Preserve unknown
                # effects on send failure; never replay the request automatically.
                pending["sent"] = True
                write_message(
                    self._writer,
                    dict(
                        code=code,
                        executionId=id_,
                        history=policy,
                    ),
                )
                await self._writer.drain()
            except Exception as error:
                self.reset(f"Cannot send code: {error}")
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Cancellation while draining an already-written request has the
            # same unknown effects and retirement barrier as awaiting its result.
            await asyncio.shield(self.reset("Call cancelled."))
            raise

    async def _start(self):
        if self._child is not None:
            if self._worker is not None and self._worker.failed:
                worker = self._worker
                await worker.settle()
                raise WorkerStartupFailure(worker.failure("The worker channel closed."))
            return
        worker = WorkerLifecycle(self._generation, self.timeout_ms / 1000, self._track, self._journal.record)
        self._worker = worker
        env = dict(
            os.environ,
            COMPUTER_USE_REMOTE_PREVIEW="1" if self.on_preview is not None else "",
            COMPUTER_USE_DEMO_FOREGROUND="1" if self.demo_foreground else "0",
            COMPUTER_USE_LIFETIME_FD="4",
            COMPUTER_USE_WORKER_PROTOCOL=FRAMED_PROTOCOL,
            **{READY_ENV: "1"},
        )
        if os.name == "nt":
            from .windows_worker import WorkerInterpreterOutsideJob, start_windows_worker

            def spawned(child, job, listener):
                self._child = child
                worker.attach(child)

                async def exited():
                    try:
                        await worker.exited
                        await job.wait_empty()
                    finally:
                        job.close()
                        await listener.wait_closed()

                self._exited = self._track(exited())

            try:
                child, self._reader, self._writer, job, listener = await start_windows_worker(
                    env, stderr=asyncio.subprocess.PIPE, on_spawned=spawned, timeout=worker.remaining()
                )
            except Exception as error:
                await worker.settle(min(EXIT_SETTLE_SECONDS, worker.remaining()))
                worker.stop_startup()
                # Only a typed launcher refusal admits its fixed safe reason.
                # Unknown exceptions may carry code, credentials or provider
                # values in __str__; retain their type, never their body.
                detail = (
                    f"{WorkerInterpreterOutsideJob.code}: {WorkerInterpreterOutsideJob.detail}"
                    if isinstance(error, WorkerInterpreterOutsideJob)
                    else f"The worker could not connect ({type(error).__name__})."
                )
                raise WorkerStartupFailure(worker.failure(detail)) from None

            self._track(self._receive(child, self._reader, worker))
            await self._wait_ready(worker)
            return
        parent_value, child_socket = socket.socketpair()
        life_read, life_write = os.pipe()
        try:
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "computer_use.native.driver_exec",
                str(child_socket.fileno()),
                str(life_write),
                sys.executable,
                "-m",
                "computer_use_mcp.runtime",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                pass_fds=(child_socket.fileno(), life_write),
                env=env,
            )
        except BaseException:
            parent_value.close()
            os.close(life_read)
            raise
        finally:
            child_socket.close()
            os.close(life_write)
        self._child = child
        worker.attach(child)
        parent_value.setblocking(False)
        self._reader, self._writer = await asyncio.open_connection(sock=parent_value, limit=12 * 1024 * 1024)
        lifetime = asyncio.StreamReader()
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(lifetime), os.fdopen(life_read, "rb", buffering=0)
        )

        async def exited():
            try:
                await worker.exited
                while await lifetime.read(65536):
                    pass
            finally:
                transport.close()

        self._exited = self._track(exited())
        self._track(self._receive(child, self._reader, worker))
        await self._wait_ready(worker)

    async def _wait_ready(self, worker):
        if worker.generation != self._generation:
            return
        try:
            ready = await asyncio.wait_for(asyncio.shield(worker.ready), worker.remaining())
        except TimeoutError:
            worker.failed = True
            raise WorkerStartupFailure(worker.failure("Timed out waiting for worker readiness.")) from None
        if not ready and worker.generation == self._generation:
            raise WorkerStartupFailure(worker.failure())

    async def _receive(self, child, reader, worker):
        error = None
        try:
            while True:
                message = await read_message(reader)
                if message is None:
                    break
                if self._child is not child:
                    continue
                if not isinstance(message, dict):
                    raise ValueError("Execution process returned invalid output.")
                if message.get("ready") is True:
                    worker.mark_ready()
                elif "preview" in message:
                    if is_preview_event(message["preview"]):
                        try:
                            if self.on_preview is not None:
                                self.on_preview(message["preview"])
                        except Exception:
                            _LOG.debug("Worker cleanup or notification did not complete.", exc_info=True)
                elif "diagnostic" in message:
                    event = message["diagnostic"]
                    if isinstance(event, dict):
                        self._journal.record({**event, "executionId": message.get("executionId")})
                        if (
                            self._pending is not None
                            and message.get("executionId") == self._pending["id"]
                            and event.get("kind") in ("command_start", "command_end")
                        ):
                            self._pending["progress"]()
                elif "output" in message:
                    pending = self._pending
                    if pending is None or message.get("executionId") != pending["id"]:
                        continue
                    if not is_output(message["output"]):
                        raise ValueError("Execution process returned invalid output.")
                    block = message["output"]
                    self._journal.record(
                        dict(
                            kind="output",
                            executionId=pending["id"],
                            content=dict(type="image", mimeType=block["mimeType"])
                            if block["type"] == "image"
                            else block,
                        )
                    )
                    pending["output"].append(block, message.get("mergeText") is True)
                else:
                    if not isinstance(message.get("content"), list) or any(
                        not is_output(block) for block in message["content"]
                    ):
                        raise ValueError("Execution process returned invalid output.")
                    self._finish(message)
        except Exception as failure:
            error = failure
        if self._child is not child:
            return
        worker.failed = True
        pending = self._pending
        sent = pending is not None and pending["sent"]
        deadline = pending["deadline"] if pending is not None else worker.deadline
        remaining = max(0, deadline - asyncio.get_running_loop().time())
        await worker.settle(
            min(EXIT_SETTLE_SECONDS, remaining) if not worker.started or pending else EXIT_SETTLE_SECONDS
        )
        if self._child is not child:
            return
        detail = f"Worker communication failed ({type(error).__name__})." if error is not None else None
        reason = worker.failure(detail, sent=sent)
        self._journal.record(
            dict(
                kind="worker_failure",
                workerGeneration=worker.generation,
                workerPid=child.pid,
                phase="startup" if not worker.started else "execution" if sent else "idle",
                returncode=child.returncode,
                reason=reason,
            )
        )
        worker.stop_startup()
        if worker.started:
            self.reset(reason)

    def _finish(self, result):
        pending, self._pending = self._pending, None
        if pending is None:
            return
        duration = (asyncio.get_running_loop().time() - pending["startedAt"]) * 1000
        self._journal.record(
            dict(
                kind="execution_end",
                executionId=pending["id"],
                durationMs=duration,
                isError=result.get("isError", False),
                content=[
                    dict(type="image", mimeType=block["mimeType"], bytes=len(block["data"]) * 3 // 4)
                    if block["type"] == "image"
                    else block
                    for block in result["content"]
                ],
            )
        )
        result["_meta"] = {
            **result.get("_meta", {}),
            "computer-use": {
                **self._journal.metadata(),
                "executionId": pending["id"],
                "durationMs": duration,
                **({"title": pending["title"]} if pending["title"] else {}),
            },
            HISTORY_KEY: self._history.complete(),
        }
        pending["cleanup"]()
        if not pending["future"].done():
            pending["future"].set_result(result)

    def reset(self, reason="Execution reset requested."):
        if self._resetting is not None:
            return self._resetting
        self._generation += 1
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop_startup()
        starting = self._start_task
        child, self._child = self._child, None
        if child is not None and child.returncode is None:
            try:
                child.kill()
            except ProcessLookupError:
                _LOG.debug("Worker cleanup or notification did not complete.", exc_info=True)
        earlier = self._retiring if self._retiring is not None else self._exited
        self._exited = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._reader = None
        if self._pending is not None:
            self._pending["cleanup"]()

        async def retire():
            if starting is not None:
                try:
                    await asyncio.shield(starting)
                except Exception:
                    _LOG.debug("Worker cleanup or notification did not complete.", exc_info=True)
            # Process creation yields in Python. A reset during that yield must
            # retire the newly created worker before admitting any later call.
            late, self._child = self._child, None
            late_worker, self._worker = self._worker, None
            if late_worker is not None:
                late_worker.stop_startup()
            if late is not None and late.returncode is None:
                try:
                    late.kill()
                except ProcessLookupError:
                    _LOG.debug("Worker cleanup or notification did not complete.", exc_info=True)
            if self._writer is not None:
                self._writer.close()
                self._writer = None
            exited, self._exited = self._exited, None
            if earlier is not None:
                await asyncio.shield(earlier)
            if exited is not None and exited is not earlier:
                await asyncio.shield(exited)
            if worker is not None and worker.exited is not None:
                await asyncio.shield(worker.exited)
            if late_worker is not None and late_worker.exited is not None and late_worker is not worker:
                await asyncio.shield(late_worker.exited)

        retired = self._track(retire())
        self._retiring = retired

        async def reset():
            failure = None
            try:
                if retired is not None:
                    await asyncio.wait_for(asyncio.shield(retired), CLEANUP_TIMEOUT_SECONDS)
            except TimeoutError:
                failure = (
                    "Native cleanup is still pending; capture stop is unconfirmed. Actions may "
                    "already have executed; do not replay them. Retry computer_use_reset before "
                    "continuing."
                )
            except Exception as error:
                failure = str(error)
            if retired is None or retired.done():
                if self._retiring is retired:
                    self._retiring = None
            elif retired is not None:

                def clear(_):
                    if self._retiring is retired:
                        self._retiring = None

                retired.add_done_callback(clear)
            result = execution_error(reason + "\n" + (failure if failure is not None else RESET_NOTICE))
            result["content"] = (self._pending["output"].content.copy() if self._pending is not None else []) + result[
                "content"
            ]
            self._journal.record(dict(kind="execution_reset", reason=reason, complete=failure is None))
            self._finish(result)
            self._history = HistoryGate()
            if failure is not None:
                return execution_error(failure)
            try:
                if self.on_preview is not None:
                    self.on_preview(dict(kind="reset"))
            except Exception:
                _LOG.debug("Worker cleanup or notification did not complete.", exc_info=True)
            return dict(
                content=[
                    dict(
                        type="text",
                        text=(
                            "Execution session reset. Python variables, App/Element handles and "
                            "observations cleared; session capture streams stopped and preview reset "
                            "requested. Apps, documents, files and completed actions are unchanged. The "
                            "next computer_use call starts a fresh "
                            "environment."
                        ),
                    )
                ]
            )

        task = asyncio.create_task(reset())
        self._resetting = task

        def clear(_):
            if self._resetting is task:
                self._resetting = None

        task.add_done_callback(clear)
        return task

    def close(self):
        self._closed = True
        return self.reset("MCP connection closed.")
