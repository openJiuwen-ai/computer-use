# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from computer_use.pipeline import Effect

if TYPE_CHECKING:
    from computer_use.protocol.records import Record


PURE, RESOURCE = {Effect.PURE}, {Effect.RESOURCE}


@dataclass(frozen=True)
class BaselineInput:
    """Delivered observation to retain under its observation-stream key.

    Attributes:
        key: Observation-stream key used by the baseline store.
        observation: Delivered observation and its source-reference metadata.
    """

    key: str
    observation: Record


@dataclass(frozen=True)
class BaselineDraft:
    """Prepared delivery baseline with its retained-size accounting.

    Attributes:
        key: Observation-stream key used by the baseline store.
        baseline: Prepared tree and reference metadata retained for diff comparison.
        size: Retained baseline size used by the store's capacity accounting.
    """

    key: str
    baseline: Record
    size: int


if TYPE_CHECKING:
    from typing import Protocol

    class BaselineStore(Protocol):
        """Existing delivered-state owner; not an alternative history/cache.

        State updates stay with the owner; passes cannot edit its cache or byte
        counter independently.
        """

        def retire_baseline(self, key: str) -> None:
            pass

        def publish_baseline(self, draft: BaselineDraft) -> None:
            pass
