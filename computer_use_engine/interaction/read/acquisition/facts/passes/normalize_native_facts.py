# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normalize native facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.model import RESOURCE, CollectedFacts
from computer_use.interaction.read.accessibility.frame import NativeReadCapture
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot
    from computer_use.interaction.read.acquisition.reader import NativeReader
    from computer_use.pipeline.core import Control


def create(*, reader: NativeReader) -> Callable[[CollectedFacts, Control], ReadSnapshot]:
    def normalize(value: CollectedFacts, control: Control) -> ReadSnapshot:
        """Normalize the collected batch using the reader's current session display aliases and
        provider facts.
        """
        target = value.profile.scope.target
        # normalizeFacts allocates/touches session aliases in its existing
        # order, including on a later normalization failure. This is a
        # RESOURCE stage, never falsely labelled as a pure transform.
        reply = value.reply
        if not isinstance(reply, NativeReadCapture):
            raise ValueError("The accessibility read did not return a Rust capture")
        capture = reply.capture
        references = reader.display_references(target.session)
        tokens = capture.source_tokens
        aliases = dict(zip(tokens, references.reference_batch(tokens), strict=True))
        return NativeSnapshot.number(capture, aliases)

    return normalize


stage = Pass.factory(
    name="normalize_native_facts",
    factory=create,
    contract=Contract(
        CollectedFacts,
        object,
        RESOURCE,
        requires=("raw native facts", "session reference owner"),
        writes=("session aliases", "numbered accessibility snapshot"),
    ),
)
