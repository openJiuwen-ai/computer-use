# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Model-facing content: block limits, observation buffering, and delivery baselines.

The server and worker each own their OutputBuffer. Only the worker owns an
ObservationPresenter and per-execution ObservationOutput; co-location does not
share their state across processes. Delivered baselines persist for this session.
"""

from computer_use.client.images import image_output_scope
from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.delivery.baseline.model import BaselineInput
from computer_use.interaction.read.delivery.baseline.passes.assemble_baseline import stage as assemble_baseline
from computer_use.interaction.read.delivery.baseline.passes.publish_observation import stage as publish_observation
from computer_use.interaction.read.delivery.baseline.pipeline import BaselinePublicationPipeline
from computer_use.interaction.read.delivery.diff.baseline import ComparisonDocument
from computer_use.interaction.read.delivery.diff.model import DeliverySelection
from computer_use.interaction.read.delivery.diff.pipeline import DeliverySelectionPipeline
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.rendering.document import prepare_document
from computer_use.interaction.read.presentation.rendering.document.formatting import format_document
from computer_use.json import dumps, utf8
from computer_use.pipeline.core import Control
from computer_use.session.observation_data import observation_data

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

    def prepare(self, observation, budget=None):
        """Prepare source chunks and small baseline drafts, without delivery authority.

        The actual diff is deliberately not retained here: pending captures may
        later be superseded or flushed in another valid combination. Complete
        compact nodes allow that decision without another full renderer after
        the originating SDK operation has ended.
        """
        control = Control(
            remaining=budget.remaining if budget is not None else None,
            cancelled=budget.cancelled if budget is not None else None,
            call_id=budget.operation_id if budget is not None else None,
        )

        def checkpoint():
            if budget is not None:
                budget.checkpoint()

        checkpoint()
        document = observation.get("document")
        prepared = (
            prepare_document(document, public_api=observation.get("publicApi", False), control=control)
            if isinstance(document, DisplayDocument)
            else None
        )
        if prepared is not None and any(
            isinstance(part, DisplayTree) and part.details for part in prepared.request.document.parts
        ):
            # Explicit diagnostics must also be complete before delivery. Normal
            # full reads request the compact tree and keep raw details deferred.
            format_document(prepared.request.document, prepared.snapshots, public_api=observation.get("publicApi", False))
        checkpoint()
        baselines = []
        for value in self.baseline_inputs(observation):
            checkpoint()
            baselines.append(assemble_baseline().run(value, control))
        checkpoint()
        return dict(
            observation,
            presentationDraft=prepared,
            baselineDrafts=tuple(baselines),
            draftState=observation["state"],
            operationBudget=budget,
        )

    def text(self, observation):
        if "draftState" in observation and observation["draftState"] is not observation["state"]:
            raise ValueError("Prepared delivery belongs to another captured observation")
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
        if "baselineDrafts" in observation:
            if observation["draftState"] is not observation["state"]:
                raise ValueError("Prepared baseline belongs to another captured observation")
            for draft in observation["baselineDrafts"]:
                publish_observation(store=self).run(draft)
            return
        for value in self.baseline_inputs(observation):
            BaselinePublicationPipeline(self).run(value)

    def baseline_inputs(self, observation):
        """Describe exact comparison scopes without committing any of them."""
        trees = self.trees(observation)
        if not trees:
            yield BaselineInput(self.key(observation), observation)
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
            yield BaselineInput(self.tree_key(observation, tree), component)

    def publish_baseline(self, draft):
        baseline = {key: value for key, value in draft.baseline.items() if key != "source"}
        self._baselines.publish(draft.key, baseline)


class ObservationOutput:
    def __init__(self, presenter):
        self.presenter = presenter
        self._pending = {}

    def observe(self, observation):
        # App/Window aliases and tree/both views can describe the exact same
        # native components. Supersede only fully covered, unprinted captures;
        # a narrower view cannot erase a popup, retained image, gap or explicit
        # complete view. before-write/explicit-print boundaries still flush.
        incoming = {self.presenter.tree_key(observation, tree) for tree in self.presenter.trees(observation)}
        for previous_key, previous in tuple(self._pending.items()):
            if self.superseded(previous, observation, incoming):
                self._pending.pop(previous_key)
        key = self.presenter.key(observation)
        if observation.get("full") and observation["view"] != "desktop":
            key = key, id(observation.get("sourceState", observation["state"]))
        previous = self._pending.get(key)
        if previous is not None and self.presenter.trees(previous):
            # Same public scope is not proof that the new capture includes all
            # of its former native components (for example, a transient popup).
            old_key = key, id(previous.get("sourceState", previous["state"]))
            self._pending[old_key] = self._pending.pop(key)
        if key not in self._pending and len(self._pending) >= 32:
            raise RuntimeError(
                "Too many observation scopes in one call; inspect the collected results before reading more."
            )
        self._pending[key] = observation

    def superseded(self, previous, observation, incoming):
        trees = self.presenter.trees(previous)
        if (
            not trees or previous.get("full") or previous["state"].get("preparation")
            or previous.get("presentationGap") or observation.get("presentationGap")
        ):
            return False
        data = observation_data(previous.get("sourceState", previous["state"]))
        if data is not None and (data["partial"] or data["gaps"]):
            return False
        if not all(self.presenter.tree_key(previous, tree) in incoming for tree in trees):
            return False
        png = previous["state"].get("screenshot")
        if png is None:
            return True
        replacement = observation["state"].get("screenshot")
        if replacement is png:
            return True
        scope = image_output_scope(png)
        return replacement is not None and scope is not None and image_output_scope(replacement) == scope

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

    def flush(self, emit, emit_image=None):
        for observation in self._pending.values():
            if gap := observation.get("presentationGap"):
                emit(gap, observation)
            else:
                emit(self.presenter.text(observation), observation)
                self.presenter.delivered(observation)
            if (
                observation["view"] in ("window", "desktop")
                and observation["state"].get("screenshot") is not None
                and emit_image is not None
            ):
                emit_image(observation["state"]["screenshot"])
        self._pending.clear()
