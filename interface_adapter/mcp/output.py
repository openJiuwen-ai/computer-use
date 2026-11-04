# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-facing content: block limits, observation buffering, and delivery baselines.

The server and worker each own their OutputBuffer. Only the worker owns an
ObservationPresenter and per-execution ObservationOutput; co-location does not
share their state across processes. Delivered baselines persist for this session.
"""

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.delivery.baseline.model import BaselineInput
from computer_use.interaction.read.delivery.baseline.pipeline import BaselinePublicationPipeline
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.interaction.read.delivery.diff.model import DeliverySelection
from computer_use.interaction.read.delivery.diff.pipeline import DeliverySelectionPipeline
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.json import dumps, utf8

from .baseline_store import DeliveredBaselineStore


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
            raise RuntimeError("Output limit reached (8 MiB / 128 blocks); this block was omitted.")
        text_bytes = len(utf8(block["text"])) + (1 if append else 0) if block["type"] == "text" else 0
        if self._text_bytes + text_bytes > 256 * 1024:
            raise RuntimeError(
                (
                    "Output limit reached (256 KiB UTF-8 text per call); this text block was omitted."
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
        self._baselines, self._sources = DeliveredBaselineStore(max_bytes, max_scopes), WeakIdentityMap()
        self._next_source = 0

    def source_id(self, observation):
        id_ = self._sources.get(observation["source"])
        if id_ is None:
            self._next_source += 1
            id_ = self._next_source
            self._sources.set(observation["source"], id_)
        return id_

    def key(self, observation):
        """Keep distinct requested views pending, including explicit image output."""
        return dumps([self.source_id(observation), observation["scope"]])

    def trees(self, observation):
        document = observation.get("document")
        if isinstance(document, DisplayDocument):
            trees = [part for part in document.parts if isinstance(part, DisplayTree)]
            if trees and all(part.scope for part in trees):
                return trees
        return ()

    def tree_key(self, observation, tree):
        """Native session/instance/window/surface/subtree identity, never display text."""
        return dumps([self.source_id(observation), "native-tree", tree.scope])

    def invalidate(self, source=None):
        if source is None:
            self._baselines.invalidate()
        elif (source_id := self._sources.get(source)) is not None:
            self._baselines.invalidate(source_id)

    def close(self):
        self._baselines.close()

    def text(self, observation):
        trees = self.trees(observation)
        previous = self._baselines.get(self.key(observation)) if not trees else None
        if trees:
            baselines = [self._baselines.get(self.tree_key(observation, tree)) for tree in trees]
            retained = [baseline for baseline in baselines if baseline is not None]
            if retained:
                previous = {
                    "view": "window",
                    "document": ComparisonDocument(tuple(tree for b in retained for tree in b["document"].trees)),
                    "refs": {ref: target for b in retained for ref, target in b["refs"].items()},
                    "coverage": {
                        "parents": {
                            ref: parent
                            for b in retained
                            for ref, parent in (b.get("coverage") or {}).get("parents", {}).items()
                        },
                        "complete": tuple(
                            ref for b in retained for ref in (b.get("coverage") or {}).get("complete", ())
                        ),
                    },
                }
        return DeliverySelectionPipeline().run(DeliverySelection(observation, previous))

    def delivered(self, observation):
        trees = self.trees(observation)
        if not trees:
            BaselinePublicationPipeline(self).run(BaselineInput(self.key(observation), observation))
            return
        for tree in trees:
            refs = {ref: observation["refs"][ref] for ref in tree.prepared.snapshot.refs if ref in observation["refs"]}
            coverage = observation.get("coverage")
            component = dict(
                observation,
                document=DisplayDocument((tree,)),
                refs=refs,
                coverage={
                    "parents": {ref: parent for ref, parent in coverage["parents"].items() if ref in refs},
                    "complete": tuple(ref for ref in coverage["complete"] if ref in refs),
                }
                if coverage is not None
                else None,
            )
            BaselinePublicationPipeline(self).run(BaselineInput(self.tree_key(observation, tree), component))

    def publish_baseline(self, draft):
        baseline = {key: value for key, value in draft.baseline.items() if key != "source"}
        self._baselines.publish(draft.key, baseline)


class ObservationOutput:
    def __init__(self, presenter):
        self.presenter = presenter
        self._pending = {}

    def observe(self, observation):
        key = self.presenter.key(observation)
        if key not in self._pending and len(self._pending) >= 32:
            raise RuntimeError(
                "Too many observation scopes in one call; inspect the collected results before reading more."
            )
        # Replacement belongs at its actual acquisition position. In particular,
        # a later parent capture must not move ahead of an earlier popup.
        self._pending.pop(key, None)
        self._pending[key] = observation

    def take(self):
        observations = tuple(self._pending.values())
        self._pending.clear()
        return observations

    def invalidate(self, source):
        self.presenter.invalidate(source)
        self.discard_pending(source)

    def discard_pending(self, source):
        for key, observation in list(self._pending.items()):
            if observation["source"] is source:
                self._pending.pop(key)

    def contains(self, state):
        """Match the captured object, never a rendered string or its contents."""
        return any(
            value.get("sourceState", value["state"]) is state or value["state"] is state
            for value in self._pending.values()
        )
