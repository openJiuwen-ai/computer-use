# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Worker-process entry point: persistent Python execution embedding the computer_use SDK."""

import ast
import asyncio
import base64
import builtins
import inspect
import io
import os
import pprint
import signal
import sys
from collections import deque
from contextvars import ContextVar

from computer_use import Computer
from computer_use.identity import Record, WeakIdentityMap
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
from computer_use.session.observation import peek_observation_text
from computer_use.session.detail_pages import CapturedDetailPage, CapturedDetailText
from computer_use.interaction.read.presentation.actions.presentation import ActionPresentation
from computer_use.native.preview import latest_preview_sink
from computer_use.protocol.png import decode_png
from computer_use.session.observation import peek_observation_text

from .output import ObservationOutput, ObservationPresenter, OutputBuffer
from .worker_transport import WorkerMessageWriter, consume_protocol, read_message

_runs = ContextVar("computer_use_execution", default=None)


class Runtime:
    def __init__(self, send, computer_factory=Computer):
        self.send, self.computer_factory = send, computer_factory
        self.presenter = ObservationPresenter()
        self.command_runs = {}
        self.computers = []
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
        self.task_namespace = self.namespace()

    @staticmethod
    def guard():
        run = _runs.get()
        if run is None or not run["active"] or run["phase"] != "code":
            raise RuntimeError(
                "Computer operation belongs to an ended tool execution. Effect: no command "
                "sent. Next: await every operation within a current tool call; do not use "
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
        run = _runs.get()
        if run is None or not run["active"]:
            return
        run["output"].append(block, merge_text)
        self.send(dict(output=block, mergeText=merge_text, executionId=run["id"]))

    def flush_observations(self, run):
        def emit(text, observation):
            self.emit(dict(type="text", text=text))
            if observation["state"].get("screenshot") is not None:
                run["imageContexts"].set(observation["state"]["screenshot"], True)

        def emit_image(png):
            if not any(image is png for image in run["images"]):
                self.print_image(png)

        run["observations"].flush(emit, emit_image)
        self.log_observations(run)

    def action(self, receipt):
        run = _runs.get()
        if run is None or not run["active"]:
            return
        self.flush_observations(run)
        self.emit(dict(type="text", text=run["actions"].render(receipt)))

    def text_page(self, source, page):
        run = _runs.get()
        if run is None or not run["active"]:
            return
        self.flush_observations(run)
        self.emit(dict(type="text", text=text_source_label(source)), True)
        run["textPages"].set(page, True)

    def observation(self, event):
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
        observation = event["observation"]
        run["observations"].observe(observation)
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

    def print(self, *values, sep=" ", end="\n", file=None, flush=False):
        run = _runs.get()
        if run is None or not run["active"]:
            return
        if file is not None and file not in (sys.stdout, sys.stderr):
            _originalPrint(*values, sep=sep, end=end, file=file, flush=flush)
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
        text = (" " if sep is None else sep).join(
            value if isinstance(value, str) else pprint.pformat(value, depth=6, sort_dicts=False) for value in values
        )
        if end not in (None, "\n"):
            text += end
        self.emit(dict(type="text", text=text), True)
        details = any(isinstance(value, (CapturedDetailPage, CapturedDetailText)) for value in values)
        observed = None if details else run["observations"].printed(text)
        if observed is not None:
            self.log_observations(run, materializedOnly=True)
        if observed is not None and observed["state"].get("screenshot") is not None:
            run["imageContexts"].set(observed["state"]["screenshot"], True)

    def print_image(self, png):
        if not isinstance(png, (bytes, bytearray, memoryview)):
            raise TypeError("print_image() expects PNG bytes, such as state.screenshot.")
        data = base64.b64encode(png).decode("ascii")
        decode_png(data)
        run = _runs.get()
        self.emit(dict(type="text", text=image_label(png, run is not None and run["imageContexts"].has(png))))
        self.emit(dict(type="image", mimeType="image/png", data=data))
        if run is not None and run["active"]:
            run["images"].append(png)
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
                        "Computer operations were not awaited. Pending operations "
                        "finished before "
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
        for png in run["images"]:
            image_delivered(png)
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
        self.computers.append(computer)
        return dict(
            __name__="__computer_use__",
            __builtins__=builtins,
            computer=computer,
            print=self.print,
            print_image=self.print_image,
        )

    async def execute(self, message):
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
        )
        token = _runs.set(run)
        try:
            try:
                namespace = self.task_namespace
                compiled = compile(
                    code + "\n",
                    "computer-use-agent.py",
                    "exec",
                    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                    dont_inherit=True,
                )
                run["evaluationStarted"] = True
                # The MCP operation explicitly runs agent-supplied Python; eval
                # retains the coroutine produced by top-level await in exec code.
                evaluated = eval(compiled, namespace, namespace)  # noqa: S307
                if inspect.isawaitable(evaluated):
                    await evaluated
            except BaseException as error:
                return await self.finish(error)
            return await self.finish()
        finally:
            _runs.reset(token)

    async def close(self):
        for computer in self.computers:
            # The pipeline shares retained bindings with their owning service.
            # pylint: disable-next=protected-access
            transport = computer._controller.transport
            if hasattr(transport, "close"):
                transport.close()
        for computer in self.computers:
            # pylint: disable-next=protected-access
            transport = computer._controller.transport
            if hasattr(transport, "waitClosed"):
                await transport.wait_closed()


_originalPrint = builtins.print


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


async def main():
    from .worker_transport import worker_connection

    protocol = consume_protocol()
    reader, writer = await worker_connection()

    messages = WorkerMessageWriter(writer, protocol=protocol)
    runtime = Runtime(messages.send)
    sys.stdout = sys.stderr = ConsoleWriter(runtime)
    builtins.print = runtime.print

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
