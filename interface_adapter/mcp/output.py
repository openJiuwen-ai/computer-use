# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-facing content: block limits, observation buffering, and delivery baselines.

The server and worker each own their OutputBuffer. Only the worker owns an
ObservationPresenter and per-execution ObservationOutput; co-location does not
share their state across processes. History authorization stays in history.py.
"""

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.delivery.baseline.model import BaselineInput
from computer_use.interaction.read.delivery.baseline.pipeline import BaselinePublicationPipeline
from computer_use.interaction.read.delivery.diff.model import DeliverySelection
from computer_use.interaction.read.delivery.diff.pipeline import DeliverySelectionPipeline
from computer_use.json import dumps, utf8
from computer_use.session.observation import peek_observation_text


class OutputBuffer:
    def __init__(self):
        self.content = []
        self._bytes = self._text_bytes = 0
        self._merge_text = False

    def append(self, block, merge_text=False):
        last = self.content[-1] if self.content else None
        append = (
            merge_text and self._merge_text and last is not None and last["type"] == "text" and block["type"] == "text"
        )
        size = len(utf8(dumps(block["text"] if append else block)))
        if self._bytes + size > 8 * 1024 * 1024 or not append and len(self.content) >= 128:
            raise RuntimeError("Output limit reached (8 MiB / 128 blocks). Emit less text or fewer screenshots.")
        text_bytes = len(utf8(block["text"])) + (1 if append else 0) if block["type"] == "text" else 0
        if self._text_bytes + text_bytes > 256 * 1024:
            raise RuntimeError(
                (
                    "Output limit reached (256 KiB UTF-8 text per call). This text block was not "
                    "emitted; earlier output and effects remain. Inspect a smaller part of the "
                    "value or evidence file; for UI use a relevant subtree, menu or read_text "
                    "page. Do not repeat preceding actions just to print their "
                    "result."
                )
            )
        self._bytes += size
        self._text_bytes += text_bytes
        if append:
            last["text"] += "\n" + block["text"]
        else:
            self.content.append(block)
        self._merge_text = merge_text


def is_output(value):
    return isinstance(value, dict) and (
        value.get("type") == "text"
        and isinstance(value.get("text"), str)
        or value.get("type") == "image"
        and value.get("mimeType") == "image/png"
        and isinstance(value.get("data"), str)
    )


class ObservationPresenter:
    def __init__(self, max_bytes=4 * 1024 * 1024, max_scopes=32):
        self.max_bytes, self.max_scopes = max_bytes, max_scopes
        self._baselines, self._sources = {}, WeakIdentityMap()
        self._next_source = self._bytes = 0

    def key(self, observation):
        id_ = self._sources.get(observation["source"])
        if id_ is None:
            self._next_source += 1
            id_ = self._next_source
            self._sources.set(observation["source"], id_)
        return dumps([id_, observation["scope"]])

    def invalidate(self, source=None):
        for key, baseline in list(self._baselines.items()):
            if source is None or baseline["source"] is source:
                self._bytes -= baseline["bytes"]
                self._baselines.pop(key)

    def text(self, observation):
        previous = self._baselines.get(self.key(observation))
        return DeliverySelectionPipeline().run(DeliverySelection(observation, previous))

    def delivered(self, observation):
        BaselinePublicationPipeline(self).run(BaselineInput(self.key(observation), observation))

    def retire_baseline(self, key):
        previous = self._baselines.pop(key, None)
        if previous is not None:
            self._bytes -= previous["bytes"]

    def publish_baseline(self, draft):
        if draft.size > self.max_bytes:
            return
        while self._baselines and (
            len(self._baselines) >= self.max_scopes or self._bytes + draft.size > self.max_bytes
        ):
            self.retire_baseline(next(iter(self._baselines)))
        self._baselines[draft.key] = draft.baseline
        self._bytes += draft.size


class ObservationOutput:
    def __init__(self, presenter, incremental=True):
        self.presenter, self.incremental = presenter, incremental
        self._pending, self._printed = {}, set()

    def observe(self, observation):
        key = self.presenter.key(observation)
        if key not in self._pending and len(self._pending) >= 32:
            raise RuntimeError(
                "Too many observation scopes in one call; inspect the collected results before reading more."
            )
        previous = self._pending.get(key)
        if previous is not None:
            self._printed.discard(id(previous))
        self._pending[key] = observation

    def invalidate(self, source):
        self.presenter.invalidate(source)
        self.discard_pending(source)

    def discard_pending(self, source):
        for key, observation in list(self._pending.items()):
            if observation["source"] is source:
                self._pending.pop(key)
                self._printed.discard(id(observation))

    def printed(self, text):
        matches = [v for v in self._pending.values() if peek_observation_text(v["state"]) == text]
        if len(matches) != 1:
            return None
        self.presenter.delivered(matches[0])
        self._printed.add(id(matches[0]))
        return matches[0]

    def flush(self, emit, emit_image=None):
        for observation in self._pending.values():
            if id(observation) not in self._printed:
                emit(
                    self.presenter.text(observation) if self.incremental else observation["state"]["text"], observation
                )
                self.presenter.delivered(observation)
                self._printed.add(id(observation))
            if (
                observation["view"] in ("window", "desktop")
                and observation["state"].get("screenshot") is not None
                and emit_image is not None
            ):
                emit_image(observation["state"]["screenshot"])
        self._pending.clear()
        self._printed.clear()
