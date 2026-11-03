# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Worker-process entry point: persistent Python execution embedding the computer_use SDK."""

import argparse
import asyncio
import base64
import builtins
import hashlib
import io
import os
import pprint
import signal
import sys
from collections import deque
from contextvars import ContextVar

from computer_use import Computer
from computer_use.client.execution import ExecutionCancellation, SessionExecutor, execution_scope
from computer_use.client.host import (
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.client.images import (
    image_delivered,
    image_label,
    image_metadata,
    require_image_delivery,
    reset_image_delivery,
)
from computer_use.client.result_presentation import result_receipt, text_source_label
from computer_use.errors import ComputerUseError
from computer_use.identity import Record, WeakIdentityMap
from computer_use.interaction.read.presentation.actions.presentation import ActionPresentation
from computer_use.native.preview import latest_preview_sink
from computer_use.session.detail_pages import CapturedDetailPage, CapturedDetailText
from computer_use.session.events import observation_delivered
from computer_use.session.observation import (
    CapturedObservationText,
    LazyObservation,
    lazy_observation,
    observation_document,
    peek_observation_text,
)

from .image_delivery import prepare_image, project_document
from .output import ObservationOutput, ObservationPresenter, OutputBuffer
from .worker_lifecycle import READY_ENV
from .worker_transport import WorkerMessageWriter, consume_protocol, read_message

_runs = ContextVar("computer_use_execution", default=None)


class Runtime:
    def __init__(self, send, computer_factory=Computer):
        self.send, self.computer_factory = send, computer_factory
        self.executor = SessionExecutor(loop=asyncio.get_running_loop())
        self.presenter = ObservationPresenter()
        self.command_runs = {}
        self.executions = set()
        self.closed = False
        require_image_delivery()
        set_execution_guard(self.guard)
        set_diagnostic_handler(self.diagnostic)
        set_action_handler(self.action)
        set_text_page_handler(self.text_page)
        set_observation_handler(self.observation)
        if os.environ.get("COMPUTER_USE_REMOTE_PREVIEW") == "1":

            async def preview(event):
                self.send(dict(preview=event))

            set_preview_handler(latest_preview_sink(preview))
        with execution_scope(self.executor):
            self.task_namespace = self.namespace()

    @staticmethod
    def guard():
        run = _runs.get()
        if run is None or not run["active"] or run["phase"] != "code":
            raise RuntimeError(
                "Computer operation belongs to an ended tool execution. Effect: no command "
                "sent. Next: execute operations within a current tool call; do not use "
                "background timers for input."
            )
        pending = asyncio.get_running_loop().create_future()
        run["pending"].add(pending)

        def done():
            run["pending"].discard(pending)
            if not pending.done():
                pending.set_result(None)

        return done

    def diagnostic(self, event):
        if not self.executor.is_owner:
            return self.executor.call(self.diagnostic, event)
        run = _runs.get()
        execution_id = run["id"] if run is not None else None
        trace = event.get("traceId")
        if isinstance(trace, str):
            if event["kind"] == "command_start":
                self.command_runs[trace] = execution_id
                if len(self.command_runs) > 1024:
                    self.command_runs.pop(next(iter(self.command_runs)))
            else:
                execution_id = self.command_runs.get(trace)
        elif event["kind"] == "backend_diagnostic":
            execution_id = None
        self.send(dict(diagnostic=event, **({"executionId": execution_id} if execution_id is not None else {})))

    def emit(self, block, merge_text=False):
        if not self.executor.is_owner:
            return self.executor.call(self.emit, block, merge_text)
        run = _runs.get()
        if run is None or not run["active"]:
            return
        run["output"].append(block, merge_text)
        self.send(dict(output=block, mergeText=merge_text, executionId=run["id"]))

    def flush_observations(self, run):
        def emit(text, observation):
            self.emit(dict(type="text", text=text))
            run["deliveredObservations"].append(observation.get("sourceState", observation["state"]))
            if observation["state"].get("screenshot") is not None:
                run["imageContexts"].set(observation["state"]["screenshot"], True)

        def emit_image(png):
            if not any(image.source is png for image in run["images"]):
                self.print_image(png)

        run["observations"].flush(emit, emit_image)
        self.log_observations(run)

    def action(self, receipt):
        if not self.executor.is_owner:
            return self.executor.call(self.action, receipt)
        run = _runs.get()
        if run is None or not run["active"]:
            return
        self.flush_observations(run)
        self.emit(dict(type="text", text=run["actions"].render(receipt)), True)

    def text_page(self, source, page):
        if not self.executor.is_owner:
            return self.executor.call(self.text_page, source, page)
        run = _runs.get()
        if run is None or not run["active"]:
            return
        self.flush_observations(run)
        self.emit(dict(type="text", text=text_source_label(source)), True)
        run["textPages"].set(page, True)

    def observation(self, event):
        if not self.executor.is_owner:
            return self.executor.call(self.observation, event)
        run = _runs.get()
        if event["kind"] in ("invalidate", "discard-pending"):
            if event["kind"] == "invalidate":
                self.presenter.invalidate(event["source"])
            if run is not None:
                if event["kind"] == "invalidate":
                    run["observations"].invalidate(event["source"])
                else:
                    run["observations"].discard_pending(event["source"])
            return
        if run is None or not run["active"]:
            return
        if event["kind"] == "action-observation":
            result = event["result"]
            failure = isinstance(result, ComputerUseError)
            receipt = None if failure else result_receipt(result)
            data = vars(result) if failure else result
            self.flush_observations(run)
            gap = data["observation_error"]
            target = result.target if failure else receipt["target"] if receipt is not None else "original target"
            outcome = f"Action effect remains {result.effect}" if failure else f"Action remains {result['status']}"
            message = (
                f"Related observation [{data['observation_status']}] for {target}: "
                f"{gap['detail']}\n{outcome}; do not replay it. {gap['next']}"
            )
            if data["window_lifecycle"] == "closed":
                message += "\nThe original Window is closed; its retained targets have not been rebound."
            if data["choices"]:
                message += "\nCurrent windows:\n" + "\n".join(
                    f"computer.window({choice['id']}) {choice['title'] or '<untitled>'}" for choice in data["choices"]
                )
            self.emit(dict(type="text", text=message), True)
            return
        observation = event["observation"]
        run["observations"].observe(self.present_observation(observation))
        run["observationLogs"].append(observation)
        data = dict(kind="observation_captured", scope=observation["scope"], view=observation["view"])
        if observation.get("document") is not None:
            data["presentation"] = observation["document"].summary_data()
        if "observed" in observation:
            data["observed"] = observation["observed"]
        self.send(dict(diagnostic=dict(**data, elementRefs=list(observation["refs"])), executionId=run["id"]))

    def log_observations(self, run, *, materializedOnly=False):
        """Record captured evidence in acquisition order at the text output boundary.

        The retained observation supplies the original immutable snapshot. Logging does
        not reacquire the interface or change the delivery baseline. During an explicit
        print, stop at the first unrendered capture so unrelated views remain structured.
        """
        while run["observationLogs"]:
            observation = run["observationLogs"][0]
            if materializedOnly and peek_observation_text(observation["state"]) is None:
                break
            data = dict(
                kind="observation",
                scope=observation["scope"],
                view=observation["view"],
                text=observation["state"]["text"],
            )
            if "observed" in observation:
                data["observed"] = observation["observed"]
            self.send(dict(diagnostic=dict(**data, elementRefs=list(observation["refs"])), executionId=run["id"]))
            run["observationLogs"].popleft()

    def prepared_image(self, png):
        """Use one actual encoded delivery for both geometry and image output."""
        run = _runs.get()
        if run is None:
            return prepare_image(png)
        fingerprint = hashlib.sha256(png).digest()
        cached = run["preparedImages"].get(id(png))
        if cached is None or cached[0] != fingerprint or cached[1].source is not png:
            cached = fingerprint, prepare_image(png)
            run["preparedImages"][id(png)] = cached
        return cached[1]

    def present_observation(self, observation):
        """Retain source facts separately from model-facing image geometry."""
        run = _runs.get()
        original = observation["state"]
        cached = run["presentations"].get(id(original))
        if cached is not None and cached["sourceState"] is original:
            return cached
        document = observation.get("document") or observation_document(original)
        png = original.get("screenshot")
        if document is not None and png is not None:
            projected = project_document(document, self.prepared_image(png).coordinates)
            state = lazy_observation(projected, png)
            presented = dict(observation, state=state, document=projected, sourceState=original)
        else:
            presented = dict(observation, sourceState=original)
        run["presentations"][id(original)] = presented
        return presented

    def print(self, *values, sep=" ", end="\n", file=None, flush=False):
        if not self.executor.is_owner:
            return self.executor.call(self.print, *values, sep=sep, end=end, file=file, flush=flush)
        run = _runs.get()
        if run is None or not run["active"]:
            return
        if file is not None and file not in (sys.stdout, sys.stderr):
            _original_print(*values, sep=sep, end=end, file=file, flush=flush)
            return
        if len(values) == 1:
            receipt = result_receipt(values[0])
            if receipt is not None and run["actions"].has(receipt):
                return
            if receipt is not None:
                self.emit(
                    dict(
                        type="text",
                        text=f"Earlier action [{receipt['status']}]: {receipt['target']}. {receipt['detail']}",
                    )
                )
                return
            if run["textPages"].has(values[0]):
                values = (dict(values[0]),)
        printed_states = []

        def present(value):
            original = (
                value
                if isinstance(value, LazyObservation)
                else value.observation
                if isinstance(value, CapturedObservationText)
                else None
            )
            if original is None:
                return value
            projected = self.present_observation(dict(state=original))["state"]
            printed_states.append((original, projected))
            return str(projected["text"])

        values = tuple(present(value) for value in values)
        text = (" " if sep is None else sep).join(
            value if isinstance(value, str) else pprint.pformat(value, depth=6, sort_dicts=False) for value in values
        )
        if end not in (None, "\n"):
            text += end
        self.emit(dict(type="text", text=text), True)
        for original, projected in printed_states:
            run["deliveredObservations"].append(original)
            run["observations"].printed(projected["text"])
            if original.get("screenshot") is not None:
                run["imageContexts"].set(original["screenshot"], True)
        details = any(isinstance(value, (CapturedDetailPage, CapturedDetailText)) for value in values)
        observed = None if details else run["observations"].printed(text)
        if observed is not None:
            run["deliveredObservations"].append(observed.get("sourceState", observed["state"]))
            self.log_observations(run, materializedOnly=True)
        if observed is not None and observed["state"].get("screenshot") is not None:
            run["imageContexts"].set(observed["state"]["screenshot"], True)

    def print_image(self, png):
        if not self.executor.is_owner:
            return self.executor.call(self.print_image, png)
        if not isinstance(png, (bytes, bytearray, memoryview)):
            raise TypeError("print_image() expects PNG bytes, such as state.screenshot.")
        delivery = self.prepared_image(png)
        data = base64.b64encode(delivery.png).decode("ascii")
        run = _runs.get()
        self.emit(
            dict(
                type="text",
                text=image_label(png, run is not None and run["imageContexts"].has(png), delivery.coordinates),
            )
        )
        self.emit(dict(type="image", mimeType="image/png", data=data))
        if run is not None and run["active"]:
            run["images"].append(delivery)
            self.send(dict(diagnostic=dict(kind="image_output", capture=image_metadata(png)), executionId=run["id"]))

    async def finish(self, error: BaseException | None = None):
        run = _runs.get()
        if run is None or not run["active"] or run["phase"] != "code":
            return
        run["phase"] = "draining"
        if run["pending"]:
            if error is None:
                error: BaseException | None = RuntimeError(
                    (
                        "Computer operations outlived their script. Pending operations finished before "
                        "this result; inspect their receipts and observations before "
                        "continuing."
                    )
                )
            await asyncio.gather(*list(run["pending"]), return_exceptions=True)
        try:
            self.flush_observations(run)
        except Exception as failure:
            if error is None:
                error = failure
        try:
            self.log_observations(run)
        except Exception as failure:
            if error is None:
                error = failure
        for delivery in run["images"]:
            image_delivered(delivery.source, delivery.coordinates)
        for state in run["deliveredObservations"]:
            observation_delivered(state)
        run["active"] = False
        run["phase"] = "finished"
        result = dict(content=run["output"].content.copy())
        message = None
        if error is not None:
            message = (type(error).__name__ + ": " + str(error))[:4096]
            effect = (
                ("This code did not execute; no statements from this call ran. Correct the syntax before continuing.")
                if not run.get("evaluationStarted")
                else "Earlier statements may have executed. Observe before retrying an action."
            )
            result["content"].append(dict(type="text", text=message + "\n" + effect))
            result["isError"] = True
        elif not result["content"]:
            result["content"].append(
                dict(
                    type="text",
                    text=(
                        "Completed (no output). Reads return observations automatically; use print() for other values."
                    ),
                )
            )
        self.send(result)
        return result

    def namespace(self):
        computer = self.computer_factory()
        return dict(
            __name__="__computer_use__",
            __builtins__=builtins,
            computer=computer,
            print=self.print,
            print_image=self.print_image,
        )

    def evaluate(self, compiled, run):
        """Run ordinary synchronous Python away from the event-loop owner.

        The trace only interrupts model-code frames; SDK/backend finally blocks
        retain their own cancellation and cleanup responsibilities. The parent
        worker supervisor still owns termination of uninterruptible native code.
        """
        cancellation = run["cancellation"]

        def trace(frame, event, argument):
            if frame.f_code.co_filename != "computer-use-agent.py":
                return None
            if cancellation.cancelled.is_set():
                raise _ExecutionCancelled("Tool execution cancelled; no further statements were run.")
            return trace

        previous = sys.gettrace()
        with execution_scope(self.executor, cancellation):
            sys.settrace(trace)
            try:
                run["evaluationStarted"] = True
                exec(compiled, self.task_namespace, self.task_namespace)  # noqa: S102
            finally:
                sys.settrace(previous)

    async def cancel_worker(self, worker, cancellation):
        cancellation.cancel()

        async def settle():
            await self.executor.drain(cancellation)
            try:
                await worker
            except BaseException:
                # The original cancellation remains the public outcome. The
                # native operation has already retained its own effect receipt.
                pass

        settling = asyncio.create_task(settle())
        while not settling.done():
            try:
                await asyncio.shield(settling)
            except asyncio.CancelledError:
                # Repeated stop requests cannot abandon cleanup or the worker.
                continue
        await settling

    async def execute(self, message):
        if self.closed:
            result = dict(content=[dict(type="text", text="Execution runtime is closed.")], isError=True)
            self.send(result)
            return result
        execution = asyncio.current_task()
        self.executions.add(execution)
        code, id_, history = message["code"], message["executionId"], message["history"]
        if history["resetText"]:
            self.presenter.invalidate()
        if history["resetImages"]:
            reset_image_delivery()
        run = Record(
            id=id_,
            active=True,
            phase="code",
            output=OutputBuffer(),
            observations=ObservationOutput(self.presenter, history["incremental"]),
            images=[],
            pending=set(),
            observationLogs=deque(),
            actions=ActionPresentation(),
            textPages=WeakIdentityMap(),
            imageContexts=WeakIdentityMap(),
            preparedImages={},
            presentations={},
            deliveredObservations=[],
            cancellation=ExecutionCancellation(),
        )
        token = _runs.set(run)
        try:
            try:
                compiled = compile(
                    code + "\n",
                    "computer-use-agent.py",
                    "exec",
                    dont_inherit=True,
                )
                worker = asyncio.create_task(asyncio.to_thread(self.evaluate, compiled, run))
                try:
                    # Cancelling the waiting task cannot abandon the executing
                    # thread. First stop SDK admission, then settle native work
                    # and the script before publishing a result.
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    await self.cancel_worker(worker, run["cancellation"])
                    raise
            except BaseException as error:
                return await self.finish(error)
            return await self.finish()
        finally:
            _runs.reset(token)
            self.executions.discard(execution)

    async def close(self):
        self.closed = True
        active = [task for task in self.executions if task is not asyncio.current_task()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        await self.executor.aclose()


_original_print = builtins.print


class _ExecutionCancelled(BaseException):
    """Stop remaining model statements without being caught as an SDK error."""


class ConsoleWriter(io.TextIOBase):
    "Discard raw process output; only the explicit print hook emits content."

    def __init__(self, runtime):
        self.runtime = runtime

    def write(self, text):
        if not isinstance(text, str):
            raise TypeError("write() argument must be str")
        return len(text)

    def flush(self):
        pass


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-global-input", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    from .worker_transport import worker_connection

    protocol = consume_protocol()
    ready_requested = os.environ.pop(READY_ENV, None) == "1"
    reader, writer = await worker_connection()

    messages = WorkerMessageWriter(writer, protocol=protocol)
    runtime = Runtime(messages.send, computer_factory=lambda: Computer(allow_global_input=args.allow_global_input))
    sys.stdout = sys.stderr = ConsoleWriter(runtime)
    builtins.print = runtime.print
    if ready_requested:
        messages.send(dict(ready=True))
        await writer.drain()

    async def stop():
        await runtime.close()
        messages.flush()
        writer.close()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(stop()))
    except RuntimeError:
        pass
    current = None
    try:
        while True:
            message = await read_message(reader, protocol=protocol)
            if message is None:
                break
            if current is not None and not current.done():
                raise RuntimeError("Worker received concurrent executions")
            current = asyncio.create_task(runtime.execute(message))
        if current is not None and not current.done():
            current.cancel()
    finally:
        await runtime.close()
        messages.flush()
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
