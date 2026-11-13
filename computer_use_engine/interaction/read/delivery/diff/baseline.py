# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Comparison baselines detached from captured accessibility trees."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.accessibility._native import require_read_engine
from computer_use.interaction.read.presentation.document import DisplayTree
from computer_use.interaction.read.presentation.sizing import DocumentSize

if TYPE_CHECKING:
    from collections.abc import Hashable

    from computer_use.interaction.read.presentation.document import DisplayDocument


_comparison_trees = WeakIdentityMap()
_current_trees = WeakIdentityMap()


@dataclass(frozen=True)
class ComparisonTree:
    scope: tuple[Hashable, ...]
    handle: object

    @classmethod
    def capture(cls, tree: DisplayTree, *, checkpoint=None):
        baseline = _comparison_trees.get(tree)
        if baseline is None:
            baseline = cls(tree.scope, native_current_tree(tree, checkpoint=checkpoint).baseline())
            _comparison_trees.set(tree, baseline)
        return baseline

    def to_data(self):
        return self.handle.to_data()


def native_current_tree(tree: DisplayTree, *, checkpoint=None):
    current = _current_trees.get(tree)
    if current is None:
        if checkpoint is not None:
            checkpoint()
        current = tree.prepared.handle.comparison(tree.scope, tree.indent, tree.details)
        if checkpoint is not None:
            checkpoint()
        _current_trees.set(tree, current)
    return current


@dataclass(frozen=True)
class ComparisonDocument:
    trees: tuple[ComparisonTree, ...]

    @classmethod
    def capture(cls, document: DisplayDocument, *, checkpoint=None):
        return cls(tuple(
            ComparisonTree.capture(part, checkpoint=checkpoint)
            for part in document.parts if isinstance(part, DisplayTree)
        ))

    def measure(self, measurement: DocumentSize) -> int:
        size = len('{"trees":[]}') + max(0, len(self.trees) - 1)
        for tree in self.trees:
            if measurement.checkpoint is not None:
                measurement.checkpoint()
            size += tree.handle.estimated_bytes()
            if measurement.checkpoint is not None:
                measurement.checkpoint()
        return size

    @cached_property
    def estimated_bytes(self):
        return self.measure(DocumentSize())

    def to_data(self):
        return {"trees": tuple(tree.to_data() for tree in self.trees)}

    def spill_data(self):
        return {"trees": tuple(tree.handle.to_spill() for tree in self.trees)}

    @classmethod
    def from_spill_data(cls, data):
        return cls(tuple(
            ComparisonTree(tuple(handle.scope()), handle)
            for serialized in data["trees"]
            for handle in (require_read_engine().restore_read_baseline(serialized),)
        ))
