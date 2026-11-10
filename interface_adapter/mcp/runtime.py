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
    disable_global_input_for_process,
    lookup_continuation_scope,
    set_action_handler,
    set_diagnostic_handler,
    set_execution_guard,
    set_observation_handler,
    set_preview_handler,
    set_text_page_handler,
)
from computer_use.client.images import (
    image_caption,
    image_delivered,
    image_label,
    image_metadata,
    image_output_scope,
    image_point_hints,
    require_image_delivery,
)
from computer_use.client.result_presentation import result_receipt, text_source_label
from computer_use.errors import ComputerUseError, confirmed_no_requested_input
from computer_use.identity import Record, WeakIdentityMap
from computer_use.interaction.read.presentation.actions.presentation import ActionPresentation
from computer_use.json import utf8
from computer_use.native.preview import latest_preview_sink
from computer_use.session.events import observation_delivered
from computer_use.session.observation import (
    CapturedObservationText,
    LazyObservation,
    bind_observation_presentation,
    lazy_observation,
    observation_document,
    observation_presentation,
    observation_screenshot,
    peek_observation_text,
)
from computer_use.session.operations import OperationDeadlineExceeded, current_operation_budget

from .image_delivery import prepare_image, project_document
from .image_presentation import PresentationImages
from .output import ObservationOutput, ObservationPresenter, OutputBuffer
from .worker_lifecycle import READY_ENV
from .worker_transport import WorkerMessageWriter, consume_protocol, read_message

_runs = ContextVar("computer_use_execution", default=None)


def _window_closed_without_input(error: ComputerUseError) -> bool:
    """Require confirmed closure and complete evidence about the requested input."""
    return error.window_lifecycle == "closed" and confirmed_no_requested_input(error.impact)


class Runtime:
    def __init__(self, send, computer_factory=Computer):
        self.send, self.computer_factory = send, computer_factory
        self.executor = SessionExecutor(loop=asyncio.get_running_loop())
        self.presenter = ObservationPresenter()
        self.presentation_images = PresentationImages()
        self.command_runs = {}
        self.executions = set()
        self.closed = False
        self._next_captured_observation = 0
        self._captured_observations = deque()
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
            raise RuntimeError("Tool execution ended. Command dispatch: not sent.")
        pending = asyncio.get_running_loop().create_future()
        run["pending"].add(pending)

        def done():
            run["pending"].discard(pending)
            if not pending.done():
                pending.set_result(None)

        return done

    def diagnostic(self, event):
        if not self.executor.is_owner:
            self.executor.call(self.diagnostic, event)
            return
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

    def emit(self, block, merge_text=False, *, progress=None):
        if not self.executor.is_owner:
            self.executor.call(self.emit, block, merge_text, progress=progress)
            return
        run = _runs.get()
        if run is None or not run["active"]:
            return
        # Receipt/stdout progress remains available to the supervisor if the
        # worker is interrupted. The final response is assembled from ordered
        # slots so superseded automatic observations need not reach the model.
        run["output"].append(dict(block), merge_text)
        if not run["publishing"]:
            run["outputEntries"].append({"kind": "block", "block": dict(block), "merge": merge_text})
        output = block if progress is None else progress
        self.send({"output": output, "mergeText": merge_text, "executionId": run["id"]})

    def flush_observations(self, run, *, explicit=False):
        """Retain chronological slots; render only the facts actually delivered.

        The SDK's before-write boundary still records where earlier facts were
        captured. A successful later capture may supersede the same view, while
        a refused write leaves the preceding facts in their original position.
        Explicit reads and prints keep their requested historical view.
        """
        for observation in run["observations"].take():
            incoming = {self.presenter.tree_key(observation, tree) for tree in self.presenter.trees(observation)}
            for previous in run["observationEntries"]:
                if not previous["pinned"] and not previous["superseded"] and run["observations"].superseded(
                    previous["observation"], observation, incoming
                ):
                    previous["superseded"] = True
            entry = {
                "kind": "observation", "observation": observation,
                "pinned": explicit or bool(observation.get("explicitRead") or observation.get("full")),
                "superseded": False,
            }
            run["observationEntries"].append(entry)
            run["outputEntries"].append(entry)

    def publish_output(self, run, *, complete):
        """Commit baselines and image authority only for retained output slots."""
        run["publishing"] = True
        run["output"] = OutputBuffer()
        failure = None
        entries = run["outputEntries"]
        absent = run["observations"].absent_transients(run["observationEntries"], run["lastWrite"]) if complete else {}

        def text_cost(entry):
            if entry["kind"] == "block" and entry["block"]["type"] == "text":
                return len(utf8(entry["block"]["text"])) + int(entry["merge"])
            return 0

        # These blocks already fit while the script ran. Captures inserted
        # during final publication must leave room for subsequent receipts.
        reserved = sum(map(text_cost, entries))
        for entry in entries:
            try:
                if entry["kind"] == "block":
                    reserved -= text_cost(entry)
                    run["output"].append(dict(entry["block"]), entry["merge"])
                elif entry["kind"] == "image":
                    self.emit_image(entry["delivery"])
                else:
                    absent_ref = absent.get(id(entry))
                    if absent_ref is not None:
                        note = (
                            f"Window {absent_ref} was observed earlier and is absent from the latest "
                            "complete application observation."
                        )
                        if len(utf8(note)) <= run["output"].remaining_text_bytes - reserved:
                            self.emit({"type": "text", "text": note})
                    elif not entry["superseded"]:
                        self.publish_observation(run, entry["observation"], reserved)
            except Exception as error:  # noqa: BLE001 - retain later receipts, then rethrow the rendering failure
                # Rendering an earlier capture must not erase receipts for
                # later inputs which already completed in the script.
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure

    def defer_observation(self, observation):
        state = observation.get("sourceState", observation["state"])
        message = "Tree: deferred (MCP text budget)."
        if not isinstance(state, LazyObservation):
            return message + " Captured detail pages are unavailable."
        namespace = self.task_namespace
        while True:
            self._next_captured_observation += 1
            name = f"captured_observation_{self._next_captured_observation}"
            if name not in namespace:
                break
        namespace[name] = state
        self._captured_observations.append((name, state))
        return message + (
            f"\npage = {name}.read_details()\nprint(page)"
            f"\nContinue with {name}.read_details(cursor=page.next) when page.next is set."
        )

    def publish_observation(self, run, observation, reserved):
        gap = observation.get("presentationGap")
        text = gap or self.presenter.text(observation)
        delivery = observation.get("imageDelivery")
        available = run["output"].remaining_text_bytes - reserved
        caption_bytes = 0
        if delivery is not None:
            # Reserve the standalone caption because a deferred tree no
            # longer supplies the window and coordinate context.
            label = image_label(delivery.source, False, delivery.coordinates)
            index = len(run["imageOutputs"]) + 1
            caption_bytes = len(utf8(f"Image {run['id']}.{index}\n{label}"))
        remaining = available - caption_bytes
        deferred = len(utf8(text)) > remaining
        if deferred:
            text = self.defer_observation(observation)
        fits = not deferred or len(utf8(text)) <= remaining
        if fits:
            self.emit({"type": "text", "text": text})
            if not gap and not deferred:
                self.presenter.delivered(observation)
                run["deliveredObservations"].append(observation.get("sourceState", observation["state"]))
        if delivery is not None and caption_bytes <= available:
            if fits and not deferred:
                run["imageContexts"].set(delivery.source, True)
            self.emit_image(delivery, captured_after_action=observation.get("capturedAfterAction"))
        if not fits:
            if delivery is not None and caption_bytes > available:
                text += " Image caption exceeds the remaining text budget."
            raise RuntimeError(text)

    def action(self, receipt):
        if not self.executor.is_owner:
            self.executor.call(self.action, receipt)
            return
        run = _runs.get()
        if run is None or not run["active"]:
            return
        self.flush_observations(run)
        number, text = run["actions"].render(receipt)
        write = run["lastWrite"]
        if write is not None:
            write["dispatched"] = receipt["status"] == "dispatched"
            write["action"] = (current_operation_budget(), number) if write["dispatched"] else None
        self.emit({"type": "text", "text": text}, True)

    def text_page(self, source, page):
        if not self.executor.is_owner:
            self.executor.call(self.text_page, source, page)
            return
        run = _runs.get()
        if run is None or not run["active"]:
            return
        self.flush_observations(run)
        self.emit(dict(type="text", text=text_source_label(source)), True)
        run["textPages"].set(page, True)

    def observation(self, event):
        if not self.executor.is_owner:
            self.executor.call(self.observation, event)
            return
        run = _runs.get()
        if event["kind"] == "invalidate":
            self.presenter.invalidate(event["source"])
            if run is not None:
                run["observations"].invalidate(event["source"])
                for entry in run["outputEntries"]:
                    if entry["kind"] == "observation" and entry["observation"]["source"] is event["source"]:
                        entry["superseded"] = True
            return
        if run is None or not run["active"]:
            return
        if event["kind"] == "before-write":
            self.flush_observations(run)
            run["lastWrite"] = {"source": event["source"], "dispatched": False}
            return
        if event["kind"] == "action-observation":
            result = event["result"]
            failure = isinstance(result, ComputerUseError)
            receipt = None if failure else result_receipt(result)
            data = vars(result) if failure else result
            self.flush_observations(run)
            gap = data["observation_error"]
            target = result.target if failure else receipt["target"] if receipt is not None else "original target"
            outcome = f"Effect: {result.effect}" if failure else f"Action: {result['status']}"
            message = f"Related observation [{data['observation_status']}] for {target}: {gap['detail']}\n{outcome}"
            if data["window_lifecycle"] == "closed":
                message += "\nOriginal window: closed."
            if data["choices"]:
                message += "\nCurrent windows:\n" + "\n".join(
                    f"window={choice['id']} {choice['title'] or '<untitled>'}" for choice in data["choices"]
                )
            self.emit(dict(type="text", text=message), True)
            return
        write = run["lastWrite"]
        observation = dict(event["observation"], write=write)
        self.flush_observations(run)
        budget = current_operation_budget()
        action = write.get("action") if write is not None and write["source"] is observation["source"] else None
        if action is not None and budget is not None and action[0] is budget:
            # An explicit read after the write has a different operation budget.
            observation["capturedAfterAction"] = action[1]
        failure = None
        try:
            if budget is not None:
                budget.checkpoint()
            presented = self.present_observation(observation)
            presented = self.presenter.prepare(presented, budget)
        except OperationDeadlineExceeded as error:
            # Source facts and an already encoded image remain usable evidence.
            # This suffix neither acknowledges the unpublished tree nor grants
            # another deadline to render it during finish().
            failure = error
            original = observation["state"]
            png = observation_screenshot(original)
            cached = run["preparedImages"].get(id(png)) if png is not None else None
            ready = (
                png if cached is not None and cached[1].source is png and cached[0] == hashlib.sha256(png).digest()
                else None
            )
            document = observation.get("document") or observation_document(original)
            presented = dict(
                observation,
                sourceState=original,
                state=lazy_observation(document, ready),
                presentationGap=(
                    "Observation presentation: partial [original operation deadline]. "
                    + str(error)
                    + ". Captured source facts remain retained. Tree delivery: not acknowledged. "
                    + ("Prepared image: available." if ready is not None else "Prepared image: unavailable.")
                ),
                operationBudget=budget,
                imageDelivery=cached[1] if ready is not None else None,
                imageScope=cached[4] if ready is not None else None,
            )
        run["presentations"][id(observation["state"])] = presented
        run["observations"].observe(presented)
        run["observationLogs"].append(observation)
        data = dict(kind="observation_captured", scope=observation["scope"], view=observation["view"])
        if budget is not None:
            data.update(
                operationId=budget.operation_id,
                operationElapsedMs=budget.elapsed_ms,
                presentationPrepared=failure is None,
            )
        if observation.get("document") is not None:
            data["presentation"] = observation["document"].summary_data()
        if "observed" in observation:
            data["observed"] = observation["observed"]
        self.send(dict(diagnostic=dict(**data, elementRefs=list(observation["refs"])), executionId=run["id"]))
        if failure is not None:
            raise failure

    def log_observations(self, run, *, materializedOnly=False):
        """Record captured evidence in acquisition order at the text output boundary.

        The retained document supplies the original snapshot; materialized text follows
        its paired public view. Logging does not reacquire or change the baseline. During an explicit
        print, stop at the first unrendered capture so unrelated views remain structured.
        """
        while run["observationLogs"]:
            observation = run["observationLogs"][0]
            materialized = peek_observation_text(observation["state"])
            if materializedOnly and materialized is None:
                break
            data = dict(
                kind="observation",
                scope=observation["scope"],
                view=observation["view"],
            )
            if materialized is not None:
                data["text"] = materialized
            else:
                # The captured graph and actual delivery are logged separately.
                # Logging a diff must not force another full-tree render.
                data["textMaterialized"] = False
                document = observation.get("document")
                if document is not None:
                    data["presentation"] = document.summary_data()
            if "observed" in observation:
                data["observed"] = observation["observed"]
            self.send(dict(diagnostic=dict(**data, elementRefs=list(observation["refs"])), executionId=run["id"]))
            run["observationLogs"].popleft()

    def prepared_image(self, png):
        """Use one actual encoded delivery for both geometry and image output."""
        run = _runs.get()
        if run is None:
            return prepare_image(png)
        presented = self.presentation_images.prepared(png)
        if presented is not None:
            # Reprinting this exact public view retains its original source and
            # inverse. It cannot adopt the last image's dimensions or binding.
            run["preparedDeliveries"][id(presented[1])] = presented
            return presented[1]
        fingerprint = hashlib.sha256(png).digest()
        cached = run["preparedImages"].get(id(png))
        if cached is None or cached[0] != fingerprint or cached[1].source is not png:
            if run["phase"] == "draining":
                raise RuntimeError(
                    "Image was not prepared within its originating operation; no late image encoding started"
                )
            budget = current_operation_budget()
            if budget is not None:
                budget.checkpoint()
            delivery = prepare_image(png)
            cached = (
                fingerprint, delivery, base64.b64encode(delivery.png).decode("ascii"),
                image_metadata(png), image_output_scope(png),
            )
            run["preparedImages"][id(png)] = cached
            # Slots retain their original encoding even if the caller mutates
            # this source object and prepares another delivery before finish.
            run["preparedDeliveries"][id(delivery)] = cached
            if budget is not None:
                budget.checkpoint()
        return cached[1]

    def present_observation(self, observation):
        """Retain source facts separately from model-facing image geometry."""
        run = _runs.get()
        original = observation["state"]
        cached = run["presentations"].get(id(original))
        if cached is not None and cached["sourceState"] is original:
            return cached
        document = observation.get("document") or observation_document(original)
        view = observation_presentation(original)
        png = view.screenshot if view is not None else observation_screenshot(original)
        delivery = self.prepared_image(png) if png is not None else None
        if view is not None:
            presented = dict(observation, state=original, document=view.document, sourceState=original)
        elif document is not None and delivery is not None:
            projected = project_document(document, delivery.coordinates)
            screenshot = self.presentation_images.create(run["preparedDeliveries"][id(delivery)])
            if isinstance(original, LazyObservation):
                bind_observation_presentation(original, projected, screenshot)
                state = original
            else:
                state = lazy_observation(projected, screenshot)
            presented = dict(observation, state=state, document=projected, sourceState=original)
        else:
            presented = dict(observation, sourceState=original)
        presented["imageDelivery"] = delivery
        presented["imageScope"] = run["preparedDeliveries"][id(delivery)][4] if delivery is not None else None
        run["presentations"][id(original)] = presented
        return presented

    def print(self, *values, sep=" ", end="\n", file=None, flush=False):
        if not self.executor.is_owner:
            self.executor.call(self.print, *values, sep=sep, end=end, file=file, flush=flush)
            return
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
        ordinary = []

        def emit_ordinary():
            if not ordinary:
                return
            text = (" " if sep is None else sep).join(
                value if isinstance(value, str) else pprint.pformat(value, depth=6, sort_dicts=False)
                for value in ordinary
            )
            if end not in (None, "\n"):
                text += end
            self.emit(dict(type="text", text=text), True)
            ordinary.clear()

        for value in values:
            captured = value if isinstance(value, LazyObservation) else (
                value.observation if isinstance(value, CapturedObservationText) else None
            )
            if captured is None:
                ordinary.append(value)
                continue
            emit_ordinary()
            # A captured observation (including a slice of its text) requests
            # the same model presentation as its automatic delivery. SDK text
            # stays complete; no string matching can promote arbitrary output
            # into an observation or commit a partial slice as a full baseline.
            if run["observations"].contains(captured):
                self.flush_observations(run, explicit=True)
            elif any(
                entry["kind"] == "observation" and not entry["superseded"]
                and entry["observation"].get("sourceState", entry["observation"]["state"]) is captured
                for entry in run["outputEntries"]
            ):
                for entry in run["outputEntries"]:
                    if entry["kind"] == "observation" and entry["observation"].get(
                        "sourceState", entry["observation"]["state"]
                    ) is captured:
                        entry["pinned"] = True
            elif not any(state is captured for state in run["deliveredObservations"]):
                self.emit(dict(type="text", text=(
                    "Retained observation: previously delivered or superseded."
                )), True)
        if not values:
            self.emit(dict(type="text", text="" if end in (None, "\n") else end), True)
        emit_ordinary()

    def print_image(self, png):
        if not self.executor.is_owner:
            self.executor.call(self.print_image, png)
            return
        if not isinstance(png, (bytes, bytearray, memoryview)):
            raise TypeError("print_image() expects PNG bytes, such as state.screenshot.")
        run = _runs.get()
        delivery = self.prepared_image(png)
        if run is not None and run["active"] and not run["publishing"]:
            # Put the observation/caption before its explicit image, including
            # related popup views captured before this parent-window read.
            self.flush_observations(run, explicit=True)
            run["outputEntries"].append({"kind": "image", "delivery": delivery})
            return
        self.emit_image(delivery)

    def emit_image(self, delivery, *, captured_after_action=None):
        run = _runs.get()
        png = delivery.source
        prepared = run["preparedDeliveries"][id(delivery)] if run is not None else None
        source_matches = prepared is None or prepared[0] == hashlib.sha256(png).digest()
        scope = image_output_scope(png) if source_matches else None
        output_key = (scope if scope is not None else ("source", id(png)), hashlib.sha256(delivery.png).digest())
        if run is not None and run["active"] and output_key in run["imageOutputs"]:
            # Output reuse does not merge the native sources. Every exact
            # capture retains its own acknowledgement after actual delivery.
            if self.record_image(run, delivery, coalesced=True):
                run["imageOutputs"][output_key]["deliveries"].append(delivery)
            return
        data = prepared[2] if prepared is not None else base64.b64encode(delivery.png).decode("ascii")
        identifier = f"{run['id']}.{len(run['imageOutputs']) + 1}" if run is not None else "standalone"
        context_shown = run is not None and run["imageContexts"].has(png)
        # Mutated bytes cannot borrow their replacement's provenance or map.
        # The immutable encoded output still depicts the originally printed image.
        caption_source = png if source_matches else delivery.png
        caption = image_caption(caption_source, context_shown, delivery.coordinates)
        label = caption if context_shown else image_caption(caption_source, True, delivery.coordinates)
        text = {"type": "text", "text": f"Image {identifier}\n{caption.render()}"}
        metadata = {"imageId": identifier, "caption": label.render()}
        if captured_after_action is not None:
            metadata["capturedAfterAction"] = captured_after_action
        block = {"type": "image", "mimeType": "image/png", "data": data, "_meta": {"computer-use": metadata}}
        if run is None or not run["active"]:
            return
        # An interrupted worker can return the images already streamed, but
        # only the completed response can say which coordinate maps survived.
        passive_text = dict(text, text=f"Image {identifier}\n{caption.render(set())}")
        self.emit(text, progress=passive_text)
        text_block = run["output"].content[-1]
        try:
            self.emit(block, progress=dict(block, _meta={"computer-use": dict(metadata, caption=label.render(set()))}))
        except Exception:
            text_block.update(passive_text)
            raise
        run["imageOutputs"][output_key] = {
            "caption": caption, "label": label, "text": text_block, "metadata": metadata, "deliveries": [delivery],
        }
        self.record_image(run, delivery, coalesced=False)

    def record_image(self, run, delivery, *, coalesced):
        if any(image.source is delivery.source and image.png == delivery.png for image in run["images"]):
            return False
        run["images"].append(delivery)
        captured = run["preparedDeliveries"][id(delivery)][3]
        self.send({
            "diagnostic": {"kind": "image_output", "capture": captured, "coalesced": coalesced},
            "executionId": run["id"],
        })
        return True

    @staticmethod
    def reconcile_image_hints(run, acknowledged, pending_point=None):
        """Describe usable maps and identify an exact newly delivered point mapping."""
        images = []
        current_windows = {}
        pending_source, pending_window = pending_point or (None, None)
        pending_image = None
        for image in run["imageOutputs"].values():
            eligible = set()
            caption = image["caption"]
            identifier = image["metadata"]["imageId"]
            for delivery in image["deliveries"]:
                if id(delivery) not in acknowledged:
                    continue
                current = image_point_hints(delivery.source)
                eligible.update(current.keys())
                for window_ref, point in zip(current.windows, current.points):
                    if point in caption.hints.points and current.owner is not None:
                        current_windows[id(current.owner), window_ref] = (current.generation, identifier)
                        if delivery.source is pending_source and window_ref == pending_window:
                            pending_image = identifier
            images.append((image, eligible))

        for image, eligible in images:
            caption, label, metadata = image["caption"], image["label"], image["metadata"]
            if (caption.hints.keys() | label.hints.keys()) <= eligible:
                continue
            hints = caption.hints
            notes = []
            for window_ref, point in zip(hints.windows, hints.points):
                replacement = current_windows.get((id(hints.owner), window_ref))
                if point in eligible or replacement is None or replacement[0] <= hints.generation:
                    continue
                note = f"Window {window_ref} coordinates superseded. Use Image {replacement[1]} pixels."
                if len(note) <= len(point):
                    # The final caption must fit the budget reserved for its original hints.
                    notes.append("\n" + note)
            note = "".join(notes)
            image["text"]["text"] = f"Image {metadata['imageId']}\n{caption.render(eligible)}{note}"
            metadata["caption"] = label.render(eligible) + note
        if pending_image is None:
            return ""
        return (
            f"\nUse the attached image (Image {pending_image}, Window {pending_window}) "
            "in the next computer_use call."
        )

    async def finish(self, error: BaseException | None = None):
        run = _runs.get()
        if run is None or not run["active"] or run["phase"] != "code":
            return
        run["phase"] = "draining"
        if run["pending"]:
            if error is None:
                error: BaseException | None = RuntimeError(
                    ("Computer operations outlived their script. Pending operations have finished.")
                )
            await asyncio.gather(*list(run["pending"]), return_exceptions=True)
        try:
            self.flush_observations(run)
            self.publish_output(run, complete=error is None)
        except Exception as failure:
            if error is None:
                error = failure
        try:
            self.log_observations(run)
        except Exception as failure:
            if error is None:
                error = failure
        acknowledged = set()
        for delivery in run["images"]:
            if run["preparedDeliveries"][id(delivery)][0] == hashlib.sha256(delivery.source).digest():
                image_delivered(delivery.source, delivery.coordinates)
                acknowledged.add(id(delivery))
        for state in run["deliveredObservations"]:
            observation_delivered(state)
        # Host-only failure metadata retains exact pending pixels for delivery feedback.
        # pylint: disable-next=protected-access
        pending_point = error._pending_point_image if isinstance(error, ComputerUseError) else None
        point_hint = self.reconcile_image_hints(run, acknowledged, pending_point)
        run["active"] = False
        run["phase"] = "finished"
        result = dict(content=run["output"].content.copy())
        message = None
        if error is not None:
            message = type(error).__name__ + ": " + str(error)
            if isinstance(error, ComputerUseError) and _window_closed_without_input(error):
                detail, newline, facts = message.partition("\n")
                outcome = "\nRequested input: not dispatched."
                if error.observation_error is None:
                    # An observation gap already reports the original window's lifecycle.
                    outcome += "\nOriginal window: closed."
                message = detail + outcome + newline + facts
            menu_hint = ""
            if (
                isinstance(error, ComputerUseError) and error.observed_menu_window is not None
                and any(state is error.observation for state in run["deliveredObservations"])
            ):
                menu_hint = (
                    f"\nA new AX menu was observed in Window {error.observed_menu_window} after the input. "
                    f"Use its menu elements in Window {error.observed_menu_window}. Exact opener unconfirmed."
                )
            hints = menu_hint + point_hint
            message = message[:4096 - len(hints)] + hints
            if not run.get("evaluationStarted"):
                message += "\nCode execution: not started."
            result["content"].append(dict(type="text", text=message))
            result["isError"] = True
        elif not result["content"]:
            result["content"].append(
                dict(
                    type="text",
                    text=("Completed (no output)."),
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
        with execution_scope(self.executor, cancellation), lookup_continuation_scope():
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
        # Bound automatic capture retention across calls. Ordinary user
        # bindings are untouched.
        while len(self._captured_observations) > 128:
            name, state = self._captured_observations.popleft()
            if self.task_namespace.get(name) is state:
                self.task_namespace.pop(name)
        code, id_ = message["code"], message["executionId"]
        run = Record(
            id=id_,
            active=True,
            phase="code",
            output=OutputBuffer(),
            outputEntries=[],
            observationEntries=[],
            lastWrite=None,
            publishing=False,
            observations=ObservationOutput(self.presenter),
            images=[],
            imageOutputs={},
            pending=set(),
            observationLogs=deque(),
            actions=ActionPresentation(),
            textPages=WeakIdentityMap(),
            imageContexts=WeakIdentityMap(),
            preparedImages={},
            preparedDeliveries={},
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
        try:
            await self.executor.aclose()
        finally:
            self.presenter.close()
            self.presentation_images.clear()


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
    if not args.allow_global_input:
        disable_global_input_for_process()
    runtime = Runtime(messages.send)
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
